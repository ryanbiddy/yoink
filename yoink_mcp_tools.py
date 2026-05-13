"""Shared Yoink MCP tool registry.

Both transports use this module:

- yoink_mcp.py wraps it with the official MCP Python SDK over stdio.
- server.py wraps it with authenticated JSON-RPC HTTP endpoints.

The registry intentionally owns no extraction business logic. It binds to the
loaded server module and calls the same helpers used by Yoink's v1/v2 HTTP API.
"""

from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


_backend = None


def bind_backend(backend_module) -> None:
    global _backend
    _backend = backend_module


def _b():
    if _backend is None:
        raise RuntimeError("Yoink MCP backend is not bound")
    return _backend


class RateLimitExceeded(Exception):
    pass


class _RateLimiter:
    def __init__(self, max_calls: int, window_sec: float = 60.0):
        self.max_calls = max_calls
        self.window_sec = window_sec
        self._lock = threading.Lock()
        self._calls: list[float] = []

    def check(self) -> None:
        now = time.monotonic()
        cutoff = now - self.window_sec
        with self._lock:
            kept = [t for t in self._calls if t > cutoff]
            if len(kept) >= self.max_calls:
                self._calls[:] = kept
                raise RateLimitExceeded(
                    f"rate limit exceeded: max {self.max_calls}/minute"
                )
            kept.append(now)
            self._calls[:] = kept


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[[dict[str, Any]], dict[str, Any]]
    rate_limiter: _RateLimiter | None = None


def _ok(**fields) -> dict[str, Any]:
    return {"ok": True, **fields}


def _err(message: str) -> dict[str, Any]:
    return {"ok": False, "error": message}


def _limit_int(value: Any, *, default: int, low: int, high: int) -> int:
    try:
        out = int(value)
    except (TypeError, ValueError):
        out = default
    return max(low, min(high, out))


def _read_json(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _sidecar_path(folder: Path) -> Path:
    return folder / f"{folder.name}.json"


def _metadata_path(folder: Path) -> Path:
    return folder / "metadata.json"


def _read_sidecar(folder: Path) -> dict[str, Any]:
    return _read_json(_sidecar_path(folder))


def _read_metadata(folder: Path) -> dict[str, Any]:
    return _read_json(_metadata_path(folder))


def _iter_yoink_folders():
    b = _b()
    root = b.DESKTOP_ROOT
    if not root.exists():
        return
    for folder in root.rglob("*"):
        if not folder.is_dir():
            continue
        corpus = b._resolve_corpus_path(folder)
        if corpus is not None:
            yield folder, corpus


def _yoink_summary(folder: Path, corpus: Path) -> dict[str, Any]:
    sidecar = _read_sidecar(folder)
    metadata = _read_metadata(folder)
    title = (
        sidecar.get("title")
        or metadata.get("title")
        or folder.name.replace("-", " ").title()
    )
    yoinked_at = sidecar.get("yoinked_at")
    if not yoinked_at:
        yoinked_at = time.strftime(
            "%Y-%m-%dT%H:%M:%S", time.localtime(corpus.stat().st_mtime)
        )
    return {
        "slug": folder.name,
        "title": title,
        "folder": str(folder),
        "yoinked_at": yoinked_at,
        "_mtime": corpus.stat().st_mtime,
        "_corpus": corpus,
    }


def _find_yoink(slug: str) -> tuple[Path, Path] | tuple[None, None]:
    if not isinstance(slug, str) or not re.match(r"^[A-Za-z0-9_-]{1,160}$", slug):
        return None, None
    matches = []
    for folder, corpus in _iter_yoink_folders() or []:
        if folder.name == slug:
            matches.append((corpus.stat().st_mtime, folder, corpus))
    if not matches:
        return None, None
    matches.sort(key=lambda item: item[0], reverse=True)
    return matches[0][1], matches[0][2]


def _saved_key() -> str | None:
    return _b()._saved_anthropic_key()


def _comments_for_folder(folder: Path) -> list[dict[str, Any]]:
    comments = _read_sidecar(folder).get("comments")
    return comments if isinstance(comments, list) else []


def _hook_context_for_folder(folder: Path) -> dict[str, Any]:
    sidecar = _read_sidecar(folder)
    metadata = _read_metadata(folder)
    transcript = " ".join(
        str(item.get("text") or "")
        for item in (sidecar.get("transcript") or [])
        if isinstance(item, dict)
    )
    comments = _comments_for_folder(folder)
    top_comment = ""
    if comments and isinstance(comments[0], dict):
        top_comment = str(comments[0].get("text") or "")
    return {
        "video_id": sidecar.get("video_id") or metadata.get("id") or "",
        "title": sidecar.get("title") or metadata.get("title") or "",
        "description": metadata.get("description") or "",
        "channel": sidecar.get("channel") or metadata.get("channel") or metadata.get("uploader") or "",
        "transcript": transcript,
        "top_comment": top_comment,
    }


def yoink_video(args: dict[str, Any]) -> dict[str, Any]:
    b = _b()
    raw_url = args.get("url")
    if not isinstance(raw_url, str):
        return _err("url required")
    url = b._normalize_youtube_url(raw_url.strip())
    if not url:
        return _err("URL must be a youtube.com or youtu.be video link")
    interval = _limit_int(args.get("interval"), default=30, low=5, high=300)

    b.DESKTOP_ROOT.mkdir(parents=True, exist_ok=True)
    started_at = b._now_iso()
    title = None
    folder = None
    with b._extract_lock:
        try:
            metadata = b._fetch_metadata(url)
            title = metadata.get("title") or "Untitled"
            topic = b._classify_topic(metadata)
            folder = (
                b.DESKTOP_ROOT
                / b._topic_folder_name(topic)
                / (b.slugify(title) or "video")
            )
            result = b._run_extraction(url, interval, folder, metadata=metadata, topic=topic)
        except BaseException as e:
            msg = b.friendly_error(e)
            b._record_single_extract_job(
                url,
                started_at,
                error=msg,
                title=title,
                folder=folder,
            )
            return _err(msg)

    folder_path = Path(result["folder"])
    b._record_single_extract_job(url, started_at, result=result)
    screenshots = [
        str(p) for p in sorted((folder_path / "screenshots").glob("shot_*.jpg"))
    ]
    return _ok(
        slug=folder_path.name,
        folder=str(folder_path),
        corpus_md=result.get("yoink_md") or "",
        screenshots=screenshots,
    )


def yoink_playlist(args: dict[str, Any]) -> dict[str, Any]:
    b = _b()
    raw_url = args.get("url")
    if not isinstance(raw_url, str):
        return _err("playlist URL invalid")
    url = b._normalize_playlist_url(raw_url.strip())
    if not url:
        return _err("playlist URL invalid")
    interval = _limit_int(args.get("interval"), default=30, low=5, high=300)
    playlist, error, _status = b._fetch_playlist_preview(url)
    if error:
        return _err(error)
    job_id, _job = b._create_playlist_job(playlist, interval)
    return _ok(job_id=job_id)


def get_job_status(args: dict[str, Any]) -> dict[str, Any]:
    b = _b()
    job_id = args.get("job_id")
    if not isinstance(job_id, str) or not b._is_valid_job_id(job_id):
        return _err("job id invalid")
    job = b._get_public_job(job_id)
    if not job:
        return _err("job not found")
    return _ok(job=job)


def cancel_job(args: dict[str, Any]) -> dict[str, Any]:
    b = _b()
    job_id = args.get("job_id")
    if not isinstance(job_id, str) or not b._is_valid_job_id(job_id):
        return _err("job id invalid")
    job, error, _status = b._cancel_playlist_job(job_id)
    if error:
        return _err(error)
    return _ok(job=job)


def list_recent_yoinks(args: dict[str, Any]) -> dict[str, Any]:
    limit = _limit_int(args.get("limit"), default=20, low=1, high=100)
    rows = []
    for folder, corpus in _iter_yoink_folders() or []:
        rows.append(_yoink_summary(folder, corpus))
    rows.sort(key=lambda row: row["_mtime"], reverse=True)
    public = [
        {k: v for k, v in row.items() if not k.startswith("_")}
        for row in rows[:limit]
    ]
    return _ok(yoinks=public)


def _snippet(text: str, needle: str, span: int = 160) -> str:
    lowered = text.lower()
    i = lowered.find(needle.lower())
    if i < 0:
        return text[:span].replace("\n", " ").strip()
    start = max(0, i - span // 2)
    end = min(len(text), i + len(needle) + span // 2)
    return text[start:end].replace("\n", " ").strip()


def search_yoinks(args: dict[str, Any]) -> dict[str, Any]:
    query = args.get("query")
    if not isinstance(query, str) or not query.strip():
        return _err("query required")
    limit = _limit_int(args.get("limit"), default=10, low=1, high=50)
    terms = [t.lower() for t in re.findall(r"[A-Za-z0-9_'-]+", query) if t]
    if not terms:
        return _err("query required")
    hits = []
    for folder, corpus in _iter_yoink_folders() or []:
        try:
            text = corpus.read_text(encoding="utf-8")
        except OSError:
            continue
        summary = _yoink_summary(folder, corpus)
        haystack = (summary["title"] + "\n" + text).lower()
        score = sum(haystack.count(term) for term in terms)
        if score <= 0:
            continue
        first = next((t for t in terms if t in haystack), terms[0])
        hits.append({
            "slug": summary["slug"],
            "title": summary["title"],
            "snippet": _snippet(text, first),
            "score": score,
        })
    hits.sort(key=lambda row: row["score"], reverse=True)
    return _ok(results=hits[:limit])


def get_yoink_corpus(args: dict[str, Any]) -> dict[str, Any]:
    slug = args.get("slug")
    folder, corpus = _find_yoink(slug)
    if not folder or not corpus:
        return _err("yoink not found")
    try:
        md = corpus.read_text(encoding="utf-8")
    except OSError as e:
        return _err(f"corpus read failed: {e}")
    sidecar = _read_sidecar(folder)
    video_id = sidecar.get("video_id")
    if not isinstance(video_id, str) or not video_id.strip():
        video_id = None
    video_url = f"https://www.youtube.com/watch?v={video_id}" if video_id else None
    return _ok(
        corpus_md=md,
        folder=str(folder),
        video_id=video_id,
        video_url=video_url,
    )


def analyze_comments_tool(args: dict[str, Any]) -> dict[str, Any]:
    b = _b()
    key = _saved_key()
    if not key:
        return _err("anthropic key not configured")
    slug = args.get("slug")
    folder, corpus = _find_yoink(slug)
    if not folder or not corpus:
        return _err("yoink not found")
    comments = _comments_for_folder(folder)
    if len(comments) < 5:
        return _err("not enough comments to analyze")
    try:
        analysis = b.analyze_comments(comments, api_key=key)
        b._replace_comment_intelligence_section(
            corpus, b._render_comment_intelligence(analysis)
        )
        b._update_sidecar_comment_intelligence(
            folder, status="fetched", analysis=analysis
        )
        return _ok(
            top_themes=analysis.get("top_themes") or [],
            mentioned_products=analysis.get("mentioned_products_tools") or [],
            notable_disagreements=analysis.get("notable_disagreements") or [],
        )
    except b.AnthropicAPIError as e:
        return _err(b._short_reason(e.reason))


def classify_hook(args: dict[str, Any]) -> dict[str, Any]:
    b = _b()
    key = _saved_key()
    if not key:
        return _err("anthropic key not configured")
    slug = args.get("slug")
    folder, corpus = _find_yoink(slug)
    if not folder or not corpus:
        return _err("yoink not found")
    try:
        context = _hook_context_for_folder(folder)
        analysis = b.analyze_hook_type(context, api_key=key)
        b._replace_hook_analysis_section(corpus, b._render_hook_analysis(analysis))
        b._update_sidecar_hook_type(
            folder,
            status="completed",
            hook_type=analysis.get("hook_type"),
            hook_explanation=analysis.get("hook_explanation"),
        )
        b._append_hook_taxonomy(context, analysis)
        return _ok(
            hook_type=analysis.get("hook_type"),
            hook_explanation=analysis.get("hook_explanation"),
        )
    except b.AnthropicAPIError as e:
        return _err(b._short_reason(e.reason))


def get_taxonomy(args: dict[str, Any]) -> dict[str, Any]:
    b = _b()
    channel = args.get("channel")
    hook_type = args.get("hook_type")
    if channel is not None and not isinstance(channel, str):
        return _err("channel must be a string")
    if hook_type is not None:
        if not isinstance(hook_type, str):
            return _err("hook_type must be a string")
        hook_type = hook_type.strip().lower()
        if hook_type and hook_type not in b.HOOK_TYPES:
            return _err("hook_type invalid")
    limit = _limit_int(args.get("limit"), default=50, low=1, high=500)
    return _ok(
        taxonomy=b._query_taxonomy(
            channel=channel,
            hook_type=hook_type,
            limit=limit,
        )
    )


def _schema(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required or [],
        "additionalProperties": False,
    }


TOOL_REGISTRY: dict[str, ToolSpec] = {
    "yoink_video": ToolSpec(
        name="yoink_video",
        description=(
            "Extract a single YouTube video into a Yoink corpus. Returns the "
            "saved folder, markdown corpus, and screenshot paths."
        ),
        input_schema=_schema({
            "url": {"type": "string", "description": "YouTube video URL."},
            "interval": {
                "type": "integer",
                "description": "Screenshot interval in seconds (5-300). Optional.",
                "minimum": 5,
                "maximum": 300,
                "default": 30,
            },
        }, ["url"]),
        handler=yoink_video,
        rate_limiter=_RateLimiter(5),
    ),
    "yoink_playlist": ToolSpec(
        name="yoink_playlist",
        description="Start asynchronous extraction for a YouTube playlist.",
        input_schema=_schema({
            "url": {"type": "string", "description": "YouTube playlist URL."},
            "interval": {
                "type": "integer",
                "description": "Screenshot interval in seconds (5-300). Optional.",
                "minimum": 5,
                "maximum": 300,
                "default": 30,
            },
        }, ["url"]),
        handler=yoink_playlist,
        rate_limiter=_RateLimiter(5),
    ),
    "get_job_status": ToolSpec(
        name="get_job_status",
        description="Return the full status object for an async Yoink job.",
        input_schema=_schema({
            "job_id": {"type": "string", "description": "Job ID from yoink_playlist."},
        }, ["job_id"]),
        handler=get_job_status,
    ),
    "cancel_job": ToolSpec(
        name="cancel_job",
        description="Cancel an async Yoink job and leave partial outputs on disk.",
        input_schema=_schema({
            "job_id": {"type": "string", "description": "Job ID to cancel."},
        }, ["job_id"]),
        handler=cancel_job,
    ),
    "list_recent_yoinks": ToolSpec(
        name="list_recent_yoinks",
        description="List recent saved Yoink corpora.",
        input_schema=_schema({
            "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
        }),
        handler=list_recent_yoinks,
    ),
    "search_yoinks": ToolSpec(
        name="search_yoinks",
        description="Keyword search across saved Yoink markdown corpora.",
        input_schema=_schema({
            "query": {"type": "string"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
        }, ["query"]),
        handler=search_yoinks,
    ),
    "get_yoink_corpus": ToolSpec(
        name="get_yoink_corpus",
        description="Return the full markdown corpus for a saved yoink by slug.",
        input_schema=_schema({
            "slug": {"type": "string", "description": "Folder slug of the saved yoink."},
        }, ["slug"]),
        handler=get_yoink_corpus,
    ),
    "analyze_comments": ToolSpec(
        name="analyze_comments",
        description=(
            "Run Comment Intelligence on an existing yoink and return themes, "
            "mentioned products/tools, and disagreements."
        ),
        input_schema=_schema({
            "slug": {"type": "string", "description": "Folder slug of the saved yoink."},
        }, ["slug"]),
        handler=analyze_comments_tool,
        rate_limiter=_RateLimiter(10),
    ),
    "classify_hook": ToolSpec(
        name="classify_hook",
        description="Classify the hook type for an existing yoink.",
        input_schema=_schema({
            "slug": {"type": "string", "description": "Folder slug of the saved yoink."},
        }, ["slug"]),
        handler=classify_hook,
        rate_limiter=_RateLimiter(10),
    ),
    "get_taxonomy": ToolSpec(
        name="get_taxonomy",
        description=(
            "Return captured Hook Type taxonomy rows, optionally filtered by "
            "channel and hook_type."
        ),
        input_schema=_schema({
            "channel": {
                "type": "string",
                "description": "Exact channel name to filter by. Optional.",
            },
            "hook_type": {
                "type": "string",
                "description": "Hook type to filter by. Optional.",
                "enum": [
                    "curiosity_gap",
                    "question",
                    "contrarian",
                    "story_open",
                    "promise_list",
                    "demo",
                    "authority",
                    "stakes",
                    "other",
                ],
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 500,
                "default": 50,
            },
        }),
        handler=get_taxonomy,
    ),
}


def list_tools() -> list[dict[str, Any]]:
    return [
        {
            "name": spec.name,
            "description": spec.description,
            "inputSchema": spec.input_schema,
        }
        for spec in TOOL_REGISTRY.values()
    ]


def call_tool(name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    spec = TOOL_REGISTRY.get(name)
    if not spec:
        return _err("tool not found")
    args = arguments or {}
    if not isinstance(args, dict):
        return _err("arguments must be an object")
    try:
        if spec.rate_limiter:
            spec.rate_limiter.check()
        return spec.handler(args)
    except RateLimitExceeded as e:
        return _err(str(e))
    except Exception as e:
        return _err(f"tool failed: {e}")
