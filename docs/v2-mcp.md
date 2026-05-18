# Yoink v2 MCP server

Status: Sprint 4 backend implemented  
SDK: official Model Context Protocol Python SDK, `mcp==1.27.1`  
Transports: stdio, plus an experimental authenticated local HTTP JSON-RPC helper

## Overview

Yoink exposes 13 MCP tools covering extraction, playlist jobs, search, corpus retrieval, citation maps, health scores, Comment Intelligence, Hook Type, hook taxonomy, and entity mentions. The tool implementation lives in `yoink_mcp_tools.py`; stdio (`yoink_mcp.py`) is the officially supported MCP transport, and the local HTTP JSON-RPC helper (`server.py` under `/mcp/v1`) wraps the same registry for clients that can use direct POST calls.

## Compatibility matrix

This is the launch-facing compatibility claim. Keep it honest: only clients Ryan smoke-tests before v2 launch are marked officially tested.

| Client | Status | Transport | Notes |
|---|---|---|---|
| Claude Desktop | Officially tested | stdio | Smoke-tested by Ryan before v2 launch. |
| Cursor | Officially tested | stdio | Smoke-tested by Ryan before v2 launch. |
| ChatGPT Desktop | Should work, community-reported | stdio | Standard stdio MCP; not smoke-tested by Ryan. |
| Continue | Should work, community-reported | stdio | Standard stdio MCP; not smoke-tested by Ryan. |
| Cline | Should work, community-reported | stdio | Standard stdio MCP; not smoke-tested by Ryan. |
| Other MCP-compatible clients | Generic stdio fallback | stdio | Use the generic stdio snippet; not individually certified. |

HTTP JSON-RPC is available for local clients that prefer HTTP, but it is experimental and not counted as an officially tested launch transport.

## Transport model

### Stdio

Stdio is the default path for Claude Desktop, ChatGPT Desktop, Cursor, Continue, Cline, and most local-agent clients.

Installed Windows command:

```json
{
  "mcpServers": {
    "yoink": {
      "command": "C:\\Users\\<you>\\AppData\\Local\\Yoink\\python\\python.exe",
      "args": ["C:\\Users\\<you>\\AppData\\Local\\Yoink\\yoink_mcp.py"]
    }
  }
}
```

Dev command:

```json
{
  "mcpServers": {
    "yoink": {
      "command": "python",
      "args": ["C:\\Users\\hello\\OneDrive\\Desktop\\Yoink-codex-v2\\yoink_mcp.py"]
    }
  }
}
```

Auth: none. The MCP client launched the subprocess, so the local process boundary is the trust boundary. The tools still validate URLs, slugs, and job IDs before touching disk or the network.

### Experimental HTTP JSON-RPC

The existing helper server also exposes an experimental local HTTP JSON-RPC helper:

```text
http://127.0.0.1:5179/mcp/v1
```

Auth: `X-Yoink-Token: <token>` required on every HTTP JSON-RPC request. `GET /mcp/v1/config` returns config metadata for setup.html; it is also token-gated.

HTTP endpoints:

- `POST /mcp/v1/initialize`
- `POST /mcp/v1/tools/list`
- `POST /mcp/v1/tools/call`
- `POST /mcp/v1` with JSON-RPC `method` set to `initialize`, `tools/list`, `tools/call`, or `ping`
- `GET /mcp/v1/sse` emits a lightweight one-shot `endpoint` event pointing clients at `/mcp/v1`

The HTTP wrapper covers the JSON-RPC POST surface and returns MCP-style JSON-RPC envelopes. Tool call results include both text content and `structuredContent` for clients that support it. It is not a spec-complete SSE or Streamable HTTP implementation; strict HTTP MCP clients may require future transport work. For launch, stdio is the supported path.

## Client config snippets

The installed setup page generates copyable snippets using the actual install path and current token. Do not hand-edit these examples unless you are in dev mode.

Claude Desktop / Cursor:

```json
{
  "mcpServers": {
    "yoink": {
      "command": "C:\\Users\\<you>\\AppData\\Local\\Yoink\\python\\python.exe",
      "args": ["C:\\Users\\<you>\\AppData\\Local\\Yoink\\yoink_mcp.py"]
    }
  }
}
```

Generic HTTP:

```json
{
  "url": "http://127.0.0.1:5179/mcp/v1",
  "headers": {
    "X-Yoink-Token": "<token from setup.html>"
  }
}
```

## Tool reference

All tool names are vendor-neutral, snake_case, and action-first.

### yoink_video

Extract a single YouTube video into a Yoink corpus.

Parameters:

```json
{
  "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
  "interval": 30
}
```

Return shape:

```json
{
  "ok": true,
  "slug": "video-slug",
  "folder": "C:\\Users\\Ryan\\Desktop\\Yoink\\Topic\\video-slug",
  "corpus_md": "# Video title\n...",
  "screenshots": ["C:\\Users\\Ryan\\Desktop\\Yoink\\Topic\\video-slug\\screenshots\\shot_0001.jpg"]
}
```

Errors:

- `{ "ok": false, "error": "url required" }`
- `{ "ok": false, "error": "URL must be a youtube.com or youtu.be video link" }`
- `{ "ok": false, "error": "<friendly extraction error>" }`

Rate limit: 5 calls/minute per process.

### yoink_playlist

Start async extraction for a YouTube playlist.

Parameters:

```json
{
  "url": "https://www.youtube.com/playlist?list=PLexample123",
  "interval": 30
}
```

Return shape:

```json
{
  "ok": true,
  "job_id": "job_20260511_120000_a1b2c3"
}
```

Errors:

- `{ "ok": false, "error": "playlist URL invalid" }`
- `{ "ok": false, "error": "yt-dlp playlist preview failed" }`
- `{ "ok": false, "error": "playlist has no videos" }`

Rate limit: 5 calls/minute per process.

### get_job_status

Return a full playlist job object. Shape is identical to `GET /jobs/<id>` in `docs/v2-api.md`.

Parameters:

```json
{ "job_id": "job_20260511_120000_a1b2c3" }
```

Errors:

- `{ "ok": false, "error": "job id invalid" }`
- `{ "ok": false, "error": "job not found" }`

### cancel_job

Cancel a running async job. Shape is identical to `POST /jobs/<id>/cancel`.

Parameters:

```json
{ "job_id": "job_20260511_120000_a1b2c3" }
```

Errors:

- `{ "ok": false, "error": "job id invalid" }`
- `{ "ok": false, "error": "job not found" }`
- `{ "ok": false, "error": "job is already finished" }`
- `{ "ok": false, "error": "job cancel failed" }`

### list_recent_yoinks

List recent saved Yoink corpora.

Parameters:

```json
{ "limit": 20 }
```

Return shape:

```json
{
  "ok": true,
  "yoinks": [
    {
      "slug": "video-slug",
      "title": "Video title",
      "folder": "C:\\Users\\Ryan\\Desktop\\Yoink\\Topic\\video-slug",
      "yoinked_at": "2026-05-11T10:15:00"
    }
  ]
}
```

### search_yoinks

Keyword search across saved Yoink markdown corpora.

Parameters:

```json
{ "query": "creator strategy", "limit": 10 }
```

Return shape:

```json
{
  "ok": true,
  "results": [
    {
      "slug": "video-slug",
      "title": "Video title",
      "snippet": "...matching markdown text...",
      "score": 4
    }
  ]
}
```

Errors:

- `{ "ok": false, "error": "query required" }`

### get_yoink_corpus

Return the full markdown corpus for a saved yoink.

Parameters:

```json
{ "slug": "video-slug" }
```

Return shape:

```json
{
  "ok": true,
  "corpus_md": "# Video title\n...",
  "folder": "C:\\Users\\Ryan\\Desktop\\Yoink\\Topic\\video-slug",
  "video_id": "abc123DEF45",
  "video_url": "https://www.youtube.com/watch?v=abc123DEF45",
  "citations": [
    {
      "video_id": "abc123DEF45",
      "kind": "transcript_chunk",
      "seq": 0,
      "timestamp_start": 0.0,
      "timestamp_end": 6.2,
      "text": "Opening transcript chunk...",
      "file_path": null,
      "youtube_deep_link": "https://youtube.com/watch?v=abc123DEF45&t=0s"
    }
  ]
}
```

`video_id`, `video_url`, and `citations` are additive fields. `video_id` and `video_url` are populated from the per-video JSON sidecar when available and are `null` for legacy/malformed yoinks without sidecar metadata. `citations` is best-effort and may be empty if the yoink has not been indexed yet.

Errors:

- `{ "ok": false, "error": "yoink not found" }`
- `{ "ok": false, "error": "corpus read failed: ..." }`

### analyze_comments

Run Comment Intelligence on an existing yoink and return structured results. Requires a configured Anthropic API key.

Parameters:

```json
{ "slug": "video-slug" }
```

Return shape:

```json
{
  "ok": true,
  "top_themes": [],
  "mentioned_products": [],
  "notable_disagreements": []
}
```

Errors:

- `{ "ok": false, "error": "anthropic key not configured" }`
- `{ "ok": false, "error": "yoink not found" }`
- `{ "ok": false, "error": "not enough comments to analyze" }`
- `{ "ok": false, "error": "<Anthropic or parsing error>" }`

Rate limit: 10 calls/minute per process.

### classify_hook

Run Hook Type classification on an existing yoink and return the category and explanation. Requires a configured Anthropic API key.

Parameters:

```json
{ "slug": "video-slug" }
```

Return shape:

```json
{
  "ok": true,
  "hook_type": "curiosity_gap",
  "hook_explanation": "The opening withholds the answer while promising a counter-intuitive payoff."
}
```

Errors:

- `{ "ok": false, "error": "anthropic key not configured" }`
- `{ "ok": false, "error": "yoink not found" }`
- `{ "ok": false, "error": "<Anthropic or parsing error>" }`

Rate limit: 10 calls/minute per process.

### get_taxonomy

Return Hook Type taxonomy rows captured from successful classifications. This is a read-only tool intended for future taxonomy viewing, CSV export, and retention controls.

Parameters:

```json
{
  "channel": "Example Channel",
  "hook_type": "curiosity_gap",
  "limit": 50
}
```

All fields are optional. `channel` is an exact channel-name filter, compared case-insensitively. `hook_type` must be one of the Hook Type categories. `limit` defaults to 50 and is clamped to 1-500.

Return shape:

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

Rows sort by `classified_at` descending.

Errors:

- `{ "ok": false, "error": "channel must be a string" }`
- `{ "ok": false, "error": "hook_type must be a string" }`
- `{ "ok": false, "error": "hook_type invalid" }`

### get_citation_map

Return pre-computed transcript and screenshot citations for a saved yoink. Each citation includes a timestamped YouTube deep link so agents can cite the source without reparsing markdown.

Parameters:

```json
{ "slug": "video-slug" }
```

Return shape:

```json
{
  "ok": true,
  "video_id": "abc123DEF45",
  "transcript_citations": [
    {
      "seq": 0,
      "timestamp_start": 0.0,
      "timestamp_end": 6.2,
      "text": "Opening transcript chunk...",
      "deep_link": "https://youtube.com/watch?v=abc123DEF45&t=0s"
    }
  ],
  "screenshot_citations": [
    {
      "seq": 0,
      "timestamp": 30.0,
      "file_path": "C:\\Users\\Ryan\\Desktop\\Yoink\\Topic\\video-slug\\screenshots\\shot_0001.jpg",
      "deep_link": "https://youtube.com/watch?v=abc123DEF45&t=30s"
    }
  ]
}
```

Fields:

- `transcript_citations` contains one entry per transcript chunk from the sidecar.
- `screenshot_citations` contains one entry per screenshot from the sidecar.
- `seq` preserves original order within each citation kind.
- `deep_link` is a YouTube `watch?v=<id>&t=<seconds>s` URL.

Errors:

- `{ "ok": false, "error": "yoink not found" }`
- `{ "ok": false, "error": "yoink has no video_id" }`
- `{ "ok": false, "error": "rate limit exceeded: max 60/minute" }`

Rate limit: 60 calls/minute per process.

### get_yoink_health

Return the extraction health score for a saved yoink. This is the same five-field health dict the popup renders in Recent yoinks.

Parameters:

```json
{ "slug": "video-slug" }
```

Return shape:

```json
{
  "ok": true,
  "video_id": "abc123DEF45",
  "health": {
    "transcript": "ok",
    "screenshots": "ok",
    "comments": "ok",
    "hook": "completed",
    "comment_intelligence": "skipped"
  }
}
```

Health fields:

| Field | Meaning |
|---|---|
| `transcript` | Transcript extraction status. |
| `screenshots` | Screenshot extraction status. |
| `comments` | Comment-fetch status. |
| `hook` | Hook Type analysis status. |
| `comment_intelligence` | Comment Intelligence status. |

Status strings are intentionally compact (`ok`, `missing`, `skipped`, `pending`, `completed`, `failed`, or related backend status strings). UI clients should display the raw value when they do not recognize a newer status.

Errors:

- `{ "ok": false, "error": "yoink not found" }`
- `{ "ok": false, "error": "no health data for this yoink" }`
- `{ "ok": false, "error": "rate limit exceeded: max 60/minute" }`

Rate limit: 60 calls/minute per process.

### find_mentions

Find every yoink in your corpus that mentions a specific entity: person, tool, product, topic, company, or other named thing.

Parameters:

```json
{
  "entity": "Claude",
  "limit": 50
}
```

`entity` is required. `name` is accepted as an alias by the backend. `limit` is optional, defaults to 50, and is clamped to 1-200.

Return shape:

```json
{
  "ok": true,
  "mentions": [
    {
      "video_id": "abc123DEF45",
      "slug": "video-slug",
      "title": "Video title",
      "channel": "Example Channel",
      "source": "transcript",
      "timestamp": 12.5,
      "context": "Claude is the AI we'll use for this workflow.",
      "deep_link": "https://youtube.com/watch?v=abc123DEF45&t=12s"
    }
  ]
}
```

Entities are extracted automatically from each yoink's transcript using the configured Anthropic API key. Matching is case-insensitive and punctuation-tolerant through the normalized entity key. If no Anthropic key is set, entity extraction is skipped at yoink time; older yoinks without entity rows will not appear in results until re-yoinked.

Errors:

- `{ "ok": false, "error": "entity name (string) is required" }`
- `{ "ok": false, "error": "rate limit exceeded: max 60/minute" }`

Rate limit: 60 calls/minute per process.

## Rate limits and abuse mitigations

- `yoink_video`: 5 calls/minute per process.
- `yoink_playlist`: 5 calls/minute per process.
- `analyze_comments`: 10 calls/minute per process.
- `classify_hook`: 10 calls/minute per process.
- `list_recent_yoinks`: 60 calls/minute per process.
- `search_yoinks`: 30 calls/minute per process.
- `get_citation_map`: 60 calls/minute per process.
- `get_yoink_health`: 60 calls/minute per process.
- `find_mentions`: 60 calls/minute per process.
- Other read-only tools (`get_yoink_corpus`, `get_job_status`, `get_taxonomy`) are not rate-limited.

Rate-limit errors return friendly tool payloads, for example:

```json
{ "ok": false, "error": "rate limit exceeded: max 5/minute" }
```

HTTP JSON-RPC remains protected by `X-Yoink-Token`; stdio MCP relies on the spawning client trust boundary. All tools keep v1/v2 URL, slug, and job validation.

## Compatibility notes

The launch-facing compatibility matrix is near the top of this document. HTTP JSON-RPC is route-smoked and available for local clients that prefer HTTP, but it is experimental. The first launch story should emphasize stdio because that is the path Ryan will certify for Claude Desktop and Cursor.

## Client-side helpers needed from Claude Code

None for Sprint 4. `extension/lib/extract.js` does not need new helpers to surface MCP status. The setup page uses existing `STC.getToken()` plus authenticated fetch to `GET /mcp/v1/config`.

## Open questions

- Should v2.1 persist MCP call logs or agent activity indicators in the popup? Out of scope for Sprint 4.
