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

# Invoke yt-dlp via the same interpreter rather than relying on PATH. pip's
# --user install puts yt-dlp.exe in %APPDATA%\Python\PythonXX\Scripts which
# isn't on PATH by default on Windows, so a bare "yt-dlp" call fails.
YTDLP_CMD = [sys.executable, "-m", "yt_dlp"]

DESKTOP_ROOT = Path(os.environ["USERPROFILE"]) / "Desktop" / "Yoink"
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
                    yoinked_at: str, topic: str) -> str:
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
    parts.append("*Yoinked with [Yoink](https://yoink.video) by ReplayRyan*")
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

    # Video + subs.
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
        **SUBPROCESS_KW,
    )

    video_files = [f for f in output_folder.glob("video.*")
                   if f.suffix in (".mp4", ".webm", ".mkv")]
    srt_files = list(output_folder.glob("video*.srt"))
    if not video_files:
        raise RuntimeError("yt-dlp finished but no video file was produced.")
    video_file = video_files[0]

    shots_dir = output_folder / "screenshots"
    shots_dir.mkdir(exist_ok=True)
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
        **SUBPROCESS_KW,
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
    )
    yoink_path = output_folder / "yoink.md"
    yoink_path.write_text(yoink_md, encoding="utf-8")

    video_file.unlink(missing_ok=True)

    # Comments fetch in background; updates yoink.md when done.
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
        yoink_path = folder / video_slug / "yoink.md"

        parts.append(f"## Video {i}: {title}")
        parts.append(f"Source: {url}")
        parts.append(f"Local folder: {rel}")
        parts.append("")

        if yoink_path.exists():
            try:
                body = yoink_path.read_text(encoding="utf-8")
                # Strip the per-video H1 (the title) — we already emitted Video N: title.
                body = re.sub(r"^# .+\n", "", body, count=1)
                # Strip the leading metadata lines we'd duplicate (URL/Yoinked/etc.).
                # The bold-prefixed lines come right after the title block.
                body = re.sub(r"^(\*\*[^*]+:\*\*[^\n]*\n)+", "", body)
                parts.append(_demote_headings(body.strip()))
            except OSError as e:
                parts.append(f"> _Failed to read yoink.md: {e}_")
        else:
            parts.append("> _yoink.md not found — extraction may have failed._")

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
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Access-Control-Max-Age", "600")
            # Private Network Access: Chrome requires this header when a public
            # HTTPS origin (youtube.com) fetches a loopback resource. Without
            # it the preflight is rejected and fetch fails as "Failed to fetch"
            # before any visible request reaches the handler.
            self.send_header("Access-Control-Allow-Private-Network", "true")

    def _send_json(self, status: int, payload: dict):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._send_cors(self._cors_origin())
        self.end_headers()
        self.wfile.write(body)

    def _read_json_body(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b"{}"
        return json.loads(raw.decode("utf-8") or "{}")

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
        if self.path == "/ping":
            log.info("GET /ping from %s -> ok", self.client_address[0])
            return self._send_json(200, {"ok": True, "version": VERSION})
        if self.path == "/session/list":
            return self._handle_session_list()
        if self.path == "/session/active":
            return self._handle_session_active()
        if self.path == "/open-prompts":
            return self._handle_open_prompts()
        if self.path == "/recent":
            return self._handle_recent()
        if self.path.startswith("/open-folder?"):
            return self._handle_open_folder()
        log.info("GET %s -> 404", self.path)
        self._send_json(404, {"ok": False, "error": "not found"})

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
                    yoink_md = video_dir / "yoink.md"
                    if not yoink_md.exists():
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
        try:
            body = self._read_json_body()
        except (ValueError, json.JSONDecodeError) as e:
            return self._send_json(400, {"ok": False, "error": f"Invalid JSON body: {e}"})

        if self.path == "/extract":
            return self._handle_extract(body)
        if self.path == "/session/start":
            return self._handle_session_start(body)
        if self.path == "/session/add":
            return self._handle_session_add(body)
        if self.path == "/session/close":
            return self._handle_session_close(body)
        if self.path == "/session/cancel":
            return self._handle_session_cancel(body)
        if self.path == "/session/open":
            return self._handle_session_open(body)

        log.info("POST %s -> 404", self.path)
        self._send_json(404, {"ok": False, "error": "not found"})

    # ---- /extract ----
    def _validate_url_interval(self, body: dict):
        url = (body.get("url") or "").strip()
        interval = body.get("interval", 30)
        try:
            interval = int(interval)
        except (TypeError, ValueError):
            return None, None, "interval must be an integer"
        if "youtube.com" not in url and "youtu.be" not in url:
            return None, None, "URL must contain youtube.com or youtu.be"
        if not (5 <= interval <= 300):
            return None, None, "interval must be between 5 and 300"
        return url, interval, None

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
        session_id = (body.get("session_id") or "").strip()
        url, interval, err = self._validate_url_interval(body)
        if err:
            return self._send_json(400, {"ok": False, "error": err})
        if not session_id:
            return self._send_json(400, {"ok": False, "error": "session_id required"})

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
        session_id = (body.get("session_id") or "").strip()
        if not session_id:
            return self._send_json(400, {"ok": False, "error": "session_id required"})

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
        session_id = (body.get("session_id") or "").strip()
        if not session_id:
            return self._send_json(400, {"ok": False, "error": "session_id required"})

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
        session_id = (body.get("session_id") or "").strip()
        if not session_id:
            return self._send_json(400, {"ok": False, "error": "session_id required"})
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
    try:
        from win11toast import toast
        toast(title, body, duration="short")
    except Exception:
        pass


def main():
    DESKTOP_ROOT.mkdir(parents=True, exist_ok=True)
    SESSIONS_ROOT.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    log.info("Yoink server v%s running on http://%s:%d", VERSION, HOST, PORT)
    log.info("Ready to yoink. Click any YouTube video's Yoink button.")
    log.info("Output: %s", DESKTOP_ROOT)
    log.info("Log file: %s", LOG_PATH)
    maybe_toast("Yoink", f"Yoink v{VERSION} is running. Ready to yoink.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("Shutting down.")
        server.server_close()


if __name__ == "__main__":
    main()
