# Yoink v2 MCP server

Status: Sprint 4 backend implemented  
SDK: official Model Context Protocol Python SDK, `mcp==1.27.1`  
Transports: stdio and authenticated local HTTP JSON-RPC

## Overview

Yoink exposes its existing extraction, playlist, search, corpus, Comment Intelligence, and Hook Type functionality as MCP tools. The tool implementation lives in `yoink_mcp_tools.py`; stdio (`yoink_mcp.py`) and HTTP (`server.py` under `/mcp/v1`) both wrap the same registry so behavior stays consistent.

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

### HTTP JSON-RPC

HTTP MCP is served by the existing helper server:

```text
http://127.0.0.1:5179/mcp/v1
```

Auth: `X-Yoink-Token: <token>` required on every HTTP MCP request. `GET /mcp/v1/config` returns config metadata for setup.html; it is also token-gated.

HTTP endpoints:

- `POST /mcp/v1/initialize`
- `POST /mcp/v1/tools/list`
- `POST /mcp/v1/tools/call`
- `POST /mcp/v1` with JSON-RPC `method` set to `initialize`, `tools/list`, `tools/call`, or `ping`
- `GET /mcp/v1/sse` emits a lightweight `endpoint` event pointing clients at `/mcp/v1`

The HTTP wrapper returns MCP-style JSON-RPC envelopes. Tool call results include both text content and `structuredContent` for clients that support it.

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
  "folder": "C:\\Users\\Ryan\\Desktop\\Yoink\\Topic\\video-slug"
}
```

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

## Rate limits and abuse mitigations

- `yoink_video`: 5 calls/minute per process.
- `yoink_playlist`: 5 calls/minute per process.
- `analyze_comments`: 10 calls/minute per process.
- `classify_hook`: 10 calls/minute per process.
- Read-only tools are not rate-limited.

Rate-limit errors return friendly tool payloads, for example:

```json
{ "ok": false, "error": "rate limit exceeded: max 5/minute" }
```

HTTP MCP remains protected by `X-Yoink-Token`; stdio MCP relies on the spawning client trust boundary. All tools keep v1/v2 URL, slug, and job validation.

## Compatibility matrix

| Client | Transport | Status |
|---|---|---|
| Claude Desktop | stdio | Config documented; live client test pending. |
| ChatGPT Desktop | stdio | Config documented; live client test pending. |
| Cursor | stdio | Config documented; live client test pending. |
| Continue / Cline / generic MCP clients | stdio | Generic config documented; live client test pending. |
| HTTP MCP clients | HTTP JSON-RPC | Route smoke-tested; live third-party client test pending. |

## Client-side helpers needed from Claude Code

None for Sprint 4. `extension/lib/extract.js` does not need new helpers to surface MCP status. The setup page uses existing `STC.getToken()` plus authenticated fetch to `GET /mcp/v1/config`.

## Open questions

- Which desktop clients will Ryan personally certify before the v2 public note? The backend is protocol-shaped, but the config UX should only claim "tested" after live client runs.
- Should v2.1 persist MCP call logs or agent activity indicators in the popup? Out of scope for Sprint 4.

