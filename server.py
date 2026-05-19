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
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

try:
    import keyring as _keyring
except Exception as _keyring_error:  # pragma: no cover - env-specific
    _keyring = None
    _KEYRING_IMPORT_ERROR = str(_keyring_error)
else:
    _KEYRING_IMPORT_ERROR = None

# --- Import helpers from the existing CLI script ---------------------------
HERE = Path(__file__).parent.resolve()
sys.path.insert(0, str(HERE))


def _read_version() -> str:
    version = (HERE / "VERSION").read_text(encoding="utf-8").strip()
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise RuntimeError(f"Invalid VERSION file value: {version!r}")
    return version

# When shipped via the Windows installer, ffmpeg.exe lives next to server.py
# in a `bin\` folder. Prepend it to PATH so subprocess calls (`ffmpeg ...`)
# find the bundled binary without depending on the user's environment. No-op
# in dev where bin\ doesn't exist — falls back to whatever's on PATH.
_BIN_DIR = HERE / "bin"
if _BIN_DIR.is_dir():
    os.environ["PATH"] = str(_BIN_DIR) + os.pathsep + os.environ.get("PATH", "")

from yt_extract import parse_srt, slugify, fmt_time  # noqa: E402
import index  # noqa: E402  -- local SQLite library-index module

# --- Constants -------------------------------------------------------------
HOST = "127.0.0.1"
PORT = 5179
VERSION = _read_version()
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
PLAYLIST_VIDEO_CAP = 10                # v2 Playlist Mode first-ship cap
MAX_SERVED_FILE_BYTES = 10 * 1024 * 1024
LONG_VIDEO_SECONDS = 2 * 60 * 60       # 2 hours -- log warning above this
YTDLP_TIMEOUT_SEC = 30 * 60            # main extract timeout
COMMENTS_TIMEOUT_SEC = 5 * 60
FFMPEG_TIMEOUT_SEC = 15 * 60
CLIPBOARD_SCREENSHOT_CAP_DEFAULT = 4
CLIPBOARD_SCREENSHOT_CAP_MAX = 12


def _env_float(name: str, default: float, *, low: float, high: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return max(low, min(high, value))


PLAYLIST_SLEEP_SEC = _env_float("YOINK_PLAYLIST_SLEEP_SEC", 5.0, low=0.0, high=120.0)
PLAYLIST_RATE_LIMIT_BACKOFF_BASE_SEC = 30.0
PLAYLIST_RATE_LIMIT_BACKOFF_MAX_SEC = 5 * 60.0

# ---- Auth token (P0-1) ----------------------------------------------------
# Per-install random token. Persisted next to server.py (which lives in
# %LOCALAPPDATA%\Yoink in the installed product, or in the dev repo
# directory in dev mode -- gitignored either way). The extension fetches
# this via /token (gated by chrome-extension:// origin) on first launch
# and includes it in X-Yoink-Token on every subsequent request.
TOKEN_PATH = HERE / "token.txt"
DATA_ROOT = (
    Path(os.environ.get("LOCALAPPDATA", str(HERE))) / "Yoink"
    if sys.platform == "win32" else HERE
)
SETTINGS_PATH = DATA_ROOT / "settings.json"
JOBS_PATH = DATA_ROOT / "jobs.json"
TAXONOMY_PATH = DATA_ROOT / "taxonomy.json"
KEYRING_SERVICE = "Yoink"
KEYRING_ANTHROPIC_USERNAME = "anthropic_key"
ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_MODEL = "claude-haiku-4-5-20251001"
ANTHROPIC_VERSION = "2023-06-01"
# Pricing source: Anthropic Claude pricing docs, verified 2026-05-12:
# https://docs.claude.com/en/docs/about-claude/pricing
ANTHROPIC_PRICING_INPUT_PER_MILLION = 1.00
ANTHROPIC_PRICING_OUTPUT_PER_MILLION = 5.00
ANTHROPIC_CI_EST_INPUT_TOKENS = 5_000
ANTHROPIC_CI_EST_OUTPUT_TOKENS = 500
ANTHROPIC_HOOK_EST_INPUT_TOKENS = 1_200
ANTHROPIC_HOOK_EST_OUTPUT_TOKENS = 80


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


# POST /taxonomy/correct rate limit (Sprint 17). Corrections are
# user-initiated (a click in the popup), so 30/min is generous for real
# use and still caps a runaway client.
_TAXONOMY_CORRECT_RATE_LIMIT = 30
_TAXONOMY_CORRECT_RATE_WINDOW_SEC = 60.0
_taxonomy_correct_request_times: list[float] = []
_taxonomy_correct_rate_lock = threading.Lock()


def _check_taxonomy_correct_rate_limit() -> bool:
    now = time.monotonic()
    with _taxonomy_correct_rate_lock:
        cutoff = now - _TAXONOMY_CORRECT_RATE_WINDOW_SEC
        kept = [t for t in _taxonomy_correct_request_times if t > cutoff]
        if len(kept) >= _TAXONOMY_CORRECT_RATE_LIMIT:
            _taxonomy_correct_request_times[:] = kept
            return False
        kept.append(now)
        _taxonomy_correct_request_times[:] = kept
    return True


# GET /memory/search rate limit (Sprint 18). Heavier than /recent because
# it runs an FTS5 query; 60/min is generous for a human paging the memory
# page and still caps a runaway client.
_MEMORY_SEARCH_RATE_LIMIT = 60
_MEMORY_SEARCH_RATE_WINDOW_SEC = 60.0
_memory_search_request_times: list[float] = []
_memory_search_rate_lock = threading.Lock()


def _check_memory_search_rate_limit() -> bool:
    now = time.monotonic()
    with _memory_search_rate_lock:
        cutoff = now - _MEMORY_SEARCH_RATE_WINDOW_SEC
        kept = [t for t in _memory_search_request_times if t > cutoff]
        if len(kept) >= _MEMORY_SEARCH_RATE_LIMIT:
            _memory_search_request_times[:] = kept
            return False
        kept.append(now)
        _memory_search_request_times[:] = kept
    return True


def _valid_iso_date(value: str) -> bool:
    """True if value is a well-formed YYYY-MM-DD date."""
    try:
        datetime.strptime(value, "%Y-%m-%d")
        return True
    except (TypeError, ValueError):
        return False


# ---- Settings (v2.1 BYO Anthropic key) ------------------------------------
class CredentialStoreError(RuntimeError):
    """Raised when the OS credential store cannot read/write a saved key."""


def _default_settings() -> dict:
    return {
        "comment_intelligence_enabled": False,
        "hook_type_enabled": False,
        "smart_screenshot_picker_enabled": False,
        "clipboard_screenshot_cap": CLIPBOARD_SCREENSHOT_CAP_DEFAULT,
        "anthropic_key_invalid": False,
        "updated_at": None,
    }


def _normalize_settings(data: dict) -> dict:
    clean = _default_settings()
    if isinstance(data, dict):
        clean.update(data)
    clean.pop("anthropic_key", None)
    clean["comment_intelligence_enabled"] = bool(
        clean.get("comment_intelligence_enabled")
    )
    clean["hook_type_enabled"] = bool(clean.get("hook_type_enabled"))
    clean["smart_screenshot_picker_enabled"] = bool(
        clean.get("smart_screenshot_picker_enabled")
    )
    try:
        cap = int(clean.get("clipboard_screenshot_cap"))
    except (TypeError, ValueError):
        cap = CLIPBOARD_SCREENSHOT_CAP_DEFAULT
    clean["clipboard_screenshot_cap"] = max(
        0,
        min(CLIPBOARD_SCREENSHOT_CAP_MAX, cap),
    )
    clean["anthropic_key_invalid"] = bool(clean.get("anthropic_key_invalid"))
    return clean


def _read_settings() -> dict:
    with _settings_lock:
        data: dict = {}
        if SETTINGS_PATH.exists():
            try:
                raw = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    data = raw
            except (OSError, json.JSONDecodeError) as e:
                log.warning("settings read failed: %s", e)
        return _normalize_settings(data)


def _write_settings(data: dict) -> None:
    with _settings_lock:
        SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        clean = _normalize_settings(data)
        tmp = SETTINGS_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(clean, indent=2), encoding="utf-8")
        tmp.replace(SETTINGS_PATH)
        try:
            os.chmod(SETTINGS_PATH, 0o600)
        except OSError:
            pass


def _atomic_write_text(path: Path, text: str, *, encoding: str = "utf-8") -> None:
    """Write text via temp file + replace so crashy exits don't leave partial files."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        tmp.write_text(text, encoding=encoding)
        # Path.replace() can raise PermissionError when the destination file
        # is momentarily held open by OneDrive sync -- and the default
        # DESKTOP_ROOT lives under OneDrive. Retry with short backoff before
        # giving up so a transient sync lock doesn't lose the write.
        for delay in (0.05, 0.2, 0.5, None):
            try:
                tmp.replace(path)
                break
            except PermissionError:
                if delay is None:
                    log.warning("atomic write to %s failed after retries "
                                "(destination locked?)", path)
                    raise
                time.sleep(delay)
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def _credential_store_error() -> CredentialStoreError | None:
    if _keyring is None:
        detail = (
            f"keyring import failed: {_KEYRING_IMPORT_ERROR}"
            if _KEYRING_IMPORT_ERROR else
            "keyring is not installed"
        )
        return CredentialStoreError(
            "Anthropic API key storage unavailable. Install keyring or run the "
            f"Windows installer. Details: {detail}"
        )
    return None


def _get_saved_anthropic_key() -> str:
    err = _credential_store_error()
    if err:
        log.debug("%s", err)
        return ""
    try:
        return (
            _keyring.get_password(KEYRING_SERVICE, KEYRING_ANTHROPIC_USERNAME)
            or ""
        )
    except Exception as e:
        log.warning("credential read failed: %s", e)
        return ""


def _store_saved_anthropic_key(key: str) -> None:
    key = (key or "").strip()
    err = _credential_store_error()
    if err:
        if key:
            raise err
        return
    try:
        if key:
            _keyring.set_password(
                KEYRING_SERVICE,
                KEYRING_ANTHROPIC_USERNAME,
                key,
            )
        else:
            try:
                _keyring.delete_password(
                    KEYRING_SERVICE,
                    KEYRING_ANTHROPIC_USERNAME,
                )
            except Exception:
                # Missing entries and unavailable delete backends both mean
                # the credential is no longer retrievable by Yoink.
                pass
    except Exception as e:
        raise CredentialStoreError(f"credential write failed: {e}") from e


def _migrate_plaintext_anthropic_key() -> None:
    """Move legacy settings.json anthropic_key into the OS credential store."""
    if not SETTINGS_PATH.exists():
        return
    try:
        raw = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        log.warning("settings migration skipped: read failed (%s)", e)
        return
    if not isinstance(raw, dict) or "anthropic_key" not in raw:
        return

    legacy_key = raw.get("anthropic_key")
    clean = _normalize_settings(raw)
    if isinstance(legacy_key, str) and legacy_key.strip():
        try:
            _store_saved_anthropic_key(legacy_key.strip())
            clean["anthropic_key_invalid"] = False
            log.info("Migrated Anthropic API key from settings.json to keyring")
        except CredentialStoreError as e:
            log.error("settings migration failed: %s", e)
            return
    clean["updated_at"] = _now_iso()
    try:
        _write_settings(clean)
    except OSError as e:
        log.warning("settings migration cleanup failed: %s", e)


def _public_settings(data: dict | None = None) -> dict:
    data = data or _read_settings()
    key = _get_saved_anthropic_key()
    return {
        "comment_intelligence_enabled": bool(data.get("comment_intelligence_enabled")),
        "hook_type_enabled": bool(data.get("hook_type_enabled")),
        "smart_screenshot_picker_enabled": bool(
            data.get("smart_screenshot_picker_enabled")
        ),
        "clipboard_screenshot_cap": int(
            data.get("clipboard_screenshot_cap", CLIPBOARD_SCREENSHOT_CAP_DEFAULT)
        ),
        "anthropic_key_set": bool(key and not data.get("anthropic_key_invalid")),
    }


def _anthropic_estimated_cost(input_tokens: int, output_tokens: int) -> float:
    return round(
        (input_tokens / 1_000_000) * ANTHROPIC_PRICING_INPUT_PER_MILLION
        + (output_tokens / 1_000_000) * ANTHROPIC_PRICING_OUTPUT_PER_MILLION,
        6,
    )


def _anthropic_pricing_payload() -> dict:
    ci = _anthropic_estimated_cost(
        ANTHROPIC_CI_EST_INPUT_TOKENS,
        ANTHROPIC_CI_EST_OUTPUT_TOKENS,
    )
    hook = _anthropic_estimated_cost(
        ANTHROPIC_HOOK_EST_INPUT_TOKENS,
        ANTHROPIC_HOOK_EST_OUTPUT_TOKENS,
    )
    return {
        "model": ANTHROPIC_MODEL,
        "display_model": "Claude Haiku 4.5",
        "input_per_million": ANTHROPIC_PRICING_INPUT_PER_MILLION,
        "output_per_million": ANTHROPIC_PRICING_OUTPUT_PER_MILLION,
        "est_tokens": {
            "ci": {
                "input": ANTHROPIC_CI_EST_INPUT_TOKENS,
                "output": ANTHROPIC_CI_EST_OUTPUT_TOKENS,
            },
            "hook": {
                "input": ANTHROPIC_HOOK_EST_INPUT_TOKENS,
                "output": ANTHROPIC_HOOK_EST_OUTPUT_TOKENS,
            },
        },
        "est_per_video": {
            "ci": ci,
            "hook": hook,
            "both": round(ci + hook, 6),
        },
        "source": "https://docs.claude.com/en/docs/about-claude/pricing",
        "source_checked": "2026-05-12",
    }


def _mark_anthropic_key_invalid() -> None:
    data = _read_settings()
    try:
        _store_saved_anthropic_key("")
    except CredentialStoreError as e:
        log.warning("credential invalid-key clear failed: %s", e)
    data["anthropic_key_invalid"] = True
    data["updated_at"] = _now_iso()
    try:
        _write_settings(data)
    except OSError as e:
        log.warning("settings invalid-key write failed: %s", e)


def _anthropic_key_for_feature(feature_flag: str) -> str | None:
    data = _read_settings()
    key = _get_saved_anthropic_key()
    if not data.get(feature_flag):
        return None
    if data.get("anthropic_key_invalid"):
        return None
    return key.strip() or None


def _saved_anthropic_key() -> str | None:
    """Return the saved key for explicit/on-demand tool calls.

    Feature flags gate automatic background work, but MCP tools are user-
    initiated calls from an agent. Those should only require that a valid
    key exists, not that the background feature toggle is enabled.
    """
    data = _read_settings()
    key = _get_saved_anthropic_key()
    if data.get("anthropic_key_invalid"):
        return None
    return key.strip() or None


def _anthropic_key_available() -> str | None:
    return _anthropic_key_for_feature("comment_intelligence_enabled")


class AnthropicAPIError(Exception):
    def __init__(self, status: int | None, reason: str):
        super().__init__(reason)
        self.status = status
        self.reason = reason


def _short_reason(reason: str, *, api_key: str | None = None) -> str:
    msg = re.sub(r"\s+", " ", str(reason or "unknown error")).strip()
    if api_key:
        msg = msg.replace(api_key, "[redacted]")
    return msg[:180] if len(msg) > 180 else msg


def _anthropic_error_reason(status: int, body: str) -> str:
    try:
        parsed = json.loads(body or "{}")
        err = parsed.get("error") if isinstance(parsed, dict) else None
        if isinstance(err, dict) and err.get("message"):
            return str(err.get("message"))
        if isinstance(parsed, dict) and parsed.get("message"):
            return str(parsed.get("message"))
    except json.JSONDecodeError:
        pass
    return f"Anthropic API returned HTTP {status}"


def _anthropic_messages(api_key: str, *, system: str, user: str,
                        max_tokens: int = 800) -> dict:
    payload = {
        "model": ANTHROPIC_MODEL,
        "max_tokens": max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": user}],
    }
    req = urllib.request.Request(
        ANTHROPIC_API_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": ANTHROPIC_VERSION,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            body = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise AnthropicAPIError(
            e.code,
            _short_reason(_anthropic_error_reason(e.code, body), api_key=api_key),
        ) from None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise AnthropicAPIError(
            None,
            _short_reason(f"network error contacting Anthropic: {e}", api_key=api_key),
        ) from None

    try:
        parsed = json.loads(body)
    except json.JSONDecodeError as e:
        raise AnthropicAPIError(None, f"Anthropic returned invalid JSON: {e}") from None
    if not isinstance(parsed, dict):
        raise AnthropicAPIError(None, "Anthropic returned an unexpected response")
    return parsed


def _anthropic_text(resp: dict) -> str:
    pieces = []
    for part in resp.get("content") or []:
        if isinstance(part, dict) and part.get("type") == "text":
            pieces.append(str(part.get("text") or ""))
    text = "\n".join(pieces).strip()
    if not text:
        raise AnthropicAPIError(None, "Anthropic returned an empty response")
    return text


def _test_anthropic_key(api_key: str) -> tuple[bool, str | None, int | None]:
    if not api_key:
        return False, "API key is required", None
    try:
        _anthropic_messages(
            api_key,
            system="Reply with exactly: ok",
            user="hi",
            max_tokens=4,
        )
        return True, None, None
    except AnthropicAPIError as e:
        return False, e.reason, e.status

# Invoke yt-dlp via the same interpreter rather than relying on PATH. pip's
# --user install puts yt-dlp.exe in %APPDATA%\Python\PythonXX\Scripts which
# isn't on PATH by default on Windows, so a bare "yt-dlp" call fails.
YTDLP_CMD = [sys.executable, "-m", "yt_dlp"]

# Hard cap on the video file yt-dlp downloads before ffmpeg runs. yt-dlp
# pulls the whole file to disk first; on a small disk a few livestream-length
# pulls could fill it. 2 GB is comfortably above a 4-hour 1080p video but
# bails out on multi-hour livestream VODs.
YTDLP_MAX_FILESIZE_BYTES = 2 * 1024 * 1024 * 1024

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


def _is_writable_dir(path: Path) -> bool:
    if not path.exists() or not path.is_dir():
        return False
    probe = path / f".yoink-write-test-{os.getpid()}-{uuid.uuid4().hex}.tmp"
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return True
    except OSError:
        try:
            probe.unlink(missing_ok=True)
        except OSError:
            pass
        return False


def _get_output_root() -> Path:
    """Return the Yoink output root.

    Dev mode can set YOINK_OUTPUT_DIR to keep personal yoinks out of a repo
    that happens to live on the Desktop. The override must already exist and
    be writable; otherwise Yoink falls back to the Desktop\\Yoink folder.
    """
    override = (os.environ.get("YOINK_OUTPUT_DIR") or "").strip()
    if override:
        try:
            candidate = Path(override).expanduser().resolve()
            if _is_writable_dir(candidate):
                return candidate
        except OSError:
            pass
    return _get_desktop_dir() / "Yoink"


DESKTOP_ROOT = _get_output_root()
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

# v2.1 persists public job snapshots to jobs.json. Worker internals stay
# process-local; on restart, non-terminal jobs are marked failed so users have
# an audit trail but must restart the extraction manually.
_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()
_JOB_TERMINAL_STATES = {"completed", "cancelled", "failed"}
_settings_lock = threading.Lock()
_corpus_update_lock = threading.Lock()
# Serializes read-modify-write of the per-video <slug>.json sidecar. The
# comments / hook-type / comment-intelligence workers run concurrently for the
# same video; without this lock two of them can interleave read->read->write
# ->write and silently drop one worker's fields.
_sidecar_update_lock = threading.Lock()
_taxonomy_lock = threading.Lock()

# ===========================================================================
# Library index (Sprint 15) -- SQLite + FTS5. See index.py.
# ===========================================================================
INDEX_PATH = DATA_ROOT / "index.db"
_index_singleton: "index.Index | None" = None
_index_open_lock = threading.Lock()
# True from an index.db corruption-recovery (open_or_recover) until the
# rebuilding backfill scan finishes. Surfaced in /health as index_recovering.
_index_recovering = False

# Backfill scan progress, polled via GET /index/backfill-status.
_backfill_state = {"state": "idle", "current": 0, "total": 0}
_backfill_lock = threading.Lock()
_backfill_cancel = threading.Event()


def _get_index() -> "index.Index":
    """Process-wide Index handle, opened lazily. A corrupt index.db is
    quarantined and rebuilt (open_or_recover); recovery sets the
    _index_recovering flag the backfill clears when it finishes."""
    global _index_singleton, _index_recovering
    with _index_open_lock:
        if _index_singleton is None:
            idx, recovered = index.Index.open_or_recover(INDEX_PATH)
            _index_singleton = idx
            if recovered:
                _index_recovering = True
        return _index_singleton


def _as_float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_hms(value) -> float | None:
    """Inverse of yt_extract.fmt_time: 'HH:MM:SS' -> seconds. Falls back to a
    plain numeric coercion so a raw number also works."""
    if not isinstance(value, str):
        return _as_float(value)
    parts = value.strip().split(":")
    try:
        nums = [int(p) for p in parts]
    except ValueError:
        return None
    secs = 0
    for n in nums:
        secs = secs * 60 + n
    return float(secs)


def _youtube_deep_link(video_id: str, seconds) -> str:
    """A watch URL deep-linked to a timestamp -- the citations contract."""
    vid = (video_id or "").strip()
    try:
        t = max(0, int(float(seconds)))
    except (TypeError, ValueError):
        t = 0
    return f"https://youtube.com/watch?v={vid}&t={t}s"


def compute_health(sidecar: dict) -> dict:
    """A per-video extraction health snapshot (A5), computed at extraction
    time. Stored on the sidecar under `health` and in the index. The
    comments / hook / comment-intelligence background workers finish *after*
    this snapshot, so those fields report in-progress status, not the final
    result."""
    comments = sidecar.get("comments")
    comments_status = sidecar.get("comments_status") or "unknown"
    if isinstance(comments, list) and len(comments) >= 5:
        comments_health = "ok"
    elif isinstance(comments, list) and comments:
        comments_health = "ok -- fewer than 5 comments"
    elif comments_status == "pending":
        comments_health = "pending"
    else:
        comments_health = "missing"
    return {
        "transcript": "ok" if sidecar.get("transcript") else "missing",
        "screenshots": "ok" if sidecar.get("screenshots") else "missing",
        "comments": comments_health,
        "hook": sidecar.get("hook_type_status") or "skipped",
        "comment_intelligence": sidecar.get("comment_intelligence_status") or "skipped",
    }


def _citations_from_sidecar(sidecar: dict, folder: Path) -> list[dict]:
    """Build the citation map (A4) from a parsed sidecar: one row per
    transcript chunk and one per screenshot, each with a timestamped
    YouTube deep link."""
    video_id = (sidecar.get("video_id") or "").strip()
    out: list[dict] = []
    for i, seg in enumerate(sidecar.get("transcript") or []):
        if not isinstance(seg, dict):
            continue
        start = _as_float(seg.get("start"))
        out.append({
            "kind": "transcript_chunk",
            "seq": i,
            "timestamp_start": start,
            "timestamp_end": _as_float(seg.get("end")),
            "text": seg.get("text"),
            "file_path": None,
            "youtube_deep_link": _youtube_deep_link(video_id, start),
        })
    for i, shot in enumerate(sidecar.get("screenshots") or []):
        if not isinstance(shot, dict):
            continue
        ts = _parse_hms(shot.get("timestamp"))
        rel = shot.get("path") or shot.get("filename") or ""
        out.append({
            "kind": "screenshot",
            "seq": i,
            "timestamp_start": ts,
            "timestamp_end": None,
            "text": None,
            "file_path": str(folder / rel) if rel else None,
            "youtube_deep_link": _youtube_deep_link(video_id, ts),
        })
    return out


def _index_yoink(folder: Path, sidecar: dict, corpus_path: Path | None,
                 sidecar_path: Path) -> bool:
    """Upsert one yoink + its citations into the library index. Best-effort
    and idempotent: callers (extraction hook, backfill) must treat a failure
    as non-fatal. Returns True if the row was indexed."""
    video_id = (sidecar.get("video_id") or "").strip()
    if not video_id:
        # video_id is the yoinks primary key + citations FK -- can't index.
        log.warning("index skip: no video_id for %s", folder)
        return False
    try:
        content = (corpus_path.read_text(encoding="utf-8")
                   if corpus_path and corpus_path.exists() else "")
    except OSError:
        content = ""
    record = {
        "video_id": video_id,
        "slug": folder.name,
        "channel": sidecar.get("channel"),
        "title": sidecar.get("title"),
        "topic": sidecar.get("topic"),
        "hook_type": sidecar.get("hook_type"),
        "yoinked_at": sidecar.get("yoinked_at") or _now_iso(),
        "corpus_path": str(corpus_path) if corpus_path else "",
        "sidecar_path": str(sidecar_path),
        "health_score_json": (
            json.dumps(sidecar["health"], ensure_ascii=False)
            if isinstance(sidecar.get("health"), dict) else None
        ),
        "metadata_json": json.dumps({
            "url": sidecar.get("url"),
            "duration_seconds": sidecar.get("duration_seconds"),
            "view_count": sidecar.get("view_count"),
            "like_count": sidecar.get("like_count"),
            "upload_date": sidecar.get("upload_date"),
        }, ensure_ascii=False),
    }
    idx = _get_index()
    idx.upsert_yoink(record, content=content)
    idx.insert_citations(video_id, _citations_from_sidecar(sidecar, folder))
    return True


def _iter_corpus_folders():
    """Yield (folder, corpus_path) for every live yoink folder under
    DESKTOP_ROOT. Soft-deleted yoinks parked under _yoink-trash/ are
    skipped so the backfill never re-indexes a trashed video."""
    if not DESKTOP_ROOT.exists():
        return
    trash = _trash_root()
    for folder in DESKTOP_ROOT.rglob("*"):
        if not folder.is_dir():
            continue
        if folder == trash or trash in folder.parents:
            continue
        corpus = _resolve_corpus_path(folder)
        if corpus is not None:
            yield folder, corpus


def _run_backfill() -> None:
    """Index every on-disk yoink folder not already in index.db. Incremental
    (skips rows already present) and cancellable via _backfill_cancel."""
    global _index_recovering
    try:
        known = _get_index().all_video_ids()
    except Exception:
        log.exception("backfill: could not read the index")
        with _backfill_lock:
            _backfill_state.update(state="complete")
        return
    folders = list(_iter_corpus_folders())
    with _backfill_lock:
        _backfill_state.update(state="running", current=0, total=len(folders))
    done = 0
    indexed = 0
    for folder, corpus in folders:
        if _backfill_cancel.is_set():
            log.info("backfill cancelled at %d/%d", done, len(folders))
            break
        done += 1
        with _backfill_lock:
            _backfill_state["current"] = done
        sidecar_path = folder / f"{folder.name}.json"
        try:
            sidecar = (json.loads(sidecar_path.read_text(encoding="utf-8"))
                       if sidecar_path.exists() else {})
        except (OSError, json.JSONDecodeError):
            sidecar = {}
        video_id = (sidecar.get("video_id") or "").strip()
        if not video_id or video_id in known:
            continue  # unindexable, or already indexed (incremental skip)
        try:
            if _index_yoink(folder, sidecar, corpus, sidecar_path):
                indexed += 1
        except Exception:
            log.exception("backfill: failed to index %s", folder)
    with _backfill_lock:
        _backfill_state["state"] = "complete"
    _index_recovering = False
    log.info("backfill complete: scanned %d folder(s), indexed %d new", done, indexed)


def _start_backfill_thread() -> None:
    """Kick the backfill scan off in the background so a missing or
    freshly-recovered index.db never delays the bind or /health."""
    _backfill_cancel.clear()

    def _runner():
        try:
            _run_backfill()
        except Exception:
            log.exception("backfill thread crashed")
            with _backfill_lock:
                _backfill_state["state"] = "complete"

    threading.Thread(target=_runner, name="index-backfill", daemon=True).start()


# Markers in yoink.md so the comments section can be replaced after the
# background fetch finishes. HTML comments are invisible in rendered markdown.
COMMENTS_START_MARK = "<!-- yoink:comments-start -->"
COMMENTS_END_MARK = "<!-- yoink:comments-end -->"
CI_START_MARK = "<!-- yoink:comment-intelligence-start -->"
CI_END_MARK = "<!-- yoink:comment-intelligence-end -->"
HOOK_START_MARK = "<!-- HOOK_START -->"
HOOK_END_MARK = "<!-- HOOK_END -->"
HOOK_TYPES = {
    "curiosity_gap",
    "question",
    "contrarian",
    "story_open",
    "promise_list",
    "demo",
    "authority",
    "stakes",
    "other",
}


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


def _fmt_likes(n) -> str:
    """Like counts are often hidden by YouTube and exposed as null by yt-dlp."""
    if n is None:
        return "not exposed by YouTube for this video"
    return _fmt_int(n)


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
class PlaylistJobCancelled(Exception):
    """Raised inside a playlist worker when the user cancels the job."""


def _raise_if_cancelled(cancel_event: threading.Event | None):
    if cancel_event is not None and cancel_event.is_set():
        raise PlaylistJobCancelled("playlist job cancelled")


def _terminate_process(proc: subprocess.Popen):
    try:
        proc.terminate()
        proc.wait(timeout=3)
    except Exception:
        try:
            proc.kill()
            proc.wait(timeout=3)
        except Exception:
            pass


def _run_subprocess(cmd: list[str], *, cancel_event: threading.Event | None = None,
                    timeout: int | float | None = None, check: bool = True,
                    stdout=None, stderr=None, text: bool = False,
                    encoding: str | None = None,
                    errors: str | None = None) -> subprocess.CompletedProcess:
    """Run a subprocess with optional cooperative cancellation.

    v1 callers pass no cancel_event and see normal subprocess behavior. v2
    playlist jobs pass a per-job Event so `/jobs/<id>/cancel` can terminate
    the active yt-dlp/ffmpeg process instead of waiting for a long timeout.
    """
    _raise_if_cancelled(cancel_event)
    proc = subprocess.Popen(
        cmd,
        stdout=stdout,
        stderr=stderr,
        text=text,
        encoding=encoding,
        errors=errors,
        **SUBPROCESS_KW,
    )
    started = time.monotonic()
    while True:
        if cancel_event is not None and cancel_event.is_set():
            _terminate_process(proc)
            raise PlaylistJobCancelled("playlist job cancelled")
        try:
            out, err = proc.communicate(timeout=0.2)
            break
        except subprocess.TimeoutExpired:
            if timeout is not None and (time.monotonic() - started) >= timeout:
                _terminate_process(proc)
                raise subprocess.TimeoutExpired(cmd, timeout)

    cp = subprocess.CompletedProcess(cmd, proc.returncode, out, err)
    if check and proc.returncode:
        raise subprocess.CalledProcessError(
            proc.returncode, cmd, output=out, stderr=err
        )
    return cp


_RATE_LIMIT_PATTERNS = (
    "http error 429",
    "rate limit",
    "rate-limit",
    "too many requests",
    "sign in to confirm you're not a bot",
    "confirm you're not a bot",
    "captcha",
)


def _error_text(e: BaseException) -> str:
    if isinstance(e, subprocess.CalledProcessError):
        stderr = (e.stderr.decode("utf-8", errors="ignore")
                  if isinstance(e.stderr, bytes) else (e.stderr or ""))
        stdout = (e.output.decode("utf-8", errors="ignore")
                  if isinstance(e.output, bytes) else (e.output or ""))
        return f"{stderr}\n{stdout}"
    return str(e)


def _is_rate_limit_error(e: BaseException) -> bool:
    text = _error_text(e).lower()
    return any(pat in text for pat in _RATE_LIMIT_PATTERNS)


def _sleep_with_cancel(seconds: float, cancel_event: threading.Event | None) -> None:
    if seconds <= 0:
        return
    deadline = time.monotonic() + seconds
    while True:
        _raise_if_cancelled(cancel_event)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(1.0, remaining))


def _fetch_metadata(url: str, *,
                    cancel_event: threading.Event | None = None) -> dict:
    """Single yt-dlp call that returns the full metadata blob without
    downloading the video. Used to derive the folder slug, fill the corpus
    header, and seed the thumbnail URL.
    """
    cp = _run_subprocess(
        [*YTDLP_CMD, "--dump-single-json", "--no-download", url],
        cancel_event=cancel_event,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=COMMENTS_TIMEOUT_SEC,
    )
    return json.loads(cp.stdout)


def _download_thumbnail(metadata: dict, output_folder: Path, *,
                        cancel_event: threading.Event | None = None) -> Path | None:
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
        _run_subprocess(
            ["ffmpeg", "-loglevel", "error", "-y",
             "-i", str(raw_path), str(jpg_path)],
            cancel_event=cancel_event,
            check=True,
            stderr=subprocess.PIPE,
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
    with _corpus_update_lock:
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


def _shape_comment_for_sidecar(c: dict) -> dict:
    """Pick the fields a downstream consumer actually wants. yt-dlp's raw
    comment objects carry a lot of internal cruft (parent ids, author
    channel ids, thumbnails) that bloats the sidecar without value."""
    return {
        "author": c.get("author"),
        "text": c.get("text"),
        "like_count": c.get("like_count") or 0,
        "time_text": c.get("_time_text") or c.get("time_text"),
        "is_pinned": bool(c.get("is_pinned")),
        "is_favorited": bool(c.get("is_favorited")),
        "reply_count": c.get("reply_count") or 0,
    }


def _update_sidecar_comments(output_folder: Path, comments: list | None,
                              status: str) -> None:
    """Patch the JSON sidecar in place once the comments worker resolves.
    Best-effort: a missing or unwritable sidecar is logged and ignored
    (the markdown is still the user-facing artifact)."""
    sidecar_path = output_folder / f"{output_folder.name}.json"
    with _sidecar_update_lock:
        if not sidecar_path.exists():
            return
        try:
            data = json.loads(sidecar_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            log.warning("sidecar comments update: read failed (%s)", e)
            return
        data["comments"] = comments
        data["comments_status"] = status
        tmp = sidecar_path.with_suffix(".json.tmp")
        try:
            tmp.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            tmp.replace(sidecar_path)
        except OSError as e:
            log.warning("sidecar comments update: write failed (%s)", e)


def _extract_json_object(text: str, *, label: str = "AI response") -> dict:
    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise AnthropicAPIError(None, f"{label} returned no JSON object")
    try:
        parsed = json.loads(cleaned[start:end + 1])
    except json.JSONDecodeError as e:
        raise AnthropicAPIError(None, f"{label} returned invalid JSON: {e}") from None
    if not isinstance(parsed, dict):
        raise AnthropicAPIError(None, f"{label} returned an unexpected shape")
    return parsed


def _clean_text(value, *, limit: int = 500) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:limit]


def _as_int(value, default: int = 0) -> int:
    if isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _normalize_comment_analysis(data: dict) -> dict:
    themes = []
    for item in (data.get("top_themes") or [])[:5]:
        if not isinstance(item, dict):
            continue
        quotes = [
            _clean_text(q, limit=280)
            for q in (item.get("quotes") or item.get("representative_quotes") or [])[:2]
            if _clean_text(q)
        ]
        themes.append({
            "label": _clean_text(item.get("label"), limit=80) or "Theme",
            "description": _clean_text(item.get("description"), limit=500),
            "count": _as_int(item.get("count"), 0),
            "quotes": quotes,
        })

    products = []
    for item in (data.get("mentioned_products_tools") or data.get("products_tools") or [])[:20]:
        if not isinstance(item, dict):
            continue
        name = _clean_text(item.get("name") or item.get("label"), limit=100)
        if not name:
            continue
        products.append({
            "name": name,
            "frequency": _as_int(item.get("frequency") or item.get("count"), 1),
        })

    disagreements = []
    for item in (data.get("notable_disagreements") or [])[:3]:
        if not isinstance(item, dict):
            continue
        samples = [
            _clean_text(q, limit=280)
            for q in (item.get("sample_comments") or item.get("quotes") or [])[:2]
            if _clean_text(q)
        ]
        disagreements.append({
            "description": _clean_text(item.get("description"), limit=500),
            "sample_comments": samples,
        })

    return {
        "model": ANTHROPIC_MODEL,
        "top_themes": themes,
        "mentioned_products_tools": products,
        "notable_disagreements": disagreements,
    }


def analyze_comments(comments: list[dict], *, api_key: str | None = None) -> dict:
    """Vendor-neutral internal interface for comment analysis.

    Future MCP can wrap this function directly as `analyze_comments`; the
    Anthropic-specific plumbing is intentionally hidden behind it.
    """
    key = (api_key or _anthropic_key_available() or "").strip()
    if not key:
        raise AnthropicAPIError(None, "Anthropic API key not configured")

    shaped = []
    for c in comments[:50]:
        text = _clean_text(c.get("text"), limit=1200)
        if not text:
            continue
        shaped.append({
            "author": _clean_text(c.get("author"), limit=80),
            "text": text,
            "like_count": _as_int(c.get("like_count"), 0),
        })
    if len(shaped) < 5:
        raise AnthropicAPIError(None, "not enough comments to analyze")

    system = (
        "You analyze YouTube comments for a creator-operator. Return valid JSON "
        "only. Do not include markdown. Cluster comments by meaning, identify "
        "mentioned products/tools, and describe substantive disagreements."
    )
    user = (
        "Analyze these top YouTube comments. Return this exact JSON shape:\n"
        "{\n"
        '  "top_themes": [{"label": string, "description": string, "count": number, "quotes": [string]}],\n'
        '  "mentioned_products_tools": [{"name": string, "frequency": number}],\n'
        '  "notable_disagreements": [{"description": string, "sample_comments": [string]}]\n'
        "}\n\n"
        "Rules: 3-5 top_themes, 1-2 quotes per theme, up to 20 products/tools, "
        "and 1-3 disagreements. If a category has no signal, return an empty "
        "array for that category.\n\n"
        f"Comments JSON:\n{json.dumps(shaped, ensure_ascii=False)}"
    )
    try:
        resp = _anthropic_messages(key, system=system, user=user, max_tokens=1200)
        return _normalize_comment_analysis(
            _extract_json_object(_anthropic_text(resp), label="Comment Intelligence")
        )
    except AnthropicAPIError as e:
        if e.status == 401:
            _mark_anthropic_key_invalid()
        raise


def _first_words(text: str, limit: int) -> str:
    words = re.split(r"\s+", (text or "").strip())
    words = [w for w in words if w]
    return " ".join(words[:limit])


def _hook_display_name(hook_type: str) -> str:
    return (hook_type or "other").replace("_", " ").title()


def _normalize_hook_analysis(data: dict) -> dict:
    hook_type = _clean_text(data.get("hook_type"), limit=80).lower()
    if hook_type not in HOOK_TYPES:
        hook_type = "other"
    return {
        "model": ANTHROPIC_MODEL,
        "hook_type": hook_type,
        "hook_explanation": _clean_text(data.get("hook_explanation"), limit=600),
    }


# The nine hook-type categories with one-line definitions, used as the
# system-prompt classification guide (Sprint 17 / A3). The names match
# HOOK_TYPES exactly.
_HOOK_TYPE_GUIDE = (
    "Hook type categories (pick exactly one):\n"
    "- curiosity_gap: teases an answer or outcome without revealing it, "
    "opening an information gap the viewer wants closed.\n"
    "- question: opens by directly asking the viewer a question.\n"
    "- contrarian: leads with a claim that challenges a common belief or "
    "consensus.\n"
    "- story_open: opens with a personal anecdote or a narrative scene.\n"
    "- promise_list: promises a specific list or count of takeaways, e.g. "
    "'5 ways to ...'.\n"
    "- demo: opens by showing the thing in action -- a visual or live "
    "demonstration.\n"
    "- authority: opens by establishing credentials, results, or proof of "
    "expertise.\n"
    "- stakes: opens by emphasizing what the viewer stands to gain or lose.\n"
    "- other: none of the above, or no identifiable hook pattern."
)


def _hook_fewshot_block(similar: list[dict]) -> str:
    """Format past user corrections as few-shot calibration anchors for the
    hook-type system prompt (A3). Empty string when there are none."""
    if not similar:
        return ""
    lines = ["", "",
             "Past corrections from this user (use as calibration anchors):"]
    for c in similar:
        title = _clean_text(c.get("title"), limit=160) or "(untitled)"
        channel = _clean_text(c.get("channel"), limit=120) or "(unknown channel)"
        line = (f'- Video "{title}" on channel "{channel}": classifier said '
                f'"{c.get("original_hook_type")}", user corrected to '
                f'"{c.get("corrected_hook_type")}".')
        reason = _clean_text(c.get("user_reason"), limit=300)
        if reason:
            line += f' Reason: "{reason}"'
        lines.append(line)
    return "\n".join(lines)


# Appended to the hook-type system prompt -- elicits an explicit 1-5
# self-confidence score on a line after the JSON (Sprint 17 / A3).
_HOOK_CONFIDENCE_GUIDE = (
    "\n\nAfter the JSON, on a separate line, output your confidence as "
    "exactly `Confidence: N`, where N is an integer from 1 to 5:\n"
    "- 5 = very confident, hook clearly fits exactly one category\n"
    "- 4 = confident, mild ambiguity\n"
    "- 3 = moderate, hook could fit one of two categories\n"
    "- 2 = uncertain, hook fits 'other' or is borderline\n"
    "- 1 = guessing, no clear pattern"
)


def _parse_hook_confidence(text: str) -> int | None:
    """Pull the 1-5 confidence integer from a hook-type model response.
    Returns None when the model emitted no parseable score."""
    text = text or ""
    m = re.search(r"confidence\s*[:=]\s*([1-5])\b", text, re.IGNORECASE)
    if m:
        return int(m.group(1))
    # Looser fallback: "confidence" followed shortly by a 1-5 digit.
    m = re.search(r"confidence\D{0,12}([1-5])\b", text, re.IGNORECASE)
    return int(m.group(1)) if m else None


def analyze_hook_type(context: dict, *, api_key: str | None = None) -> dict:
    """Classify one video's opening style.

    A3 (Sprint 17): self-calibrating. Past user corrections relevant to the
    video (same channel, then topic, then recent) are fetched from the
    library index and injected as few-shot anchors. Kept vendor-neutral so
    the MCP tool surface stays decoupled from Anthropic.
    """
    key = (api_key or _anthropic_key_for_feature("hook_type_enabled") or "").strip()
    if not key:
        raise AnthropicAPIError(None, "Anthropic API key not configured")

    title = _clean_text(context.get("title"), limit=220)
    description = _clean_text(context.get("description"), limit=1200)
    if not title and not description:
        raise AnthropicAPIError(None, "no title or description to classify")

    payload = {
        "title": title,
        "channel": _clean_text(context.get("channel"), limit=160),
        "description": description,
        "transcript_first_250_words": _first_words(
            str(context.get("transcript") or ""), 250
        ),
        "top_comment": _clean_text(context.get("top_comment"), limit=600),
    }

    # A3: past corrections relevant to this video become few-shot anchors.
    # Best-effort -- an index failure must never fail the classification.
    similar: list[dict] = []
    video_id = (context.get("video_id") or "").strip()
    if video_id:
        try:
            similar = _get_index().similar_corrections(video_id, limit=8)
        except Exception as e:
            log.warning("hook similar-corrections fetch failed: %s", e)
            similar = []

    system = (
        "You classify YouTube video hook styles for a creator-operator.\n\n"
        + _HOOK_TYPE_GUIDE
        + _hook_fewshot_block(similar)
        + "\n\nReturn valid JSON only, of exactly this shape:\n"
        '{"hook_type": string, "hook_explanation": string}\n'
        "hook_type must be exactly one of the categories above. "
        "hook_explanation is one or two sentences on what makes the opening "
        "fit that type."
        + _HOOK_CONFIDENCE_GUIDE
    )
    user = (
        "Classify this video's hook style.\n\n"
        f"Video context JSON:\n{json.dumps(payload, ensure_ascii=False)}"
    )
    try:
        resp = _anthropic_messages(key, system=system, user=user, max_tokens=400)
        text = _anthropic_text(resp)
        analysis = _normalize_hook_analysis(
            _extract_json_object(text, label="Hook Type")
        )
    except AnthropicAPIError as e:
        if e.status == 401:
            _mark_anthropic_key_invalid()
        raise
    # Confidence rides on a separate line after the JSON; parse it off the
    # raw text. None when the model didn't emit a parseable score.
    analysis["confidence"] = _parse_hook_confidence(text)
    analysis["similar_corrections_used"] = len(similar)
    return analysis


def _render_hook_analysis(analysis: dict) -> str:
    return "\n".join([
        "## Hook Analysis",
        HOOK_START_MARK,
        f"**Hook Type:** {_hook_display_name(analysis.get('hook_type') or 'other')}",
        f"**Analysis:** {analysis.get('hook_explanation') or 'No explanation returned.'}",
        HOOK_END_MARK,
    ])


def _render_hook_failure(reason: str) -> str:
    return "\n".join([
        "## Hook Analysis",
        HOOK_START_MARK,
        f"Hook Type: analysis failed - {reason}",
        HOOK_END_MARK,
    ])


def _replace_hook_analysis_section(yoink_path: Path, body: str) -> None:
    with _corpus_update_lock:
        try:
            text = yoink_path.read_text(encoding="utf-8")
        except OSError as e:
            log.warning("could not read corpus to update Hook Type: %s", e)
            return

        pattern = re.compile(
            r"(?:^|\n)## Hook Analysis\s*\n"
            + re.escape(HOOK_START_MARK)
            + r".*?"
            + re.escape(HOOK_END_MARK)
            + r"\n?",
            re.DOTALL,
        )
        if pattern.search(text):
            new_text = pattern.sub("\n" + body.rstrip() + "\n\n", text, count=1)
        else:
            # Insert immediately after the top metadata block, before the first
            # horizontal rule that separates metadata from the rest of the corpus.
            marker = "\n---\n"
            if marker in text:
                new_text = text.replace(marker, "\n" + body.rstrip() + "\n\n---\n", 1)
            else:
                new_text = text.rstrip() + "\n\n" + body.rstrip() + "\n"

        tmp = yoink_path.with_suffix(".md.tmp")
        try:
            tmp.write_text(new_text, encoding="utf-8")
            tmp.replace(yoink_path)
        except OSError as e:
            log.warning("could not write Hook Type section: %s", e)


def _update_sidecar_hook_type(output_folder: Path, *, status: str,
                              hook_type: str | None = None,
                              hook_explanation: str | None = None,
                              confidence: int | None = None,
                              error: str | None = None) -> None:
    sidecar_path = output_folder / f"{output_folder.name}.json"
    with _sidecar_update_lock:
        if not sidecar_path.exists():
            return
        try:
            data = json.loads(sidecar_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            log.warning("sidecar Hook Type update: read failed (%s)", e)
            return
        data["hook_type_status"] = status
        data["hook_type"] = hook_type
        data["hook_explanation"] = hook_explanation
        data["hook_type_confidence"] = confidence
        data["hook_type_error"] = error
        data["hook_type_updated_at"] = _now_iso()
        tmp = sidecar_path.with_suffix(".json.tmp")
        try:
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(sidecar_path)
        except OSError as e:
            log.warning("sidecar Hook Type update: write failed (%s)", e)


def _record_correction_in_sidecar(sidecar_path: Path, original: str,
                                  corrected: str) -> None:
    """Reflect a hook-type correction in the per-video sidecar (Sprint 17):
    promote hook_type to the corrected value and append an entry to the
    append-only hook_type_corrections log. Best-effort, serialised through
    _sidecar_update_lock."""
    with _sidecar_update_lock:
        try:
            data = json.loads(sidecar_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            log.warning("sidecar correction update: read failed (%s)", e)
            return
        data["hook_type"] = corrected
        log_entries = data.get("hook_type_corrections")
        if not isinstance(log_entries, list):
            log_entries = []
        log_entries.append({
            "original": original,
            "corrected": corrected,
            "corrected_at": _now_iso(),
        })
        data["hook_type_corrections"] = log_entries
        tmp = sidecar_path.with_suffix(".json.tmp")
        try:
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                           encoding="utf-8")
            tmp.replace(sidecar_path)
        except OSError as e:
            log.warning("sidecar correction update: write failed (%s)", e)


def _append_hook_taxonomy(context: dict, analysis: dict) -> None:
    """Record a Hook Type classification in the library index, deduplicated
    by video_id (INSERT OR REPLACE). Best-effort -- a failure here must not
    fail the classification it accompanies."""
    video_id = (context.get("video_id") or "").strip()
    if not video_id:
        return
    try:
        _get_index().upsert_taxonomy({
            "video_id": video_id,
            "hook_type": analysis.get("hook_type"),
            "hook_explanation": analysis.get("hook_explanation"),
            "channel": context.get("channel") or None,
            "title": context.get("title") or None,
            "classified_at": _now_iso(),
            "confidence": analysis.get("confidence"),
        })
    except Exception as e:
        log.warning("hook taxonomy index write failed: %s", e)


def _migrate_taxonomy_json_to_index() -> None:
    """One-time: import a pre-Sprint-15 taxonomy.json into the index
    `taxonomy` table, then rename it to taxonomy.json.migrated. A no-op once
    the file is gone. On any error the source is left intact and the helper
    still boots."""
    if not TAXONOMY_PATH.exists():
        return
    try:
        raw = json.loads(TAXONOMY_PATH.read_text(encoding="utf-8"))
        rows = raw if isinstance(raw, list) else []
        idx = _get_index()
        imported = 0
        for row in rows:
            if isinstance(row, dict) and (row.get("video_id") or "").strip():
                idx.upsert_taxonomy(row)
                imported += 1
        TAXONOMY_PATH.replace(
            TAXONOMY_PATH.with_name(TAXONOMY_PATH.name + ".migrated"))
        log.info("Migrated %d taxonomy record(s) into the index", imported)
    except Exception:
        log.exception("taxonomy.json migration failed; leaving the file in place")


def _query_taxonomy(*, channel: str | None = None,
                    hook_type: str | None = None,
                    limit: int = 50) -> list[dict]:
    """Hook taxonomy rows from the library index, newest classification
    first, with optional channel / hook_type filters. Return shape matches
    the pre-index file-backed version (video_id, hook_type,
    hook_explanation, channel, title, classified_at)."""
    hook_filter = (hook_type or "").strip().lower() or None
    channel_filter = (channel or "").strip() or None
    try:
        return _get_index().query_taxonomy(
            channel=channel_filter, hook_type=hook_filter, limit=limit,
        )
    except Exception as e:
        log.warning("taxonomy query failed: %s", e)
        return []


def _hook_type_context(metadata: dict, entries: list, top_comment: str | None = None) -> dict:
    transcript = " ".join(t for _s, _e, t in entries)
    return {
        "video_id": metadata.get("id") or "",
        "title": metadata.get("title") or "",
        "description": metadata.get("description") or "",
        "channel": metadata.get("channel") or metadata.get("uploader") or "",
        "transcript": transcript,
        "top_comment": top_comment or "",
    }


def _should_start_hook_type(metadata: dict) -> bool:
    if not _anthropic_key_for_feature("hook_type_enabled"):
        return False
    return bool((metadata.get("title") or "").strip()
                or (metadata.get("description") or "").strip())


def _hook_type_worker(output_folder: Path, yoink_path: Path,
                      context: dict) -> None:
    try:
        analysis = analyze_hook_type(context)
        _replace_hook_analysis_section(yoink_path, _render_hook_analysis(analysis))
        _update_sidecar_hook_type(
            output_folder,
            status="completed",
            hook_type=analysis.get("hook_type"),
            hook_explanation=analysis.get("hook_explanation"),
            confidence=analysis.get("confidence"),
        )
        _append_hook_taxonomy(context, analysis)
        log.info("Hook Type appended to %s", yoink_path)
    except AnthropicAPIError as e:
        reason = _short_reason(e.reason)
        if e.status == 401:
            _mark_anthropic_key_invalid()
            log.warning("Hook Type skipped: Anthropic API key invalid")
        else:
            log.warning("Hook Type failed: %s", reason)
        _replace_hook_analysis_section(yoink_path, _render_hook_failure(reason))
        _update_sidecar_hook_type(
            output_folder,
            status="failed",
            error=reason,
        )
    except Exception as e:
        reason = _short_reason(str(e))
        log.warning("Hook Type crashed: %s", reason)
        _replace_hook_analysis_section(yoink_path, _render_hook_failure(reason))
        _update_sidecar_hook_type(
            output_folder,
            status="failed",
            error=reason,
        )


def _start_hook_type_thread(output_folder: Path, yoink_path: Path,
                            metadata: dict, entries: list,
                            top_comment: str | None = None) -> threading.Thread | None:
    if not _should_start_hook_type(metadata):
        return None
    t = threading.Thread(
        target=_hook_type_worker,
        args=(output_folder, yoink_path,
              _hook_type_context(metadata, entries, top_comment)),
        name=f"hook-type-{output_folder.name}",
        daemon=True,
    )
    t.start()
    return t


def _render_comment_intelligence(analysis: dict) -> str:
    out = ["## Comment Intelligence", ""]

    out.append("### Top Themes")
    themes = analysis.get("top_themes") or []
    if not themes:
        out.append("- None found.")
    for t in themes:
        count = t.get("count") or 0
        out.append(
            f"- **{t.get('label') or 'Theme'}** ({count} comments): "
            f"{t.get('description') or 'No description.'}"
        )
        for q in t.get("quotes") or []:
            out.append(f"  - \"{q}\"")
    out.append("")

    out.append("### Mentioned Products/Tools")
    products = analysis.get("mentioned_products_tools") or []
    if not products:
        out.append("- None found.")
    for p in products:
        out.append(f"- **{p.get('name')}** ({p.get('frequency') or 1})")
    out.append("")

    out.append("### Notable Disagreements")
    disagreements = analysis.get("notable_disagreements") or []
    if not disagreements:
        out.append("- None found.")
    for d in disagreements:
        out.append(f"- {d.get('description') or 'Disagreement noted.'}")
        for q in d.get("sample_comments") or []:
            out.append(f"  - \"{q}\"")
    return "\n".join(out).rstrip()


def _replace_comment_intelligence_section(yoink_path: Path, body: str) -> None:
    block = f"{CI_START_MARK}\n{body.rstrip()}\n{CI_END_MARK}"
    with _corpus_update_lock:
        try:
            text = yoink_path.read_text(encoding="utf-8")
        except OSError as e:
            log.warning("could not read corpus to update Comment Intelligence: %s", e)
            return

        pattern = re.compile(
            re.escape(CI_START_MARK) + r".*?" + re.escape(CI_END_MARK),
            re.DOTALL,
        )
        if pattern.search(text):
            new_text = pattern.sub(block, text, count=1)
        elif COMMENTS_END_MARK in text:
            new_text = text.replace(COMMENTS_END_MARK, COMMENTS_END_MARK + "\n\n" + block, 1)
        else:
            new_text = text.rstrip() + "\n\n" + block + "\n"

        tmp = yoink_path.with_suffix(".md.tmp")
        try:
            tmp.write_text(new_text, encoding="utf-8")
            tmp.replace(yoink_path)
        except OSError as e:
            log.warning("could not write Comment Intelligence section: %s", e)


def _update_sidecar_comment_intelligence(output_folder: Path, *,
                                         status: str,
                                         analysis: dict | None = None,
                                         error: str | None = None) -> None:
    sidecar_path = output_folder / f"{output_folder.name}.json"
    with _sidecar_update_lock:
        if not sidecar_path.exists():
            return
        try:
            data = json.loads(sidecar_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            log.warning("sidecar CI update: read failed (%s)", e)
            return
        data["comment_intelligence_status"] = status
        data["comment_intelligence"] = analysis
        data["comment_intelligence_error"] = error
        data["comment_intelligence_updated_at"] = _now_iso()
        tmp = sidecar_path.with_suffix(".json.tmp")
        try:
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(sidecar_path)
        except OSError as e:
            log.warning("sidecar CI update: write failed (%s)", e)


def _comment_intelligence_worker(output_folder: Path, yoink_path: Path,
                                 comments: list[dict]) -> None:
    if len(comments) < 5:
        return
    try:
        analysis = analyze_comments(comments)
        _replace_comment_intelligence_section(
            yoink_path,
            _render_comment_intelligence(analysis),
        )
        _update_sidecar_comment_intelligence(
            output_folder,
            status="fetched",
            analysis=analysis,
        )
        log.info("Comment Intelligence appended to %s", yoink_path)
    except AnthropicAPIError as e:
        reason = _short_reason(e.reason)
        if e.status == 401:
            _mark_anthropic_key_invalid()
            log.warning("Comment Intelligence skipped: Anthropic API key invalid")
        else:
            log.warning("Comment Intelligence failed: %s", reason)
        body = "## Comment Intelligence\n\n" + (
            f"Comment Intelligence: analysis failed - {reason}"
        )
        _replace_comment_intelligence_section(yoink_path, body)
        _update_sidecar_comment_intelligence(
            output_folder,
            status="failed",
            analysis=None,
            error=reason,
        )
    except Exception as e:
        reason = _short_reason(str(e))
        log.warning("Comment Intelligence crashed: %s", reason)
        body = f"## Comment Intelligence\n\nComment Intelligence: analysis failed - {reason}"
        _replace_comment_intelligence_section(yoink_path, body)
        _update_sidecar_comment_intelligence(
            output_folder,
            status="failed",
            analysis=None,
            error=reason,
        )


def _start_comment_intelligence_thread(output_folder: Path, yoink_path: Path,
                                       comments: list[dict]) -> threading.Thread | None:
    if len(comments) < 5 or not _anthropic_key_available():
        return None
    t = threading.Thread(
        target=_comment_intelligence_worker,
        args=(output_folder, yoink_path, comments[:50]),
        name=f"comment-intelligence-{output_folder.name}",
        daemon=True,
    )
    t.start()
    return t


# ===========================================================================
# Entity extraction (Sprint 16) -- A2 minimal.
# ===========================================================================
# Transcript words sent to the model, ~3000 tokens, capped for cost control.
_ENTITY_TRANSCRIPT_WORD_CAP = 2200


def _entity_transcript_text(sidecar: dict) -> str:
    """Flatten the sidecar transcript into timestamped lines for the entity
    extractor. Each chunk is prefixed with its start time in seconds so the
    model can attribute a real timestamp to every mention. Capped at
    _ENTITY_TRANSCRIPT_WORD_CAP words."""
    lines: list[str] = []
    for seg in sidecar.get("transcript") or []:
        if not isinstance(seg, dict):
            continue
        text = str(seg.get("text") or "").strip()
        if not text:
            continue
        ts = _as_float(seg.get("start"))
        lines.append(f"[{ts:.1f}] {text}" if ts is not None else text)
    return _first_words("\n".join(lines), _ENTITY_TRANSCRIPT_WORD_CAP)


def _update_sidecar_entity_extraction(output_folder: Path, *, status: str,
                                      error: str | None = None) -> None:
    """Patch the sidecar's entity_extraction_status / _error fields. Mirrors
    _update_sidecar_hook_type; serialised through _sidecar_update_lock so it
    cannot clobber a concurrent comments / hook / CI sidecar write."""
    sidecar_path = output_folder / f"{output_folder.name}.json"
    with _sidecar_update_lock:
        if not sidecar_path.exists():
            return
        try:
            data = json.loads(sidecar_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            log.warning("sidecar entity update: read failed (%s)", e)
            return
        data["entity_extraction_status"] = status
        data["entity_extraction_error"] = error
        data["entity_extraction_updated_at"] = _now_iso()
        tmp = sidecar_path.with_suffix(".json.tmp")
        try:
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                           encoding="utf-8")
            tmp.replace(sidecar_path)
        except OSError as e:
            log.warning("sidecar entity update: write failed (%s)", e)


def extract_entities(transcript: str, *, title: str = "", channel: str = "",
                     api_key: str | None = None) -> list[dict]:
    """Vendor-neutral entity extraction over a video transcript.

    Returns a list of ``{name, type, mentions: [{timestamp, context}]}``
    dicts. Kept as a small Anthropic-free interface, mirroring
    analyze_comments / analyze_hook_type, so a future MCP surface can wrap
    it directly.
    """
    key = (api_key or _saved_anthropic_key() or "").strip()
    if not key:
        raise AnthropicAPIError(None, "Anthropic API key not configured")
    transcript = (transcript or "").strip()
    if not transcript:
        raise AnthropicAPIError(None, "no transcript to extract entities from")

    system = (
        "You extract named entities from a YouTube video transcript for a "
        "creator-operator's research library. Return valid JSON only, no "
        "markdown. Only include entities explicitly named in the transcript."
    )
    user = (
        "Extract the named entities from this video. Return this exact JSON "
        "shape:\n"
        "{\n"
        '  "entities": [\n'
        '    {"name": string, "type": string, '
        '"mentions": [{"timestamp": number, "context": string}]}\n'
        "  ]\n"
        "}\n\n"
        "Allowed type values: person, tool, product, company, topic, other.\n"
        "Each transcript chunk is prefixed with its start time in seconds "
        "like [12.5]; use the nearest one for each mention's timestamp. "
        "context is a short quote (<=200 chars) of where the entity comes "
        "up. Merge repeated references to the same entity into one "
        "entities[] item with multiple mentions. Return an empty array if "
        "the transcript names no clear entities.\n\n"
        f"Title: {title}\nChannel: {channel}\n\nTranscript:\n{transcript}"
    )
    try:
        resp = _anthropic_messages(key, system=system, user=user, max_tokens=2500)
        data = _extract_json_object(_anthropic_text(resp), label="Entity extraction")
    except AnthropicAPIError as e:
        if e.status == 401:
            _mark_anthropic_key_invalid()
        raise
    entities = data.get("entities") if isinstance(data, dict) else None
    return [e for e in (entities or []) if isinstance(e, dict)]


def _extract_entities(output_folder: Path, video_id: str, sidecar: dict) -> None:
    """Entity extraction worker body (Sprint 16). Best-effort background
    thread: pulls named entities off the transcript via Claude Haiku and
    writes them into the library index. Never raises -- a failure just
    records entity_extraction_status="failed" on the sidecar, with no
    retry. Skipped silently when no Anthropic key is configured.

    Note: the brief sketched this as _extract_entities(video_id, corpus_md,
    sidecar). It takes output_folder instead of corpus_md -- the transcript
    is read from the structured sidecar (which carries per-chunk
    timestamps the markdown corpus would force a re-parse of), and the
    folder is needed to write the sidecar status the brief itself requires.
    """
    video_id = (video_id or "").strip()
    if not video_id:
        return
    transcript = _entity_transcript_text(sidecar)
    if not transcript:
        # No transcript (e.g. a video with no captions) -- nothing to do.
        _update_sidecar_entity_extraction(output_folder, status="skipped")
        return
    try:
        entities = extract_entities(
            transcript,
            title=_clean_text(sidecar.get("title"), limit=220),
            channel=_clean_text(sidecar.get("channel"), limit=160),
        )
        written = _get_index().record_entities(
            video_id, entities, source="transcript"
        )
        _update_sidecar_entity_extraction(output_folder, status="completed")
        log.info("entity extraction: %s -> %d entities, %d mentions",
                 output_folder.name, len(entities), written)
    except AnthropicAPIError as e:
        reason = _short_reason(e.reason)
        if e.status == 401:
            log.warning("entity extraction skipped: Anthropic API key invalid")
        else:
            log.warning("entity extraction failed: %s", reason)
        _update_sidecar_entity_extraction(
            output_folder, status="failed", error=reason
        )
    except Exception as e:
        reason = _short_reason(str(e))
        log.warning("entity extraction crashed: %s", reason)
        _update_sidecar_entity_extraction(
            output_folder, status="failed", error=reason
        )


def _start_entity_extraction_thread(output_folder: Path,
                                    video_id: str | None,
                                    sidecar: dict) -> threading.Thread | None:
    """Spawn the entity extraction worker. Returns None (skips silently) when
    no Anthropic key is configured or the video has no id -- mirrors the
    Hook Type / Comment Intelligence skip pattern."""
    if not _saved_anthropic_key() or not (video_id or "").strip():
        return None
    t = threading.Thread(
        target=_extract_entities,
        args=(output_folder, video_id, sidecar),
        name=f"entity-extraction-{output_folder.name}",
        daemon=True,
    )
    t.start()
    return t


def _comments_worker(url: str, output_folder: Path, yoink_path: Path,
                     metadata: dict | None = None, entries: list | None = None,
                     max_comments: int = 100, top_n: int = 50) -> None:
    """Background-thread body. Fetches comments via yt-dlp, rewrites the
    comments section of the corpus md AND patches the JSON sidecar with
    structured comment objects + a comments_status field. Never raises --
    failures leave the disabled/unavailable note + matching status.
    """
    shaped_comments: list[dict] = []

    def _start_hook_after_comments():
        if metadata is None or entries is None:
            return
        top_comment = shaped_comments[0].get("text") if shaped_comments else None
        _start_hook_type_thread(
            output_folder, yoink_path, metadata, entries, top_comment=top_comment
        )

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
            _update_sidecar_comments(output_folder, [], "unavailable")
            _start_hook_after_comments()
            return
        info = json.loads(info_files[0].read_text(encoding="utf-8"))
        raw_comments = info.get("comments") or []
        if not raw_comments:
            _replace_comments_section(yoink_path,
                "*Comments are disabled on this video.*")
            _update_sidecar_comments(output_folder, [], "disabled")
            _start_hook_after_comments()
            return
        ranked = sorted(
            raw_comments,
            key=lambda c: c.get("like_count") or 0,
            reverse=True,
        )[:top_n]
        shaped_comments = [_shape_comment_for_sidecar(c) for c in ranked]
        _replace_comments_section(yoink_path, _render_comments(ranked))
        _update_sidecar_comments(output_folder, shaped_comments, "fetched")
        _start_hook_after_comments()
        _start_comment_intelligence_thread(output_folder, yoink_path, shaped_comments)
        log.info("comments appended to %s (%d of %d)",
                 yoink_path, len(ranked), len(raw_comments))
    except subprocess.CalledProcessError as e:
        stderr = (e.stderr or b"").decode("utf-8", errors="ignore").strip()
        log.warning("comments yt-dlp failed: %s", stderr.splitlines()[-1] if stderr else e.returncode)
        _replace_comments_section(yoink_path,
            "*Comments are disabled on this video.*")
        _update_sidecar_comments(output_folder, [], "disabled")
        _start_hook_after_comments()
    except Exception as e:
        log.warning("comments worker crashed: %s", e)
        _replace_comments_section(yoink_path,
            "*Comments could not be retrieved.*")
        _update_sidecar_comments(output_folder, [], "unavailable")
        _start_hook_after_comments()


def _start_comments_thread(url: str, output_folder: Path,
                           yoink_path: Path,
                           metadata: dict | None = None,
                           entries: list | None = None) -> threading.Thread:
    t = threading.Thread(
        target=_comments_worker,
        args=(url, output_folder, yoink_path, metadata, entries),
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
    likes = _fmt_likes(metadata.get("like_count"))
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
    parts.append("*[Yoinked with Yoink by ReplayRyan](https://ryanbiddy.com/yoink)*")
    parts.append("")

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Extraction core (shared by /extract and /session/add)
# ---------------------------------------------------------------------------
def _run_extraction(url: str, interval: int, output_folder: Path,
                    *, open_explorer: bool = True,
                    metadata: dict | None = None,
                    topic: str | None = None,
                    generate_paste: bool = True,
                    cancel_event: threading.Event | None = None,
                    phase_callback=None) -> dict:
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
        if phase_callback:
            phase_callback("metadata")
        metadata = _fetch_metadata(url, cancel_event=cancel_event)
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
    _download_thumbnail(metadata, output_folder, cancel_event=cancel_event)

    # Video + subs. Bounded to YTDLP_TIMEOUT_SEC so a stuck download doesn't
    # hold _extract_lock forever and block other yoinks.
    try:
        if phase_callback:
            phase_callback("download")
        _run_subprocess(
            [
                *YTDLP_CMD,
                "--write-auto-subs",
                "--write-subs",
                "--sub-lang", "en.*,en",
                "--convert-subs", "srt",
                # Require a video stream. Plain `worst` can pick audio-only
                # on some Shorts, which makes ffmpeg screenshot extraction
                # fail with "no packets" even though yt-dlp succeeded.
                "-f", "worst*[vcodec!=none][height>=360]/worst*[vcodec!=none]/worst",
                # Bail before downloading a multi-GB file (livestream VODs).
                "--max-filesize", str(YTDLP_MAX_FILESIZE_BYTES),
                "-o", str(output_folder / "video.%(ext)s"),
                url,
            ],
            cancel_event=cancel_event,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=YTDLP_TIMEOUT_SEC,
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
        raise RuntimeError(
            "yt-dlp produced no video file. The video may exceed the 2 GB "
            "download cap (set in helper config), or it may be unavailable, "
            "private, or region-locked."
        )
    video_file = video_files[0]

    shots_dir = output_folder / "screenshots"
    shots_dir.mkdir(exist_ok=True)
    try:
        if phase_callback:
            phase_callback("screenshots")
        _run_subprocess(
            [
                "ffmpeg", "-loglevel", "error", "-y",
                "-i", str(video_file),
                "-vf", f"fps=1/{interval}",
                "-q:v", "2",
                str(shots_dir / "shot_%04d.jpg"),
            ],
            cancel_event=cancel_event,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=FFMPEG_TIMEOUT_SEC,
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
    _atomic_write_text(yoink_path, yoink_md)
    hook_type_pending = _should_start_hook_type(metadata)

    # Structured JSON sidecar (STRAT). Same data the markdown carries but
    # in a machine-shaped form: future MCP server / programmatic tooling
    # consumes this without having to parse the human-facing md. Written
    # next to the md so it travels with the folder.
    #
    # `comments` ships as `null` here and is filled in by the comments
    # worker once yt-dlp returns -- mirrors the markdown placeholder
    # behavior. Consumers see `comments_status: "pending"` until the
    # worker either succeeds (`fetched`), finds none (`disabled`), or
    # fails (`unavailable`).
    try:
        sidecar = {
            "schema_version": 2,  # bumped: structured screenshots + comments
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
            # Structured shape: timestamp + relative path + bare filename so
            # consumers don't have to parse paths or recompute timestamps.
            "screenshots": [
                {
                    "timestamp": fmt_time(int(i * interval)),
                    "path": f"screenshots/{p.name}",
                    "filename": p.name,
                }
                for i, p in enumerate(shots)
            ],
            "channel_context": channel_ctx,
            "comments": None,
            "comments_status": "pending",
            "hook_type_status": "pending" if hook_type_pending else "skipped",
            "hook_type": None,
            "hook_explanation": None,
            "hook_type_confidence": None,
            "hook_type_error": None,
            "comment_intelligence": None,
            "comment_intelligence_status": "not_run",
            "comment_intelligence_error": None,
            # Sprint 16: entity extraction runs in the background once the
            # row is indexed. "pending" when a key is set, "skipped"
            # otherwise; the worker flips it to completed / failed.
            "entity_extraction_status": (
                "pending" if _saved_anthropic_key() else "skipped"
            ),
            "entity_extraction_error": None,
        }
        # A5: extraction-time health snapshot, stored on the sidecar.
        sidecar["health"] = compute_health(sidecar)
        sidecar_path = output_folder / f"{output_folder.name}.json"
        _atomic_write_text(sidecar_path, json.dumps(sidecar, ensure_ascii=False, indent=2))
    except (OSError, TypeError) as e:
        # Non-fatal: the markdown is the user-facing artifact. Sidecar is
        # for future tooling.
        log.warning("could not write JSON sidecar: %s", e)

    video_file.unlink(missing_ok=True)

    # Refresh the master _all-yoinks-index.md after every successful yoink.
    # Cheap (one stat per video folder), and re-scanning means a folder the
    # user manually deleted simply drops out of the index next time.
    _regenerate_index()

    # Sprint 15 (A1/A4/A5): incrementally index this yoink + its citation
    # map + health score in index.db. Best-effort -- a library-index failure
    # must never fail an otherwise-successful extraction. (This is separate
    # from _regenerate_index above, which maintains the human-readable
    # _all-yoinks-index.md file.)
    try:
        _index_yoink(output_folder, sidecar, yoink_path, sidecar_path)
    except Exception as e:
        log.warning("library index update failed for %s: %s", output_folder, e)

    # Sprint 16 (A2): extract named entities off the transcript in the
    # background, in parallel with the comments / Comment Intelligence
    # pipeline (it does not wait on either). Started after _index_yoink so
    # the yoinks row exists for the entity_mentions foreign key. Best-effort
    # -- a failure never fails an otherwise-successful extraction.
    _start_entity_extraction_thread(
        output_folder, sidecar.get("video_id"), sidecar
    )

    # Build the clipboard / paste version once we know the on-disk md is
    # final. Session adds skip this -- the session corpus is built at
    # /session/close time, so the per-video paste version would be unused
    # bytes shipped over the chrome.runtime message.
    paste_md: str | None = None
    if generate_paste:
        try:
            paste_md = _generate_paste_corpus(output_folder)
        except Exception as e:
            log.warning("paste corpus generation failed: %s", e)
            paste_md = None

    # Comments fetch in background; updates the corpus file when done. Hook
    # Type waits for this comments worker to finish so it can include the top
    # comment when one is available.
    if phase_callback:
        phase_callback("comments")
    _start_comments_thread(url, output_folder, yoink_path, metadata, entries)
    if phase_callback:
        phase_callback("done")

    if open_explorer:
        try:
            os.startfile(str(output_folder))  # type: ignore[attr-defined]
        except Exception as e:
            log.warning("startfile failed: %s", e)

    return {
        "ok": True,
        "folder": str(output_folder),
        "yoink_md": yoink_md,
        # Multimodal clipboard version: same content as yoink_md but with
        # screenshots inlined as base64 data URIs. Extension prefers this
        # over yoink_md when copying to the clipboard. None on session adds
        # or when generation fails -- caller falls back to yoink_md.
        "corpus_md_paste": paste_md,
        "screenshot_count": len(shots),
        "title": title,
        "video_slug": video_slug,
        "caption_count": len(entries),
        "topic": topic,
    }


INSTALL_HELP_URL = "https://ryanbiddy.com/yoink/install"


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
_PLAYLIST_ID_RE = re.compile(r"^[A-Za-z0-9_-]{2,}$")
_JOB_ID_RE = re.compile(r"^job_[A-Za-z0-9_-]{1,96}$")


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


def _normalize_playlist_url(raw: str) -> str | None:
    """Return canonical YouTube playlist URL, or None for unsupported input.

    Accepts youtube.com/playlist?list=... and watch URLs that carry a list=
    parameter. The returned URL intentionally drops any watch `v=` start
    position; Playlist Mode always processes the selected playlist from the
    first entry after the Python-side cap is applied.
    """
    if not raw or not isinstance(raw, str):
        return None
    try:
        u = urlparse(raw if "://" in raw else "https://" + raw)
    except ValueError:
        return None
    host = (u.hostname or "").lower()
    if host not in {"youtube.com", "www.youtube.com", "m.youtube.com"}:
        return None
    qs = parse_qs(u.query)
    list_id = (qs.get("list") or [""])[0]
    if not list_id or not _PLAYLIST_ID_RE.match(list_id):
        return None
    if u.path not in ("", "/", "/playlist", "/watch"):
        return None
    return f"https://www.youtube.com/playlist?list={list_id}"


def _is_valid_job_id(s: str) -> bool:
    return bool(s) and bool(_JOB_ID_RE.match(s))


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


# ---- Multimodal paste corpus (clipboard version) -------------------------
# The on-disk <slug>.md keeps local image refs (screenshots/shot_NNNN.jpg)
# so VS Code preview / Obsidian render the file straight from the folder.
# The CLIPBOARD version inlines a curated subset of screenshots as base64
# data URIs so a single Ctrl+V into Claude or ChatGPT delivers transcript +
# images without the user having to re-upload anything.
#
PASTE_SCREENSHOT_WIDTH = 800
PASTE_SCREENSHOT_QUALITY = 80
PASTE_SIZE_WARN_MB = 4

_SCREENSHOT_BLOCK_RE = re.compile(
    r"### \[([^\]]+)\]\n\n!\[Screenshot at [^\]]+\]\(screenshots/(shot_\d+\.jpg)\)\n",
)


def _select_paste_indices(n: int, target: int) -> list[int]:
    """Pick `target` evenly-distributed indices from [0, n). Always includes
    0 and n-1 (linear interpolation lands on those endpoints exactly).
    Returns sorted unique indices, so a small `n` may produce fewer than
    target points after rounding collisions are deduped."""
    if target <= 0:
        return []
    if n <= target:
        return list(range(n))
    if target == 1:
        return [0]
    return sorted({round(i * (n - 1) / (target - 1)) for i in range(target)})


def _clipboard_screenshot_cap() -> int:
    settings = _read_settings()
    try:
        cap = int(settings.get("clipboard_screenshot_cap"))
    except (TypeError, ValueError):
        cap = CLIPBOARD_SCREENSHOT_CAP_DEFAULT
    return max(0, min(CLIPBOARD_SCREENSHOT_CAP_MAX, cap))


def _encode_screenshot_b64(path: Path, *, max_width: int, quality: int) -> str:
    """Resize + JPEG-recompress + base64 a screenshot for clipboard
    embedding. Imports Pillow lazily so the rest of server.py keeps
    working in dev environments where Pillow isn't installed (the
    bundled installer always ships it)."""
    from PIL import Image  # type: ignore[import-not-found]
    import base64
    import io
    img = Image.open(path)
    if img.mode != "RGB":
        img = img.convert("RGB")
    if img.width > max_width:
        new_h = max(1, int(img.height * (max_width / img.width)))
        img = img.resize((max_width, new_h), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality, optimize=True)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _paste_header(size_mb: float) -> str:
    """Lead-in for the clipboard corpus. The blockquote shape lets it
    survive paste into Claude / ChatGPT without breaking the surrounding
    transcript markup."""
    lines = [
        "> This corpus includes embedded images. When pasted into Claude or",
        "> ChatGPT, the AI sees both the transcript text and the screenshots",
        "> inline.",
    ]
    if size_mb > PASTE_SIZE_WARN_MB:
        lines.append(">")
        lines.append(
            f"> Note: This corpus is large ({size_mb:.1f} MB). If pasting"
            " into the AI fails, open the .md file directly and paste"
            " manually."
        )
    lines.append("")
    lines.append("")
    return "\n".join(lines)


def _generate_paste_corpus(folder: Path) -> str:
    """Build the clipboard version of the corpus from <folder>/<slug>.md.

    Replaces local image refs (`screenshots/shot_NNNN.jpg`) with base64
    data URIs for up to the configured clipboard_screenshot_cap
    evenly-distributed shots.
    Drops the rest of the per-shot blocks (so the markdown stays readable
    instead of silently shrinking only some images).

    Returns the empty string if the corpus file isn't found, falls back
    to the unmodified file content when Pillow isn't installed (dev mode
    without the bundled distribution)."""
    corpus_path = _resolve_corpus_path(folder)
    if corpus_path is None:
        return ""
    md = corpus_path.read_text(encoding="utf-8")

    try:
        from PIL import Image  # noqa: F401  -- import probe
    except ImportError:
        log.warning(
            "Pillow not installed; clipboard corpus will keep local image"
            " references. Install Pillow or rebuild via the installer."
        )
        return md

    matches = list(_SCREENSHOT_BLOCK_RE.finditer(md))
    if not matches:
        # No screenshots to embed -- still prepend the header so the user
        # can tell the clipboard version was generated. Size is just the
        # md length.
        size_mb = len(md.encode("utf-8")) / (1024 * 1024)
        return _paste_header(size_mb) + md

    cap = _clipboard_screenshot_cap()
    selected = set(_select_paste_indices(len(matches), cap)) if cap > 0 else set()
    kept_count = len(selected)
    reduction_note = ""
    if kept_count < len(matches):
        reduction_note = (
            f"[Showing {kept_count} of {len(matches)} screenshots in clipboard; "
            "full set on disk]\n\n"
        )

    # Counter-aware substitution: we need the index of each match to know
    # whether it's in the selected set, but re.sub doesn't pass an index.
    counter = {"i": 0}

    def replacer(m: re.Match) -> str:
        idx = counter["i"]
        counter["i"] += 1
        if idx not in selected:
            return ""  # drop this block entirely
        ts = m.group(1)
        shot_name = m.group(2)
        try:
            b64 = _encode_screenshot_b64(
                folder / "screenshots" / shot_name,
                max_width=PASTE_SCREENSHOT_WIDTH,
                quality=PASTE_SCREENSHOT_QUALITY,
            )
        except (OSError, ValueError) as e:
            log.warning("paste: failed to encode %s: %s", shot_name, e)
            return m.group(0)  # leave the original block on encode failure
        return (
            f"### [{ts}]\n\n"
            f"![Screenshot at {ts}](data:image/jpeg;base64,{b64})\n"
        )

    paste_md = _SCREENSHOT_BLOCK_RE.sub(replacer, md)
    size_mb = len(paste_md.encode("utf-8")) / (1024 * 1024)
    return _paste_header(size_mb) + reduction_note + paste_md


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
# v2 Playlist jobs
# ---------------------------------------------------------------------------
_IMAGE_REF_LINE_RE = re.compile(r"^\s*!\[[^\]]*\]\([^)]+\)\s*$", re.MULTILINE)


def _strip_image_refs(md: str) -> str:
    """Clipboard playlist corpora are text-only; on-disk corpora keep images."""
    return _IMAGE_REF_LINE_RE.sub("", md)


def _strip_paste_header(md: str) -> str:
    """Remove the multimodal clipboard-only notice from persisted job text."""
    lines = md.splitlines()
    if not lines or not lines[0].startswith("> This corpus includes embedded images."):
        return md
    i = 0
    while i < len(lines) and (lines[i].startswith(">") or not lines[i].strip()):
        i += 1
    return "\n".join(lines[i:]).lstrip("\n")


def _job_text_only_corpus(md: str) -> str:
    """Small `/jobs` payload: no base64/data URI or local image references."""
    if not isinstance(md, str):
        return ""
    return _strip_image_refs(_strip_paste_header(md)).strip()


def _sanitize_single_job_result(result):
    """Strip legacy multimodal payloads from single-video job records."""
    if not isinstance(result, dict):
        return result
    clean = dict(result)
    clean.pop("corpus_md_paste", None)
    text = clean.get("combined_md_text")
    if isinstance(text, str):
        clean["combined_md_text"] = _job_text_only_corpus(text)
    return clean


def _coerce_nullable_int(v):
    if isinstance(v, bool) or v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _video_url_from_flat_entry(e: dict) -> str | None:
    vid = e.get("id")
    if isinstance(vid, str) and _VIDEO_ID_RE.match(vid):
        return f"https://www.youtube.com/watch?v={vid}"
    raw = e.get("webpage_url") or e.get("url")
    if isinstance(raw, str):
        if _VIDEO_ID_RE.match(raw):
            return f"https://www.youtube.com/watch?v={raw}"
        return _normalize_youtube_url(raw)
    return None


def _fetch_playlist_preview(url: str) -> tuple[dict | None, str | None, int]:
    """Return (playlist, error, status_code) for a validated playlist URL."""
    normalized = _normalize_playlist_url(url)
    if not normalized:
        return None, "playlist URL invalid", 400
    try:
        cp = _run_subprocess(
            [*YTDLP_CMD, "--dump-single-json", "--flat-playlist", normalized],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=COMMENTS_TIMEOUT_SEC,
        )
        data = json.loads(cp.stdout)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired,
            json.JSONDecodeError, OSError) as e:
        log.warning("playlist preview failed: %s", e)
        return None, "yt-dlp playlist preview failed", 200

    entries = [e for e in (data.get("entries") or []) if isinstance(e, dict)]
    videos = []
    for e in entries:
        video_url = _video_url_from_flat_entry(e)
        if not video_url:
            continue
        videos.append({
            "index": len(videos) + 1,
            "id": e.get("id") if isinstance(e.get("id"), str) else None,
            "url": video_url,
            "title": e.get("title") or "(untitled)",
            "channel": e.get("channel") or e.get("uploader"),
            "duration_seconds": _coerce_nullable_int(e.get("duration")),
        })

    if not videos:
        return None, "playlist has no videos", 200

    raw_count = data.get("playlist_count") or data.get("n_entries")
    video_count = _coerce_nullable_int(raw_count) or len(videos)
    truncated = video_count > PLAYLIST_VIDEO_CAP or len(videos) > PLAYLIST_VIDEO_CAP
    capped = videos[:PLAYLIST_VIDEO_CAP]
    for i, v in enumerate(capped, 1):
        v["index"] = i
    warnings = ["playlist exceeds cap"] if truncated else []
    message = (
        f"Playlist has {video_count} videos -- yoinking the first {PLAYLIST_VIDEO_CAP}."
        if truncated else
        f"Playlist has {len(capped)} video{'s' if len(capped) != 1 else ''}."
    )
    playlist = {
        "url": normalized,
        "title": data.get("title") or "YouTube Playlist",
        "uploader": data.get("uploader") or data.get("channel"),
        "video_count": video_count,
        "cap": PLAYLIST_VIDEO_CAP,
        "will_process_count": len(capped),
        "truncated": truncated,
        "message": message,
        "warnings": warnings,
        "videos": capped,
    }
    return playlist, None, 200


def _make_job_id() -> str:
    return f"job_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"


def _public_job(job: dict) -> dict:
    kind = job.get("kind") or "playlist"
    result = job.get("result")
    if kind == "single":
        result = _sanitize_single_job_result(result)
    return {
        "id": job.get("id"),
        "kind": kind,
        "state": job.get("state") or "failed",
        "source_url": job.get("source_url"),
        "title": job.get("title"),
        "playlist_title": job.get("playlist_title"),
        "session_folder": job.get("session_folder"),
        "videos_total": int(job.get("videos_total") or 0),
        "videos_done": int(job.get("videos_done") or 0),
        "videos_failed": int(job.get("videos_failed") or 0),
        "current_video": job.get("current_video"),
        "current_video_phase": job.get("current_video_phase"),
        "started_at": job.get("started_at"),
        "updated_at": job.get("updated_at"),
        "completed_at": job.get("completed_at"),
        "error": job.get("error"),
        "result": result,
        "warnings": list(job.get("warnings") or []),
        "message": job.get("message"),
    }


def _index_job_row(job: dict) -> dict:
    """Map an in-memory job dict (or an already-public job dict) to an index
    `jobs` table row. The full public projection is stored in metadata_json
    minus any corpus text -- jobs.metadata_json must never carry
    combined_md_text (the architectural bloat the Sprint 14b audit flagged)."""
    public = _public_job(job)
    result = public.get("result")
    if isinstance(result, dict) and "combined_md_text" in result:
        result = {k: v for k, v in result.items() if k != "combined_md_text"}
        public = {**public, "result": result}
    folder = job.get("session_folder")
    return {
        "job_id": job.get("id"),
        "kind": job.get("kind") or "playlist",
        "status": job.get("state") or "failed",
        "slug": Path(folder).name if folder else None,
        "title": job.get("title") or job.get("playlist_title"),
        "error": job.get("error"),
        "started_at": job.get("started_at"),
        "updated_at": job.get("updated_at") or _now_iso(),
        "metadata_json": json.dumps(public, ensure_ascii=False),
    }


def _persist_jobs_locked(changed_job: dict | None = None) -> None:
    """Persist job state into the library index. Caller must hold _jobs_lock.

    With `changed_job`, upserts just that one row -- the hot path: a single
    per-row SQLite write, replacing the old rewrite-the-entire-jobs.json-file
    pattern. With no argument, upserts every in-memory job (used once at
    restore, after non-terminal jobs are flipped to failed)."""
    try:
        idx = _get_index()
        jobs = [changed_job] if changed_job is not None else list(_jobs.values())
        for job in jobs:
            idx.upsert_job(_index_job_row(job))
    except Exception as e:
        log.warning("job persistence write failed: %s", e)


def _validate_persisted_job(raw: dict) -> dict | None:
    if not isinstance(raw, dict):
        return None
    job_id = raw.get("id")
    kind = raw.get("kind")
    state = raw.get("state")
    if not isinstance(job_id, str) or not job_id:
        return None
    if kind not in ("playlist", "single"):
        return None
    if state not in ("queued", "running", "completed", "cancelled", "failed"):
        return None

    job = _public_job(raw)
    if job["state"] not in _JOB_TERMINAL_STATES:
        now = _now_iso()
        job.update({
            "state": "failed",
            "current_video": None,
            "current_video_phase": None,
            "completed_at": now,
            "updated_at": now,
            "error": "server restarted",
            "result": None,
            "message": "Job failed because the Yoink helper restarted.",
        })
    return job


def _start_fresh_jobs(reason: str) -> None:
    log.warning("%s; starting fresh", reason)
    with _jobs_lock:
        _jobs.clear()
        _persist_jobs_locked()


def _restore_jobs_from_disk() -> None:
    """Hydrate the in-memory _jobs dict from the library index at startup.
    Non-terminal jobs are flipped to failed (their worker thread did not
    survive the restart) and the corrected state is written back.

    Named for historical continuity; the source is now index.db, not
    jobs.json (which _migrate_jobs_json_to_index folds in once)."""
    try:
        rows = _get_index().list_jobs(limit=1000)
    except Exception as e:
        log.warning("job restore from the index failed: %s", e)
        return
    restored: dict[str, dict] = {}
    for row in rows:
        meta = row.get("metadata_json")
        try:
            public = json.loads(meta) if meta else None
        except (json.JSONDecodeError, TypeError):
            public = None
        if not isinstance(public, dict):
            continue
        job = _validate_persisted_job(public)
        if job is not None:
            restored[job["id"]] = job
    with _jobs_lock:
        _jobs.clear()
        _jobs.update(restored)
        # _validate_persisted_job flipped non-terminal jobs to failed; write
        # those corrected states back so the index matches memory.
        _persist_jobs_locked()
    log.info("Restored %d job record(s) from the library index", len(restored))


def _migrate_jobs_json_to_index() -> None:
    """One-time: import a pre-Sprint-15 jobs.json into the index `jobs`
    table, then rename it to jobs.json.migrated. A no-op once the file is
    gone. combined_md_text is dropped by _index_job_row. On any error the
    source file is left intact and the helper still boots."""
    if not JOBS_PATH.exists():
        return
    try:
        raw = json.loads(JOBS_PATH.read_text(encoding="utf-8"))
        jobs_raw = raw.get("jobs") if isinstance(raw, dict) else None
        if not isinstance(jobs_raw, list):
            jobs_raw = []
        idx = _get_index()
        imported = 0
        for item in jobs_raw:
            if isinstance(item, dict) and item.get("id"):
                idx.upsert_job(_index_job_row(item))
                imported += 1
        JOBS_PATH.replace(JOBS_PATH.with_name(JOBS_PATH.name + ".migrated"))
        log.info("Migrated %d job(s) from jobs.json into the index", imported)
    except Exception:
        log.exception("jobs.json migration failed; leaving the file in place")


def _add_job_record(job: dict) -> dict:
    with _jobs_lock:
        _jobs[job["id"]] = job
        _persist_jobs_locked(job)
        return _public_job(job)


def _record_single_extract_job(url: str, started_at: str, *,
                               result: dict | None = None,
                               error: str | None = None,
                               title: str | None = None,
                               folder: Path | None = None) -> dict:
    now = _now_iso()
    ok = result is not None and not error
    folder_path = Path(result["folder"]) if result and result.get("folder") else folder
    corpus_path = _resolve_corpus_path(folder_path) if folder_path else None
    job = {
        "id": _make_job_id(),
        "kind": "single",
        "state": "completed" if ok else "failed",
        "source_url": url,
        "title": (result or {}).get("title") or title,
        "playlist_title": None,
        "session_folder": str(folder_path) if folder_path else None,
        "videos_total": 1,
        "videos_done": 1 if ok else 0,
        "videos_failed": 0 if ok else 1,
        "current_video": None,
        "current_video_phase": None,
        "started_at": started_at,
        "updated_at": now,
        "completed_at": now,
        "error": None if ok else (error or "single-video extraction failed"),
        "result": {
            "combined_md_path": str(corpus_path) if corpus_path else None,
            # Full corpus text is intentionally NOT persisted into the
            # jobs.json record. jobs.json is re-serialized in full on every
            # job mutation, so storing per-extract corpus text grew the file
            # linearly with lifetime yoink count. Consumers read the corpus
            # from combined_md_path / folder on demand.
            "combined_md_text": "",
            "folder": str(folder_path) if folder_path else None,
        } if ok else None,
        "warnings": [],
        "message": "Single-video yoink complete." if ok else "Single-video yoink failed.",
    }
    return _add_job_record(job)


def _get_public_job(job_id: str) -> dict | None:
    with _jobs_lock:
        job = _jobs.get(job_id)
        return _public_job(job) if job else None


def _update_job(job_id: str, **updates) -> dict | None:
    with _jobs_lock:
        job = _jobs.get(job_id)
        if not job:
            return None
        if job.get("state") in _JOB_TERMINAL_STATES:
            return _public_job(job)
        job.update(updates)
        job["updated_at"] = _now_iso()
        _persist_jobs_locked(job)
        return _public_job(job)


def _job_cancel_event(job_id: str) -> threading.Event | None:
    with _jobs_lock:
        job = _jobs.get(job_id)
        return job.get("_cancel_event") if job else None


def _list_public_jobs(kind: str | None = None) -> list[dict]:
    with _jobs_lock:
        jobs = [
            _public_job(j)
            for j in _jobs.values()
            if kind is None or j.get("kind") == kind
        ]
    return sorted(jobs, key=lambda j: j.get("updated_at") or "", reverse=True)


def _create_playlist_job(playlist: dict, interval: int) -> tuple[str, dict]:
    """Create + start a playlist job from an already-previewed playlist.

    Shared by the HTTP `/playlist/start` route and the MCP `yoink_playlist`
    tool so both entry points get identical job shapes and lifecycle.
    """
    job_id = _make_job_id()
    title = playlist.get("title") or "YouTube Playlist"
    folder_slug = slugify(title) or "playlist"
    folder = _session_folder(folder_slug)
    if folder.exists():
        folder = _session_folder(f"{folder_slug}_{job_id[-6:]}")
    cancel_event = threading.Event()
    now = _now_iso()
    job = {
        "id": job_id,
        "kind": "playlist",
        "state": "queued",
        "source_url": playlist["url"],
        "playlist_title": title,
        "session_folder": str(folder),
        "videos_total": playlist["will_process_count"],
        "videos_done": 0,
        "videos_failed": 0,
        "current_video": None,
        "current_video_phase": None,
        "started_at": None,
        "updated_at": now,
        "completed_at": None,
        "error": None,
        "result": None,
        "warnings": playlist.get("warnings") or [],
        "message": playlist.get("message"),
        "per_video": [],
        "_videos": playlist["videos"],
        "_interval": interval,
        "_folder": str(folder),
        "_cancel_event": cancel_event,
    }
    worker = threading.Thread(
        target=_playlist_worker,
        args=(job_id,),
        name=f"playlist-{job_id}",
        daemon=True,
    )
    job["_thread"] = worker
    with _jobs_lock:
        _jobs[job_id] = job
        _persist_jobs_locked(job)
        public = _public_job(job)
    worker.start()
    return job_id, public


def _cancel_playlist_job(job_id: str) -> tuple[dict | None, str | None, int]:
    """Cancel a running async job. Returns (job, error, status)."""
    with _jobs_lock:
        job = _jobs.get(job_id)
        if not job:
            return None, "job not found", 404
        if job.get("state") in _JOB_TERMINAL_STATES:
            return None, "job is already finished", 200
        event = job.get("_cancel_event")
        if not isinstance(event, threading.Event):
            return None, "job cancel failed", 200
        event.set()
        now = _now_iso()
        job.update({
            "state": "cancelled",
            "current_video": None,
            "current_video_phase": None,
            "completed_at": now,
            "error": None,
            "result": None,
            "message": "Playlist job cancelled. Partial outputs were left on disk.",
            "updated_at": now,
        })
        _persist_jobs_locked(job)
        return _public_job(job), None, 200


def _unique_child_folder(parent: Path, preferred: str, used: set[str]) -> Path:
    base = slugify(preferred) or "video"
    slug = base
    n = 2
    while slug in used or (parent / slug).exists():
        slug = f"{base}_{n}"
        n += 1
    used.add(slug)
    return parent / slug


def _build_playlist_corpus(job: dict, *, text_only: bool) -> str:
    title = job.get("playlist_title") or "YouTube Playlist"
    parts = [
        f"# Playlist Corpus: {title}",
        f"**Source:** {job.get('source_url')}",
        f"**Yoinked:** {_now_iso()}",
        f"**Videos:** {job.get('videos_done', 0)} succeeded, {job.get('videos_failed', 0)} failed",
        "",
        "---",
        "",
    ]

    for item in job.get("per_video", []):
        title = item.get("title") or "(unknown)"
        url = item.get("url") or ""
        parts.append(f"## Video {item.get('index')}: {title}")
        parts.append(f"Source: {url}")
        if item.get("folder"):
            parts.append(f"Local folder: {item.get('folder')}")
        parts.append("")

        if not item.get("ok"):
            parts.append(f"> _Failed: {item.get('error') or 'unknown error'}_")
        else:
            md_path = item.get("md_path")
            try:
                body = Path(md_path).read_text(encoding="utf-8")
                body = re.sub(r"^# .+\n", "", body, count=1)
                body = re.sub(r"^(\*\*[^*]+:\*\*[^\n]*\n)+", "", body)
                if text_only:
                    body = _strip_image_refs(body)
                parts.append(_demote_headings(body.strip()))
            except (OSError, TypeError) as e:
                parts.append(f"> _Failed to read corpus file: {e}_")

        parts.append("")
        parts.append("---")
        parts.append("")

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Authenticated file serving for extension UI thumbnails
# ---------------------------------------------------------------------------
_SERVED_IMAGE_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}


def _path_has_parent_ref(raw: str) -> bool:
    parts = str(raw).replace("\\", "/").split("/")
    return any(part == ".." for part in parts)


def _magic_matches(path: Path, mime: str) -> bool:
    try:
        head = path.read_bytes()[:16]
    except OSError:
        return False
    if mime == "image/png":
        return head.startswith(b"\x89PNG\r\n\x1a\n")
    if mime == "image/jpeg":
        return head.startswith(b"\xff\xd8\xff")
    if mime == "image/webp":
        return len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"WEBP"
    return False


def _resolve_served_file(raw_path: str) -> tuple[Path | None, str | None, int, str | None]:
    if not raw_path:
        return None, None, 400, "path required"
    if _path_has_parent_ref(raw_path):
        return None, None, 400, "path invalid"
    try:
        p = Path(raw_path)
        if not p.is_absolute():
            return None, None, 400, "path invalid"
        resolved = p.resolve()
        if any(part == ".." for part in resolved.parts):
            return None, None, 400, "path invalid"
        yoink_root = DESKTOP_ROOT.resolve()
        try:
            resolved.relative_to(yoink_root)
        except ValueError:
            return None, None, 403, "path escapes Yoink root"
    except (OSError, ValueError):
        return None, None, 400, "path invalid"

    if not resolved.exists() or not resolved.is_file():
        return None, None, 404, "file not found"
    try:
        if resolved.stat().st_size > MAX_SERVED_FILE_BYTES:
            return None, None, 400, "file too large"
    except OSError:
        return None, None, 404, "file not found"

    mime = _SERVED_IMAGE_TYPES.get(resolved.suffix.lower())
    if not mime or not _magic_matches(resolved, mime):
        return None, None, 415, "unsupported file type"
    return resolved, mime, 200, None


# ---------------------------------------------------------------------------
# MCP HTTP transport helpers
# ---------------------------------------------------------------------------
MCP_PROTOCOL_VERSION = "2025-11-25"
MCP_SUPPORTED_PROTOCOL_VERSIONS = {
    "2024-11-05",
    "2025-03-26",
    "2025-06-18",
    "2025-11-25",
}


def _mcp_tools_module():
    import yoink_mcp_tools

    yoink_mcp_tools.bind_backend(sys.modules[__name__])
    return yoink_mcp_tools


def _mcp_request_id(body: dict):
    return body.get("id") if isinstance(body, dict) else None


def _mcp_initialize_result(body: dict) -> dict:
    params = body.get("params") if isinstance(body.get("params"), dict) else {}
    requested = params.get("protocolVersion")
    protocol = (
        requested
        if isinstance(requested, str) and requested in MCP_SUPPORTED_PROTOCOL_VERSIONS
        else MCP_PROTOCOL_VERSION
    )
    return {
        "protocolVersion": protocol,
        "capabilities": {
            "tools": {"listChanged": False},
        },
        "serverInfo": {
            "name": "yoink",
            "version": VERSION,
        },
        "instructions": (
            "Yoink exposes local YouTube extraction tools. Outputs are stored "
            "under the user's Yoink output folder on this machine."
        ),
    }


def _mcp_stdio_command() -> tuple[str, list[str]]:
    """Command/args for client config snippets.

    Installed builds should use the bundled console `python.exe` for stdio;
    `pythonw.exe` has no standard streams and would break JSON-RPC.
    """
    bundled = HERE / "python" / "python.exe"
    command = bundled if bundled.exists() else Path(sys.executable)
    return str(command), [str(HERE / "yoink_mcp.py")]


def _mcp_config_payload() -> dict:
    command, args = _mcp_stdio_command()
    return {
        "ok": True,
        "stdio": {
            "command": command,
            "args": args,
        },
        "http": {
            "url": f"http://{HOST}:{PORT}/mcp/v1",
            "sse_url": f"http://{HOST}:{PORT}/mcp/v1/sse",
            "auth_header": "X-Yoink-Token",
        },
    }


def _finish_job_cancelled(job_id: str):
    _update_job(
        job_id,
        state="cancelled",
        current_video=None,
        current_video_phase=None,
        completed_at=_now_iso(),
        error=None,
        result=None,
        message="Playlist job cancelled. Partial outputs were left on disk.",
    )


def _write_failed_marker(folder: Path, *, url: str | None,
                         index: int | None, reason: str) -> None:
    lines = [
        "Yoink playlist item failed",
        "",
        f"Timestamp: {_now_iso()}",
    ]
    if index is not None:
        lines.append(f"Playlist index: {index}")
    if url:
        lines.append(f"URL: {url}")
    lines.extend(["", "Reason:", reason, ""])
    try:
        folder.mkdir(parents=True, exist_ok=True)
        _atomic_write_text(folder / "FAILED.txt", "\n".join(lines))
    except OSError as e:
        log.warning("could not write playlist failure marker: %s", e)


def _playlist_worker(job_id: str):
    public = _get_public_job(job_id)
    if not public:
        return
    cancel_event = _job_cancel_event(job_id)
    used_slugs: set[str] = set()

    with _jobs_lock:
        job = _jobs.get(job_id)
        videos = list(job.get("_videos") or []) if job else []
        interval = int(job.get("_interval") or 30) if job else 30
        folder = Path(job.get("_folder")) if job else SESSIONS_ROOT / job_id
    folder.mkdir(parents=True, exist_ok=True)

    if cancel_event is not None and cancel_event.is_set():
        _finish_job_cancelled(job_id)
        return

    _update_job(
        job_id,
        state="running",
        started_at=_now_iso(),
        message=f"Yoinking video 1 of {len(videos)}." if videos else "Starting playlist job.",
    )

    per_video = []
    videos_done = 0
    videos_failed = 0
    rate_limit_hits = 0

    try:
        for v in videos:
            _raise_if_cancelled(cancel_event)
            if per_video and PLAYLIST_SLEEP_SEC > 0:
                _update_job(
                    job_id,
                    message=(
                        f"Waiting {PLAYLIST_SLEEP_SEC:g}s before the next video "
                        "to avoid YouTube rate limits."
                    ),
                )
                _sleep_with_cancel(PLAYLIST_SLEEP_SEC, cancel_event)
            idx = int(v.get("index") or (len(per_video) + 1))
            current = {
                "index": idx,
                "title": v.get("title") or "(untitled)",
                "url": v.get("url"),
            }
            target: Path | None = None
            _update_job(
                job_id,
                current_video=current,
                current_video_phase="metadata",
                message=f"Yoinking video {idx} of {len(videos)}.",
            )

            try:
                metadata = _fetch_metadata(v["url"], cancel_event=cancel_event)
                title = metadata.get("title") or current["title"] or "Untitled"
                current["title"] = title
                target = _unique_child_folder(folder, title, used_slugs)
                _update_job(job_id, current_video=current)

                def phase_cb(phase: str, *, _job_id=job_id):
                    _update_job(_job_id, current_video_phase=phase)

                with _extract_lock:
                    _raise_if_cancelled(cancel_event)
                    result = _run_extraction(
                        v["url"],
                        interval,
                        target,
                        open_explorer=False,
                        metadata=metadata,
                        topic="Playlist",
                        generate_paste=False,
                        cancel_event=cancel_event,
                        phase_callback=phase_cb,
                    )

                corpus_path = _resolve_corpus_path(target)
                item = {
                    "index": idx,
                    "title": result.get("title") or title,
                    "url": v["url"],
                    "folder": str(target),
                    "md_path": str(corpus_path) if corpus_path else None,
                    "json_path": str(target / f"{target.name}.json"),
                    "ok": True,
                    "error": None,
                }
                per_video.append(item)
                videos_done += 1
                _update_job(
                    job_id,
                    videos_done=videos_done,
                    current_video_phase="done",
                    message=f"Finished video {idx} of {len(videos)}.",
                )
            except PlaylistJobCancelled:
                raise
            except BaseException as e:
                msg = friendly_error(e)
                log.error("playlist job %s video %d failed: %s", job_id, idx, msg)
                if target is None:
                    target = _unique_child_folder(
                        folder,
                        current.get("title") or v.get("id") or f"video-{idx}",
                        used_slugs,
                    )
                _write_failed_marker(
                    target,
                    url=v.get("url"),
                    index=idx,
                    reason=msg,
                )
                per_video.append({
                    "index": idx,
                    "title": current.get("title") or "(untitled)",
                    "url": v.get("url"),
                    "folder": str(target),
                    "md_path": None,
                    "json_path": None,
                    "failed_marker_path": str(target / "FAILED.txt"),
                    "ok": False,
                    "error": msg,
                })
                videos_failed += 1
                _update_job(
                    job_id,
                    videos_failed=videos_failed,
                    message=f"Video {idx} failed; continuing.",
                )
                if _is_rate_limit_error(e):
                    rate_limit_hits += 1
                    backoff = min(
                        PLAYLIST_RATE_LIMIT_BACKOFF_MAX_SEC,
                        PLAYLIST_RATE_LIMIT_BACKOFF_BASE_SEC * (2 ** (rate_limit_hits - 1)),
                    )
                    _update_job(
                        job_id,
                        message=(
                            "YouTube appears to be rate-limiting; backing off "
                            f"for {backoff:g}s before continuing."
                        ),
                    )
                    _sleep_with_cancel(backoff, cancel_event)

        with _jobs_lock:
            job = _jobs.get(job_id)
            if job:
                job["per_video"] = per_video
                job["videos_done"] = videos_done
                job["videos_failed"] = videos_failed
                _persist_jobs_locked(job)

        _raise_if_cancelled(cancel_event)
        if videos_done == 0:
            _update_job(
                job_id,
                state="failed",
                current_video=None,
                current_video_phase=None,
                completed_at=_now_iso(),
                error="playlist extraction failed: zero videos succeeded",
                result=None,
                message="Playlist failed: zero videos succeeded.",
            )
            return

        with _jobs_lock:
            job = dict(_jobs[job_id])
        disk_md = _build_playlist_corpus(job, text_only=False)
        clipboard_md = _build_playlist_corpus(job, text_only=True)
        corpus_path = folder / "corpus.md"
        _atomic_write_text(corpus_path, disk_md)
        _raise_if_cancelled(cancel_event)
        result = {
            "combined_md_path": str(corpus_path),
            "combined_md_text": clipboard_md,
            "per_video": per_video,
        }
        _update_job(
            job_id,
            state="completed",
            current_video=None,
            current_video_phase=None,
            completed_at=_now_iso(),
            error=None,
            result=result,
            message="Playlist complete.",
        )
    except PlaylistJobCancelled:
        log.info("playlist job %s cancelled", job_id)
        _finish_job_cancelled(job_id)
    except BaseException as e:
        msg = friendly_error(e)
        log.error("playlist job %s failed: %s", job_id, msg)
        _update_job(
            job_id,
            state="failed",
            current_video=None,
            current_video_phase=None,
            completed_at=_now_iso(),
            error=msg,
            result=None,
            message="Playlist failed.",
        )


# ---------------------------------------------------------------------------
# Soft delete -- _yoink-trash/ (Sprint 18 / B1)
# ---------------------------------------------------------------------------
def _trash_root() -> Path:
    """The trash folder soft-deleted yoinks are moved into."""
    return DESKTOP_ROOT / "_yoink-trash"


def _fs_safe_ts(iso: str) -> str:
    """A filesystem-safe rendering of an ISO timestamp -- drops the colons
    Windows forbids in path names. Deterministic, so a trash folder name
    can be recomputed from the stored deleted_at."""
    return (iso or "").replace(":", "")


def _trash_folder_for(row: dict) -> Path:
    """The trash destination for a soft-deleted yoink row:
    _yoink-trash/<topic-folder>/<slug>__deleted-<deleted_at>. Derived from
    corpus_path so it mirrors the on-disk topic folder exactly, and from
    deleted_at so delete / restore / purge all agree on the same path."""
    original = Path(row["corpus_path"]).parent
    topic_folder = original.parent.name
    slug = original.name
    ts = _fs_safe_ts(row.get("deleted_at") or "")
    return _trash_root() / topic_folder / f"{slug}__deleted-{ts}"


# Trash purge cadence: a pass at startup, then once a day.
_TRASH_PURGE_INTERVAL_SEC = 24 * 60 * 60


def _purge_trash() -> int:
    """One trash-purge pass: hard-delete every soft-deleted yoink past the
    30-day retention window -- both its _yoink-trash/ folder and its index
    row (the FK cascade then clears its citations, entity_mentions, and
    taxonomy_corrections). Returns the number purged."""
    try:
        idx = _get_index()
        stale = idx.prune_trash(datetime.now())
    except Exception as e:
        log.warning("trash purge: could not query the index: %s", e)
        return 0
    purged = 0
    for video_id in stale:
        row = idx.get_yoink(video_id)
        if not row:
            continue
        try:
            trash = _trash_folder_for(row)
            if trash.exists():
                shutil.rmtree(trash, ignore_errors=True)
            idx.delete_yoink(video_id)
            purged += 1
        except Exception:
            log.exception("trash purge: failed to purge %s", video_id)
    if purged:
        log.info("trash purge: hard-removed %d expired yoink(s)", purged)
    return purged


def _start_trash_purge_thread() -> None:
    """Run the trash purge once at startup, then every 24h. Daemon thread
    so it never delays the bind or blocks shutdown."""
    def _runner():
        while True:
            try:
                _purge_trash()
            except Exception:
                log.exception("trash purge pass crashed")
            time.sleep(_TRASH_PURGE_INTERVAL_SEC)

    threading.Thread(target=_runner, name="trash-purge", daemon=True).start()


def _enrich_yoink_row(idx, r: dict) -> dict | None:
    """Shape one index `yoinks` row into the enriched result the popup's
    /recent list and the memory page both consume: fresh health (Sprint
    15), entity stats (Sprint 16), hook type + confidence (Sprint 17), and
    the thumbnail path (Sprint 18). Returns None when the row lacks the
    video_id / corpus_path needed to render it."""
    video_id = r.get("video_id")
    corpus_path = r.get("corpus_path") or ""
    if not video_id or not corpus_path:
        return None
    folder = Path(corpus_path).parent
    sidecar_path = r.get("sidecar_path") or ""

    # Fresh health from the live sidecar -- the stored snapshot is captured
    # at extraction time, before the AI workers finish, so re-computing from
    # the current sidecar reflects the latest hook / CI / entity status.
    health = None
    if sidecar_path and Path(sidecar_path).exists():
        try:
            live = json.loads(Path(sidecar_path).read_text(encoding="utf-8"))
            health = compute_health(live)
        except (OSError, json.JSONDecodeError):
            pass
    if health is None and r.get("health_score_json"):
        try:
            health = json.loads(r["health_score_json"])
        except (json.JSONDecodeError, TypeError):
            pass

    # Hook Type + confidence (Sprint 17). Both live on the taxonomy row --
    # the hook worker updates the taxonomy table, not yoinks, when
    # classification completes, so taxonomy is the authoritative read.
    hook_type = r.get("hook_type")
    confidence = None
    try:
        with idx._lock:
            tr = idx._conn.execute(
                "SELECT hook_type, confidence FROM taxonomy WHERE video_id=?",
                (video_id,),
            ).fetchone()
        if tr:
            if tr["hook_type"]:
                hook_type = tr["hook_type"]
            if tr["confidence"] is not None:
                confidence = int(tr["confidence"])
    except Exception:
        pass

    # Entity stats (Sprint 16): distinct entity count + top 5 by mentions.
    entity_count = 0
    top_entities: list[str] = []
    try:
        with idx._lock:
            ec = idx._conn.execute(
                "SELECT COUNT(DISTINCT entity_id) AS c "
                "FROM entity_mentions WHERE video_id=?", (video_id,),
            ).fetchone()
            if ec:
                entity_count = int(ec["c"] or 0)
            es = idx._conn.execute(
                "SELECT e.name FROM entity_mentions em "
                "JOIN entities e ON e.entity_id = em.entity_id "
                "WHERE em.video_id = ? "
                "GROUP BY em.entity_id ORDER BY COUNT(*) DESC LIMIT 5",
                (video_id,),
            ).fetchall()
            top_entities = [row["name"] for row in es]
    except Exception:
        pass

    # Thumbnail (Sprint 18): absolute path when thumbnail.jpg is on disk so
    # the memory page can fetch it via the token-gated /file endpoint.
    thumb = folder / "thumbnail.jpg"
    thumbnail_path = str(thumb) if thumb.exists() else None

    return {
        "title": r.get("title") or "",
        "topic": r.get("topic") or "",
        "folder": str(folder),
        "video_id": video_id,
        "channel": r.get("channel"),
        "yoinked_at": r.get("yoinked_at"),
        "hook_type": hook_type,
        "hook_type_confidence": confidence,
        "health": health,
        "entity_count": entity_count,
        "top_entities": top_entities,
        "thumbnail_path": thumbnail_path,
    }


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    server_version = f"Yoink/{VERSION}"
    # Per-request socket timeout. BaseHTTPRequestHandler.setup() applies this
    # to the connection, so a client that opens a socket (or sends a
    # Content-Length header) and then stalls cannot pin a worker thread
    # indefinitely. Each socket read is bounded to 30s; legitimate requests
    # -- including the largest allowed body -- complete well within that.
    timeout = 30

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
        """Pull the auth token from the X-Yoink-Token header.

        Header-only by design: the previous ?token= query-param fallback
        was unused (the extension always set the header) and would have
        leaked the token into the user's browser history, the server's
        own access logs, and any HTTP debugging tooling that captures
        URLs but redacts headers."""
        return (self.headers.get("X-Yoink-Token") or "").strip()

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

    def _send_empty(self, status: int = 202):
        self.send_response(status)
        self.send_header("Content-Length", "0")
        self._send_cors(self._cors_origin())
        self.end_headers()

    def _send_file(self, path: Path, mime: str):
        try:
            body = path.read_bytes()
        except OSError:
            return self._send_json(404, {"ok": False, "error": "file not found"})
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "private, max-age=300")
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
            return self._send_json(200, {
                "ok": True,
                "version": VERSION,
                # True while a corrupt index.db is being rebuilt from disk.
                "index_recovering": _index_recovering,
            })
        if bare == "/index/backfill-status":
            # Public, read-only progress counts (same posture as /health) so
            # the popup can poll a backfill banner without the token dance.
            with _backfill_lock:
                snapshot = dict(_backfill_state)
            return self._send_json(200, {"ok": True, **snapshot})
        if bare == "/token":
            return self._handle_token()
        # Everything below mutates state or reveals user data -- token-gated.
        if not self._require_token():
            return
        if bare == "/session/list":
            return self._handle_session_list()
        if bare == "/session/active":
            return self._handle_session_active()
        if bare == "/settings":
            return self._handle_settings_get()
        if bare == "/settings/pricing":
            return self._handle_settings_pricing()
        if bare == "/file":
            return self._handle_file()
        if bare == "/mcp/v1/config":
            return self._send_json(200, _mcp_config_payload())
        if bare == "/mcp/v1/sse":
            return self._handle_mcp_sse()
        if bare == "/skill/system-prompt":
            return self._handle_skill_system_prompt()
        if bare == "/open-prompts":
            return self._handle_open_prompts()
        if bare == "/open-index":
            return self._handle_open_index()
        if bare == "/recent":
            return self._handle_recent()
        if bare == "/open-folder":
            return self._handle_open_folder()
        if bare == "/jobs":
            return self._handle_jobs_list()
        if bare.startswith("/jobs/"):
            return self._handle_job_get(bare)
        if bare == "/taxonomy":
            return self._handle_taxonomy()
        if bare == "/taxonomy/corrections":
            return self._handle_taxonomy_corrections()
        if bare == "/memory/search":
            return self._handle_memory_search()
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

    # ---- /settings ----
    def _handle_settings_get(self):
        self._send_json(200, {"ok": True, "settings": _public_settings()})

    def _handle_settings_pricing(self):
        self._send_json(200, {"ok": True, "pricing": _anthropic_pricing_payload()})

    def _handle_settings_post(self, body: dict):
        boolean_fields = (
            "comment_intelligence_enabled",
            "hook_type_enabled",
            "smart_screenshot_picker_enabled",
        )
        integer_fields = ("clipboard_screenshot_cap",)
        if (
            not any(f in body for f in boolean_fields)
            and not any(f in body for f in integer_fields)
            and "anthropic_key" not in body
        ):
            return self._send_json(400, {
                "ok": False,
                "error": "settings field required",
            })
        for field in boolean_fields:
            if field in body and not isinstance(body.get(field), bool):
                return self._send_json(400, {
                    "ok": False,
                    "error": f"{field} must be boolean",
                })
        if "clipboard_screenshot_cap" in body:
            cap = body.get("clipboard_screenshot_cap")
            if isinstance(cap, bool) or not isinstance(cap, int):
                return self._send_json(400, {
                    "ok": False,
                    "error": "clipboard_screenshot_cap must be an integer",
                })
            if cap < 0 or cap > CLIPBOARD_SCREENSHOT_CAP_MAX:
                return self._send_json(400, {
                    "ok": False,
                    "error": f"clipboard_screenshot_cap must be 0-{CLIPBOARD_SCREENSHOT_CAP_MAX}",
                })
        if "anthropic_key" in body and body.get("anthropic_key") is not None:
            if not isinstance(body.get("anthropic_key"), str):
                return self._send_json(400, {
                    "ok": False,
                    "error": "anthropic_key must be a string or null",
                })
            if len(body.get("anthropic_key")) > 4096:
                return self._send_json(400, {
                    "ok": False,
                    "error": "anthropic_key is too long",
                })

        data = _read_settings()
        for field in boolean_fields:
            if field in body:
                data[field] = body[field]
        if "clipboard_screenshot_cap" in body:
            data["clipboard_screenshot_cap"] = int(body["clipboard_screenshot_cap"])
        if "anthropic_key" in body:
            raw_key = body.get("anthropic_key")
            key = "" if raw_key is None else raw_key.strip()
            try:
                _store_saved_anthropic_key(key)
            except CredentialStoreError as e:
                log.warning("settings credential write failed: %s", e)
                return self._send_json(200, {
                    "ok": False,
                    "error": "credential store unavailable",
                })
            data["anthropic_key_invalid"] = False
        data["updated_at"] = _now_iso()
        try:
            _write_settings(data)
        except OSError as e:
            log.warning("settings write failed: %s", e)
            return self._send_json(200, {"ok": False, "error": "settings write failed"})
        self._send_json(200, {"ok": True, "settings": _public_settings(data)})

    # ---- /settings/test-key ----
    def _handle_settings_test_key(self, body: dict):
        provided = "anthropic_key" in body and body.get("anthropic_key") is not None
        if provided and not isinstance(body.get("anthropic_key"), str):
            return self._send_json(400, {
                "ok": False,
                "error": "anthropic_key must be a string or null",
            })
        if provided:
            key = body.get("anthropic_key").strip()
            using_stored_key = False
        else:
            data = _read_settings()
            key = _get_saved_anthropic_key().strip()
            using_stored_key = True

        ok, reason, status = _test_anthropic_key(key)
        if not ok and status == 401 and using_stored_key:
            _mark_anthropic_key_invalid()
        self._send_json(200, {
            "ok": True,
            "valid": ok,
            "error": None if ok else reason,
            "settings": _public_settings(),
        })

    # ---- /file?path=... ----
    # Authenticated thumbnail serving for extension UI. MV3 popups cannot
    # reliably render file:// paths, so the helper exposes a very narrow
    # image-only, Yoink-output-root-only file endpoint.
    def _handle_file(self):
        qs = parse_qs(urlparse(self.path).query)
        raw_path = (qs.get("path") or [""])[0]
        path, mime, status, error = _resolve_served_file(raw_path)
        if error:
            return self._send_json(status, {"ok": False, "error": error})
        return self._send_file(path, mime)

    # ---- MCP HTTP transport ----
    # This is a small JSON-RPC HTTP wrapper over the same tool registry used
    # by yoink_mcp.py's stdio server. It intentionally keeps state out of the
    # transport; auth remains the v1 X-Yoink-Token gate.
    def _send_mcp_result(self, request_id, result: dict):
        return self._send_json(200, {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": result,
        })

    def _send_mcp_error(self, request_id, code: int, message: str):
        return self._send_json(200, {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": code, "message": message},
        })

    def _handle_mcp_sse(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self._send_cors(self._cors_origin())
        self.end_headers()
        # Compatibility shim for HTTP/SSE clients: advertise the JSON-RPC
        # POST endpoint. Most desktop agents use stdio; HTTP clients can use
        # /mcp/v1 directly with the same JSON-RPC messages.
        self.wfile.write(b"event: endpoint\ndata: /mcp/v1\n\n")
        self.wfile.flush()
        self.close_connection = True

    def _mcp_tool_call_result(self, payload: dict) -> dict:
        is_error = not bool(payload.get("ok", True))
        text = json.dumps(payload, ensure_ascii=False)
        return {
            "content": [{"type": "text", "text": text}],
            "structuredContent": payload,
            "isError": is_error,
        }

    def _handle_mcp_post(self, bare: str, body: dict):
        request_id = _mcp_request_id(body)
        method = body.get("method") if isinstance(body.get("method"), str) else None
        # Support both a single RPC endpoint (/mcp/v1 with method in body) and
        # explicit helper paths (/mcp/v1/tools/call) because different HTTP
        # MCP clients are still converging on transport details.
        if bare == "/mcp/v1/initialize" or (bare == "/mcp/v1" and method == "initialize"):
            return self._send_mcp_result(request_id, _mcp_initialize_result(body))
        if method == "notifications/initialized":
            # JSON-RPC notifications have no response id/body. Return an
            # empty 202 so strict clients don't see a non-MCP `{ok:true}`.
            return self._send_empty(202)
        if method == "ping":
            return self._send_mcp_result(request_id, {})
        if bare == "/mcp/v1/tools/list" or (bare == "/mcp/v1" and method == "tools/list"):
            return self._send_mcp_result(request_id, {
                "tools": _mcp_tools_module().list_tools(),
            })
        if bare == "/mcp/v1/tools/call" or (bare == "/mcp/v1" and method == "tools/call"):
            params = body.get("params") if isinstance(body.get("params"), dict) else body
            name = params.get("name")
            args = params.get("arguments") or {}
            if not isinstance(name, str) or not isinstance(args, dict):
                return self._send_mcp_error(request_id, -32602, "invalid tool call")
            payload = _mcp_tools_module().call_tool(name, args)
            return self._send_mcp_result(request_id, self._mcp_tool_call_result(payload))
        return self._send_mcp_error(request_id, -32601, "method not found")

    # ---- /recent ----
    # Walk Desktop\Yoink\<topic>\<slug>\ and return the 3 most recent video
    # folders. A folder counts as a yoink if it has a yoink.md inside it.
    # Sessions root (_sessions/) is excluded.
    def _handle_recent(self):
        """Recent yoinks for the popup. Sprint 15.1 follow-up:
        replaces the disk-walk with an Index.list_recent read and enriches
        each row via _enrich_yoink_row (the same helper the memory page
        uses). Falls back to an empty list if the index is unavailable."""
        idx = _get_index()
        try:
            rows = idx.list_recent(limit=10)
        except Exception as e:
            log.warning("recent: index unavailable: %s", e)
            rows = []
        results = [er for er in (_enrich_yoink_row(idx, r) for r in rows) if er]
        self._send_json(200, {"ok": True, "recent": results})

    # ---- /memory/search ----
    def _handle_memory_search(self):
        """Filtered/paginated yoink search behind the memory page (B1).
        Token-gated, rate-limited (heavier than /recent due to FTS)."""
        if not _check_memory_search_rate_limit():
            return self._send_json(429, {"ok": False, "error": "too many requests"})
        qs = parse_qs(urlparse(self.path).query)

        def _one(name: str) -> str | None:
            value = (qs.get(name) or [""])[0].strip()
            return value or None

        hook_type = _one("hook_type")
        if hook_type:
            hook_type = hook_type.lower()
            if hook_type not in HOOK_TYPES:
                return self._send_json(
                    400, {"ok": False, "error": "hook_type invalid"})
        date_from = _one("date_from")
        date_to = _one("date_to")
        for label, value in (("date_from", date_from), ("date_to", date_to)):
            if value and not _valid_iso_date(value):
                return self._send_json(
                    400, {"ok": False, "error": f"{label} must be YYYY-MM-DD"})
        try:
            limit = max(1, min(200, int(_one("limit") or "50")))
            offset = max(0, int(_one("offset") or "0"))
        except (TypeError, ValueError):
            return self._send_json(
                400, {"ok": False, "error": "limit/offset must be integers"})

        idx = _get_index()
        try:
            res = idx.search_yoinks_for_memory(
                q=_one("q"), channel=_one("channel"), topic=_one("topic"),
                hook_type=hook_type, date_from=date_from, date_to=date_to,
                limit=limit, offset=offset,
            )
        except Exception as e:
            log.warning("memory search: index error: %s", e)
            return self._send_json(500, {"ok": False, "error": "search failed"})
        results = [er for er in (_enrich_yoink_row(idx, r)
                                 for r in res["results"]) if er]
        self._send_json(200, {
            "ok": True,
            "total": res["total"],
            "limit": limit,
            "offset": offset,
            "results": results,
        })

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
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                **SUBPROCESS_KW,
            )
        except Exception as e:
            return self._send_json(200, {"ok": False, "error": str(e)})
        log.info("GET /open-prompts -> %s", prompts_path)
        self._send_json(200, {"ok": True, "path": str(prompts_path)})

    # ---- /skill/system-prompt ----
    # setup.html uses this to offer a copyable fallback prompt for clients
    # that do not load SKILL.md natively. Token-gated because it reveals the
    # local install layout and should follow the rest of setup's private API.
    def _handle_skill_system_prompt(self):
        prompt_path = HERE / "skills" / "yoink" / "system-prompt.md"
        try:
            body = prompt_path.read_text(encoding="utf-8").encode("utf-8")
        except OSError:
            return self._send_json(404, {
                "ok": False,
                "error": "skill system prompt not found",
            })
        self.send_response(200)
        self.send_header("Content-Type", "text/markdown; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "private, max-age=300")
        self._send_cors(self._cors_origin())
        self.end_headers()
        self.wfile.write(body)

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
        if bare == "/settings":
            return self._handle_settings_post(body)
        if bare == "/settings/test-key":
            return self._handle_settings_test_key(body)
        if bare.startswith("/mcp/v1"):
            return self._handle_mcp_post(bare, body)
        if bare == "/playlist/preview":
            return self._handle_playlist_preview(body)
        if bare == "/playlist/start":
            return self._handle_playlist_start(body)
        if bare.startswith("/jobs/") and bare.endswith("/cancel"):
            return self._handle_job_cancel(bare)
        if bare == "/extract":
            return self._handle_extract(body)
        if bare == "/index/backfill-cancel":
            _backfill_cancel.set()
            return self._send_json(200, {"ok": True, "cancelled": True})
        if bare == "/taxonomy/correct":
            return self._handle_taxonomy_correct(body)
        if bare == "/memory/delete":
            return self._handle_memory_delete(body)
        if bare == "/memory/restore":
            return self._handle_memory_restore(body)
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

    def _validate_playlist_body(self, body: dict, *, require_interval: bool = False):
        raw = body.get("url")
        if not isinstance(raw, str):
            return None, None, "playlist URL invalid", 400
        url = _normalize_playlist_url(raw.strip())
        if not url:
            return None, None, "playlist URL invalid", 400
        interval = body.get("interval", 30)
        if require_interval or "interval" in body:
            try:
                interval = int(interval)
            except (TypeError, ValueError):
                return None, None, "interval must be an integer", 400
            if not (5 <= interval <= 300):
                return None, None, "interval must be between 5 and 300", 400
        return url, interval, None, 200

    def _job_id_from_path(self, bare: str, *, cancel: bool = False):
        prefix = "/jobs/"
        suffix = "/cancel" if cancel else ""
        if not bare.startswith(prefix) or (suffix and not bare.endswith(suffix)):
            return None, "job id invalid", 400
        job_id = bare[len(prefix):]
        if suffix:
            job_id = job_id[:-len(suffix)]
        job_id = job_id.strip("/")
        if not _is_valid_job_id(job_id):
            return None, "job id invalid", 400
        return job_id, None, 200

    # ---- /playlist/preview ----
    def _handle_playlist_preview(self, body: dict):
        url, _interval, err, status = self._validate_playlist_body(body)
        if err:
            return self._send_json(status, {"ok": False, "error": err})
        playlist, err, status = _fetch_playlist_preview(url)
        if err:
            return self._send_json(status, {"ok": False, "error": err})
        self._send_json(200, {"ok": True, "playlist": playlist})

    # ---- /playlist/start ----
    def _handle_playlist_start(self, body: dict):
        url, interval, err, status = self._validate_playlist_body(body, require_interval=True)
        if err:
            return self._send_json(status, {"ok": False, "error": err})
        playlist, err, status = _fetch_playlist_preview(url)
        if err:
            return self._send_json(status, {"ok": False, "error": err})

        job_id, public = _create_playlist_job(playlist, interval)
        self._send_json(200, {"ok": True, "job_id": job_id, "job": public})

    # ---- /jobs/<id> ----
    def _handle_job_get(self, bare: str):
        job_id, err, status = self._job_id_from_path(bare)
        if err:
            return self._send_json(status, {"ok": False, "error": err})
        job = _get_public_job(job_id)
        if not job:
            return self._send_json(404, {"ok": False, "error": "job not found"})
        self._send_json(200, {"ok": True, "job": job})

    # ---- /jobs/<id>/cancel ----
    def _handle_job_cancel(self, bare: str):
        job_id, err, status = self._job_id_from_path(bare, cancel=True)
        if err:
            return self._send_json(status, {"ok": False, "error": err})
        public, error, status = _cancel_playlist_job(job_id)
        if error:
            return self._send_json(status, {"ok": False, "error": error})
        self._send_json(200, {"ok": True, "job": public})

    # ---- /jobs ----
    def _handle_jobs_list(self):
        qs = parse_qs(urlparse(self.path).query)
        kind = (qs.get("kind") or [None])[0]
        if kind not in (None, "", "playlist", "single"):
            return self._send_json(400, {
                "ok": False,
                "error": "kind must be playlist or single",
            })
        self._send_json(200, {
            "ok": True,
            "jobs": _list_public_jobs(kind or None),
        })

    # ---- /taxonomy ----
    def _handle_taxonomy(self):
        qs = parse_qs(urlparse(self.path).query)
        channel = (qs.get("channel") or [None])[0]
        hook_type = (qs.get("hook_type") or [None])[0]
        if hook_type:
            hook_type = hook_type.strip().lower()
            if hook_type not in HOOK_TYPES:
                return self._send_json(400, {
                    "ok": False,
                    "error": "hook_type invalid",
                })
        limit_raw = (qs.get("limit") or ["50"])[0]
        try:
            limit = int(limit_raw)
        except (TypeError, ValueError):
            return self._send_json(400, {
                "ok": False,
                "error": "limit invalid",
            })
        limit = max(1, min(500, limit))
        self._send_json(200, {
            "ok": True,
            "taxonomy": _query_taxonomy(
                channel=channel,
                hook_type=hook_type,
                limit=limit,
            ),
        })

    # ---- /taxonomy/corrections ----
    def _handle_taxonomy_corrections(self):
        """List recent Hook Type corrections (Sprint 17 / A3 follow-up).
        Feeds the setup.html "Hook Type calibration" review surface.
        Read-only sibling of POST /taxonomy/correct."""
        qs = parse_qs(urlparse(self.path).query)
        channel = (qs.get("channel") or [None])[0]
        topic = (qs.get("topic") or [None])[0]
        limit_raw = (qs.get("limit") or ["50"])[0]
        try:
            limit = int(limit_raw)
        except (TypeError, ValueError):
            return self._send_json(400, {
                "ok": False,
                "error": "limit invalid",
            })
        limit = max(1, min(200, limit))
        idx = _get_index()
        try:
            corrections = idx.list_corrections(
                limit=limit,
                channel=channel,
                topic=topic,
            )
        except Exception as e:
            log.warning("taxonomy corrections: index read failed: %s", e)
            return self._send_json(
                500, {"ok": False, "error": "index unavailable"})
        self._send_json(200, {
            "ok": True,
            "corrections": corrections,
        })

    # ---- /taxonomy/correct ----
    def _handle_taxonomy_correct(self, body: dict):
        """Record a user's Hook Type correction (Sprint 17 / A3). The
        corrected value becomes the canonical classification and feeds back
        into future classifications as a few-shot anchor."""
        if not _check_taxonomy_correct_rate_limit():
            return self._send_json(429, {"ok": False, "error": "too many requests"})
        video_id = (body.get("video_id") or "").strip()
        corrected = (body.get("corrected_hook_type") or "").strip().lower()
        user_reason = body.get("user_reason")
        if not video_id:
            return self._send_json(400, {"ok": False, "error": "video_id required"})
        if corrected not in HOOK_TYPES:
            return self._send_json(
                400, {"ok": False, "error": "corrected_hook_type invalid"})
        if user_reason is not None and not isinstance(user_reason, str):
            return self._send_json(
                400, {"ok": False, "error": "user_reason must be a string"})
        user_reason = (user_reason or "").strip() or None

        idx = _get_index()
        try:
            yoink = idx.get_yoink(video_id)
        except Exception as e:
            log.warning("taxonomy correct: index read failed: %s", e)
            return self._send_json(500, {"ok": False, "error": "index unavailable"})
        if not yoink:
            return self._send_json(404, {"ok": False, "error": "video not found"})

        # The original (pre-correction) hook type is read from the sidecar,
        # which the Hook Type worker keeps current; it is also the file this
        # endpoint updates.
        sidecar_path = Path(yoink.get("sidecar_path") or "")
        original = None
        try:
            sc = json.loads(sidecar_path.read_text(encoding="utf-8"))
            original = (sc.get("hook_type") or "").strip() or None
        except (OSError, json.JSONDecodeError):
            original = None
        if not original:
            return self._send_json(409, {
                "ok": False,
                "error": "video has no hook classification to correct",
            })

        try:
            correction_id = idx.upsert_taxonomy_correction(
                video_id, original, corrected,
                user_reason=user_reason,
                channel=yoink.get("channel"),
                topic=yoink.get("topic"),
            )
        except Exception as e:
            log.warning("taxonomy correct: write failed: %s", e)
            return self._send_json(
                500, {"ok": False, "error": "could not store correction"})

        # Sidecar update is best-effort -- the index row is authoritative.
        _record_correction_in_sidecar(sidecar_path, original, corrected)
        log.info("taxonomy correction: %s %s -> %s (#%s)",
                 video_id, original, corrected, correction_id)
        self._send_json(200, {"ok": True, "correction_id": correction_id})

    # ---- /memory/delete ----
    def _handle_memory_delete(self, body: dict):
        """Soft-delete a yoink: move its folder into _yoink-trash/ and set
        the index row's deleted_at. Reversible via /memory/restore until the
        30-day purge runs."""
        video_id = (body.get("video_id") or "").strip()
        if not video_id:
            return self._send_json(400, {"ok": False, "error": "video_id required"})
        idx = _get_index()
        row = idx.get_yoink(video_id)
        if not row:
            return self._send_json(404, {"ok": False, "error": "yoink not found"})
        if row.get("deleted_at"):
            return self._send_json(409, {"ok": False, "error": "already deleted"})

        src = Path(row.get("corpus_path") or "").parent
        if not src.exists() or not src.is_dir():
            return self._send_json(
                409, {"ok": False, "error": "yoink folder missing on disk"})

        # Mark deleted first so the trash folder name derives from the same
        # deleted_at the index stores; roll the row back if the move fails.
        updated = idx.soft_delete_yoink(video_id)
        dst = _trash_folder_for(updated)
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))
        except (OSError, shutil.Error) as e:
            idx.restore_yoink(video_id)
            log.warning("memory delete: move to trash failed: %s", e)
            return self._send_json(
                500, {"ok": False, "error": "could not move folder to trash"})
        log.info("memory delete: %s -> %s", video_id, dst)
        self._send_json(200, {
            "ok": True,
            "restored_at": None,
            "deleted_at": updated.get("deleted_at"),
        })

    # ---- /memory/restore ----
    def _handle_memory_restore(self, body: dict):
        """Restore a soft-deleted yoink: move its folder back from
        _yoink-trash/ and clear the index row's deleted_at."""
        video_id = (body.get("video_id") or "").strip()
        if not video_id:
            return self._send_json(400, {"ok": False, "error": "video_id required"})
        idx = _get_index()
        row = idx.get_yoink(video_id)
        if not row:
            return self._send_json(404, {"ok": False, "error": "yoink not found"})
        if not row.get("deleted_at"):
            return self._send_json(409, {"ok": False, "error": "yoink is not deleted"})

        trash = _trash_folder_for(row)
        dst = Path(row.get("corpus_path") or "").parent
        if not trash.exists():
            return self._send_json(
                409, {"ok": False, "error": "trash folder not found"})
        if dst.exists():
            return self._send_json(
                409, {"ok": False, "error": "original location is occupied"})
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(trash), str(dst))
        except (OSError, shutil.Error) as e:
            log.warning("memory restore: move from trash failed: %s", e)
            return self._send_json(
                500, {"ok": False, "error": "could not restore folder"})
        idx.restore_yoink(video_id)
        log.info("memory restore: %s <- %s", video_id, trash)
        self._send_json(200, {"ok": True, "restored_at": _now_iso()})

    def _handle_extract(self, body: dict):
        url, interval, err = self._validate_url_interval(body)
        if err:
            log.info("POST /extract -> 400 (%s)", err)
            return self._send_json(400, {"ok": False, "error": err})

        log.info("POST /extract url=%s interval=%d -> running", url, interval)
        DESKTOP_ROOT.mkdir(parents=True, exist_ok=True)
        started_at = _now_iso()
        title = None
        folder = None
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
                _record_single_extract_job(
                    url,
                    started_at,
                    error=msg,
                    title=title,
                    folder=folder,
                )
                return self._send_json(200, {"ok": False, "error": msg})

        _record_single_extract_job(url, started_at, result=result)
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

                # Session adds don't go to the clipboard one-by-one (the
                # whole session is concatenated and copied at /session/close),
                # so skip the per-video paste-corpus generation -- it would
                # just inflate the runtime message payload for nothing.
                result = _run_extraction(url, interval, target,
                                          open_explorer=False,
                                          metadata=metadata, topic=topic,
                                          generate_paste=False)
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
            _atomic_write_text(corpus_path, corpus_md)

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


class _YoinkHTTPServer(ThreadingHTTPServer):
    """ThreadingHTTPServer with a bounded listen() backlog so a burst of
    connections is refused at the OS layer instead of piling up unbounded
    accept()s. Worker threads stay daemonic (inherited from ThreadingHTTPServer)
    so Ctrl+C still exits promptly."""
    request_queue_size = 16


def main():
    # Output directories are created lazily by the write paths themselves
    # (_run_extraction, _atomic_write_text, and the jobs/taxonomy/settings
    # writers all mkdir(parents=True, exist_ok=True) their own parents).
    # Creating them here would touch a possibly-locked OneDrive Desktop
    # before the server can even bind and answer /health.

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
        server = _YoinkHTTPServer((HOST, PORT), Handler)
    except OSError as e:
        # Port held by something we couldn't probe via /health (different
        # app, half-open socket, etc). Exit 0 so the Windows autostart
        # mechanism doesn't surface an error dialog to the user.
        log.error("Failed to bind %s:%d -- %s", HOST, PORT, e)
        sys.exit(0)

    _migrate_plaintext_anthropic_key()
    # Sprint 15: open the library index (quarantining + rebuilding a corrupt
    # index.db if needed) before anything reads from or migrates into it.
    _get_index()
    # One-time: fold any pre-index jobs.json / taxonomy.json into index.db.
    _migrate_jobs_json_to_index()
    _migrate_taxonomy_json_to_index()
    # Hydrate the in-memory job dict from the index.
    _restore_jobs_from_disk()
    # Backfill the index from disk in the background so a missing index
    # never delays the bind or /health.
    _start_backfill_thread()
    # Sprint 18: hard-delete _yoink-trash/ entries past the 30-day window,
    # once at startup and every 24h after.
    _start_trash_purge_thread()

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
