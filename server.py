r"""Local HTTP server for the Yoink browser extension.

Runs on http://127.0.0.1:5179. Pure stdlib — no fastapi/flask required.
Reuses parse_srt/slugify/fmt_time from yt_extract.py.

Endpoints:
    GET  /ping
    POST /extract           single-video, drops in Desktop\Yoink\
    POST /session/start
    POST /session/add       runs extraction into the session folder
    POST /session/close     concatenates per-video yoink.md files into corpus.md
    POST /session/cancel
    GET  /session/list
    GET  /session/active
"""

import json
import logging
import os
import re
import secrets
import subprocess
import sys
import threading
import urllib.request
import uuid
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

# --- Import helpers from the existing CLI script ---------------------------
HERE = Path(__file__).parent.resolve()
sys.path.insert(0, str(HERE))

# When shipped via the Windows installer, ffmpeg.exe lives next to server.py
# in a `bin\` folder. Prepend it to PATH so subprocess calls (`ffmpeg ...`)
# find the bundled binary without depending on the user's environment. No-op
# in dev where bin\ doesn't exist — falls back to whatever's on PATH.
_BIN_DIR = HERE / "bin"
if _BIN_DIR.is_dir():
    os.environ["PATH"] = str(_BIN_DIR) + os.pathsep + os.environ.get("PATH", "")

from yt_extract import parse_srt, slugify, fmt_time  # noqa: E402

# --- Constants -------------------------------------------------------------
HOST = "127.0.0.1"
PORT = 5179
VERSION = "1.0.0"
ALLOWED_ORIGINS = {
    "https://www.youtube.com",
    "https://m.youtube.com",
    "https://youtube.com",
}

CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0
SUBPROCESS_KW = {"creationflags": CREATE_NO_WINDOW} if sys.platform == "win32" else {}

# ---- Hardening limits (P1-3 / P1-4) ---------------------------------------
MAX_BODY_BYTES = 64 * 1024            # 64KB POST body cap
MAX_SCREENSHOTS = 200                  # cap per video
LONG_VIDEO_SECONDS = 2 * 60 * 60       # 2 hours -- log warning above this
YTDLP_TIMEOUT_SEC = 30 * 60            # main extract timeout
COMMENTS_TIMEOUT_SEC = 5 * 60
FFMPEG_TIMEOUT_SEC = 15 * 60

# ---- Auth token (P0-1) ----------------------------------------------------
# Per-install random token. Persisted next to server.py (which lives in
# %LOCALAPPDATA%\Yoink in the installed product, or in the dev repo
# directory in dev mode -- gitignored either way). The extension fetches
# this via /token (gated by chrome-extension:// origin) on first launch
# and includes it in X-Yoink-Token on every subsequent request.
TOKEN_PATH = HERE / "token.txt"


def _load_or_create_token() -> str:
    if TOKEN_PATH.exists():
        try:
            existing = TOKEN_PATH.read_text(encoding="utf-8").strip()
            if existing:
                return existing
        except OSError:
            pass
    fresh = secrets.token_urlsafe(32)
    try:
        TOKEN_PATH.write_text(fresh, encoding="utf-8")
        # Best-effort: tighten file perms on POSIX. On Windows, ACLs default
        # to user-only for files in %LOCALAPPDATA%, so chmod is a no-op but
        # harmless.
        try:
            os.chmod(TOKEN_PATH, 0o600)
        except OSError:
            pass
    except OSError:
        # Non-fatal: keep an in-memory token for the session. Persistence is
        # nice-to-have; auth still works within this server's lifetime.
        pass
    return fresh


TOKEN = _load_or_create_token()

# /token rate limit -- defends the relaxed Origin gate. The legitimate
# caller (the extension) fetches /token once per install plus the rare 403
# retry, so 10/min is comfortable for real use and tight enough that a
# noisy script can't grind through tokens hunting for racing conditions.
_TOKEN_RATE_LIMIT = 10
_TOKEN_RATE_WINDOW_SEC = 60.0
_token_request_times: list[float] = []
_token_rate_lock = threading.Lock()
_YOINK_CLIENT_HEADER_VALUE = "yoink-extension"


def _check_token_rate_limit() -> bool:
    import time
    now = time.monotonic()
    with _token_rate_lock:
        # Drop stale entries (older than the window) and decide.
        cutoff = now - _TOKEN_RATE_WINDOW_SEC
        kept = [t for t in _token_request_times if t > cutoff]
        if len(kept) >= _TOKEN_RATE_LIMIT:
            _token_request_times[:] = kept
            return False
        kept.append(now)
        _token_request_times[:] = kept
    return True

# Invoke yt-dlp via the same interpreter rather than relying on PATH. pip's
# --user install puts yt-dlp.exe in %APPDATA%\Python\PythonXX\Scripts which
# isn't on PATH by default on Windows, so a bare "yt-dlp" call fails.
YTDLP_CMD = [sys.executable, "-m", "yt_dlp"]

def _get_desktop_dir() -> Path:
    """Resolve the user's actual Desktop, honoring OneDrive Desktop
    redirection. Naive %USERPROFILE%\\Desktop misses users whose Desktop is
    redirected to OneDrive (Documents and Desktop opt-in by default in
    consumer OneDrive setups), and the yoinks would land in a directory the
    user can't see in Explorer."""
    fallback = Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Desktop"
    if sys.platform != "win32":
        return fallback
    try:
        import ctypes
        from ctypes import wintypes

        class _GUID(ctypes.Structure):
            _fields_ = [
                ("Data1", ctypes.c_uint32),
                ("Data2", ctypes.c_uint16),
                ("Data3", ctypes.c_uint16),
                ("Data4", ctypes.c_ubyte * 8),
            ]

        # FOLDERID_Desktop = {B4BFCC3A-DB2C-424C-B029-7FE99A87C641}
        FOLDERID_Desktop = _GUID(
            0xB4BFCC3A, 0xDB2C, 0x424C,
            (ctypes.c_ubyte * 8)(0xB0, 0x29, 0x7F, 0xE9, 0x9A, 0x87, 0xC6, 0x41),
        )
        SHGetKnownFolderPath = ctypes.windll.shell32.SHGetKnownFolderPath
        SHGetKnownFolderPath.argtypes = [
            ctypes.POINTER(_GUID),
            wintypes.DWORD,
            wintypes.HANDLE,
            ctypes.POINTER(ctypes.c_wchar_p),
        ]
        SHGetKnownFolderPath.restype = ctypes.c_long  # HRESULT

        out = ctypes.c_wchar_p()
        hr = SHGetKnownFolderPath(
            ctypes.byref(FOLDERID_Desktop), 0, None, ctypes.byref(out)
        )
        if hr == 0 and out.value:
            try:
                return Path(out.value)
            finally:
                ctypes.windll.ole32.CoTaskMemFree(out)
    except Exception:
        # Module loads before logging is configured and pythonw.exe has no
        # stderr, so we silently fall back. Users will still see their files
        # under %USERPROFILE%\\Desktop -- not optimal for OneDrive users,
        # but workable as a degraded mode.
        pass
    return fallback


DESKTOP_ROOT = _get_desktop_dir() / "Yoink"
SESSIONS_ROOT = DESKTOP_ROOT / "_sessions"

# --- Logging ---------------------------------------------------------------
LOG_PATH = HERE / "server.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("yoink")

# Serialize extractions — yt-dlp + ffmpeg are I/O heavy.
_extract_lock = threading.Lock()
# Serialize session.json mutations to keep the on-disk state consistent.
_session_lock = threading.Lock()

# Markers in yoink.md so the comments section can be replaced after the
# background fetch finishes. HTML comments are invisible in rendered markdown.
COMMENTS_START_MARK = "<!-- yoink:comments-start -->"
COMMENTS_END_MARK = "<!-- yoink:comments-end -->"


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------
def format_count(n) -> str:
    """13500 -> '13.5K', 1500000 -> '1.5M', 2_000_000_000 -> '2.0B'."""
    if n is None:
        return "—"
    try:
        n = int(n)
    except (TypeError, ValueError):
        return "—"
    if n < 0:
        return str(n)
    if n >= 1_000_000_000:
        return f"{n / 1_000_000_000:.1f}B"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


# Subscribers use the same compact format. Aliased so callers can read clearly.
format_subscribers = format_count


def format_duration(seconds) -> str:
    """3725 -> '01:02:05', 245 -> '04:05'."""
    if seconds is None:
        return "—"
    try:
        seconds = int(seconds)
    except (TypeError, ValueError):
        return "—"
    if seconds < 0:
        seconds = 0
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def _fmt_int(n) -> str:
    """29142 -> '29,142'. Used for views/likes/comments header fields."""
    if n is None:
        return "—"
    try:
        return f"{int(n):,}"
    except (TypeError, ValueError):
        return "—"


def _fmt_iso_date(s) -> str:
    """yt-dlp returns upload_date as 'YYYYMMDD'. Convert to 'YYYY-MM-DD'."""
    if not s:
        return "—"
    s = str(s)
    if len(s) == 8 and s.isdigit():
        return f"{s[0:4]}-{s[4:6]}-{s[6:8]}"
    return s


TOPICS_PATH = HERE / "topics.json"


def _load_topics() -> dict:
    """Read topics.json from project root. Returns a dict with a 'topics'
    list of {name, keywords} and a 'fallback' string. On any error (missing
    or malformed file) returns an empty topic list with a sane fallback so
    classification just degrades to 'Uncategorized'.
    """
    if not TOPICS_PATH.exists():
        log.warning("topics.json missing at %s — falling back to 'Uncategorized'",
                    TOPICS_PATH)
        return {"topics": [], "fallback": "Uncategorized"}
    try:
        return json.loads(TOPICS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        log.warning("topics.json read failed: %s", e)
        return {"topics": [], "fallback": "Uncategorized"}


def _classify_topic(metadata: dict) -> str:
    """Pick the best topic name for this video by counting keyword
    substring matches across the title, description, tags, channel, and
    uploader. Topic with the most matches wins; ties go to the topic
    defined first in topics.json. Falls back when nothing matches.
    """
    haystack = " ".join([
        metadata.get("title") or "",
        metadata.get("description") or "",
        " ".join(metadata.get("tags") or []),
        metadata.get("channel") or "",
        metadata.get("uploader") or "",
    ]).lower()

    cfg = _load_topics()
    fallback = (cfg.get("fallback") or "Uncategorized").strip() or "Uncategorized"
    best_name = fallback
    best_score = 0

    for t in cfg.get("topics", []):
        name = (t.get("name") or "").strip()
        kws = t.get("keywords") or []
        if not name or not kws:
            continue
        score = sum(1 for kw in kws if kw and str(kw).lower() in haystack)
        if score > best_score:
            best_score = score
            best_name = name

    return best_name


# Trim and normalize a topic name into a Windows-safe folder segment without
# stripping the spaces — we want "Social Media Research" on disk, not the
# slugified "Social_Media_Research".
_FORBIDDEN_PATH_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _topic_folder_name(topic: str) -> str:
    cleaned = _FORBIDDEN_PATH_CHARS.sub("", topic).strip().rstrip(".")
    return cleaned or "Uncategorized"


# ---------------------------------------------------------------------------
# Metadata, thumbnail, channel context, comments
# ---------------------------------------------------------------------------
def _fetch_metadata(url: str) -> dict:
    """Single yt-dlp call that returns the full metadata blob without
    downloading the video. Used to derive the folder slug, fill the corpus
    header, and seed the thumbnail URL.
    """
    raw = subprocess.check_output(
        [*YTDLP_CMD, "--dump-single-json", "--no-download", url],
        text=True, stderr=subprocess.PIPE, encoding="utf-8", errors="replace",
        timeout=COMMENTS_TIMEOUT_SEC,
        **SUBPROCESS_KW,
    )
    return json.loads(raw)


def _download_thumbnail(metadata: dict, output_folder: Path) -> Path | None:
    """Download highest-resolution thumbnail to <folder>/thumbnail.jpg.
    Always re-encodes through ffmpeg so the output is jpg even if YouTube
    served webp/png. Returns the jpg path on success, None on failure.
    """
    thumbs = metadata.get("thumbnails") or []
    candidates = [t for t in thumbs if t.get("url")]
    if candidates:
        candidates.sort(
            key=lambda t: (t.get("width") or 0) * (t.get("height") or 0),
            reverse=True,
        )
        url = candidates[0]["url"]
    else:
        url = metadata.get("thumbnail")
    if not url:
        return None

    raw_path = output_folder / "thumbnail.raw"
    try:
        with urllib.request.urlopen(url, timeout=30) as resp, open(raw_path, "wb") as f:
            f.write(resp.read())
    except Exception as e:
        log.warning("thumbnail download failed: %s", e)
        return None

    jpg_path = output_folder / "thumbnail.jpg"
    try:
        subprocess.run(
            ["ffmpeg", "-loglevel", "error", "-y",
             "-i", str(raw_path), str(jpg_path)],
            check=True, stderr=subprocess.PIPE, **SUBPROCESS_KW,
        )
    except subprocess.CalledProcessError as e:
        stderr = (e.stderr or b"").decode("utf-8", errors="ignore").strip()
        log.warning("ffmpeg thumbnail convert failed: %s", stderr)
        return None
    finally:
        raw_path.unlink(missing_ok=True)

    return jpg_path if jpg_path.exists() else None


def _fetch_channel_context(channel_url: str) -> dict:
    """Best-effort fetch of channel description + last 5 video stubs.
    Returns {'description': str, 'recent_videos': [{title, view_count,
    upload_date}, ...]}. Empty dict-shape on failure.
    """
    empty = {"description": "", "recent_videos": []}
    if not channel_url:
        return empty

    # Prefer the /videos tab so we get videos (not playlists/shorts/featured).
    target = channel_url.rstrip("/")
    if not target.endswith("/videos"):
        target = target + "/videos"

    try:
        raw = subprocess.check_output(
            [*YTDLP_CMD, "--dump-single-json", "--flat-playlist",
             "--playlist-end", "5", target],
            text=True, stderr=subprocess.PIPE, encoding="utf-8", errors="replace",
            **SUBPROCESS_KW,
        )
    except Exception as e:
        log.warning("channel context fetch failed: %s", e)
        return empty

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        log.warning("channel context parse failed: %s", e)
        return empty

    description = (data.get("description") or "").strip()
    entries = data.get("entries") or []
    recent = []
    for e in entries[:5]:
        if not isinstance(e, dict):
            continue
        recent.append({
            "title": e.get("title") or "",
            "view_count": e.get("view_count"),
            "upload_date": e.get("upload_date"),
        })
    return {"description": description, "recent_videos": recent}


def _render_comments(comments: list[dict]) -> str:
    """Render top comments as markdown. Each: bold author + meta, then
    blockquoted body. Preserves line breaks within a comment.
    """
    out = []
    for c in comments:
        author = (c.get("author") or "Anonymous").strip() or "Anonymous"
        text = (c.get("text") or "").strip()
        likes = c.get("like_count") or 0
        time_text = (c.get("time_text") or "").strip()
        meta = f"{format_count(likes)} likes"
        if time_text:
            meta += f", {time_text}"
        out.append(f"**{author}** ({meta})")
        for ln in (text.splitlines() or [""]):
            out.append(f"> {ln}" if ln else ">")
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def _replace_comments_section(yoink_path: Path, body: str) -> None:
    """Atomically rewrite the COMMENTS_START..COMMENTS_END block in yoink.md.
    Safe to call from a background thread.
    """
    try:
        text = yoink_path.read_text(encoding="utf-8")
    except OSError as e:
        log.warning("could not read yoink.md to update comments: %s", e)
        return

    pattern = re.compile(
        re.escape(COMMENTS_START_MARK) + r".*?" + re.escape(COMMENTS_END_MARK),
        re.DOTALL,
    )
    replacement = f"{COMMENTS_START_MARK}\n{body.rstrip()}\n{COMMENTS_END_MARK}"
    new_text, n = pattern.subn(replacement, text, count=1)
    if n == 0:
        log.warning("comments markers not found in yoink.md; skipping update")
        return

    tmp = yoink_path.with_suffix(".md.tmp")
    try:
        tmp.write_text(new_text, encoding="utf-8")
        tmp.replace(yoink_path)
    except OSError as e:
        log.warning("could not write yoink.md to update comments: %s", e)


def _comments_worker(url: str, output_folder: Path, yoink_path: Path,
                     max_comments: int = 100, top_n: int = 50) -> None:
    """Background-thread body. Fetches comments via yt-dlp, then rewrites
    the comments section of yoink.md in place. Never raises — failures
    just leave a 'disabled or unavailable' note.
    """
    try:
        info_template = output_folder / "%(id)s_yoink_comments.%(ext)s"
        subprocess.run(
            [*YTDLP_CMD,
             "--write-info-json",
             "--write-comments",
             "--skip-download",
             "--extractor-args",
             f"youtube:max_comments={max_comments},all,all,all",
             "-o", str(info_template),
             url],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            timeout=COMMENTS_TIMEOUT_SEC,
            **SUBPROCESS_KW,
        )
        info_files = list(output_folder.glob("*_yoink_comments.info.json"))
        if not info_files:
            log.warning("comments info.json not found for %s", url)
            _replace_comments_section(yoink_path,
                "*Comments could not be retrieved.*")
            return
        info = json.loads(info_files[0].read_text(encoding="utf-8"))
        raw_comments = info.get("comments") or []
        if not raw_comments:
            _replace_comments_section(yoink_path,
                "*Comments are disabled on this video.*")
            return
        ranked = sorted(
            raw_comments,
            key=lambda c: c.get("like_count") or 0,
            reverse=True,
        )[:top_n]
        _replace_comments_section(yoink_path, _render_comments(ranked))
        log.info("comments appended to %s (%d of %d)",
                 yoink_path, len(ranked), len(raw_comments))
    except subprocess.CalledProcessError as e:
        stderr = (e.stderr or b"").decode("utf-8", errors="ignore").strip()
        log.warning("comments yt-dlp failed: %s", stderr.splitlines()[-1] if stderr else e.returncode)
        _replace_comments_section(yoink_path,
            "*Comments are disabled on this video.*")
    except Exception as e:
        log.warning("comments worker crashed: %s", e)
        _replace_comments_section(yoink_path,
            "*Comments could not be retrieved.*")


def _start_comments_thread(url: str, output_folder: Path,
                           yoink_path: Path) -> threading.Thread:
    t = threading.Thread(
        target=_comments_worker,
        args=(url, output_folder, yoink_path),
        name=f"comments-{output_folder.name}",
        daemon=True,
    )
    t.start()
    return t


# ---------------------------------------------------------------------------
# yoink.md builder
# ---------------------------------------------------------------------------
def _build_yoink_md(metadata: dict, url: str, entries: list, shots: list,
                    interval: int, channel_ctx: dict,
                    yoinked_at: str, topic: str,
                    cap_warning: str | None = None) -> str:
    """Produce the v1 corpus markdown. Comments section is a placeholder
    that the background worker rewrites once the fetch completes.
    """
    title = metadata.get("title") or "Untitled"
    channel = metadata.get("channel") or metadata.get("uploader") or "—"
    sub_count = format_subscribers(metadata.get("channel_follower_count"))
    upload_date = _fmt_iso_date(metadata.get("upload_date"))
    duration = format_duration(metadata.get("duration"))
    views = _fmt_int(metadata.get("view_count"))
    likes = _fmt_int(metadata.get("like_count"))
    description = (metadata.get("description") or "").strip()
    tags = metadata.get("tags") or []
    chapters = metadata.get("chapters") or []

    parts: list[str] = []
    parts.append(f"# {title}")
    parts.append("")
    parts.append(f"**Channel:** {channel} ({sub_count} subscribers)")
    parts.append(
        f"**Uploaded:** {upload_date} | **Duration:** {duration} | "
        f"**Views:** {views} | **Likes:** {likes}"
    )
    parts.append(f"**URL:** {url}")
    parts.append(f"**Yoinked:** {yoinked_at}")
    parts.append(f"**Topic:** {topic}")
    if cap_warning:
        parts.append(f"**Note:** {cap_warning}")
    parts.append("")
    parts.append("---")
    parts.append("")

    # Thumbnail
    parts.append("## Thumbnail")
    parts.append("")
    parts.append("![Thumbnail](thumbnail.jpg)")
    parts.append("")

    # Description
    parts.append("## Description")
    parts.append("")
    parts.append(description if description else "*No description.*")
    parts.append("")

    # Tags
    parts.append("## Tags")
    parts.append("")
    parts.append(", ".join(tags) if tags else "No tags")
    parts.append("")
    parts.append("---")
    parts.append("")

    # Transcript
    parts.append("## Transcript")
    parts.append("")
    if not entries:
        parts.append("*No captions available for this video.*")
        parts.append("")
    else:
        if chapters:
            # Group entries by chapter ranges. Chapters have start_time/end_time.
            for ch in chapters:
                ch_start = ch.get("start_time") or 0
                ch_end = ch.get("end_time")
                ch_title = ch.get("title") or "Chapter"
                parts.append(f"### Chapter: {ch_title} ({fmt_time(int(ch_start))})")
                parts.append("")
                for s, _e, t in entries:
                    if s < ch_start:
                        continue
                    if ch_end is not None and s >= ch_end:
                        continue
                    parts.append(f"[{fmt_time(int(s))}] {t}")
                parts.append("")
        else:
            for s, _e, t in entries:
                parts.append(f"[{fmt_time(int(s))}] {t}")
            parts.append("")
    parts.append("---")
    parts.append("")

    # Screenshots
    parts.append("## Screenshots")
    parts.append("")
    for i, shot in enumerate(shots):
        start = i * interval
        ts = fmt_time(start)
        parts.append(f"### [{ts}]")
        parts.append("")
        parts.append(f"![Screenshot at {ts}](screenshots/{shot.name})")
        parts.append("")
    parts.append("---")
    parts.append("")

    # Top Comments — placeholder, filled in by the background worker.
    parts.append("## Top Comments")
    parts.append("")
    parts.append(COMMENTS_START_MARK)
    parts.append("*Fetching comments... they'll appear here when ready.*")
    parts.append(COMMENTS_END_MARK)
    parts.append("")
    parts.append("---")
    parts.append("")

    # Channel Context
    parts.append("## Channel Context")
    parts.append("")
    parts.append(f"**About {channel}:**")
    ch_desc = (channel_ctx.get("description") or "").strip()
    parts.append(ch_desc if ch_desc else "*No channel description available.*")
    parts.append("")
    parts.append("**Recent videos from this channel:**")
    recent = channel_ctx.get("recent_videos") or []
    if not recent:
        parts.append("- *No recent videos found.*")
    else:
        for v in recent:
            v_title = v.get("title") or "(untitled)"
            v_views = format_count(v.get("view_count"))
            v_date = _fmt_iso_date(v.get("upload_date"))
            parts.append(f"- {v_title} ({v_views} views, {v_date})")
    parts.append("")
    parts.append("---")
    parts.append("")
    parts.append("*[Yoinked with Yoink by ReplayRyan](https://yoink.video)*")
    parts.append("")

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Extraction core (shared by /extract and /session/add)
# ---------------------------------------------------------------------------
def _run_extraction(url: str, interval: int, output_folder: Path,
                    *, open_explorer: bool = True,
                    metadata: dict | None = None,
                    topic: str | None = None) -> dict:
    """Yoink a single video into output_folder.

    Steps:
      1. Fetch full metadata (cached as metadata.json) — already done if the
         caller passed `metadata` (avoids a second yt-dlp call).
      2. Download highest-res thumbnail to thumbnail.jpg.
      3. Download video + subs, run ffmpeg screenshots, parse the SRT.
      4. Fetch lightweight channel context (description + last 5 videos).
      5. Write yoink.md with a placeholder Top Comments section.
      6. Spawn a background thread that fetches comments and rewrites
         the comments block in place.
    Returns a dict with folder, yoink_md (current text), screenshot_count,
    title, video_slug, caption_count.
    """
    output_folder.mkdir(parents=True, exist_ok=True)

    if metadata is None:
        metadata = _fetch_metadata(url)
    if topic is None:
        topic = _classify_topic(metadata)

    title = metadata.get("title") or "Untitled"
    video_slug = slugify(title) or "video"
    log.info("Yoinking '%s' -> %s (topic=%s)", title, output_folder, topic)

    # P1-4: bound screenshot count so a 4-hour video at 5s interval doesn't
    # produce thousands of jpgs. Recompute interval upward when needed and
    # surface the change in the corpus md.
    duration = float(metadata.get("duration") or 0)
    if duration > LONG_VIDEO_SECONDS:
        log.warning("Long video: %.0f minutes -- yoink may take a while",
                    duration / 60.0)
    requested_interval = interval
    cap_warning: str | None = None
    if duration > 0 and (duration / max(1, interval)) > MAX_SCREENSHOTS:
        # Round up so we land at <= MAX_SCREENSHOTS shots, not slightly over.
        new_interval = max(interval, int((duration + MAX_SCREENSHOTS - 1) // MAX_SCREENSHOTS))
        cap_warning = (
            f"Capped screenshots at {MAX_SCREENSHOTS}: interval raised from "
            f"{requested_interval}s to {new_interval}s for this video "
            f"(duration {int(duration // 60)}m)."
        )
        log.warning(cap_warning)
        interval = new_interval

    # Persist the raw metadata blob for debugging without re-downloading.
    try:
        (output_folder / "metadata.json").write_text(
            json.dumps(metadata, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError as e:
        log.warning("could not write metadata.json: %s", e)

    # Thumbnail (best-effort; absence shouldn't fail the extraction).
    _download_thumbnail(metadata, output_folder)

    # Video + subs. Bounded to YTDLP_TIMEOUT_SEC so a stuck download doesn't
    # hold _extract_lock forever and block other yoinks.
    try:
        subprocess.run(
            [
                *YTDLP_CMD,
                "--write-auto-subs",
                "--write-subs",
                "--sub-lang", "en.*,en",
                "--convert-subs", "srt",
                "-f", "worst[height>=360]/worst",
                "-o", str(output_folder / "video.%(ext)s"),
                url,
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=YTDLP_TIMEOUT_SEC,
            **SUBPROCESS_KW,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(
            "Video too long for current settings -- try again with a longer "
            "screenshot interval, or this video may be too long for Yoink."
        )

    video_files = [f for f in output_folder.glob("video.*")
                   if f.suffix in (".mp4", ".webm", ".mkv")]
    srt_files = list(output_folder.glob("video*.srt"))
    if not video_files:
        raise RuntimeError("yt-dlp finished but no video file was produced.")
    video_file = video_files[0]

    shots_dir = output_folder / "screenshots"
    shots_dir.mkdir(exist_ok=True)
    try:
        subprocess.run(
            [
                "ffmpeg", "-loglevel", "error", "-y",
                "-i", str(video_file),
                "-vf", f"fps=1/{interval}",
                "-q:v", "2",
                str(shots_dir / "shot_%04d.jpg"),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=FFMPEG_TIMEOUT_SEC,
            **SUBPROCESS_KW,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(
            "Screenshot generation timed out -- try a longer screenshot "
            "interval (current: %ds)." % interval
        )
    shots = sorted(shots_dir.glob("shot_*.jpg"))

    entries = list(parse_srt(srt_files[0])) if srt_files else []

    if entries:
        plain = "\n".join(text for _, _, text in entries)
        (output_folder / "transcript.txt").write_text(plain, encoding="utf-8")

    # Channel context (description + recent videos). Best-effort.
    channel_url = (metadata.get("channel_url")
                   or metadata.get("uploader_url")
                   or "")
    channel_ctx = _fetch_channel_context(channel_url)

    # Build the corpus markdown.
    yoink_md = _build_yoink_md(
        metadata=metadata, url=url, entries=entries, shots=shots,
        interval=interval, channel_ctx=channel_ctx,
        yoinked_at=_now_iso(), topic=topic,
        cap_warning=cap_warning,
    )
    # Filename matches the folder's slug -- "kapathy-talk/kapathy-talk.md"
    # rather than "kapathy-talk/yoink.md" -- so the file is identifiable
    # outside its folder.
    yoink_path = _corpus_path(output_folder)
    yoink_path.write_text(yoink_md, encoding="utf-8")

    # Structured JSON sidecar (STRAT). Same data the markdown carries but
    # in a machine-shaped form: future MCP server / programmatic tooling
    # consumes this without having to parse the human-facing md. Written
    # next to the md so it travels with the folder.
    try:
        sidecar = {
            "schema_version": 1,
            "url": url,
            "title": title,
            "topic": topic,
            "yoinked_at": _now_iso(),
            "interval_seconds": interval,
            "requested_interval_seconds": requested_interval,
            "screenshot_cap_warning": cap_warning,
            "duration_seconds": duration,
            "channel": metadata.get("channel") or metadata.get("uploader"),
            "channel_url": metadata.get("channel_url") or metadata.get("uploader_url"),
            "upload_date": metadata.get("upload_date"),
            "view_count": metadata.get("view_count"),
            "like_count": metadata.get("like_count"),
            "video_id": metadata.get("id"),
            "transcript": [
                {"start": s, "end": e, "text": t} for s, e, t in entries
            ],
            "screenshots": [
                f"screenshots/{p.name}" for p in shots
            ],
            "channel_context": channel_ctx,
        }
        sidecar_path = output_folder / f"{output_folder.name}.json"
        sidecar_path.write_text(
            json.dumps(sidecar, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except (OSError, TypeError) as e:
        # Non-fatal: the markdown is the user-facing artifact. Sidecar is
        # for future tooling.
        log.warning("could not write JSON sidecar: %s", e)

    video_file.unlink(missing_ok=True)

    # Refresh the master _all-yoinks-index.md after every successful yoink.
    # Cheap (one stat per video folder), and re-scanning means a folder the
    # user manually deleted simply drops out of the index next time.
    _regenerate_index()

    # Comments fetch in background; updates the corpus file when done.
    _start_comments_thread(url, output_folder, yoink_path)

    if open_explorer:
        try:
            os.startfile(str(output_folder))  # type: ignore[attr-defined]
        except Exception as e:
            log.warning("startfile failed: %s", e)

    return {
        "ok": True,
        "folder": str(output_folder),
        "yoink_md": yoink_md,
        "screenshot_count": len(shots),
        "title": title,
        "video_slug": video_slug,
        "caption_count": len(entries),
        "topic": topic,
    }


INSTALL_HELP_URL = "https://yoink.video/install"


def friendly_error(e: BaseException) -> str:
    """Translate raw exceptions into copy the user can act on."""
    if isinstance(e, FileNotFoundError):
        return ("Yoink can't find yt-dlp or ffmpeg on this machine. "
                f"Install both, then try again. See {INSTALL_HELP_URL}")

    if isinstance(e, subprocess.CalledProcessError):
        stderr = (e.stderr.decode("utf-8", errors="ignore") if isinstance(e.stderr, bytes)
                  else (e.stderr or "")).strip()
        # Match known YouTube failures so the user doesn't see a yt-dlp stack.
        if "Sign in to confirm you're not a bot" in stderr or "captcha" in stderr.lower():
            return ("YouTube wants a sign-in check. Open YouTube in this browser, "
                    "sign in once, then try again.")
        if "Video unavailable" in stderr or "This video is private" in stderr:
            return "This video isn't available (private, deleted, or region-locked)."
        if "Members-only" in stderr or "members only" in stderr.lower():
            return "Members-only video — Yoink can't reach it without an account."
        if "is live" in stderr.lower() or "premiere" in stderr.lower():
            return "Yoink can't grab livestreams or premieres yet. Try again after the broadcast ends."
        if "HTTP Error 429" in stderr:
            return "YouTube is rate-limiting. Wait a minute, then try again."

        last = stderr.splitlines()[-1] if stderr else f"exit code {e.returncode}"
        tool = Path(e.cmd[0]).name if e.cmd else "subprocess"
        # Strip yt-dlp's "ERROR:" prefix if present so the message doesn't shout.
        last = re.sub(r"^ERROR:\s*", "", last)
        return f"Yoink hit an error from {tool}: {last}"

    if isinstance(e, RuntimeError):
        return f"Yoink couldn't finish this video: {e}"

    return f"Yoink hit an unexpected error: {e}"


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------
_YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"}
# ASCII-explicit so non-ASCII unicode word chars can't sneak through \w.
_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{6,}$")
_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _normalize_youtube_url(raw: str) -> str | None:
    """Parse the URL, verify the hostname is in the YouTube allowlist, pull
    the video ID, and return the canonical https://www.youtube.com/watch?v=
    form. Returns None for anything that isn't a real YouTube video URL --
    bare strings, attacker-shaped URLs like https://evil.com/youtube.com/x,
    non-video YouTube paths (channels, search), etc.
    """
    if not raw:
        return None
    try:
        u = urlparse(raw if "://" in raw else "https://" + raw)
    except ValueError:
        return None
    host = (u.hostname or "").lower()
    if host not in _YOUTUBE_HOSTS:
        return None

    video_id = None
    if host == "youtu.be":
        first = (u.path or "").lstrip("/").split("/", 1)[0]
        if _VIDEO_ID_RE.match(first):
            video_id = first
    else:
        if u.path == "/watch":
            qs = parse_qs(u.query)
            v = (qs.get("v") or [""])[0]
            if _VIDEO_ID_RE.match(v):
                video_id = v
        elif u.path.startswith("/shorts/"):
            seg = u.path.split("/", 3)[2] if len(u.path.split("/", 3)) > 2 else ""
            if _VIDEO_ID_RE.match(seg):
                video_id = seg
        elif u.path.startswith("/embed/"):
            seg = u.path.split("/", 3)[2] if len(u.path.split("/", 3)) > 2 else ""
            if _VIDEO_ID_RE.match(seg):
                video_id = seg
    if not video_id:
        return None
    return f"https://www.youtube.com/watch?v={video_id}"


INDEX_FILENAME = "_all-yoinks-index.md"


def _index_path() -> Path:
    """Master index location -- DESKTOP_ROOT/_all-yoinks-index.md. Leading
    underscore keeps it sorted to the top in Explorer."""
    return DESKTOP_ROOT / INDEX_FILENAME


def _corpus_path(folder: Path) -> Path:
    """Canonical corpus file path: <folder>/<folder.name>.md.

    Per-video filename matches the folder's slug so the file stays
    identifiable when moved out of its folder, and so the master index can
    link to it cleanly. The legacy filename was always 'yoink.md', which
    made every corpus indistinguishable once dragged out."""
    return folder / f"{folder.name}.md"


def _resolve_corpus_path(folder: Path) -> Path | None:
    """Return the corpus md file in `folder`, falling back to the legacy
    yoink.md name if the new <slug>.md isn't there yet. Returns None if
    neither exists."""
    candidate = _corpus_path(folder)
    if candidate.exists():
        return candidate
    legacy = folder / "yoink.md"
    if legacy.exists():
        return legacy
    return None


def _scan_yoinks() -> list[dict]:
    """Walk DESKTOP_ROOT/<topic>/<slug>/ and collect index metadata for
    every per-video yoink that still exists on disk. Folders the user has
    deleted simply drop out of future regenerations -- the index reflects
    what's actually there now, not historical state.

    Dedupes by URL: if the same video URL appears in two folders (e.g.,
    user yoinked it once, renamed the title in YouTube, yoinked again),
    keep the most recent. Falls back to relative path when URL is missing.

    Skips _sessions/ and any other underscore-prefixed top-level folder
    (the index file itself lives there, plus future internal folders)."""
    if not DESKTOP_ROOT.exists():
        return []
    by_key: dict[str, dict] = {}
    for topic_dir in DESKTOP_ROOT.iterdir():
        if not topic_dir.is_dir():
            continue
        if topic_dir.name.startswith("_") or topic_dir.name.startswith("."):
            continue
        topic = topic_dir.name
        for video_dir in topic_dir.iterdir():
            if not video_dir.is_dir():
                continue
            corpus = _resolve_corpus_path(video_dir)
            if corpus is None:
                continue

            title = video_dir.name
            url = ""
            channel = ""
            meta_path = video_dir / "metadata.json"
            if meta_path.exists():
                try:
                    m = json.loads(meta_path.read_text(encoding="utf-8"))
                    title = m.get("title") or title
                    url = (m.get("webpage_url")
                           or m.get("original_url") or "")
                    channel = (m.get("channel") or m.get("uploader") or "")
                except (OSError, json.JSONDecodeError):
                    pass

            mtime = corpus.stat().st_mtime
            yoinked_at = datetime.fromtimestamp(mtime).date().isoformat()
            rel_path = f"{topic}/{video_dir.name}/{corpus.name}"
            entry = {
                "title": title,
                "topic": topic,
                "channel": channel,
                "yoinked_at": yoinked_at,
                "yoinked_at_ts": mtime,
                "rel_path": rel_path,
                "url": url,
            }

            key = url or rel_path
            existing = by_key.get(key)
            if existing is None or mtime > existing["yoinked_at_ts"]:
                by_key[key] = entry
    return list(by_key.values())


def _render_index(entries: list[dict]) -> str:
    """Markdown for _all-yoinks-index.md. Topic sections sorted A-Z; videos
    within each topic sorted most-recent first. 'Recent (last 20)' section
    at the bottom for a quick chronological view."""
    parts = [
        "# All Yoinks",
        f"_Last updated: {_now_iso()}_  ",
        f"_Total yoinks: {len(entries)}_",
        "",
    ]

    if not entries:
        parts.append("_No yoinks yet. Click the orange Y on any YouTube video to start._")
        parts.append("")
        return "\n".join(parts)

    # By topic
    parts.append("## By topic")
    parts.append("")
    by_topic: dict[str, list[dict]] = {}
    for e in entries:
        by_topic.setdefault(e["topic"], []).append(e)
    for topic in sorted(by_topic.keys(), key=str.lower):
        items = sorted(by_topic[topic], key=lambda x: x["yoinked_at_ts"], reverse=True)
        plural = "" if len(items) == 1 else "s"
        parts.append(f"### {topic} ({len(items)} yoink{plural})")
        for e in items:
            byline = f" -- {e['channel']}" if e["channel"] else ""
            parts.append(
                f"- [{e['title']}]({_md_link_path(e['rel_path'])}) "
                f"-- Yoinked {e['yoinked_at']}{byline}"
            )
        parts.append("")

    # Recent (last 20)
    recent = sorted(entries, key=lambda x: x["yoinked_at_ts"], reverse=True)[:20]
    parts.append("## Recent (last 20)")
    parts.append("")
    for e in recent:
        parts.append(
            f"- [{e['title']}]({_md_link_path(e['rel_path'])}) -- {e['yoinked_at']}"
        )
    parts.append("")

    return "\n".join(parts)


def _md_link_path(rel: str) -> str:
    """Markdown links want forward slashes. On Windows our Path joins
    produce backslashes; replace so Obsidian / VS Code preview / GitHub
    render the link correctly."""
    return rel.replace("\\", "/")


def _regenerate_index() -> None:
    """Rebuild _all-yoinks-index.md from a fresh scan of DESKTOP_ROOT.

    Best-effort: failures here shouldn't fail the yoink that triggered the
    regeneration, so we log + swallow rather than raise. Runs synchronously
    after each successful extraction; the scan is small (one stat per
    video folder) and dwarfed by the actual extraction cost."""
    try:
        entries = _scan_yoinks()
        DESKTOP_ROOT.mkdir(parents=True, exist_ok=True)
        _index_path().write_text(_render_index(entries), encoding="utf-8")
    except Exception as e:
        log.warning("index regeneration failed: %s", e)


def _is_valid_session_id(s: str) -> bool:
    """Session IDs become path segments under SESSIONS_ROOT, so anything
    that isn't a strict alphanumeric+_- token would let a caller traverse
    the filesystem (../, absolute paths, drive letters)."""
    return bool(s) and bool(_SESSION_ID_RE.match(s))


# ---------------------------------------------------------------------------
# Session helpers
# ---------------------------------------------------------------------------
def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _session_folder(slug: str) -> Path:
    return SESSIONS_ROOT / slug


def _read_session(slug: str) -> dict | None:
    path = _session_folder(slug) / "session.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        log.warning("Failed to read session %s: %s", slug, e)
        return None


def _write_session(slug: str, data: dict) -> None:
    folder = _session_folder(slug)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "session.json"
    tmp = folder / "session.json.tmp"
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(path)


def _all_sessions() -> list[dict]:
    if not SESSIONS_ROOT.exists():
        return []
    out = []
    for sub in SESSIONS_ROOT.iterdir():
        if not sub.is_dir():
            continue
        data = _read_session(sub.name)
        if data:
            out.append(data)
    out.sort(key=lambda d: d.get("created_at", ""), reverse=True)
    return out


def _active_session() -> dict | None:
    for s in _all_sessions():
        if s.get("status") == "open":
            return s
    return None


def _demote_headings(md: str) -> str:
    """Demote H1/H2 in a video's yoink.md so they nest under the corpus's H2.

    H1 -> H3, H2 -> H3 (we want everything below the per-video heading to read
    as a sub-section, but timestamp headings can stay at the same depth).
    """
    out_lines = []
    for ln in md.splitlines():
        m = re.match(r"^(#+)(\s)", ln)
        if m:
            level = len(m.group(1))
            new_level = min(level + 2, 6)
            ln = "#" * new_level + ln[level:]
        out_lines.append(ln)
    return "\n".join(out_lines)


def _build_corpus(session: dict) -> str:
    name = session.get("name") or session.get("slug")
    created = session.get("created_at", "")
    videos = session.get("videos", [])
    folder = _session_folder(session["slug"])

    parts = [
        f"# Research Session: {name}",
        f"# Created: {created}",
        f"# Videos: {len(videos)}",
        "",
        "---",
        "",
    ]
    for i, v in enumerate(videos, 1):
        title = v.get("title", "(unknown)")
        url = v.get("url", "")
        video_slug = v.get("video_slug", "")
        rel = f"{video_slug}/"
        # Resolver handles both <slug>.md (new) and yoink.md (legacy folders
        # captured before the rename).
        yoink_path = _resolve_corpus_path(folder / video_slug)

        parts.append(f"## Video {i}: {title}")
        parts.append(f"Source: {url}")
        parts.append(f"Local folder: {rel}")
        parts.append("")

        if yoink_path is not None and yoink_path.exists():
            try:
                body = yoink_path.read_text(encoding="utf-8")
                # Strip the per-video H1 (the title) -- we already emitted Video N: title.
                body = re.sub(r"^# .+\n", "", body, count=1)
                # Strip the leading metadata lines we'd duplicate (URL/Yoinked/etc.).
                # The bold-prefixed lines come right after the title block.
                body = re.sub(r"^(\*\*[^*]+:\*\*[^\n]*\n)+", "", body)
                parts.append(_demote_headings(body.strip()))
            except OSError as e:
                parts.append(f"> _Failed to read corpus file: {e}_")
        else:
            parts.append("> _Corpus file not found -- extraction may have failed._")

        parts.append("")
        parts.append("---")
        parts.append("")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    server_version = f"Yoink/{VERSION}"

    def log_message(self, fmt, *args):
        return

    # ---- CORS helpers ----
    def _cors_origin(self) -> str | None:
        origin = self.headers.get("Origin")
        if not origin:
            return None
        if origin in ALLOWED_ORIGINS:
            return origin
        # Some Chromium builds send the extension origin instead of the page
        # origin for content-script fetches.
        if origin.startswith("chrome-extension://"):
            return origin
        return None

    def _send_cors(self, origin: str | None):
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            # X-Yoink-Token is the auth header the extension sends on every
            # mutating request. X-Yoink-Client is the /token gate header.
            # Browsers won't send custom headers without the OPTIONS
            # preflight allowing them explicitly.
            self.send_header("Access-Control-Allow-Headers",
                             "Content-Type, X-Yoink-Token, X-Yoink-Client")
            self.send_header("Access-Control-Max-Age", "600")
            # Private Network Access: Chrome requires this header when a public
            # HTTPS origin (youtube.com) fetches a loopback resource. Without
            # it the preflight is rejected and fetch fails as "Failed to fetch"
            # before any visible request reaches the handler.
            self.send_header("Access-Control-Allow-Private-Network", "true")

    # ---- Auth helpers ----
    def _request_token(self) -> str:
        """Pull the auth token from either the X-Yoink-Token header or a
        ?token=... query param. Header is preferred."""
        h = self.headers.get("X-Yoink-Token")
        if h:
            return h.strip()
        try:
            qs = parse_qs(urlparse(self.path).query)
            return (qs.get("token") or [""])[0].strip()
        except Exception:
            return ""

    def _check_token(self) -> bool:
        return secrets.compare_digest(self._request_token(), TOKEN)

    def _is_extension_origin(self) -> bool:
        """True if Origin looks like a browser extension OR is absent.
        Some Chromium forks (Comet, observed in v1 testing) issue
        same-process service-worker fetches with no Origin header at all,
        so a strict allowlist locks them out. Browser-side CSRF defense
        moves to the X-Yoink-Client header gate + the existing CORS ACAO
        allowlist; see docs/security.md."""
        origin = (self.headers.get("Origin", "") or "")
        if not origin:
            return True
        return (origin.startswith("chrome-extension://")
                or origin.startswith("moz-extension://"))

    def _has_yoink_client_header(self) -> bool:
        """Defense-in-depth header that the extension sets on /token. A
        webpage can't set custom request headers cross-origin without
        triggering a CORS preflight, and our preflight only echoes ACAO
        for chrome-extension://* + the YouTube allowlist -- so the actual
        request from a malicious origin is blocked by the browser before
        it even runs the GET."""
        return self.headers.get("X-Yoink-Client", "").strip() == _YOINK_CLIENT_HEADER_VALUE

    def _require_token(self) -> bool:
        """Returns True if request authenticates. Otherwise sends a 403 and
        returns False -- caller should `return` immediately."""
        if self._check_token():
            return True
        log.info("auth: rejected %s %s (token mismatch)",
                 self.command, self.path.split("?", 1)[0])
        self._send_json(403, {"ok": False, "error": "missing or invalid token"})
        return False

    def _send_json(self, status: int, payload: dict):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._send_cors(self._cors_origin())
        self.end_headers()
        self.wfile.write(body)

    # Sentinel raised by _read_json_body when validation fails. Carries the
    # HTTP status the caller should send back. Keeps the caller code simple
    # (one try/except instead of three checks per endpoint).
    class _BodyError(Exception):
        def __init__(self, status: int, message: str):
            super().__init__(message)
            self.status = status
            self.message = message

    def _read_json_body(self) -> dict:
        # P1-3: bound everything we trust from the network. Without these
        # checks Content-Length was unbounded (memory exhaustion via large
        # POST), Content-Type was unchecked (HTML form posts could trigger
        # mutations), and a JSON array body would blow up later code that
        # called body.get(...).
        ctype = (self.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
        if ctype != "application/json":
            raise Handler._BodyError(415, "Content-Type must be application/json")
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise Handler._BodyError(400, "Bad Content-Length")
        if length < 0:
            raise Handler._BodyError(400, "Bad Content-Length")
        if length > MAX_BODY_BYTES:
            raise Handler._BodyError(413, f"Body too large (>{MAX_BODY_BYTES} bytes)")
        raw = self.rfile.read(length) if length else b"{}"
        try:
            parsed = json.loads(raw.decode("utf-8") or "{}")
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            raise Handler._BodyError(400, f"Bad JSON: {e}")
        if not isinstance(parsed, dict):
            raise Handler._BodyError(400, "Top-level JSON must be an object")
        return parsed

    # ---- Methods ----
    def do_OPTIONS(self):
        raw_origin = self.headers.get("Origin")
        origin = self._cors_origin()
        pna = self.headers.get("Access-Control-Request-Private-Network")
        log.info("OPTIONS %s origin=%r allowed=%r pna=%r -> 200",
                 self.path, raw_origin, origin, pna)
        self.send_response(200)
        self._send_cors(origin)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        # /health is a friendlier alias for the same liveness probe; both
        # paths return the same payload so existing clients keep working.
        bare = self.path.split("?", 1)[0]
        if bare == "/ping" or bare == "/health":
            # Public liveness probe -- intentionally unauthenticated.
            log.info("GET %s from %s -> ok", bare, self.client_address[0])
            return self._send_json(200, {"ok": True, "version": VERSION})
        if bare == "/token":
            return self._handle_token()
        # Everything below mutates state or reveals user data -- token-gated.
        if not self._require_token():
            return
        if bare == "/session/list":
            return self._handle_session_list()
        if bare == "/session/active":
            return self._handle_session_active()
        if bare == "/open-prompts":
            return self._handle_open_prompts()
        if bare == "/open-index":
            return self._handle_open_index()
        if bare == "/recent":
            return self._handle_recent()
        if bare == "/open-folder":
            return self._handle_open_folder()
        log.info("GET %s -> 404", self.path)
        self._send_json(404, {"ok": False, "error": "not found"})

    # ---- /token ----
    # Returns the per-install auth token. CSRF defense layered as:
    #   1. X-Yoink-Client header must equal "yoink-extension". A drive-by
    #      browser request from a random site can't set this without a
    #      CORS preflight, and our preflight refuses ACAO for any origin
    #      outside the youtube + chrome-extension allowlist.
    #   2. Origin (if present) must be a browser-extension origin.
    #      Absent Origin is allowed -- some Chromium forks (Comet) issue
    #      service-worker fetches with no Origin header.
    #   3. Per-install rate limit (10/min) so a noisy attacker can't
    #      poll the endpoint indefinitely.
    # Local processes (curl, malicious scripts on the same machine) CAN
    # bypass all of this; they already run with the user's privileges and
    # could read token.txt directly. The gate exists for CSRF, not for
    # local-attacker defense.
    def _handle_token(self):
        if not self._has_yoink_client_header():
            log.info("GET /token rejected (missing X-Yoink-Client)")
            return self._send_json(403, {"ok": False, "error": "forbidden"})
        if not self._is_extension_origin():
            log.info("GET /token rejected (origin=%r)", self.headers.get("Origin"))
            return self._send_json(403, {"ok": False, "error": "forbidden"})
        if not _check_token_rate_limit():
            log.info("GET /token rate-limited")
            return self._send_json(429, {"ok": False, "error": "too many requests"})
        self._send_json(200, {"ok": True, "token": TOKEN})

    # ---- /recent ----
    # Walk Desktop\Yoink\<topic>\<slug>\ and return the 3 most recent video
    # folders. A folder counts as a yoink if it has a yoink.md inside it.
    # Sessions root (_sessions/) is excluded.
    def _handle_recent(self):
        results = []
        if DESKTOP_ROOT.exists():
            candidates = []
            for topic_dir in DESKTOP_ROOT.iterdir():
                if not topic_dir.is_dir() or topic_dir.name.startswith("_"):
                    continue
                for video_dir in topic_dir.iterdir():
                    if not video_dir.is_dir():
                        continue
                    if _resolve_corpus_path(video_dir) is None:
                        continue
                    candidates.append((video_dir.stat().st_mtime,
                                       topic_dir.name, video_dir))
            candidates.sort(key=lambda c: c[0], reverse=True)
            for _mtime, topic_name, video_dir in candidates[:3]:
                title = video_dir.name
                # Prefer the title from metadata.json if available — it's the
                # readable form, not the slugified folder name.
                meta_path = video_dir / "metadata.json"
                if meta_path.exists():
                    try:
                        m = json.loads(meta_path.read_text(encoding="utf-8"))
                        title = m.get("title") or title
                    except (OSError, json.JSONDecodeError):
                        pass
                results.append({
                    "title": title,
                    "topic": topic_name,
                    "folder": str(video_dir),
                })
        self._send_json(200, {"ok": True, "recent": results})

    # ---- /open-folder?path=... ----
    # Pop Explorer at an arbitrary folder. Used by the "Recent yoinks" list
    # so clicking a row opens that folder. The path must be inside
    # DESKTOP_ROOT — we don't want this turning into an arbitrary-folder
    # opener.
    def _handle_open_folder(self):
        qs = parse_qs(urlparse(self.path).query)
        target = (qs.get("path") or [""])[0]
        if not target:
            return self._send_json(400, {"ok": False, "error": "path required"})
        try:
            p = Path(target).resolve()
            # Sandboxing: only allow folders inside DESKTOP_ROOT. relative_to
            # raises ValueError when p is outside the root.
            p.relative_to(DESKTOP_ROOT.resolve())
        except (ValueError, OSError):
            return self._send_json(400, {
                "ok": False, "error": "path is outside the Yoink folder",
            })
        if not p.exists() or not p.is_dir():
            return self._send_json(404, {"ok": False, "error": "folder not found"})
        try:
            os.startfile(str(p))  # type: ignore[attr-defined]
        except Exception as e:
            return self._send_json(200, {"ok": False, "error": str(e)})
        self._send_json(200, {"ok": True, "folder": str(p)})

    # ---- /open-index ----
    # Open _all-yoinks-index.md in the user's default markdown viewer
    # (typically VS Code, Obsidian, or Notepad). Regenerates the file first
    # in case it doesn't exist yet (e.g. user hasn't yoinked anything in
    # this install but is exploring the popup).
    def _handle_open_index(self):
        try:
            _regenerate_index()
            target = _index_path()
            if not target.exists():
                return self._send_json(200, {
                    "ok": False,
                    "error": "Index file couldn't be created.",
                })
            os.startfile(str(target))  # type: ignore[attr-defined]
        except Exception as e:
            return self._send_json(200, {"ok": False, "error": str(e)})
        log.info("GET /open-index -> %s", target)
        self._send_json(200, {"ok": True, "path": str(target)})

    # ---- /open-prompts ----
    # Pop Explorer at extension/prompts.json so the user can edit their custom
    # prompts without hunting through the project folder. Selected so the file
    # is highlighted (not just the parent folder opened).
    def _handle_open_prompts(self):
        prompts_path = HERE / "extension" / "prompts.json"
        if not prompts_path.exists():
            return self._send_json(200, {
                "ok": False,
                "error": f"prompts.json not found at {prompts_path}",
            })
        try:
            subprocess.Popen(
                ["explorer", f"/select,{prompts_path}"],
                **SUBPROCESS_KW,
            )
        except Exception as e:
            return self._send_json(200, {"ok": False, "error": str(e)})
        log.info("GET /open-prompts -> %s", prompts_path)
        self._send_json(200, {"ok": True, "path": str(prompts_path)})

    def do_POST(self):
        # Auth first so we don't even read the body for unauthenticated
        # callers. Public POST endpoints don't exist today, so the gate is
        # unconditional here.
        if not self._require_token():
            return
        try:
            body = self._read_json_body()
        except Handler._BodyError as e:
            return self._send_json(e.status, {"ok": False, "error": e.message})

        bare = self.path.split("?", 1)[0]
        if bare == "/extract":
            return self._handle_extract(body)
        if bare == "/session/start":
            return self._handle_session_start(body)
        if bare == "/session/add":
            return self._handle_session_add(body)
        if bare == "/session/close":
            return self._handle_session_close(body)
        if bare == "/session/cancel":
            return self._handle_session_cancel(body)
        if bare == "/session/open":
            return self._handle_session_open(body)

        log.info("POST %s -> 404", bare)
        self._send_json(404, {"ok": False, "error": "not found"})

    def _validate_session_id(self, body: dict):
        """Pull and validate session_id from a request body. Returns
        (session_id, None) on success or (None, error_message) on failure.
        Rejects anything that isn't strictly alphanumeric+_-, since the id
        becomes a path segment under SESSIONS_ROOT."""
        session_id = (body.get("session_id") or "").strip()
        if not session_id:
            return None, "session_id required"
        if not _is_valid_session_id(session_id):
            return None, "session_id has invalid characters"
        return session_id, None

    # ---- /extract ----
    def _validate_url_interval(self, body: dict):
        url = (body.get("url") or "").strip()
        interval = body.get("interval", 30)
        try:
            interval = int(interval)
        except (TypeError, ValueError):
            return None, None, "interval must be an integer"
        if not (5 <= interval <= 300):
            return None, None, "interval must be between 5 and 300"
        # Strict hostname allowlist. Substring checks ("youtube.com" in url)
        # accept attacker-shaped URLs like https://evil.com/youtube.com/foo,
        # which yt-dlp would happily fetch as an arbitrary URL.
        normalized = _normalize_youtube_url(url)
        if not normalized:
            return None, None, "URL must be a youtube.com or youtu.be video link"
        return normalized, interval, None

    def _handle_extract(self, body: dict):
        url, interval, err = self._validate_url_interval(body)
        if err:
            log.info("POST /extract -> 400 (%s)", err)
            return self._send_json(400, {"ok": False, "error": err})

        log.info("POST /extract url=%s interval=%d -> running", url, interval)
        DESKTOP_ROOT.mkdir(parents=True, exist_ok=True)
        with _extract_lock:
            try:
                # One metadata fetch up front — used both to derive the folder
                # slug here and re-used by _run_extraction (avoids a 2nd call).
                metadata = _fetch_metadata(url)
                title = metadata.get("title") or "Untitled"
                topic = _classify_topic(metadata)
                folder = DESKTOP_ROOT / _topic_folder_name(topic) / (slugify(title) or "video")
                result = _run_extraction(url, interval, folder,
                                          metadata=metadata, topic=topic)
            except BaseException as e:
                msg = friendly_error(e)
                log.error("POST /extract -> error: %s", msg)
                return self._send_json(200, {"ok": False, "error": msg})

        log.info("POST /extract -> ok (%d shots, %s)",
                 result["screenshot_count"], result["folder"])
        self._send_json(200, result)

    # ---- /session/start ----
    def _handle_session_start(self, body: dict):
        name = (body.get("name") or "").strip()
        with _session_lock:
            existing = _active_session()
            if existing:
                msg = (f"A session is already open: '{existing.get('name')}'. "
                       "Close or cancel it before starting a new one.")
                log.info("POST /session/start -> 409 (active=%s)", existing.get("slug"))
                return self._send_json(409, {"ok": False, "error": msg, "active_session": {
                    "id": existing["slug"], "name": existing.get("name"),
                    "video_count": len(existing.get("videos", [])),
                }})

            slug_base = slugify(name) if name else datetime.now().strftime("session_%Y%m%d_%H%M%S")
            slug = slug_base or datetime.now().strftime("session_%Y%m%d_%H%M%S")
            # Disambiguate if a folder with that slug already exists.
            if _session_folder(slug).exists():
                slug = f"{slug}_{uuid.uuid4().hex[:6]}"

            session = {
                "name": name or slug,
                "slug": slug,
                "created_at": _now_iso(),
                "status": "open",
                "videos": [],
            }
            _write_session(slug, session)

        folder = _session_folder(slug)
        log.info("POST /session/start -> created %s", folder)
        self._send_json(200, {
            "ok": True,
            "session_id": slug,
            "name": session["name"],
            "folder": str(folder),
        })

    # ---- /session/add ----
    def _handle_session_add(self, body: dict):
        session_id, sid_err = self._validate_session_id(body)
        if sid_err:
            return self._send_json(400, {"ok": False, "error": sid_err})
        url, interval, err = self._validate_url_interval(body)
        if err:
            return self._send_json(400, {"ok": False, "error": err})

        session = _read_session(session_id)
        if not session:
            return self._send_json(404, {"ok": False, "error": f"session '{session_id}' not found"})
        if session.get("status") != "open":
            return self._send_json(409, {
                "ok": False,
                "error": f"session '{session_id}' is {session.get('status')}, not open",
            })

        log.info("POST /session/add session=%s url=%s -> running", session_id, url)
        sess_folder = _session_folder(session_id)
        # Disambiguate the per-video subfolder by title — fetch metadata once
        # and re-use it inside _run_extraction.
        with _extract_lock:
            try:
                metadata = _fetch_metadata(url)
                title = metadata.get("title") or "Untitled"
                topic = _classify_topic(metadata)
                video_slug = slugify(title) or "video"
                target = sess_folder / video_slug
                # Disambiguate if same-named video already added.
                if target.exists():
                    video_slug = f"{video_slug}_{uuid.uuid4().hex[:6]}"
                    target = sess_folder / video_slug

                result = _run_extraction(url, interval, target,
                                          open_explorer=False,
                                          metadata=metadata, topic=topic)
            except BaseException as e:
                msg = friendly_error(e)
                log.error("POST /session/add -> error: %s", msg)
                return self._send_json(200, {"ok": False, "error": msg, "session_id": session_id})

        with _session_lock:
            session = _read_session(session_id) or session
            session.setdefault("videos", []).append({
                "url": url,
                "title": result["title"],
                "video_slug": result["video_slug"],
                "screenshot_count": result["screenshot_count"],
                "caption_count": result.get("caption_count", 0),
                "added_at": _now_iso(),
            })
            _write_session(session_id, session)

        log.info("POST /session/add -> ok (%d shots, total videos=%d)",
                 result["screenshot_count"], len(session["videos"]))
        result.update({"session_id": session_id, "video_count": len(session["videos"])})
        self._send_json(200, result)

    # ---- /session/close ----
    def _handle_session_close(self, body: dict):
        session_id, sid_err = self._validate_session_id(body)
        if sid_err:
            return self._send_json(400, {"ok": False, "error": sid_err})

        with _session_lock:
            session = _read_session(session_id)
            if not session:
                return self._send_json(404, {"ok": False, "error": f"session '{session_id}' not found"})
            if session.get("status") != "open":
                return self._send_json(409, {
                    "ok": False,
                    "error": f"session is {session.get('status')}, cannot close",
                })

            corpus_md = _build_corpus(session)
            corpus_path = _session_folder(session_id) / "corpus.md"
            corpus_path.write_text(corpus_md, encoding="utf-8")

            session["status"] = "closed"
            session["closed_at"] = _now_iso()
            _write_session(session_id, session)

        sess_folder = _session_folder(session_id)
        try:
            os.startfile(str(sess_folder))  # type: ignore[attr-defined]
        except Exception as e:
            log.warning("startfile failed: %s", e)

        total_captions = sum(v.get("caption_count", 0) for v in session.get("videos", []))
        log.info("POST /session/close -> ok (%d videos, %d chars)",
                 len(session.get("videos", [])), len(corpus_md))
        self._send_json(200, {
            "ok": True,
            "corpus_path": str(corpus_path),
            "corpus_md": corpus_md,
            "video_count": len(session.get("videos", [])),
            "caption_count": total_captions,
            "session_folder": str(sess_folder),
            "name": session.get("name"),
        })

    # ---- /session/cancel ----
    def _handle_session_cancel(self, body: dict):
        session_id, sid_err = self._validate_session_id(body)
        if sid_err:
            return self._send_json(400, {"ok": False, "error": sid_err})

        with _session_lock:
            session = _read_session(session_id)
            if not session:
                return self._send_json(404, {"ok": False, "error": f"session '{session_id}' not found"})
            if session.get("status") not in ("open",):
                return self._send_json(409, {
                    "ok": False,
                    "error": f"session is {session.get('status')}, cannot cancel",
                })
            session["status"] = "cancelled"
            session["cancelled_at"] = _now_iso()
            _write_session(session_id, session)

        log.info("POST /session/cancel -> ok (%s)", session_id)
        self._send_json(200, {"ok": True, "session_id": session_id})

    # ---- /session/open ----
    def _handle_session_open(self, body: dict):
        session_id, sid_err = self._validate_session_id(body)
        if sid_err:
            return self._send_json(400, {"ok": False, "error": sid_err})
        folder = _session_folder(session_id)
        if not folder.exists():
            return self._send_json(404, {"ok": False, "error": f"session '{session_id}' not found"})
        try:
            os.startfile(str(folder))  # type: ignore[attr-defined]
        except Exception as e:
            return self._send_json(200, {"ok": False, "error": str(e)})
        log.info("POST /session/open -> %s", folder)
        self._send_json(200, {"ok": True, "folder": str(folder)})

    # ---- /session/list ----
    def _handle_session_list(self):
        sessions = _all_sessions()
        summaries = [{
            "session_id": s["slug"],
            "name": s.get("name"),
            "status": s.get("status"),
            "video_count": len(s.get("videos", [])),
            "created_at": s.get("created_at"),
            "closed_at": s.get("closed_at"),
            "cancelled_at": s.get("cancelled_at"),
            "folder": str(_session_folder(s["slug"])),
        } for s in sessions]
        log.info("GET /session/list -> %d sessions", len(summaries))
        self._send_json(200, {"ok": True, "sessions": summaries})

    # ---- /session/active ----
    def _handle_session_active(self):
        s = _active_session()
        if not s:
            return self._send_json(200, {"ok": True, "session": None})
        recent = list(reversed(s.get("videos", [])))[:3]
        self._send_json(200, {
            "ok": True,
            "session": {
                "session_id": s["slug"],
                "name": s.get("name"),
                "status": s.get("status"),
                "video_count": len(s.get("videos", [])),
                "created_at": s.get("created_at"),
                "folder": str(_session_folder(s["slug"])),
                "recent": [{"title": v.get("title"), "url": v.get("url")} for v in recent],
            },
        })


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------
def maybe_toast(title: str, body: str):
    """Show a transient balloon-style notification on Windows so the user
    knows the server actually started. Uses System.Windows.Forms.NotifyIcon
    via PowerShell -- works on Win10/11 with no pip dependencies, lives in
    the user session, auto-dismisses after the system's balloon timeout,
    and is suppressed gracefully by Focus Assist instead of throwing.

    Fire-and-forget: we Popen the PowerShell process and return. It exits
    on its own ~6s later after disposing the tray icon. Quietly no-ops on
    non-Windows or when PowerShell isn't on PATH."""
    if sys.platform != "win32":
        return
    # Single-quote escape for PowerShell single-quoted strings.
    t = title.replace("'", "''")
    b = body.replace("'", "''")
    ps = (
        "Add-Type -AssemblyName System.Windows.Forms;"
        "Add-Type -AssemblyName System.Drawing;"
        "$n = New-Object System.Windows.Forms.NotifyIcon;"
        "$n.Icon = [System.Drawing.SystemIcons]::Information;"
        "$n.BalloonTipIcon = 'Info';"
        f"$n.BalloonTipTitle = '{t}';"
        f"$n.BalloonTipText = '{b}';"
        "$n.Visible = $true;"
        "$n.ShowBalloonTip(5000);"
        # Keep the tray icon alive long enough for Windows to render the
        # balloon (the timeout arg is advisory; Windows uses a fixed ~5s).
        "Start-Sleep -Seconds 6;"
        "$n.Dispose()"
    )
    try:
        subprocess.Popen(
            ["powershell.exe", "-NoProfile", "-WindowStyle", "Hidden",
             "-Command", ps],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            **SUBPROCESS_KW,
        )
    except Exception as e:
        log.debug("toast spawn failed: %s", e)


def _existing_server_responds() -> bool:
    """Probe /health on the loopback port. True if another Yoink is already
    running here -- used to short-circuit a duplicate launch from the
    Start Menu / autostart key without writing a stale PID file."""
    try:
        with urllib.request.urlopen(
            f"http://{HOST}:{PORT}/health", timeout=0.5
        ) as r:
            return r.status == 200
    except Exception:
        return False


def main():
    DESKTOP_ROOT.mkdir(parents=True, exist_ok=True)
    SESSIONS_ROOT.mkdir(parents=True, exist_ok=True)

    # Single-instance guard. The Start Menu shortcut + the HKCU\Run autostart
    # entry can both fire on a fresh login, and a user clicking the shortcut
    # twice would otherwise spawn parallel pythonw.exe processes that all
    # try to bind 5179. Probe the canonical /health endpoint first.
    if _existing_server_responds():
        log.info("Yoink server already running on http://%s:%d -- exiting", HOST, PORT)
        sys.exit(0)

    # Bind FIRST. Writing the PID file before the bind would create stale
    # files when another instance still owns the port (and would also have
    # the wrong PID -- ours, not the live one).
    try:
        server = ThreadingHTTPServer((HOST, PORT), Handler)
    except OSError as e:
        # Port held by something we couldn't probe via /health (different
        # app, half-open socket, etc). Exit 0 so the Windows autostart
        # mechanism doesn't surface an error dialog to the user.
        log.error("Failed to bind %s:%d -- %s", HOST, PORT, e)
        sys.exit(0)

    # Bind succeeded -- now safe to claim the PID file.
    pid_file = HERE / "server.pid"
    try:
        pid_file.write_text(str(os.getpid()), encoding="utf-8")
    except OSError:
        pass
    import atexit
    atexit.register(lambda: pid_file.unlink(missing_ok=True))

    log.info("Yoink server v%s running on http://%s:%d", VERSION, HOST, PORT)
    log.info("Ready to yoink. Click any YouTube video's Yoink button.")
    log.info("Output: %s", DESKTOP_ROOT)
    log.info("Log file: %s", LOG_PATH)
    # Only fires here -- the single-instance / bind-failure paths above
    # exit() before reaching this line, so a duplicate launch doesn't
    # double-notify.
    maybe_toast(
        "Yoink is running",
        "Click the orange Y on any YouTube video to yoink. "
        "To stop, find 'Stop Yoink Server' in your Start Menu.",
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("Shutting down.")
        server.server_close()


if __name__ == "__main__":
    main()
