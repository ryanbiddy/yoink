# Changelog

All notable changes to Yoink are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). This project follows [semantic versioning](https://semver.org/spec/v2.0.0.html).

## [2.0.0] — TBD (launch pending)

The "YouTube layer for any AI agent" release. Three adoption funnels: Chrome extension for creators, MCP server for developers and agents, and the Yoink Operator Skill for clients that support portable skills or system prompts.

### Added

- **MCP server** with 13 tools (`yoink_video`, `yoink_playlist`, `get_job_status`, `cancel_job`, `list_recent_yoinks`, `search_yoinks`, `get_yoink_corpus`, `analyze_comments`, `classify_hook`, `get_taxonomy`, `get_citation_map`, `get_yoink_health`, `find_mentions`). Stdio transport officially tested with Claude Desktop and Cursor. Local HTTP JSON-RPC transport available, marked experimental.
- **Library Index (SQLite FTS5).** `%LOCALAPPDATA%\Yoink\index.db` replaces scan-based search/recent/get-taxonomy code paths where indexed consumers need fast library access. First boot backfills existing corpora; subsequent yoinks update incrementally.
- **Migration framework.** `schema_version` table plus numbered `migrations/NNNN_*.sql` scripts for future schema changes.
- **Citation map.** Pre-computed at extraction/index time; new MCP tool `get_citation_map(slug)` returns transcript and screenshot citations with YouTube deep links.
- **Health score.** Sidecar/index health snapshot for transcript, screenshots, comments, Hook Type, and Comment Intelligence; new MCP tool `get_yoink_health(slug)` returns the dict used by popup Recent health icons.
- **Entity extraction from transcripts.** Optional BYO-Anthropic-key worker extracts people, tools, products, topics, companies, and other named entities from new yoinks when AI features are enabled.
- **Entity graph tables.** Migration 0002 adds `entities` and `entity_mentions` tables to `index.db`, keyed by normalized entity name/type and linked back to `yoinks.video_id`.
- **MCP `find_mentions(entity, limit)` tool.** Agents can ask where a person/tool/product/topic/company appears across the local corpus; results include title, channel, timestamp, context, and YouTube deep link.
- **Yoink Operator Skill** - drop-in `SKILL.md` (agentskills.io open standard) covering identity, default chat, hook-autopsy tweet mode, and citation discipline. Distributed via Claude Code plugin, OpenClaw ClawHub, Hermes URL install, and copyable system prompt for everywhere else.
- **Playlist Mode.** Paste a YouTube playlist URL, yoink up to 10 videos per job. Async job system with live progress, cancellation, and partial-failure tolerance. Combined corpus (text-only) to clipboard; per-video corpora with screenshots on disk.
- **Comment Intelligence.** Optional Anthropic-powered analysis of comment threads. Three structured sections appended per video: top themes, mentioned products/tools, notable disagreements.
- **Hook Type classification.** Optional Anthropic-powered classification of each video's opening style across 9 categories: curiosity gap, question, contrarian, story open, promise/list, demo, authority, stakes, other.
- **Smart Screenshot Picker.** Opt-in post-extraction grid for selecting which screenshots make the clipboard.
- **Setup page** (`setup.html`) with BYO Anthropic API key flow, feature toggles, and MCP config snippet generator for Claude Desktop, Cursor, and generic stdio clients.
- **Anthropic API key encryption.** Keys stored via Windows Credential Manager (`keyring` library), never plaintext. Migrates any plaintext anthropic_key from settings.json into Windows Credential Manager on first run.
- **Job persistence across helper restarts.** `/jobs` state survives helper restart via `%LOCALAPPDATA%\Yoink\index.db`. In-flight jobs are marked failed with `error="server restarted"`; users restart them manually.
- **Hook Type taxonomy capture.** Every successful classification upserts into the local `taxonomy` table in `index.db` (deduplicated by video ID) for v2.0 dataset queries via `GET /taxonomy` and the `get_taxonomy` MCP tool.
- **jobs.json / taxonomy.json migration.** Existing file-based persistence is imported into `index.db` on first boot and the old files are renamed with `.migrated` suffixes.
- **Lazy entity backfill policy.** Existing yoinks do not receive retroactive entity rows; re-yoink an older video to populate entities for it.
- **Entity graph scope guard.** Mention sentiment, temporal trends, co-occurrence, and cross-creator citation graph are deferred to Sprint 16.5+.
- **Job recovery on popup reopen.** If you close the popup mid-playlist, reopening it resumes from the running job state via `GET /jobs`.
- **Polling resilience.** Helper-disconnect banner appears after 5 seconds of failed polls. After 30 seconds, the setup guide auto-opens in a new tab (rate-limited to once per 5 minutes across popup sessions). Recovery is automatic when the helper comes back.
- **Active-playlist pill.** When a playlist is running and the user switches to single-video mode, a persistent pill shows playlist progress. Click to return to the playlist view.
- **"Last yoink completed" affordance.** Popup boot surfaces recently completed yoinks (within 30 minutes) with an Open Folder button. Works for both single-video and playlist jobs.
- **`GET /jobs` API** with `?kind=playlist|single` filtering and `updated_at` desc sorting.
- **`/file` endpoint** for sandboxed thumbnail serving to the popup.
- **MCP `yoink_video` job logging.** Agent-triggered single-video yoinks now appear in `/jobs` and the recent-yoinks surface, matching the extension flow.
- **`docs/security.md`** rewritten to cover v2 reality: keyring, token-gated endpoints, `/file` sandbox, MCP HTTP, `index.db` persistence, and the v2 threat model.
- **`docs/v2-smoke-test.md`** - 91-checkpoint pre-launch smoke checklist.
- **Banner-link accessibility.** Disconnect-banner setup link announces "Opens setup guide in a new tab" to screen readers.

### Changed

- **Anthropic API key storage moved from plaintext `settings.json` to Windows Credential Manager.** Existing keys are migrated automatically on first v2.0 startup.
- **`get_yoink_corpus` MCP tool** now returns `video_id` and `video_url` alongside `corpus_md` and `folder` for downstream tool composition.
- **HTTP MCP transport reframed as experimental.** Stdio remains the officially tested and supported transport. Setup page and docs updated accordingly.
- **Single-video job records in `/jobs`** no longer persist the multimodal clipboard payload (`corpus_md_paste`), preventing job-table bloat. Full corpus remains available via `get_yoink_corpus` and the on-disk session folder. Legacy bloated records are stripped during migration.
- **Corpus and sidecar writes** now use atomic tmp-then-rename pattern (already used for settings, jobs, taxonomy), eliminating partial-file risk on crash.
- **JSON-RPC `notifications/initialized`** now returns 202 with no body, aligning with HTTP semantics for fire-and-forget notifications.
- **`build.ps1`** SHA256 hash constants are locked. Comments and build-installer.md narrative updated to reflect locked state.

### Security

- **`docs/security.md` rewritten** to accurately describe the v2 threat model. Includes keyring-backed key storage, complete token-gated endpoint inventory, `/file` sandbox semantics, and persistence file locations.
- Chrome Web Store extension ID pinning deferred until the published listing ID is stable (planned for v2.1).

### Documentation

- New: `docs/v2-api.md`, `docs/v2-mcp.md`, `docs/v2-comment-intelligence.md`, `docs/v2-hook-type.md`, `docs/v2-smoke-test.md`, `docs/v2-prelaunch-review.md`, `docs/setup-copy-revisions.md`, `docs/backlog-review-notes.md`, `CHANGELOG.md`.
- Updated: `README.md`, `docs/security.md`, `docs/build-installer.md`, `docs/store-listing.md`, `BACKLOG.md`.

## [1.0.0] — 2026-04-XX (originally shipped before v2 development cycle; baseline)

The first public release. Single-video extraction with creator-research-grade output, local-first, no accounts, fully open source.

### Added

- **One-click "Yoink" button** under every YouTube video.
- **Right-click context menu** to yoink any YouTube thumbnail without opening the video.
- **Full timestamped transcript** with chapter awareness.
- **Timestamped screenshots** throughout the video.
- **Top 50 comments** with author and like count.
- **Full video metadata** (views, likes, tags, description, upload date).
- **Thumbnail image** included in the corpus.
- **Channel context** — subscriber count + recent videos from the same channel.
- **Auto topic-classification** into folders on disk via `topics.json` keyword rules.
- **Built-in prompt library** (11 starter prompts: "Decode the hook," "Outline the structure," etc.).
- **Two destination buttons** — Send to Claude, Send to ChatGPT.
- **Windows installer** (Inno Setup, Python embeddable, ~120 MB) with auto-start on login and clean uninstall.
- **Multimodal clipboard format** — text + up to 12 base64-inlined screenshots, fits in Claude/ChatGPT in one paste.
- **Research sessions** for multi-video corpora (v1.0 manual-session flow; v2.0 introduces Playlist Mode).
- **Local helper server** on `127.0.0.1:5179` with token-based extension auth.
- **Master `_all-yoinks-index.md`** at the Desktop\Yoink root tracking every yoink.

### Notes

- v1.0 was Windows-only. Mac installer is on the v1.1 roadmap.
- v1.0 shipped without telemetry; opt-in install-success telemetry is on the v1.1 roadmap.
