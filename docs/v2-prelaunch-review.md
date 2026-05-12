# Yoink v2 pre-launch backend audit

Review branch: `codex/v2-prelaunch-audit`  
Base reviewed: `c6bfbe5 Merge codex/v2-sprint7 into v2-integration`  
Scope: `server.py`, `yoink_mcp_tools.py`, MCP HTTP handlers, `docs/v2-*.md`, `docs/security.md`, `docs/build-installer.md`, `build.ps1`  
Out of scope: `extension/*` except setup copy read for `docs/setup-copy-revisions.md`

## server.py

### serious - Single-video job records can persist multi-megabyte base64 clipboard payloads

- Reference: `server.py:2739`, `server.py:2750`, `server.py:2770`, `server.py:2644`, `server.py:3905`
- What I saw: `_record_single_extract_job()` stores `result.get("corpus_md_paste")` before `yoink_md`, then `_public_job()` returns the full `result` in every `/jobs` listing. `corpus_md_paste` is the multimodal clipboard payload with base64 screenshots.
- Why it matters: Sprint 7 made single-video jobs visible through `/jobs`. A few normal single-video yoinks can now bloat `%LOCALAPPDATA%\Yoink\jobs.json` and every popup `/jobs` response by many MB. That can make the last-yoink affordance slow or fragile, especially because `/jobs` is now a recovery/status endpoint.
- Recommended fix: For `kind="single"` job records, store text-only markdown in `combined_md_text` or omit `combined_md_text` from list responses and expose full corpus through `get_yoink_corpus` / file reads. If the UI only needs "last completed", keep a small result summary in `/jobs`.

### polish - Several output corpus writes still bypass the tmp+rename pattern

- Reference: `server.py:1868`, `server.py:1923`, `server.py:3236`, `server.py:4076`
- What I saw: primary video markdown, sidecar JSON, playlist `corpus.md`, and session `corpus.md` are written directly with `write_text()`. Settings, jobs, taxonomy, session metadata, and AI section rewrites use tmp+replace.
- Why it matters: A helper crash or Windows shutdown during a direct write can leave a partial `.md` or `.json`. The risk is low during normal operation, but these are the files users inspect and MCP tools read later.
- Recommended fix: Add a small `_atomic_write_text(path, text)` helper and use it for corpus/sidecar writes. Keep existing tmp+replace behavior for settings/jobs/taxonomy.

### no issues found - Token-gated mutating routes

- Reference: `server.py:3426`, `server.py:3440`, `server.py:3762`
- Notes: `/health`, `/ping`, and `/token` are the only unauthenticated GET paths. All other GET paths and every POST path require `X-Yoink-Token` before body parsing. This matches the v2 security model.

### no issues found - `/file` sandbox and MIME checks

- Reference: `server.py:2978`, `server.py:2989`, `server.py:3004`, `server.py:3011`
- Notes: `/file` requires an absolute path, rejects raw or resolved `..`, resolves symlinks before checking containment, rejects paths outside `DESKTOP_ROOT`, caps files at 10 MB, and validates image magic bytes against extension-derived MIME type.

### no issues found - Sprint 7 settings/jobs/taxonomy file handling

- Reference: `server.py:197`, `server.py:2650`, `server.py:2703`, `server.py:1228`
- Notes: `settings.json`, `jobs.json`, and `taxonomy.json` all use tmp+replace writes. Corrupt `jobs.json` and `taxonomy.json` are logged and replaced with a fresh structure instead of crashing startup.

## yoink_mcp_tools.py

### serious - MCP `yoink_video` bypasses Sprint 7 single-video job logging

- Reference: `yoink_mcp_tools.py:191`, `yoink_mcp_tools.py:212`
- What I saw: The HTTP `/extract` path now records `kind="single"` jobs, but MCP `yoink_video()` calls `_run_extraction()` directly and never calls `_record_single_extract_job()` on success or failure.
- Why it matters: v2's launch story includes "any AI agent". Agent-triggered single-video yoinks will not appear in `/jobs`, so the popup's recent/last-yoink affordance can disagree with MCP activity. It also means persisted `jobs.json` is incomplete as an audit trail.
- Recommended fix: Mirror `/extract`: capture `started_at`, call `_record_single_extract_job()` after success/failure, and keep the MCP return shape unchanged.

### no issues found - MCP tool input validation and rate limits

- Reference: `yoink_mcp_tools.py:79`, `yoink_mcp_tools.py:147`, `yoink_mcp_tools.py:519`, `yoink_mcp_tools.py:407`
- Notes: URL tools canonicalize through backend validators, slugs/job IDs use strict regex paths, list/search limits are bounded, and rate limits cover extraction and Anthropic-cost tools.

### no issues found - Anthropic key handling through MCP tools

- Reference: `yoink_mcp_tools.py:341`, `yoink_mcp_tools.py:370`, `server.py:1076`, `server.py:1145`
- Notes: MCP tools never receive or return the key. They call backend analysis helpers, which mark 401 keys invalid and redact API-key text from short errors.

## MCP HTTP transport

### serious - HTTP/SSE launch claim is stronger than the implementation

- Reference: `server.py:3606`, `server.py:3616`, `docs/v2-mcp.md:54`, `docs/v2-api.md:652`
- What I saw: `GET /mcp/v1/sse` emits one `endpoint` event and immediately closes. The real HTTP support is a JSON-RPC POST helper under `/mcp/v1`; it is not a full stateful MCP SSE or Streamable HTTP implementation.
- Why it matters: The docs and setup page may lead users to expect generic HTTP/SSE MCP clients to work. Strict clients that expect a long-lived SSE session will fail, even though stdio and direct JSON-RPC POST work.
- Recommended fix: Before launch, either downgrade the claim to "experimental local HTTP JSON-RPC helper" or implement a spec-complete HTTP/SSE transport. Keep stdio as the officially tested path.

### polish - JSON-RPC notification handling is minimal

- Reference: `server.py:3637`
- What I saw: `notifications/initialized` returns HTTP 202 with `{ "ok": true }` rather than silently no-content or returning a JSON-RPC-shaped response.
- Why it matters: Most clients tolerate this, but strict MCP-over-HTTP clients may treat the non-MCP envelope as protocol drift.
- Recommended fix: Align this path with the MCP HTTP transport semantics chosen above. If keeping the JSON-RPC helper, document the 202 behavior; if implementing full MCP HTTP, follow the SDK/spec behavior exactly.

## docs/v2-*.md

### polish - API docs do not warn that single-job `combined_md_text` may be heavy

- Reference: `docs/v2-api.md:949`, `docs/v2-api.md:954`, `server.py:2750`
- What I saw: The contract documents single-video `result.combined_md_text`, but does not say whether it is text-only, multimodal, capped, or omitted from list responses.
- Why it matters: The current implementation stores the multimodal paste corpus, which is a surprising and expensive behavior for a job-listing API.
- Recommended fix: Decide and document the intended single-job result size. My recommendation: text-only or summary-only in `/jobs`; full corpus remains available through the saved file/MCP corpus tool.

### no issues found - Playlist job contract matches the main backend path

- Reference: `docs/v2-api.md:459`, `docs/v2-api.md:883`, `server.py:3863`, `server.py:3873`, `server.py:3905`
- Notes: Preview/start/status/cancel/list shapes match the code, including `session_folder`, `kind`, `?kind=`, restart persistence, cap, and continue-on-per-video-failure semantics.

### no issues found - Comment Intelligence and Hook Type contracts match keyring-era behavior

- Reference: `docs/v2-comment-intelligence.md:55`, `docs/v2-hook-type.md:38`, `server.py:224`, `server.py:309`
- Notes: The v2-specific AI docs correctly document keyring storage, destructive 401 clear, skip behavior, and taxonomy aggregation.

## security docs

### serious - `docs/security.md` is stale after Sprint 7

- Reference: `docs/security.md:14`, `docs/security.md:15`, `docs/security.md:34`, `docs/security.md:49`
- What I saw: The security doc still says v2 will pin the published extension ID, says dependency SHA constants ship empty, and lists only older token-gated endpoint groups. It does not cover `/settings`, `/jobs`, `/file`, `/mcp/v1`, keyring storage, jobs persistence, or taxonomy storage.
- Why it matters: This is the launch-facing threat model. It currently understates new endpoints and overstates planned hardening. If users read it, they get the wrong security story.
- Recommended fix: Update `docs/security.md` before public launch. Either actually pin the Chrome Web Store extension ID for `/token`/CORS or revise the doc to say v2 still trusts installed browser extensions. Also document keyring, `/file`, `/mcp/v1`, `jobs.json`, and `taxonomy.json`.

## build.ps1 and build docs

### polish - Hash-lock comments/docs still say launch hashes are empty

- Reference: `build.ps1:66`, `build.ps1:70`, `build.ps1:78`, `build.ps1:79`, `build.ps1:80`, `docs/build-installer.md:84`, `docs/build-installer.md:91`, `docs/build-installer.md:106`
- What I saw: `build.ps1` now has locked SHA256 values for Python, ffmpeg, and get-pip, but the surrounding comments and build guide still say this is a TODO and should not ship while empty.
- Why it matters: Not a runtime bug, but it creates packaging confusion at exactly the launch gate.
- Recommended fix: Reword the comments and docs to say direct-download hashes are locked as of this version, and describe how to update them when versions change.

### no issues found - Installer version pins are centralized

- Reference: `build.ps1:40`, `build.ps1:50`, `build.ps1:54`, `build.ps1:57`, `build.ps1:60`
- Notes: Python, ffmpeg, yt-dlp, Pillow, MCP SDK, and keyring versions are now explicit constants. Build failure on direct-download hash mismatch is wired through `Confirm-Hash`.

## Launch confidence

No blocker-class backend security issue showed up. I would ship after fixing or explicitly accepting the two serious product-quality risks: `/jobs` payload bloat from single-video base64 corpora and overclaiming HTTP/SSE MCP compatibility.
