# Yoink v2 API contract

Status: implemented through v2.1 Sprint 11
Scope: Playlist Mode, settings, file serving, and MCP HTTP backend contract
Non-goal: UI design, Channel Decoder, Niche Corpus, or Mac installer work

## Overview

Yoink v2 adds an async job model for playlist extraction while preserving the v1 single-video flow exactly as-is. `/extract` and `/session/add` remain synchronous and keep their current request and response shapes for backward compatibility; as of v2.1, `/extract` also writes a side-effect `kind: "single"` job record for recent-activity UI. Playlist Mode uses new `/playlist/*` and `/jobs/*` endpoints: the client previews a playlist, starts a job, polls progress, and can cancel mid-flight. This job model is the foundation for later Channel Decoder and Niche Corpus work, but those endpoints are intentionally out of scope for this contract.

## Auth and protocol baseline

All new endpoints use the same local-server auth model as v1:

- `/health` and `/ping` stay public and unauthenticated.
- `/token` stays the token-issuance endpoint and requires `X-Yoink-Client: yoink-extension`.
- All endpoints in this document require `X-Yoink-Token: <token>`.
- POST endpoints require `Content-Type: application/json`.
- POST request bodies must be top-level JSON objects and remain under the existing 64 KB body limit.

Protocol/validation failures use non-200 HTTP status codes:

```json
{ "ok": false, "error": "missing or invalid token" }
```

Handled application failures use the v1 pattern: HTTP 200 with `{"ok": false, "error": "..."}`.

## URL formats supported

Single-video entry points (`/extract`, MCP `yoink_video`, and v1 session adds) accept these YouTube video URL shapes and canonicalize them to `https://www.youtube.com/watch?v=<id>` before extraction:

- `https://www.youtube.com/watch?v=<id>`
- `https://youtu.be/<id>`
- `https://www.youtube.com/shorts/<id>`
- `https://www.youtube.com/embed/<id>`

Playlist entry points accept `youtube.com/playlist?list=<id>` and `youtube.com/watch?v=<id>&list=<id>`, but intentionally drop the selected video position and process the playlist from the first item after applying the 10-video cap.

## Single-video multimodal clipboard cap

Single-video `/extract` still writes the complete screenshot set to disk. The clipboard paste intentionally embeds a smaller, evenly distributed subset so long videos fit real Claude/ChatGPT context windows. Default: `clipboard_screenshot_cap: 4`; valid range: `0-12`. When a video has more screenshots than the cap, the clipboard corpus includes a near-top note:

```text
[Showing 4 of 18 screenshots in clipboard; full set on disk]
```

The setting is returned by `GET /settings` and accepted by `POST /settings`. Setting it to `0` produces a text-only clipboard corpus while keeping all screenshots on disk.

## Endpoint reference

### GET /settings/pricing

Return the local cost-estimator constants used by setup.html for optional BYO Anthropic features. This endpoint does not call Anthropic and does not inspect the saved API key.

Auth: `X-Yoink-Token` required.

Request body: none.

Success response: HTTP 200

```json
{
  "ok": true,
  "pricing": {
    "model": "claude-haiku-4-5-20251001",
    "display_model": "Claude Haiku 4.5",
    "input_per_million": 1.0,
    "output_per_million": 5.0,
    "est_tokens": {
      "ci": { "input": 5000, "output": 500 },
      "hook": { "input": 1200, "output": 80 }
    },
    "est_per_video": {
      "ci": 0.0075,
      "hook": 0.0016,
      "both": 0.0091
    },
    "source": "https://docs.claude.com/en/docs/about-claude/pricing",
    "source_checked": "2026-05-12"
  }
}
```

Notes:

- Estimates are deliberately conservative approximations for trust/UX, not billing guarantees.
- Comment Intelligence estimate assumes 5,000 input tokens and 500 output tokens.
- Hook Type estimate assumes 1,200 input tokens and 80 output tokens.

### POST /playlist/preview

Preview a playlist without extracting anything. Used by the popup to show the user what Yoink will process before starting.

Auth: `X-Yoink-Token` required.

Request body:

| Field | Type | Required | Notes |
|---|---:|---:|---|
| `url` | string | yes | YouTube playlist URL. Must canonicalize to a playlist accepted by yt-dlp. |

Success response: HTTP 200

```json
{
  "ok": true,
  "playlist": {
    "url": "https://www.youtube.com/playlist?list=PLexample123",
    "title": "Creator Strategy Interviews",
    "uploader": "Example Channel",
    "video_count": 23,
    "cap": 10,
    "will_process_count": 10,
    "truncated": true,
    "message": "Playlist has 23 videos -- yoinking the first 10.",
    "warnings": ["playlist exceeds cap"],
    "videos": [
      {
        "index": 1,
        "id": "dQw4w9WgXcQ",
        "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "title": "How creators build durable content systems",
        "channel": "Example Channel",
        "duration_seconds": 1842
      }
    ]
  }
}
```

Error responses:

- HTTP 400: body is missing `url`, `url` is not a string, or URL fails basic playlist validation.

```json
{ "ok": false, "error": "playlist URL invalid" }
```

- HTTP 200: yt-dlp could not preview the playlist.

```json
{ "ok": false, "error": "yt-dlp playlist preview failed" }
```

- HTTP 200: playlist preview succeeded but no videos were returned.

```json
{ "ok": false, "error": "playlist has no videos" }
```

Notes:

- Playlist Mode is hard-capped at 10 videos for the first v2 ship.
- A playlist with more than 10 videos is not an error. The response truncates `videos` to 10 and includes `warnings: ["playlist exceeds cap"]`.
- `channel` and `duration_seconds` are nullable. Preview uses fast `yt-dlp --flat-playlist` data and does not hydrate each video individually.
- Clients should show `message` when present.

Example request:

```http
POST /playlist/preview HTTP/1.1
Content-Type: application/json
X-Yoink-Token: <token>

{
  "url": "https://www.youtube.com/playlist?list=PLexample123"
}
```

Example response:

```json
{
  "ok": true,
  "playlist": {
    "url": "https://www.youtube.com/playlist?list=PLexample123",
    "title": "Creator Strategy Interviews",
    "uploader": "Example Channel",
    "video_count": 12,
    "cap": 10,
    "will_process_count": 10,
    "truncated": true,
    "message": "Playlist has 12 videos -- yoinking the first 10.",
    "warnings": ["playlist exceeds cap"],
    "videos": [
      {
        "index": 1,
        "id": "abc123DEF45",
        "url": "https://www.youtube.com/watch?v=abc123DEF45",
        "title": "A practical guide to creator research",
        "channel": "Example Channel",
        "duration_seconds": 1550
      }
    ]
  }
}
```

### POST /playlist/start

Start playlist extraction asynchronously. Returns immediately with a `job_id`; the client polls `/jobs/<id>`.

Auth: `X-Yoink-Token` required.

Request body:

| Field | Type | Required | Notes |
|---|---:|---:|---|
| `url` | string | yes | YouTube playlist URL. |
| `interval` | integer | no | Screenshot interval in seconds. Same bounds as v1: 5 to 300. Defaults to 30 if omitted. |

Success response: HTTP 200

```json
{
  "ok": true,
  "job_id": "job_20260510_143012_a1b2c3",
  "job": {
    "id": "job_20260510_143012_a1b2c3",
    "kind": "playlist",
    "state": "queued",
    "source_url": "https://www.youtube.com/playlist?list=PLexample123",
    "playlist_title": "Creator Strategy Interviews",
    "session_folder": "C:\\Users\\Ryan\\Desktop\\Yoink\\_sessions\\creator-strategy-interviews",
    "videos_total": 10,
    "videos_done": 0,
    "videos_failed": 0,
    "current_video": null,
    "current_video_phase": null,
    "started_at": null,
    "updated_at": "2026-05-10T14:30:12",
    "completed_at": null,
    "error": null,
    "result": null,
    "warnings": ["playlist exceeds cap"],
    "message": "Playlist has 12 videos -- yoinking the first 10."
  }
}
```

Error responses:

- HTTP 400: missing/invalid `url`, invalid JSON body, or invalid `interval`.

```json
{ "ok": false, "error": "playlist URL invalid" }
```

```json
{ "ok": false, "error": "interval must be between 5 and 300" }
```

- HTTP 200: yt-dlp could not preview the playlist before creating a job.

```json
{ "ok": false, "error": "yt-dlp playlist preview failed" }
```

- HTTP 200: playlist preview returned no videos.

```json
{ "ok": false, "error": "playlist has no videos" }
```

Example request:

```http
POST /playlist/start HTTP/1.1
Content-Type: application/json
X-Yoink-Token: <token>

{
  "url": "https://www.youtube.com/playlist?list=PLexample123",
  "interval": 30
}
```

Example response:

```json
{
  "ok": true,
  "job_id": "job_20260510_143012_a1b2c3",
  "job": {
    "id": "job_20260510_143012_a1b2c3",
    "kind": "playlist",
    "state": "queued",
    "source_url": "https://www.youtube.com/playlist?list=PLexample123",
    "playlist_title": "Creator Strategy Interviews",
    "session_folder": "C:\\Users\\Ryan\\Desktop\\Yoink\\_sessions\\creator-strategy-interviews",
    "videos_total": 10,
    "videos_done": 0,
    "videos_failed": 0,
    "current_video": null,
    "current_video_phase": null,
    "started_at": null,
    "updated_at": "2026-05-10T14:30:12",
    "completed_at": null,
    "error": null,
    "result": null,
    "warnings": ["playlist exceeds cap"],
    "message": "Playlist has 12 videos -- yoinking the first 10."
  }
}
```

### GET /jobs/<id>

Return the latest state for one async job.

Auth: `X-Yoink-Token` required.

Request body: none.

Success response: HTTP 200

```json
{
  "ok": true,
  "job": {
    "id": "job_20260510_143012_a1b2c3",
    "kind": "playlist",
    "state": "running",
    "source_url": "https://www.youtube.com/playlist?list=PLexample123",
    "playlist_title": "Creator Strategy Interviews",
    "session_folder": "C:\\Users\\Ryan\\Desktop\\Yoink\\_sessions\\creator-strategy-interviews",
    "videos_total": 10,
    "videos_done": 3,
    "videos_failed": 0,
    "current_video": {
      "index": 4,
      "title": "A practical guide to creator research",
      "url": "https://www.youtube.com/watch?v=abc123DEF45"
    },
    "current_video_phase": "screenshots",
    "started_at": "2026-05-10T14:30:13",
    "updated_at": "2026-05-10T14:38:41",
    "completed_at": null,
    "error": null,
    "result": null,
    "warnings": ["playlist exceeds cap"],
    "message": "Yoinking video 4 of 10."
  }
}
```

Error responses:

- HTTP 400: `id` has invalid characters or length.

```json
{ "ok": false, "error": "job id invalid" }
```

- HTTP 404: no job exists with that ID.

```json
{ "ok": false, "error": "job not found" }
```

Example request:

```http
GET /jobs/job_20260510_143012_a1b2c3 HTTP/1.1
X-Yoink-Token: <token>
```

Example response:

```json
{
  "ok": true,
  "job": {
    "id": "job_20260510_143012_a1b2c3",
    "kind": "playlist",
    "state": "completed",
    "source_url": "https://www.youtube.com/playlist?list=PLexample123",
    "playlist_title": "Creator Strategy Interviews",
    "session_folder": "C:\\Users\\Ryan\\Desktop\\Yoink\\_sessions\\creator-strategy-interviews",
    "videos_total": 10,
    "videos_done": 10,
    "videos_failed": 0,
    "current_video": null,
    "current_video_phase": null,
    "started_at": "2026-05-10T14:30:13",
    "updated_at": "2026-05-10T15:01:22",
    "completed_at": "2026-05-10T15:01:22",
    "error": null,
    "result": {
      "combined_md_path": "C:\\Users\\Ryan\\Desktop\\Yoink\\_sessions\\creator-strategy-interviews\\corpus.md",
      "combined_md_text": "# Playlist Corpus: Creator Strategy Interviews\n\n...",
      "per_video": [
        {
          "index": 1,
          "title": "A practical guide to creator research",
          "url": "https://www.youtube.com/watch?v=abc123DEF45",
          "folder": "C:\\Users\\Ryan\\Desktop\\Yoink\\_sessions\\creator-strategy-interviews\\video-1",
          "md_path": "C:\\Users\\Ryan\\Desktop\\Yoink\\_sessions\\creator-strategy-interviews\\video-1\\video-1.md",
          "json_path": "C:\\Users\\Ryan\\Desktop\\Yoink\\_sessions\\creator-strategy-interviews\\video-1\\video-1.json",
          "ok": true,
          "error": null
        }
      ]
    },
    "warnings": ["playlist exceeds cap"],
    "message": "Playlist complete."
  }
}
```

### POST /jobs/<id>/cancel

Cancel a queued or running async job.

Auth: `X-Yoink-Token` required.

Request body: `{}` or omitted JSON object.

Success response: HTTP 200

```json
{
  "ok": true,
  "job": {
    "id": "job_20260510_143012_a1b2c3",
    "kind": "playlist",
    "state": "cancelled",
    "source_url": "https://www.youtube.com/playlist?list=PLexample123",
    "playlist_title": "Creator Strategy Interviews",
    "session_folder": "C:\\Users\\Ryan\\Desktop\\Yoink\\_sessions\\creator-strategy-interviews",
    "videos_total": 10,
    "videos_done": 3,
    "videos_failed": 0,
    "current_video": null,
    "current_video_phase": null,
    "started_at": "2026-05-10T14:30:13",
    "updated_at": "2026-05-10T14:40:02",
    "completed_at": "2026-05-10T14:40:02",
    "error": null,
    "result": null,
    "warnings": ["playlist exceeds cap"],
    "message": "Playlist job cancelled. Partial outputs were left on disk."
  }
}
```

Cancel semantics:

- Cancel aborts the current video's yt-dlp/ffmpeg subprocess immediately.
- Job state becomes `cancelled`.
- Already-completed videos stay where they are.
- Partial outputs remain on disk for inspection.
- No combined playlist clipboard payload is produced for cancelled jobs.

Error responses:

- HTTP 400: invalid job ID.

```json
{ "ok": false, "error": "job id invalid" }
```

- HTTP 404: job does not exist.

```json
{ "ok": false, "error": "job not found" }
```

- HTTP 200: job is already in `completed`, `cancelled`, or `failed`.

```json
{ "ok": false, "error": "job is already finished" }
```

- HTTP 200: cancellation was requested but the worker could not stop the current process.

```json
{ "ok": false, "error": "job cancel failed" }
```

Example request:

```http
POST /jobs/job_20260510_143012_a1b2c3/cancel HTTP/1.1
Content-Type: application/json
X-Yoink-Token: <token>

{}
```

Example response:

```json
{
  "ok": true,
  "job": {
    "id": "job_20260510_143012_a1b2c3",
    "kind": "playlist",
    "state": "cancelled",
    "source_url": "https://www.youtube.com/playlist?list=PLexample123",
    "playlist_title": "Creator Strategy Interviews",
    "session_folder": "C:\\Users\\Ryan\\Desktop\\Yoink\\_sessions\\creator-strategy-interviews",
    "videos_total": 10,
    "videos_done": 3,
    "videos_failed": 0,
    "current_video": null,
    "current_video_phase": null,
    "started_at": "2026-05-10T14:30:13",
    "updated_at": "2026-05-10T14:40:02",
    "completed_at": "2026-05-10T14:40:02",
    "error": null,
    "result": null,
    "warnings": ["playlist exceeds cap"],
    "message": "Playlist job cancelled. Partial outputs were left on disk."
  }
}
```

### GET /jobs

List recent jobs so the popup can recover playlist state after close/reopen and show recent single-video yoinks.

Auth: `X-Yoink-Token` required.

Query params:

| Field | Type | Required | Notes |
|---|---:|---:|---|
| `kind` | string | no | Optional filter: `playlist` or `single`. Omit to return both. |

Request body: none.

Persistence: jobs persist across helper restarts via `%LOCALAPPDATA%\Yoink\jobs.json` on Windows. In-flight jobs from a previous helper process are restored as records with `state: "failed"` and `error: "server restarted"`; users restart them manually. Jobs are returned sorted by `updated_at` descending. Single-video job records store a text-only corpus snapshot so `/jobs` stays small; the full multimodal clipboard payload is never persisted in `jobs.json`.

Success response: HTTP 200

```json
{
  "ok": true,
  "jobs": [
    {
      "id": "job_20260510_143012_a1b2c3",
      "kind": "playlist",
      "state": "running",
      "source_url": "https://www.youtube.com/playlist?list=PLexample123",
      "title": null,
      "playlist_title": "Creator Strategy Interviews",
      "session_folder": "C:\\Users\\Ryan\\Desktop\\Yoink\\_sessions\\creator-strategy-interviews",
      "videos_total": 10,
      "videos_done": 3,
      "videos_failed": 0,
      "current_video": {
        "index": 4,
        "title": "A practical guide to creator research",
        "url": "https://www.youtube.com/watch?v=abc123DEF45"
      },
      "current_video_phase": "download",
      "started_at": "2026-05-10T14:30:13",
      "updated_at": "2026-05-10T14:36:10",
      "completed_at": null,
      "error": null,
      "result": null,
      "warnings": ["playlist exceeds cap"],
      "message": "Yoinking video 4 of 10."
    },
    {
      "id": "job_20260510_151010_d4e5f6",
      "kind": "single",
      "state": "completed",
      "source_url": "https://www.youtube.com/watch?v=abc123DEF45",
      "title": "A practical guide to creator research",
      "playlist_title": null,
      "session_folder": "C:\\Users\\Ryan\\Desktop\\Yoink\\Creator Research\\a-practical-guide-to-creator-research",
      "videos_total": 1,
      "videos_done": 1,
      "videos_failed": 0,
      "current_video": null,
      "current_video_phase": null,
      "started_at": "2026-05-10T15:10:10",
      "updated_at": "2026-05-10T15:11:04",
      "completed_at": "2026-05-10T15:11:04",
      "error": null,
      "result": {
        "combined_md_path": "C:\\Users\\Ryan\\Desktop\\Yoink\\Creator Research\\a-practical-guide-to-creator-research\\a-practical-guide-to-creator-research.md",
        "combined_md_text": "# A practical guide to creator research\n\n...",
        "folder": "C:\\Users\\Ryan\\Desktop\\Yoink\\Creator Research\\a-practical-guide-to-creator-research"
      },
      "warnings": [],
      "message": "Single-video yoink complete."
    }
  ]
}
```

Error responses:

- HTTP 403: missing or invalid token.
- HTTP 400: invalid `kind` filter.

```json
{ "ok": false, "error": "missing or invalid token" }
```

Example request:

```http
GET /jobs?kind=playlist HTTP/1.1
X-Yoink-Token: <token>
```

Example response:

```json
{
  "ok": true,
  "jobs": [
    {
      "id": "job_20260510_143012_a1b2c3",
      "kind": "playlist",
      "state": "completed",
      "source_url": "https://www.youtube.com/playlist?list=PLexample123",
      "title": null,
      "playlist_title": "Creator Strategy Interviews",
      "session_folder": "C:\\Users\\Ryan\\Desktop\\Yoink\\_sessions\\creator-strategy-interviews",
      "videos_total": 10,
      "videos_done": 10,
      "videos_failed": 0,
      "current_video": null,
      "current_video_phase": null,
      "started_at": "2026-05-10T14:30:13",
      "updated_at": "2026-05-10T15:01:22",
      "completed_at": "2026-05-10T15:01:22",
      "error": null,
      "result": {
        "combined_md_path": "C:\\Users\\Ryan\\Desktop\\Yoink\\_sessions\\creator-strategy-interviews\\corpus.md",
        "combined_md_text": "# Playlist Corpus: Creator Strategy Interviews\n\n...",
        "per_video": []
      },
      "warnings": ["playlist exceeds cap"],
      "message": "Playlist complete."
    }
  ]
}
```

### GET /file?path=<absolute-path>

Serve an image file from the Yoink output root so the MV3 extension popup can render screenshot thumbnails without relying on `file://` URLs.

Auth: `X-Yoink-Token` required.

Request body: none.

Query parameters:

| Field | Type | Required | Notes |
|---|---:|---:|---|
| `path` | string | yes | Absolute path to an image file under the Yoink output root. URL-encode the value. |

Success response: HTTP 200

Headers:

```http
Content-Type: image/jpeg
Cache-Control: private, max-age=300
```

Body: raw image bytes.

Example request:

```http
GET /file?path=C%3A%5CUsers%5CRyan%5CDesktop%5CYoink%5CMarketing%5Cvideo-1%5Cscreenshots%5Cshot_0001.jpg HTTP/1.1
X-Yoink-Token: <token>
```

Path validation rules:

- Resolve the absolute path before serving.
- Reject missing, relative, malformed, or parent-directory paths.
- Reject any raw or resolved path containing a `..` path segment.
- Reject paths that do not resolve under the Yoink output root (`Desktop\Yoink`, resolved through the same Windows known-folder logic used for single-video and session output, or `YOINK_OUTPUT_DIR` when explicitly set in dev/support mode).
- Reject missing paths and non-regular files.
- Reject files larger than 10 MB.
- Allow only `.png`, `.jpg`, `.jpeg`, and `.webp`.
- Validate magic bytes match the extension-derived MIME type before serving.

Error responses:

| HTTP status | Error string | Meaning |
|---:|---|---|
| 400 | `path required` | Query string omitted `path`. |
| 400 | `path invalid` | Path is relative, malformed, or contains a parent-directory segment. |
| 400 | `file too large` | File exceeds the 10 MB cap. |
| 403 | `missing or invalid token` | `X-Yoink-Token` missing or stale. |
| 403 | `path escapes Yoink root` | Resolved path is outside the Yoink output root. |
| 404 | `file not found` | File is missing or not a regular file. |
| 415 | `unsupported file type` | Extension or magic bytes are not an allowed image type. |

### GET /taxonomy

Return Hook Type taxonomy rows captured from successful classifications. This is the HTTP mirror of the MCP `get_taxonomy` tool and is intended as the foundation for future taxonomy viewer/export work.

Auth: `X-Yoink-Token` required.

Request body: none.

Query parameters:

| Field | Type | Required | Notes |
|---|---:|---:|---|
| `channel` | string | no | Exact channel-name filter, case-insensitive. |
| `hook_type` | string | no | One of the Hook Type categories. |
| `limit` | integer | no | Defaults to 50. Clamped to 1-500. |

Success response: HTTP 200

```json
{
  "ok": true,
  "taxonomy": [
    {
      "video_id": "abc123DEF45",
      "hook_type": "curiosity_gap",
      "hook_explanation": "The opening withholds the payoff while promising a counter-intuitive reveal.",
      "channel": "Example Channel",
      "title": "How creators build durable content systems",
      "classified_at": "2026-05-12T10:30:00"
    }
  ]
}
```

Rows sort by `classified_at` descending. Corrupt or missing `taxonomy.json` yields an empty array and a server log warning rather than crashing the helper.

Error responses:

| HTTP status | Error string | Meaning |
|---:|---|---|
| 400 | `hook_type invalid` | `hook_type` is not one of the allowed categories. |
| 400 | `limit invalid` | `limit` cannot be parsed as an integer. |
| 403 | `missing or invalid token` | `X-Yoink-Token` missing or stale. |

### GET /skill/system-prompt

Return the copyable Yoink Operator Skill fallback prompt for setup.html. This is for AI clients that do not load `SKILL.md` natively.

Auth: `X-Yoink-Token` required.

Request body: none.

Success response: HTTP 200

Headers:

```http
Content-Type: text/markdown; charset=utf-8
Cache-Control: private, max-age=300
```

Body: raw markdown text from `%LOCALAPPDATA%\Yoink\skills\yoink\system-prompt.md` in installed builds, or `skills\yoink\system-prompt.md` in dev mode.

Error responses:

| HTTP status | Error string | Meaning |
|---:|---|---|
| 403 | `missing or invalid token` | `X-Yoink-Token` missing or stale. |
| 404 | `skill system prompt not found` | Skill files are missing from the install layout. |

### MCP HTTP JSON-RPC helper endpoints

Yoink v2 Sprint 4 adds MCP over stdio plus an experimental local HTTP JSON-RPC helper. Stdio clients launch `yoink_mcp.py` and are the officially supported MCP transport for launch. HTTP clients can use the existing helper server under `/mcp/v1` for direct JSON-RPC POST calls, but this is not a spec-complete SSE or Streamable HTTP implementation.

Auth: `X-Yoink-Token` required for every HTTP JSON-RPC helper endpoint, including config and SSE discovery.

#### GET /mcp/v1/config

Returns helper-generated config values for setup.html.

Success response: HTTP 200

```json
{
  "ok": true,
  "stdio": {
    "command": "C:\\Users\\Ryan\\AppData\\Local\\Yoink\\python\\python.exe",
    "args": ["C:\\Users\\Ryan\\AppData\\Local\\Yoink\\yoink_mcp.py"]
  },
  "http": {
    "url": "http://127.0.0.1:5179/mcp/v1",
    "sse_url": "http://127.0.0.1:5179/mcp/v1/sse",
    "auth_header": "X-Yoink-Token"
  }
}
```

#### POST /mcp/v1/initialize

MCP JSON-RPC initialize helper path. Also supported as `POST /mcp/v1` with method `initialize`.

Request body:

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "initialize",
  "params": {
    "protocolVersion": "2025-11-25",
    "capabilities": {},
    "clientInfo": { "name": "ExampleClient", "version": "1.0.0" }
  }
}
```

Success response:

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "protocolVersion": "2025-11-25",
    "capabilities": {
      "tools": { "listChanged": false }
    },
    "serverInfo": {
      "name": "yoink",
      "version": "1.0.0"
    },
    "instructions": "Yoink exposes local YouTube extraction tools. Outputs are stored under the user's Yoink output folder on this machine."
  }
}
```

#### POST /mcp/v1/tools/list

MCP tool listing helper path. Also supported as `POST /mcp/v1` with method `tools/list`.

Request body:

```json
{ "jsonrpc": "2.0", "id": 2, "method": "tools/list" }
```

Success response:

```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "result": {
    "tools": [
      {
        "name": "yoink_video",
        "description": "Extract a single YouTube video into a Yoink corpus. Returns the saved folder, markdown corpus, and screenshot paths.",
        "inputSchema": {
          "type": "object",
          "properties": {
            "url": { "type": "string", "description": "YouTube video URL." },
            "interval": { "type": "integer", "minimum": 5, "maximum": 300, "default": 30 }
          },
          "required": ["url"],
          "additionalProperties": false
        }
      }
    ]
  }
}
```

Tools currently exposed:

- `yoink_video`
- `yoink_playlist`
- `get_job_status`
- `cancel_job`
- `list_recent_yoinks`
- `search_yoinks`
- `get_yoink_corpus`
- `analyze_comments`
- `classify_hook`
- `get_taxonomy`

Full schemas and return shapes live in `docs/v2-mcp.md`.

#### POST /mcp/v1/tools/call

MCP tool call helper path. Also supported as `POST /mcp/v1` with method `tools/call`.

Request body:

```json
{
  "jsonrpc": "2.0",
  "id": 3,
  "method": "tools/call",
  "params": {
    "name": "search_yoinks",
    "arguments": {
      "query": "creator strategy",
      "limit": 5
    }
  }
}
```

Success response:

```json
{
  "jsonrpc": "2.0",
  "id": 3,
  "result": {
    "content": [
      {
        "type": "text",
        "text": "{\"ok\": true, \"results\": []}"
      }
    ],
    "structuredContent": {
      "ok": true,
      "results": []
    },
    "isError": false
  }
}
```

Handled tool failures still return JSON-RPC success with `isError: true` and a structured payload:

```json
{
  "jsonrpc": "2.0",
  "id": 3,
  "result": {
    "content": [
      {
        "type": "text",
        "text": "{\"ok\": false, \"error\": \"anthropic key not configured\"}"
      }
    ],
    "structuredContent": {
      "ok": false,
      "error": "anthropic key not configured"
    },
    "isError": true
  }
}
```

Protocol errors use JSON-RPC error envelopes:

```json
{
  "jsonrpc": "2.0",
  "id": 3,
  "error": { "code": -32602, "message": "invalid tool call" }
}
```

#### GET /mcp/v1/sse

Experimental one-shot SSE compatibility endpoint. It emits an `endpoint` event pointing to `/mcp/v1` and then closes. It is useful for lightweight discovery, but it is not a long-lived spec-complete MCP SSE transport.

Headers:

```http
Content-Type: text/event-stream; charset=utf-8
Cache-Control: no-cache
```

Event:

```text
event: endpoint
data: /mcp/v1
```

## Job state machine

All async jobs use the same state machine:

```text
queued -> running -> completed
                  -> cancelled
                  -> failed
```

States:

| State | Meaning | Required populated fields |
|---|---|---|
| `queued` | Job has been accepted but no video is currently extracting. | `id`, `kind`, `state`, `source_url`, `session_folder`, `videos_total`, `videos_done`, `videos_failed`, `updated_at`. `started_at`, `completed_at`, `current_video`, `current_video_phase`, `error`, and `result` are null. |
| `running` | Job is actively extracting one playlist video. | `started_at`, `updated_at`, `current_video`, `current_video_phase`. `result` is null. |
| `completed` | Job finished and produced a combined corpus. | `completed_at`, `result`, `videos_done`. `current_video`, `current_video_phase`, and `error` are null. |
| `cancelled` | User requested cancellation. Current subprocess was aborted if one was active. Partial outputs remain on disk. | `completed_at`, `videos_done`, `videos_failed`, `message`. `result` is null. |
| `failed` | Job cannot produce a useful playlist corpus. This happens when playlist preview/start fails before useful work begins, when a fatal worker-level error occurs, or when zero selected videos succeed. Individual per-video failures increment `videos_failed` and do not fail the job if at least one video succeeds. | `completed_at`, `error`. `result` is null unless an implementation later chooses to expose partial results. |

Allowed transitions:

- `queued -> running`
- `queued -> cancelled`
- `running -> completed`
- `running -> cancelled`
- `running -> failed`

No terminal state transitions back to `queued` or `running`.

## Progress reporting shape

Every job object returned by `/playlist/start`, `/jobs/<id>`, `/jobs/<id>/cancel`, and `/jobs` uses this shape. Playlist jobs populate `playlist_title`; single-video jobs populate `title`.

```json
{
  "id": "job_20260510_143012_a1b2c3",
  "kind": "playlist",
  "state": "queued|running|completed|cancelled|failed",
  "source_url": "https://www.youtube.com/playlist?list=PLexample123",
  "title": null,
  "playlist_title": "Creator Strategy Interviews",
  "session_folder": "C:\\Users\\Ryan\\Desktop\\Yoink\\_sessions\\creator-strategy-interviews",
  "videos_total": 10,
  "videos_done": 0,
  "videos_failed": 0,
  "current_video": {
    "index": 1,
    "title": "A practical guide to creator research",
    "url": "https://www.youtube.com/watch?v=abc123DEF45"
  },
  "current_video_phase": "metadata|download|screenshots|comments|done",
  "started_at": "2026-05-10T14:30:13",
  "updated_at": "2026-05-10T14:31:00",
  "completed_at": null,
  "error": null,
  "result": null,
  "warnings": [],
  "message": "Yoinking video 1 of 10."
}
```

Field rules:

- `state` is always one of `queued`, `running`, `completed`, `cancelled`, `failed`.
- `kind` is `playlist` for async playlist jobs and `single` for synchronous `/extract` side-effect records.
- `title` is populated for `single` jobs; `playlist_title` is populated for `playlist` jobs.
- `session_folder` is the absolute path to the output folder on disk. Playlist jobs populate it from `queued` onwards; single jobs populate it when a folder is known.
- `videos_total` is the number of videos selected for processing after the 10-video cap for playlists, or `1` for single jobs.
- `videos_done` counts successful per-video extractions.
- `videos_failed` counts per-video failures. Playlist jobs continue after private, age-restricted, geoblocked, deleted, or otherwise failed individual videos. Single-video failures set `videos_failed` to `1`.
- `current_video` is `{ "title": string, "index": number, "url": string }` while running, otherwise null.
- `current_video_phase` is one of `metadata`, `download`, `screenshots`, `comments`, `done`, or null.
- `started_at`, `updated_at`, `completed_at` are ISO timestamps, null when not applicable.
- `error` is a string only when `state` is `failed`; otherwise null.
- `result` is populated only when `state` is `completed`. Playlist results include `per_video`; single-video results include `folder` and no `per_video` list.

Completed `result` shape:

```json
{
  "combined_md_path": "C:\\Users\\Ryan\\Desktop\\Yoink\\_sessions\\creator-strategy-interviews\\corpus.md",
  "combined_md_text": "# Playlist Corpus: Creator Strategy Interviews\n\n...",
  "per_video": [
    {
      "index": 1,
      "title": "A practical guide to creator research",
      "url": "https://www.youtube.com/watch?v=abc123DEF45",
      "folder": "C:\\Users\\Ryan\\Desktop\\Yoink\\_sessions\\creator-strategy-interviews\\video-1",
      "md_path": "C:\\Users\\Ryan\\Desktop\\Yoink\\_sessions\\creator-strategy-interviews\\video-1\\video-1.md",
      "json_path": "C:\\Users\\Ryan\\Desktop\\Yoink\\_sessions\\creator-strategy-interviews\\video-1\\video-1.json",
      "ok": true,
      "error": null
    },
    {
      "index": 2,
      "title": "Private or rate-limited video",
      "url": "https://www.youtube.com/watch?v=def456GHI78",
      "folder": "C:\\Users\\Ryan\\Desktop\\Yoink\\_sessions\\creator-strategy-interviews\\private-or-rate-limited-video",
      "md_path": null,
      "json_path": null,
      "failed_marker_path": "C:\\Users\\Ryan\\Desktop\\Yoink\\_sessions\\creator-strategy-interviews\\private-or-rate-limited-video\\FAILED.txt",
      "ok": false,
      "error": "yt-dlp failed: sign in to confirm you're not a bot"
    }
  ]
}
```

Completed single-video `result` shape:

```json
{
  "combined_md_path": "C:\\Users\\Ryan\\Desktop\\Yoink\\Creator Research\\a-practical-guide-to-creator-research\\a-practical-guide-to-creator-research.md",
  "combined_md_text": "# A practical guide to creator research\n\n...",
  "folder": "C:\\Users\\Ryan\\Desktop\\Yoink\\Creator Research\\a-practical-guide-to-creator-research"
}
```

Single-video `combined_md_text` is text-only. It is derived from the on-disk markdown corpus with local image references stripped. The full multimodal clipboard payload with base64 screenshots is intentionally not stored in `jobs.json` or returned by `/jobs`; clients that need the full saved corpus should open `combined_md_path` or call `get_yoink_corpus`.

## Combined corpus delivery

When a playlist job completes, `/jobs/<id>` returns `result.combined_md_text`. The extension copies that value to the clipboard.

Important transport rule:

- `combined_md_text` is text-only and strips screenshot image blocks from the clipboard payload.
- Per-video corpora on disk retain screenshot references.
- The combined `.md` at `combined_md_path` also retains screenshot references.
- Only the clipboard string is stripped, because a 10-video playlist with v1 screenshot density can exceed 5 MB and overflow practical Claude/ChatGPT context.
- Comments follow the v1 fire-and-forget behavior. The combined corpus snapshots each per-video `.md` when the job completes; comments may still show the pending placeholder there. The per-video files continue updating as background comment fetches finish.
- Comment Intelligence follows the same background semantics. If a per-video `.md` already has a Comment Intelligence section when the playlist job transitions to `completed`, that section is included in both the on-disk combined corpus and the text-only clipboard payload. If the analysis finishes later, it appears only in the per-video `.md`.

Clients must not infer that `combined_md_text` is byte-for-byte identical to the file at `combined_md_path`.

## Playlist pacing and per-video failures

Playlist extraction deliberately sleeps between videos before the next yt-dlp/ffmpeg pass. Default: `5` seconds, configurable for dev/support via `YOINK_PLAYLIST_SLEEP_SEC` (`0-120`). This makes 10-video playlist jobs slower, but lowers the chance that YouTube rate-limits a burst of back-to-back downloads.

If yt-dlp returns a rate-limit-like error (`429`, `too many requests`, `rate limit`, `sign in to confirm you're not a bot`, captcha/bot checks), the worker continues the playlist after exponential backoff: 30s, 60s, 120s, 240s, then capped at 300s. Individual failures increment `videos_failed`; the job still completes if at least one selected video succeeds.

When a playlist item fails after creating or reserving an output folder, Yoink writes `FAILED.txt` into that per-video folder with the URL, playlist index, timestamp, and short failure reason. This keeps the disk output honest: a user inspecting the folder can distinguish a failed partial from a successful corpus.

## Backward compatibility

The following v1 endpoints keep their current shapes and semantics:

- `POST /extract`
- `POST /session/start`
- `POST /session/add`
- `POST /session/close`
- `POST /session/cancel`
- `POST /session/open`
- `GET /session/list`
- `GET /session/active`
- `GET /ping`
- `GET /health`
- `GET /token`
- `GET /open-prompts`
- `GET /open-index`
- `GET /recent`
- `GET /open-folder`

No v2 playlist work may break existing single-video, session, health, token, recent-yoinks, or open-folder clients.

## Client-side helpers needed from Claude Code

Claude Code owns `extension/lib/extract.js`. Backend contracts now need these client helpers:

- `getScreenshotThumbnail(path)` wraps authenticated `GET /file?path=<absolute-path>`, validates the response is an image, and returns a blob URL for use in `<img src>`.
- `getSettings`, `updateSettings`, and `testAnthropicKey` already exist. Confirm their settings shape includes `hook_type_enabled`, `smart_screenshot_picker_enabled`, and `clipboard_screenshot_cap` alongside `comment_intelligence_enabled` and `anthropic_key_set`.
- Sprint 4 MCP needs no new `extract.js` helpers. setup.html uses existing `STC.getToken()` plus authenticated fetch to `GET /mcp/v1/config`.
- Sprint 12 Skill install needs no new `extract.js` helpers. setup.html uses existing `STC.getToken()` plus authenticated fetch to `GET /skill/system-prompt`.

## Error model

Reuse the v1 split:

- Protocol/auth/body validation errors use non-200 HTTP status codes.
- Handled application errors use HTTP 200 with `{"ok": false, "error": "..."}`.
- Clients should display `error` directly unless a friendlier mapped message exists in the extension.

Protocol/auth/body errors:

| HTTP status | Error string | Meaning |
|---:|---|---|
| 400 | `Bad JSON: ...` | Body is not valid JSON. |
| 400 | `Top-level JSON must be an object` | Body parsed as array/string/number instead of object. |
| 400 | `playlist URL invalid` | URL is missing, malformed, or not a supported YouTube playlist URL. |
| 400 | `interval must be an integer` | `interval` cannot be parsed as integer. |
| 400 | `interval must be between 5 and 300` | `interval` outside existing v1 bounds. |
| 400 | `job id invalid` | Job ID fails validation. |
| 403 | `missing or invalid token` | `X-Yoink-Token` missing or stale. |
| 413 | `Body too large (>65536 bytes)` | Body exceeds 64 KB cap. |
| 415 | `Content-Type must be application/json` | POST without JSON content type. |
| 404 | `job not found` | Job ID is valid but unknown. |

Handled application errors and warnings:

| String | Kind | Client behavior |
|---|---|---|
| `yt-dlp playlist preview failed` | error | Show failure; user can retry or choose another playlist. |
| `playlist has no videos` | error | Show failure; do not start job. |
| `playlist exceeds cap` | warning | Not fatal. Show that only the first 10 videos will be processed. |
| `playlist extraction failed: zero videos succeeded` | error | Show failure. Per-video outputs may exist on disk, but there is no completed combined clipboard payload. |
| `job is already finished` | error | Cancel button should stop showing after terminal states. |
| `job cancel failed` | error | Show failure; keep polling job state. |

## Known limitations and resolved decisions

- Per-video failures continue. The job completes if at least one video succeeds and fails only when zero selected videos succeed or the playlist itself cannot preview/start.
- Job state persists across helper restarts via `jobs.json`. In-flight jobs are marked failed on restart; users restart them manually.
- Comments remain background/fire-and-forget.
- Preview `channel` and `duration_seconds` fields are nullable.
