# Yoink v2 API contract

Status: draft for review  
Scope: Playlist Mode backend contract only  
Non-goal: no implementation details, UI design, Channel Decoder, Niche Corpus, or installer work

## Overview

Yoink v2 adds an async job model for playlist extraction while preserving the v1 single-video flow exactly as-is. `/extract` and `/session/add` remain synchronous and keep their current request and response shapes for backward compatibility. Playlist Mode uses new `/playlist/*` and `/jobs/*` endpoints: the client previews a playlist, starts a job, polls progress, and can cancel mid-flight. This job model is the foundation for later Channel Decoder and Niche Corpus work, but those endpoints are intentionally out of scope for this contract.

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

## Endpoint reference

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

List recent async jobs so the popup can recover state after close/reopen.

Auth: `X-Yoink-Token` required.

Request body: none.

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
      "playlist_title": "Creator Strategy Interviews",
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
    }
  ]
}
```

Error responses:

- HTTP 403: missing or invalid token.

```json
{ "ok": false, "error": "missing or invalid token" }
```

Example request:

```http
GET /jobs HTTP/1.1
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
      "playlist_title": "Creator Strategy Interviews",
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
| `queued` | Job has been accepted but no video is currently extracting. | `id`, `kind`, `state`, `source_url`, `videos_total`, `videos_done`, `videos_failed`, `updated_at`. `started_at`, `completed_at`, `current_video`, `current_video_phase`, `error`, and `result` are null. |
| `running` | Job is actively extracting one playlist video. | `started_at`, `updated_at`, `current_video`, `current_video_phase`. `result` is null. |
| `completed` | Job finished and produced a combined corpus. | `completed_at`, `result`, `videos_done`. `current_video`, `current_video_phase`, and `error` are null. |
| `cancelled` | User requested cancellation. Current subprocess was aborted if one was active. Partial outputs remain on disk. | `completed_at`, `videos_done`, `videos_failed`, `message`. `result` is null. |
| `failed` | Job cannot continue because of a fatal playlist-level or worker-level error. | `completed_at`, `error`. `result` is null unless an implementation later chooses to expose partial results. |

Allowed transitions:

- `queued -> running`
- `queued -> cancelled`
- `running -> completed`
- `running -> cancelled`
- `running -> failed`

No terminal state transitions back to `queued` or `running`.

## Progress reporting shape

Every job object returned by `/playlist/start`, `/jobs/<id>`, `/jobs/<id>/cancel`, and `/jobs` uses this shape:

```json
{
  "id": "job_20260510_143012_a1b2c3",
  "kind": "playlist",
  "state": "queued|running|completed|cancelled|failed",
  "source_url": "https://www.youtube.com/playlist?list=PLexample123",
  "playlist_title": "Creator Strategy Interviews",
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
- `videos_total` is the number of videos selected for processing after the 10-video cap.
- `videos_done` counts successful per-video extractions.
- `videos_failed` counts per-video failures if the chosen failure policy allows continuing.
- `current_video` is `{ "title": string, "index": number, "url": string }` while running, otherwise null.
- `current_video_phase` is one of `metadata`, `download`, `screenshots`, `comments`, `done`, or null.
- `started_at`, `updated_at`, `completed_at` are ISO timestamps, null when not applicable.
- `error` is a string only when `state` is `failed`; otherwise null.
- `result` is populated only when `state` is `completed`.

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
    }
  ]
}
```

## Combined corpus delivery

When a playlist job completes, `/jobs/<id>` returns `result.combined_md_text`. The extension copies that value to the clipboard.

Important transport rule:

- `combined_md_text` is text-only and strips screenshot image blocks from the clipboard payload.
- Per-video corpora on disk retain screenshot references.
- The combined `.md` at `combined_md_path` also retains screenshot references.
- Only the clipboard string is stripped, because a 10-video playlist with v1 screenshot density can exceed 5 MB and overflow practical Claude/ChatGPT context.

Clients must not infer that `combined_md_text` is byte-for-byte identical to the file at `combined_md_path`.

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
| `job is already finished` | error | Cancel button should stop showing after terminal states. |
| `job cancel failed` | error | Show failure; keep polling job state. |

## Open questions

1. **Per-video failure policy.** If video 4 of 10 fails because it is private, age-restricted, geoblocked, or deleted, should the job continue to video 5 and complete with `videos_failed > 0`, or should the whole job transition to `failed` immediately? The status shape supports either; product decision needed before implementation.

2. **Job persistence across server restarts.** Should `/jobs` recover queued/running/completed job state after the helper restarts, or is in-memory state acceptable for v2 first ship? The popup recovery use case is clear for close/reopen; restart recovery is undecided.

3. **Comments phase semantics.** v1 fetches comments asynchronously after the per-video response. For playlist jobs, should `current_video_phase: "comments"` mean the job waits for comments before moving to the next video, or should comments keep arriving in the background while the job advances? Waiting gives a more complete combined corpus; background keeps playlists faster.

4. **Preview metadata completeness.** The preview shape includes `duration_seconds` and `channel` as useful UI fields, but yt-dlp flat playlist data may omit them for some playlist types. Should clients treat those fields as nullable, or should the server do slower per-video metadata hydration during preview?
