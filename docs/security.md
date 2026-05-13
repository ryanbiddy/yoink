# Yoink security model

Status: launch-facing for v2.1 pre-launch

Yoink is local-first software with two pieces:

- A browser extension that injects UI on YouTube and talks to a local helper.
- A Python helper bound to `127.0.0.1:5179` that downloads public YouTube data, writes local corpus files, serves local thumbnails, and exposes optional MCP tools.

The helper never binds to a public network interface. The main security boundary is therefore local-machine trust: browser pages should not be able to drive the helper without the extension token, but software or browser extensions already running with the user's privileges are treated as trusted local code.

## Threat model

| Threat | Defended? | Notes |
|---|---:|---|
| Malicious webpage tries to call `127.0.0.1:5179` | Yes | Token-gated endpoints require `X-Yoink-Token`; `/token` requires a custom `X-Yoink-Client` header and browser CORS/PNA preflight blocks normal webpages from setting it cross-origin. |
| Malicious webpage probes whether Yoink is running | Not treated as secret | `/health` and `/ping` are public liveness probes. They reveal only `{ok:true, version}`. |
| Local malware reads files or calls localhost | No | Malware already running as the user can read local files, call local ports, and modify output. Yoink does not try to sandbox against same-user malware. |
| Another installed browser extension calls `/token` | Not fully | v2 accepts `chrome-extension://*` / `moz-extension://*` origins so Chromium forks and dev installs work. Published Chrome Web Store extension ID pinning is deferred until the final ID is known and stable. |
| Network attacker | Mostly not applicable | The helper listens only on `127.0.0.1`, not LAN/public interfaces. |
| Anthropic API key disclosure through settings | Mitigated | The key is stored in the OS credential store via `keyring`, not in `settings.json`, and is never returned by `GET /settings`. |
| Dependency compromise | Partially | Direct downloads are SHA256-checked in `build.ps1`; pip packages are version-pinned but not hash-locked. |

## Public endpoints

These do not require `X-Yoink-Token`:

- `GET /health`
- `GET /ping`
- `GET /token`

`/health` and `/ping` are intentionally public because the extension, setup page, and YouTube button need to detect whether the helper is running before auth/token refresh completes.

`/token` returns the per-install helper token and is guarded by:

- `X-Yoink-Client: yoink-extension`
- `Origin` that is empty, `chrome-extension://*`, or `moz-extension://*`
- A server-wide 10 requests/minute rate limit

The empty-Origin allowance is deliberate. Some Chromium service-worker fetches observed during Comet testing omit `Origin`. The custom-header+CORS preflight gate is the load-bearing browser CSRF defense.

## Token-gated endpoints

All other helper endpoints require `X-Yoink-Token`:

- Single-video extraction: `POST /extract`
- Playlist jobs: `POST /playlist/preview`, `POST /playlist/start`, `GET /jobs`, `GET /jobs/<id>`, `POST /jobs/<id>/cancel`
- Sessions: `POST /session/start`, `POST /session/add`, `POST /session/close`, `POST /session/cancel`, `POST /session/open`, `GET /session/list`, `GET /session/active`
- Settings, AI key testing, and local cost estimates: `GET /settings`, `GET /settings/pricing`, `POST /settings`, `POST /settings/test-key`
- Local files, folders, and hook taxonomy: `GET /file`, `GET /taxonomy`, `GET /recent`, `GET /open-folder`, `GET /open-index`, `GET /open-prompts`
- MCP HTTP JSON-RPC helper: `GET /mcp/v1/config`, `GET /mcp/v1/sse`, `POST /mcp/v1`, `POST /mcp/v1/initialize`, `POST /mcp/v1/tools/list`, `POST /mcp/v1/tools/call`

The token is accepted only in the `X-Yoink-Token` header. Query-string token auth is intentionally unsupported so tokens do not leak into browser history, server logs, or HTTP debug tools that capture URLs.

## CORS and Private Network Access

For allowed origins, the helper sends:

```http
Access-Control-Allow-Origin: <allowed origin>
Access-Control-Allow-Methods: GET, POST, OPTIONS
Access-Control-Allow-Headers: Content-Type, X-Yoink-Token, X-Yoink-Client
Access-Control-Allow-Private-Network: true
```

Allowed web origins are YouTube pages used by the content script:

- `https://www.youtube.com`
- `https://m.youtube.com`
- `https://youtube.com`

Extension origins are accepted as `chrome-extension://*`. This is broad by design for v2 dev/fork compatibility; extension ID pinning should be revisited after Chrome Web Store publication.

`Access-Control-Allow-Private-Network: true` is required by Chromium's Private Network Access rules when a public HTTPS origin preflights a request to loopback.

## POST hardening

All POST routes are authenticated before reading the request body. After auth:

- `Content-Type: application/json` is required.
- `Content-Length` is capped at 64 KB.
- Top-level JSON must be an object.

Protocol validation failures return `4xx` JSON errors. Handled application failures generally follow the existing Yoink pattern of HTTP 200 with `{ "ok": false, "error": "..." }`.

## URL and identifier validation

- Video URLs are parsed with `urllib.parse.urlparse`, checked against an explicit YouTube host allowlist, and canonicalized to `https://www.youtube.com/watch?v=<id>`.
- Video IDs must match ASCII `^[A-Za-z0-9_-]{6,}$`.
- Playlist IDs must match ASCII `^[A-Za-z0-9_-]{2,}$`; playlist processing is capped at 10 videos.
- Session IDs must match `^[A-Za-z0-9_-]{1,64}$`.
- Job IDs must match `^job_[A-Za-z0-9_-]{1,96}$`.
- Folder names come from `slugify()`, which emits ASCII path segments and guards Windows reserved device names.

## `/file` sandbox

`GET /file?path=<absolute-path>` serves screenshot thumbnails to the popup. It is token-gated and intentionally narrow:

- The path must be absolute.
- Raw and resolved paths containing a `..` segment are rejected.
- Symlinks are resolved before sandbox checks.
- The resolved file must be under the Yoink output root (`Desktop\Yoink` via Windows known-folder resolution).
- The path must exist and be a regular file.
- Files larger than 10 MB are rejected.
- Only `.png`, `.jpg`, `.jpeg`, and `.webp` are served.
- Magic bytes must match the extension-derived MIME type.
- Responses use `Cache-Control: private, max-age=300`.

## Anthropic API key storage

Comment Intelligence and Hook Type are optional BYO-key features. Normal Yoink extraction works without an Anthropic key.

Starting in v2.1, the key is stored through Python `keyring`:

- Service: `Yoink`
- Username: `anthropic_key`
- Windows backend: Windows Credential Manager

`settings.json` stores only public booleans and key status flags. `GET /settings` returns `anthropic_key_set: true|false`, never the key itself.

Migration behavior:

1. On helper startup, if legacy `%LOCALAPPDATA%\Yoink\settings.json` contains plaintext `anthropic_key`, Yoink attempts to move it into keyring.
2. On successful migration, the plaintext field is removed from `settings.json`.
3. If keyring is unavailable, migration is skipped and logged; Yoink does not silently create a new plaintext fallback.

Anthropic 401 responses destructively clear the saved key from keyring and mark `anthropic_key_set` false until the user saves a key again.

## Persistence files

Installed Windows builds store helper state under `%LOCALAPPDATA%\Yoink\`:

- `settings.json` - feature toggles and public key status only; no API key.
- `jobs.json` - recent playlist and single-video job records. In-flight jobs from a previous helper process are restored as failed with `error="server restarted"`.
- `taxonomy.json` - local Hook Type classification records, deduped by `video_id`.
- `token.txt` - random helper token generated with `secrets.token_urlsafe(32)`.
- `server.pid` - best-effort helper process id for Stop Yoink Server.
- `server.log` - local diagnostic log.

Corpus, sidecar, settings, jobs, and taxonomy writes use temp-file-and-replace patterns. Corrupt `jobs.json` or `taxonomy.json` is logged and replaced with a fresh structure rather than crashing the helper.

## MCP security model

Yoink supports MCP over stdio and an experimental local HTTP JSON-RPC helper.

- Stdio MCP is the officially supported launch transport. The MCP client launches `yoink_mcp.py` as a local subprocess, so the trust boundary is the spawning local client.
- HTTP JSON-RPC under `/mcp/v1` is token-gated with `X-Yoink-Token`. It supports direct JSON-RPC POST calls but is not a spec-complete SSE or Streamable HTTP MCP implementation.

MCP tools reuse the same backend validation for URLs, slugs, job IDs, file paths, and Anthropic key behavior.

## Dependency and installer integrity

`build.ps1` pins runtime package versions and verifies SHA256 for directly downloaded artifacts:

- Python embeddable
- ffmpeg
- `get-pip.py`

Pip-installed packages (`yt-dlp`, `Pillow`, `mcp`, `keyring`) are version-pinned but not hash-locked yet. Full pip `--require-hashes` is a future hardening item.

The installer is unsigned for launch unless a code-signing certificate is added. Windows SmartScreen warnings are expected for unsigned builds.

## What Yoink does not collect

Yoink has no Yoink cloud service, account system, telemetry endpoint, or hosted analytics. Extraction and files stay on the user's machine.

Optional AI features send selected comment/hook context to Anthropic only when the user provides an API key and enables those features. YouTube downloads still contact YouTube, and yt-dlp may contact YouTube-owned endpoints as part of extraction.

## Reporting

If you find a vulnerability, please open a private GitHub Security Advisory or email the maintainer directly. Do not open a public issue with reproduction details until a fix is shipped.
