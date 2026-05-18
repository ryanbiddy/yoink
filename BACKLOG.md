# Yoink — Backlog

This is the canonical list of what's shipped, what's planned, and what's been ruled out. Every unshipped entry has a destination, rationale, and trigger condition.

## Format
- **Idea:** one line
- **Destination:** v1.1 / v2.1 / v2.5 / v3 / v4 / never / undecided
- **Rationale:** why it's not currently shipped
- **Trigger:** what has to happen for this to move forward

---

## Shipped

### v1.0 — built (awaiting public launch alongside v2.0)

- One-click "Yoink" button under every YouTube watch page
- Right-click any thumbnail to yoink without opening the video
- Full timestamped transcript with chapter awareness
- Timestamped screenshots throughout the video
- Top 50 comments with author and like count
- Full video metadata (views, likes, tags, description, upload date)
- Thumbnail image
- Channel context (subscriber count, recent videos)
- Auto topic-classification into folders on disk (`topics.json` ships with installer; read-only post-install)
- Built-in prompt library (11 starter prompts, read-only in v1)
- Two destination buttons: Send to Claude, Send to ChatGPT
- Local-first, no cloud, no accounts, fully open source
- Windows installer (Inno Setup + Python embeddable, ~120 MB)
- Auto-start on Windows login (HKCU Run key); cleanly removed on uninstall
- Start Menu group with Server start, Stop, Folder, Uninstall entries
- `/open-prompts`, `/open-folder`, `/open-index` HTTP endpoints

### v2.0 — built (awaiting smoke test then public launch)

**Core v2 product**
- **MCP server** with 9 tools (`yoink_video`, `yoink_playlist`, `get_job_status`, `cancel_job`, `list_recent_yoinks`, `search_yoinks`, `get_yoink_corpus`, `analyze_comments`, `classify_hook`). Stdio transport officially tested with Claude Desktop and Cursor; community-reported support for ChatGPT Desktop, Continue, Cline; generic stdio fallback for any MCP-compatible client.
- **Playlist Mode** — yoink up to 10 videos per job, async with live progress, cancel mid-flight, partial-failure tolerance, combined corpus to clipboard (text-only)
- **Comment Intelligence** — Anthropic-powered theme clustering, mentioned-product extraction, disagreement flagging (BYO key)
- **Hook Type classification** — 9-category per-video classifier with brief explanation
- **Smart Screenshot Picker** — opt-in post-extraction grid for selecting which screenshots make the clipboard
- **Setup page** — BYO Anthropic key flow (test, save, clear), feature toggles, MCP config snippet generator, deep-link from popup

**v2 platform infrastructure**
- `/jobs`, `/jobs/<id>`, `/jobs/<id>/cancel`, `/jobs?kind=playlist|single` async job API
- `/file` endpoint — sandboxed thumbnail serving (absolute-path required, `..` rejected, symlinks resolved, 10MB cap, magic-byte validation)
- `session_folder` field on every job for universal Open Folder
- Continue-on-failure policy (fails only when zero videos succeed)
- `_corpus_update_lock` prevents CI/Hook/comments stomping each other's `.md` writes
- `MCP yoink_video` records single-video jobs to `/jobs` (matches `/extract` flow for consistency across both adoption funnels)
- `get_yoink_corpus` returns `video_id` and `video_url` for downstream tool composition
- YouTube Shorts support (`/shorts/` URLs normalize correctly, UI and extraction handle them seamlessly)

**v2 reliability + UX polish**
- Job persistence across helper restarts via `%LOCALAPPDATA%\Yoink\index.db` (in-flight jobs marked failed on restart with `error="server restarted"`)
- Single-video job records persist text-safe content only (no base64 clipboard payload bloat)
- Atomic tmp-then-rename writes for corpus, sidecar, settings, and migration files; jobs/taxonomy now persist in SQLite
- Job recovery on popup reopen via `GET /jobs`
- Polling resilience: disconnect banner after 5s, auto-open setup tab after 30s (rate-limited to once per 5 minutes across popup sessions), slow-poll cadence with auto-recovery
- Active-playlist pill (mode-switch override; click to return to playlist view)
- "Last yoink completed" affordance for both playlist and single-video jobs
- Single-video failed-state affordance to surface dropped/failed single-video jobs
- Cost estimator for AI features in setup.html based on Anthropic per-token pricing
- 401 destructive key clearing
- Banner-link aria-label for accessibility

**v2 security**
- API key encryption via Windows Credential Manager (`keyring`), automatic migration from plaintext settings.json on first v2.0 startup
- `docs/security.md` rewritten to cover v2 threat model (keyring, all token-gated endpoints, `/file` sandbox, MCP HTTP, persistence files)
- HTTP MCP transport marked experimental (stdio is the officially supported path)

**v2 backend hardening (Hook taxonomy capture)**
- Every successful Hook Type classification upserts into `%LOCALAPPDATA%\Yoink\index.db` with dedupe by `video_id`. HTTP and MCP query surfaces shipped in v2.0.

**Strategic positioning shift**
- v2 ships as "the YouTube layer for any AI agent" with two adoption funnels (Chrome extension for creators, MCP for developers). Originally scoped v2 was Channel Decoder + Niche Corpus + CI + Hook taxonomy; the actual shipped v2 is Playlist + CI + Hook Type + Picker + MCP.

---

## v1.1 punch list (post-launch, 2-4 weeks after v1+v2 GA)

Tier-1 small wins first (Codex's review reordering: low-risk, high-leverage). Larger items (Mac, system tray) sit below.

### Right-click "Yoink this video" context menu (video links)
- **Destination:** v1.1
- **Rationale:** Manifest already has `contextMenus` permission. Right-clicking any link to a YouTube video shouldn't require opening the video first. v1 has the right-click-thumbnail flow; this extends it to any YouTube video URL anywhere on the web. Catches users who never open the popup.
- **Trigger:** v1.1 cycle

### Keyboard shortcut (Ctrl+Shift+Y)
- **Destination:** v1.1
- **Rationale:** Six-line `manifest.commands` add. Power-user catnip. Triggers Yoink on the active YouTube tab. Lower backend risk than tray app or Mac installer — quick visible v1.1 win.
- **Trigger:** v1.1 cycle

### Diagnostic export button
- **Destination:** v1.1 (moved up per Codex's review — cheap, more valuable with v2 surface area)
- **Rationale:** In setup.html. "Copy diagnostic info" bundles version + platform + (sanitized) settings + keyring state (present/absent only) + recent error log + MCP config status + AI feature toggle state → clipboard. Cuts support friction. With v2's settings/keyring/jobs/MCP/feature toggles, the value of a one-click diagnostic dump went up.
- **Trigger:** v1.1 cycle

### Crash report opt-in
- **Destination:** v1.1
- **Rationale:** When `server.py` hits an unhandled exception, write a local crash file + offer "send to ryanbiddy via mailto" link. No server infra needed. Real ops signal as install base grows.
- **Trigger:** v1.1 cycle

### Opt-in install-success telemetry
- **Destination:** v1.1
- **Rationale:** Single binary signal — "did install complete." Most important data point for activation funnel diagnosis. Anonymous, opt-in, no per-user data. Foundation for the broader v1.2 telemetry plans.
- **Trigger:** v1.1 cycle

### "What's new" toast on extension version change
- **Destination:** v1.1
- **Rationale:** First popup open after extension update shows a one-liner. `chrome.storage` check on previous-version vs current-version. Particularly valuable for the v1.0 → v1.1 → v2.0 transitions if those land separately.
- **Trigger:** v1.1 cycle

### Auto-update check for installer
- **Destination:** v1.1
- **Rationale:** Extension polls GitHub releases monthly, surfaces "update available" pill in popup. Closes the "is my Yoink current" gap that otherwise needs the tray app to surface. Single fetch, tiny UI.
- **Trigger:** v1.1 cycle

### Editable prompts library
- **Destination:** v1.1
- **Rationale:** v1 ships with 11 read-only starter prompts in `extension/prompts.json`. Installed users have no `extension/` folder, so the original "Edit prompts" link was removed. Need an inline popup editor that persists user prompts via `chrome.storage.local` so they're portable across installs and editable without touching the filesystem.
- **Trigger:** v1.1 cycle

### Editable topics.json
- **Destination:** v1.1
- **Storage decision required (Codex flagged):** edits must persist outside the install folder so they survive reinstall. Three options:
  - (a) User data file at `%APPDATA%\Yoink\topics.json` with the installer's copy as fallback default
  - (b) Server-managed setting (POST /topics, stored in settings.json)
  - (c) Extension-side storage (`chrome.storage.sync` for cross-device sync)
  Recommend (a) for v1.1 — file-level edits match the current power-user mental model; the popup adds a UI editor later if usage suggests demand.
- **Trigger:** v1.1 cycle

### Open .md in default markdown editor
- **Destination:** v1.1
- **Rationale:** Currently Open Folder always opens Explorer. Adding "Open .md" that opens in the user's default markdown editor adds value for Obsidian/Typora workflows. One-line addition to existing `/open-folder` pattern.
- **Trigger:** v1.1 cycle

### Browser support test matrix beyond Chrome
- **Destination:** v1.1
- **Rationale:** Chrome works; Edge works (manifest V3). Comet (Perplexity's Chromium fork) bit Yoink in v1 — fixed with fallback patterns but other Chromium forks untested. Untested: Brave, Opera GX, Vivaldi, Arc.
- **Trigger:** v1.1 cycle (just expand the testing matrix in `docs/build-installer.md` and run smoke test on each)

### Installer update/migration smoke matrix
- **Destination:** v1.1 (new entry per Codex's review)
- **Rationale:** Keyring migration, legacy jobs/taxonomy import into `index.db`, Start Menu entries, and auto-start behavior need testing across three install paths: upgrade-from-v1, uninstall-then-reinstall, and clean-install. Not a feature — a test discipline. Captured here so it doesn't get skipped.
- **Trigger:** v1.1 cycle

### Mac installer
- **Destination:** v1.1 (larger item, sequenced after the small wins above)
- **Rationale:** doubles QA load; ship Windows first
- **Trigger:** v1+v2 launch ships and runs clean for 2 weeks
- **Notes:** Resend waitlist on landing page is already collecting interest

### System tray status app (treat as headline mini-sprint, not punch-list polish)
- **Destination:** v1.1 (sequenced last in v1.1 — larger than other items)
- **What:** Persistent system tray icon (bottom-right of Windows near clock) showing live server status. Right-click menu shows: server status (green/red), recent yoinks (last 5 with click-to-open), in-progress yoinks with progress, "Open Yoink folder," "Stop server."
- **Rationale:** Per Codex's review — this is NOT a small punch-list item. It creates a new packaged runtime surface, tray icon lifecycle, Windows notification quirks, and future Mac divergence. Real new component with new failure modes (tray icon visibility, AV conflicts, Windows icon caching). Sequence it as a dedicated mini-sprint with its own smoke test, not as a polish bullet.
- **Implementation notes:** Use pystray (Python tray icon library) plus a small background thread that polls server state. Server already exposes `/health` and `/recent` endpoints — tray would consume them.
- **Trigger:** Ship after other v1.1 small wins land and stabilize

---

## v2.1 punch list (post-v2 polish, post-launch)

### Semantic embeddings on top of FTS5
- **Destination:** v2.1
- **Rationale:** Sprint 15 ships keyword FTS5; embeddings (sentence-transformers or hosted) ride on top of the same `yoinks` table as a sibling vector index.
- **Trigger:** traction signal + user feedback that keyword search is insufficient.

### Cross-corpus citation linking
- **Destination:** Sprint 16.5
- **Rationale:** Given citations and entity graph (Sprint 16), the next step is "this point also appears in video Y at [12:34]."
- **Trigger:** Sprint 16 ships and find_mentions sees real use.

### Entity sentiment scoring
- **Destination:** Sprint 16.5 / v2.1
- **Rationale:** Sprint 16 ships entity extraction without sentiment. Adding sentiment per mention enables "how do creators talk about X?" queries.
- **Trigger:** Sprint 16 ships and find_mentions sees real use.

### Temporal trends + co-occurrence on entities
- **Destination:** Sprint 16.5 / v2.1
- **Rationale:** With entity_mentions accumulating, surface trend curves and co-occurrence patterns. New tools: entity_trend(entity), entity_cooccurrence(entity_a, entity_b).
- **Trigger:** ≥100 entity mentions in the average user's index.

### Cross-creator citation graph
- **Destination:** v2.5 (originally bundled with A2 in strategy brief; pulled out per build-chat scoping)
- **Rationale:** Identifying when creators reference each other requires creator-disambiguation logic that's its own non-trivial system.
- **Trigger:** v2.5 cycle, after entity graph has accumulated meaningful data.

### User-correctable entity disambiguation
- **Destination:** Sprint 17.5 (after A3's correction-UI pattern is established)
- **Rationale:** Automatic clustering will miss edge cases ("Claude" the AI vs "Claude" the person; "React" the framework vs the verb). A3's hook-type correction affordance is the template for entity corrections.
- **Trigger:** Sprint 17 ships and exposes the correction-UI pattern.

### Taxonomy retention / export (UX surface)
- **Destination:** v2.1 (new entry per Codex's review)
- **Rationale:** HTTP `/taxonomy` and MCP `get_taxonomy` now exist and are backed by SQLite. This remaining item covers the UX side: setup.html taxonomy viewer, CSV export button, and retention controls.
- **Trigger:** v2.1 cycle

### Chrome Web Store extension ID pinning
- **Destination:** v2.1 (new entry per Codex's review — deferred from v2 GA because Web Store ID needs to be stable first)
- **Rationale:** Currently the backend accepts any `chrome-extension://` origin for the /token endpoint. Once the Web Store publishes Yoink, the extension gets a stable ID that can be pinned in /token and CORS allowlists. Closes the "any installed extension can call our local helper" trust gap.
- **Trigger:** Web Store listing live with stable published ID

### MCP Skill / prompt package
- **Destination:** v2.1 (new entry per Codex's review — but actually we're building this NOW for v2.0 per the Skill design discussion)
- **Rationale:** MCP server exposes tools; the Skill turns Claude into a YouTube research operator that knows how to use them. Distribution via SKILL.md packaged with the installer + Claude Code plugin manifest + copyable system prompt for non-Claude-Code clients. Decision 1 in the strategy chat: ship minimal Skill v1 (4 modes: identity + citation + default + tweet) with v2.0 launch; expanded Skill v1.2 ships modes 5-7 (thread, comments, research, compare, intel) post-launch.
- **Trigger:** Skill v1 → v2.0 launch; Skill v1.2 → post-launch refinement with real calibration anchors

### MOCK_FORCE_RECOVERY_* mutual exclusion enforcement
- **Destination:** v2.1 (CC's Sprint 7 deferred)
- **Rationale:** Three dev fixtures (`RUNNING`, `COMPLETED`, `SINGLE_COMPLETED`) are documented as "set at most one to true" but not enforced. Acceptable for dev fixtures.
- **Trigger:** v2.1 cycle (drop or wire enforcement)

### chrome.storage namespace convention
- **Destination:** v2.1 (CC's Sprint 7 open question)
- **Rationale:** Yoink writes several keys to `chrome.storage.local` (e.g., `yoink_setup_auto_open_at`). No formal namespace prefix policy. Worth establishing "all Yoink keys start with `yoink_`" and documenting it.
- **Trigger:** v2.1 cycle

### Full-range Smart Screenshot Picker selection
- **Destination:** v2.1
- **Rationale:** Allow users to pick from the full set of extracted screenshots instead of just the default embedded ones. Requires backend API changes to serve thumbnail grid data.
- **Trigger:** v2.1 cycle

---

## v2.5 candidates (build if v2 hits traction signal)

### Channel Decoder
- **Destination:** v2.5 (originally scoped as v2 headline; deferred when v2 became the MCP-headlined launch)
- **Rationale:** Multi-video corpus mechanics over a single channel's recent uploads. Surfaces patterns: hook frequencies, structural cadence, topic clustering. Compounds with shipped Hook Type classification and taxonomy capture.
- **Trigger:** v2 launch ships and gets qualitative traction signal (unsolicited feature requests, non-friend GitHub stars, organic community posts)

### Niche Corpus mode
- **Destination:** v2.5 (originally scoped as v2 headline; deferred)
- **Source-strategy decision required (Codex flagged):** four viable approaches; pick one before scoping:
  - (a) YouTube search scraping via yt-dlp (fragile, no official sanction, breaks when YouTube changes search HTML)
  - (b) YouTube Data API (requires API key — breaks free positioning; usage quotas)
  - (c) User-provided playlist/search URL (manual but reliable; reuses existing Playlist Mode pipeline)
  - (d) MCP-agent-assisted search (Claude finds candidates via web search, Yoink yoinks them)
  Recommend (c) for v2.5 ship and (d) as v3 evolution; (a) and (b) compromise differentiation.
- **Trigger:** Same as Channel Decoder

### Corpus schema / version migration
- **Destination:** v2.5 (new entry per Codex's review — prerequisite for multi-video features)
- **Rationale:** Channel Decoder and Niche Corpus will rely on per-video sidecar JSON stability. The schema needs a version field and a migration story before those features ship. Otherwise v2.5 features will break against v1.x sidecar files in user libraries.
- **Trigger:** Land before Channel Decoder / Niche Corpus

### Hook taxonomy labeled dataset
- **Destination:** v2.5 (capture and basic query surfaces shipped in v2.0; full labeled-dataset story here)
- **Rationale:** Aggregate Hook Type classifications across all yoinks into a queryable taxonomy with patterns over time. Compounds with use. Local-first means each user has their own taxonomy unless they opt to contribute.
- **Trigger:** v2.5 cycle AND opt-in user-contribution mechanism designed

### Per-video quality preset (fast / standard / thorough)
- **Destination:** v2.5
- **Rationale:** Single dropdown in popup that adjusts screenshot interval, comment count, and which AI features run. Saves to `chrome.storage.sync`. Reduces per-yoink wait time for quick lookups.
- **Trigger:** v2.5 cycle

### Custom output folder picker
- **Destination:** v2.5
- **Premise fix (Codex flagged):** current code resolves the Desktop known folder and writes `Desktop\Yoink\`, NOT `Documents\Yoink\`. Picker would allow Dropbox/iCloud/network drives. Setup.html dropdown + server-side path validation.
- **Trigger:** v2.5 cycle (or first power-user complaint)

### Pin / favorite specific yoinks
- **Destination:** v2.5
- **Rationale:** Mark a session as pinned so it always appears in `list_recent_yoinks` MCP results regardless of recency. Useful for reference yoinks the user keeps coming back to.
- **Trigger:** v2.5 cycle

### Multi-language support
- **Destination:** v2.5 (was "v2 announcement")
- **Rationale:** Whisper handles transcription natively across many languages; market expansion play. UI strings still need extraction for true localization (separate v4 item).
- **Trigger:** v2.5 cycle

### Notion / Obsidian integrations
- **Destination:** v2.5 (was "v2")
- **Rationale:** Each integration is 2 weeks of auth + schema + maintenance. MCP solves the Claude Desktop case already. Notion has an official MCP server in beta — could integrate via that pattern. Obsidian needs a custom plugin or local-vault file write.
- **Trigger:** Signal that paste-from-clipboard isn't enough for power users in non-Claude workflows

### First-run onboarding / topic intake form
- **Destination:** v2.5 (was "v2")
- **Rationale:** Asks user about their interests on install, generates a personalized `topics.json`. Front-loads work currently solved by editing `topics.json` directly. Real intake forms benefit from progressive disclosure ("we'll learn from your first 5 yoinks") rather than asking everything upfront.
- **Trigger:** v2 GA ships AND signal that default `topics.json` + manual editing is insufficient (e.g., 5+ users report bad classifications in first week of use)

### Bulk and batch operations
- **Destination:** v2.5 (was "v2 paid-tier feature"; no paid tier yet)
- **Rationale:** Multi-select operations across recent yoinks: bulk delete, bulk re-run, bulk export.
- **Trigger:** v2.5 cycle or paid-tier launch decision

### Thumbnail pattern analysis
- **Destination:** v2.5 (was "v2")
- **Rationale:** Vision-model dependency; better with corpus context. Synergizes with Channel Decoder for "what thumbnails work in this channel."
- **Trigger:** Channel Decoder ships first

### Script structure parser
- **Destination:** v2.5 (was "v2 moat-builder")
- **Rationale:** Compounding labeled dataset of script structures (intro/setup/payoff/CTA, etc). Moat-builder.
- **Trigger:** v2.5 cycle

---

## v3 candidates (build if Yoink becomes the thing)

### Critique-against-corpus
- **Destination:** v3 headline feature, possibly standalone product
- **Rationale:** Requires v2.5 corpus features (Channel Decoder + Niche Corpus) to exist. User drops their own video script or rough cut, Yoink compares against high-performing videos in their niche, surfacing where hook is weak, structure deviates, pacing differs from winners. Becomes the natural v3 headline once Channel Decoder + Niche Corpus ship.
- **Trigger:** v2 ships and gets traction; Channel Decoder + Niche Corpus stable

### Lineage detection (idea propagation across niches)
- **Destination:** v3
- **Rationale:** Novel feature, hard to build well, needs data scale. Show how an idea originated, scaled, and spread across creators.
- **Trigger:** v3 build kickoff

### Trend detection within saved niches
- **Destination:** v3
- **Index-strategy decision required (Codex flagged):** needs a time-series index, not just folders. Options:
  - Upload date (creator-side trends)
  - Yoink date (user-engagement trends in your library)
  - Hook Type taxonomy (style trends over time within niche)
  - Topic tags
  - Channel metadata (subscriber growth correlation)
  Likely needs more than one; recommend Hook Type + upload date as v3 baseline.
- **Trigger:** Paid tier exists with saved-niches feature

### Creator clone mode
- **Destination:** v3 (likely evolved Skill, not standalone feature — Codex's review flagged this)
- **Rationale:** Ethically gray; needs careful positioning. Extract voice, structure, transitions, opening patterns from a creator's videos into a Skill that mimics them. Per the Skill architecture, this is a specialized variant of the Yoink operator Skill rather than a separate code feature.
- **Trigger:** Deliberate strategic decision, not feature pull

### Yoink integration with Claude/ChatGPT Projects
- **Destination:** v3 (Send to Project shortcut), v4+ (true API sync when available)
- **Strategic fit:** Strong. Projects are where serious AI research happens. Without Project integration, every yoink is ephemeral. With it, Yoink becomes the engine that builds research bases.
- **Technical reality:** Neither Claude nor ChatGPT currently exposes a public API for adding files to Projects from external tools. Three workarounds: brittle UI automation (don't), MCP-based future approach (depends on Anthropic shipping API), local Project mirror (manual sync, works today).
- **Project capacity constraint:** Claude Projects (~25 files, ~200K context) and ChatGPT Projects (similar) aren't built for research bases of 50+ corpora. Each yoink is 100KB+. Auto-sync would hit caps within 5-10 yoinks. Right mental model: Projects are curated workspaces, not data lakes.
- **Shippable v3 version:** "Send to Project" button in popup. User configures Project list, picks one, Yoink opens the Project in browser, user drags the file in. One drag instead of three steps.
- **Trigger:** v3 build kickoff for the manual "Send to Project" version

### Podcast extraction (Yoink expands beyond video)
- **Destination:** v3 expansion or sibling product under ReplayRyan
- **Strategic fit:** Strong. Same job-to-be-done as YouTube. Same audience.
- **Architectural divergence from YouTube:**
  - No equivalent to yt-dlp. Data acquisition fragments across Apple Podcasts (no public API), Spotify (Web API metadata, transcripts locked), and RSS (universal substrate for metadata, audio URLs, show notes; no transcripts).
  - Transcripts require Whisper running on audio files. ~10-15 min compute per hour of audio on local hardware.
  - No comments equivalent. Closest signal is sparse Apple/Spotify reviews.
  - No screenshots — audio-only content.
- **Corpus shape:** 4-5 sections vs YouTube's 8.
- **UX implications:** Whisper compute means podcasts take 10-20 min vs YouTube's 30-90 sec. Fire-and-forget background processing with notification on completion. Local Whisper preserves architectural promise; hosted Whisper breaks it.
- **Branding decision (deferred):** Expand Yoink to cover podcasts (dilutes positioning) or sibling product under ReplayRyan family (doubles maintenance).
- **Trigger:** v2 ships and gets clear traction AND deliberate decision to expand product surface AND user demand from existing Yoink users
- **Don't launch alongside v2** — would dilute v2's "YouTube layer for any AI agent" story.

### Multi-platform video extraction (beyond YouTube)
- **Destination:** v3 (after YouTube depth is established)
- **Strategic fit:** Real but risks diluting the core positioning. yt-dlp supports 1,800+ sites — extraction layer is largely solved. The hard part is corpus quality across platforms.
- **Three viable paths:**
  1. **Targeted expansion** (preferred): Vimeo (professional creators), Twitch VODs, TED/conference talks
  2. **Generic "any video URL" mode** (degraded): lowest-common-denominator corpus across all yt-dlp sites — dilutes
  3. **Vertical platforms** (separate products)
- **Trigger:** v2 ships and YouTube depth (Channel Decoder, Niche Corpus, Playlist mode) is stable AND clear user demand for specific platforms

### Strategic ranking note for v3
- Original v3 ranking pitted agent-friendly (MCP) vs podcasts as the key trade-off. **MCP shipped in v2.0**, so that question is resolved.
- v3's strategic question now becomes: Critique-against-corpus vs Send to Project vs Podcast extraction vs Multi-platform video.
- **Recommended v3 ranking:** Critique-against-corpus first (compounds with shipped Skill + Hook taxonomy), then Send to Project (closes a real workflow gap), then Podcast extraction. Multi-platform video last (highest risk of diluting positioning).

---

## v4+ (hosted era)

### Hosted version + accounts + payments
- **Destination:** v4
- **Rationale:** Breaks local-only differentiation, introduces ops overhead. Right move only when paid v2 tier hits clear revenue floor.
- **Trigger:** Paid v2 tier hits $5k MRR

### Public HTTPS API (Yoink-as-a-service)
- **Destination:** v4 or never
- **Rationale:** Exposes yoink-as-a-service with public endpoints. Requires hosted infrastructure. 4-6 week build minimum + ongoing ops cost. Breaks local-only promise.
- **Trigger:** v4 hosted architecture decision AND 3+ unsolicited requests from third parties wanting to embed Yoink

### Leaderboard of most-yoinked videos
- **Destination:** v4 conditional
- **Rationale:** Requires hosted layer; network effect potential. Most-yoinked videos in a category becomes a discovery surface and a reason to install.
- **Trigger:** Hosted-layer architecture decision in v4

### Localization (UI strings, not just transcription)
- **Destination:** v4 or never
- **Rationale:** Different from multi-language transcription support (v2.5). True UI localization requires string extraction, translation, locale switching, RTL support consideration.
- **Trigger:** First 100 non-English-speaking installs AND clear demand for localized UI

---

## Likely never (capture so they stop nagging)

### Linux support
- **Destination:** reconsider given MCP-led v2 positioning (Codex flagged); previously "likely never"
- **Rationale revision:** With MCP as the agent-developer adoption funnel, Linux dev users are a real audience. You don't need a full Mac-style tray/installer for them — a documented dev/stdio path (clone repo, install requirements, run `python server.py`) could satisfy technical users cheaply. Capture as "v2.5 documented dev path" rather than full Linux installer.
- **Trigger:** Decide whether to publish dev-path docs as part of v2.5

### Formal accessibility audit pass
- **Destination:** v1.5 if user feedback warrants, otherwise punt to "as-needed"
- **Rationale:** v2 added partial a11y (role/tabindex on the active-playlist pill, keyboard handler, banner-link aria-label). No formal screen-reader pass. Solo builder constraint — formal a11y pass is a focused 8-12 hour project.
- **Trigger:** First a11y-related user report or contributor PR

### Mobile app with auto-sync
- **Destination:** never
- **Rationale:** 4-month build for a workflow people already do via "text yourself the link"
- **Trigger:** 50+ unsolicited user requests

### Built-in video editor
- **Destination:** never
- **Rationale:** Scope creep into a different product category

### Auto-clip generator (shorts/reels)
- **Destination:** never
- **Rationale:** Opus Clip and Submagic own this category and have funding

### Live video monitoring
- **Destination:** never
- **Rationale:** Most analysis happens after the fact; live adds infrastructure cost for marginal value

### Twitter/X content extraction
- **Destination:** likely never; possibly sibling product under ReplayRyan
- **Strategic fit:** Weak as Yoink feature.
- **Architectural barriers:** X API is $200/mo minimum (kills free positioning); scraping violates ToS.
- **Trigger:** X API economics or ToS landscape changes meaningfully

### In-page button chooser (Claude vs ChatGPT before yoink)
- **Destination:** v1.5 if user research demands, otherwise never
- **Rationale:** Adding a chooser to the in-page button slows the most-used path.
- **Trigger:** 5+ unsolicited user requests for click-time destination choice

### Folder-mirrored Projects (yoink topic folders → Claude/ChatGPT Projects auto-sync)
- **Destination:** never as designed; superseded by "Send to Project" in v3 candidates
- **Rationale:** Claude and ChatGPT Projects have hard file count and context limits. Yoinks can be 100KB+ each. Auto-stuffing would hit limits fast and silently drop content.
- **Trigger:** never (this specific design)

---

## Audit metadata

- This file last reorganized: 2026-05-12 (post-Sprint 9, post-launch-audit)
- Items moved out of "candidates" because shipped in v2.0: ~14 (full v2 shipped list above)
- Items added based on Codex's pre-launch audit review: 7 (MCP skill/prompt package, extension ID pinning, taxonomy retention/export, corpus schema migration, installer migration smoke matrix, semantic embeddings, cross-corpus citation linking)
- Items reordered based on Codex's review: 6 (Cost estimator → v2.1, Shorts audit → pre-launch/v2.1, System tray reframed as mini-sprint, keyboard shortcut + right-click reordered earlier in v1.1, Diagnostic export earlier in v1.1, taxonomy UX moved after shipped query surfaces)
- Scope questions resolved: topics.json storage (option a recommended), Niche Corpus source (option c recommended), Trend detection index (Hook Type + upload date recommended), Custom output folder (Desktop not Documents — corrected)
