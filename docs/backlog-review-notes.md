# Backlog draft review notes from build lens

Source reviewed: `C:\Users\hello\OneDrive\Desktop\Yoink\BACKLOG-draft.md` because `BACKLOG-draft.md` is not present in this worktree at `c6bfbe5`. `BACKLOG.md` in this worktree is older than the draft and still describes pre-v2 planning.

- SCOPE-QUESTION: `BACKLOG-draft.md` is not committed on `v2-integration` in this worktree. If it is intended to be the canonical backlog, commit it or rename it to `BACKLOG.md` before using it as planning input.
- REORDER: API key encryption, job persistence across server restart, and single-video job logging are listed under v2.1 but shipped in Sprint 7. Move them to Shipped or a "v2.1 built, awaiting integration smoke" section.
- REORDER: Hook taxonomy / labeled dataset is listed as v2.5, but Sprint 7 now captures every successful Hook Type classification to `taxonomy.json`. Move "taxonomy query/export surface" earlier; the hard prerequisite is already landed.
- MISSING: Add a `taxonomy.json` retention/export/query item. Capture exists, but there is no endpoint, MCP tool, CSV export, or UI surface for using it.
- MISSING: Add a `jobs.json` compaction/retention policy. Persistent jobs are useful, but without pruning they can grow forever, especially if single-video records keep large `combined_md_text`.
- MISSING: Add "extension ID pinning after Chrome Web Store publication." `docs/security.md` says v2 will pin the published ID, but the backend still accepts any `chrome-extension://` origin.
- MISSING: Add "security docs refresh" as a pre-launch or v2.1 item. The public security model needs to cover keyring, `/file`, `/mcp/v1`, `jobs.json`, and `taxonomy.json`.
- REORDER: Cost estimator for AI features is easier than v2.5. It is mostly setup.html copy plus model/token constants and would reduce BYOK anxiety; consider v2.1/v1.2.
- REORDER: YouTube Shorts support audit should be pre-launch or immediate v2.1, not v2.5. The backend normalizes `/shorts/`, but content-script UI and screenshot density still need confirmation.
- SCOPE-QUESTION: System tray status app is probably not a small v1.1 polish item. It creates a new packaged runtime surface, tray icon lifecycle, Windows notification quirks, and future Mac divergence. Keep it, but treat as a headline mini-sprint.
- REORDER: Keyboard shortcut and right-click video-link context menu are lower backend risk than Mac installer/system tray. If v1.1 needs quick visible wins, these are likely safer earlier picks.
- SCOPE-QUESTION: Editable topics.json should specify storage. Current installer ships `topics.json` read-only under `%LOCALAPPDATA%\Yoink`; decide whether edits live in server-managed settings, a user data file, or extension storage.
- MISSING: Add "corpus schema/version migration" before Channel Decoder and Niche Corpus. Multi-video features will rely on sidecar JSON stability; the schema needs a version and migration story.
- SCOPE-QUESTION: Niche Corpus mode is underspecified enough that build cannot start. It needs source strategy: YouTube search scraping via yt-dlp, YouTube Data API, user-provided playlist/search URL, or MCP-agent-assisted search.
- SCOPE-QUESTION: Trend detection within saved niches requires a time-series index, not just folders. Decide whether it uses upload date, yoink date, Hook Type taxonomy, topic tags, or channel metadata.
- MISSING: Add "MCP skill/prompt package" as a v2.1/v2.5 adoption item. Now that MCP exists, shipping example agent workflows may drive more use than another UI feature.
- SCOPE-QUESTION: Linux support may not be "likely never" if MCP-led positioning works. You do not need a full tray/installer first; a documented dev/stdio path could satisfy technical users cheaply.
- MISSING: Add "installer update/migration smoke matrix." Keyring migration, jobs.json restore, Start Menu entries, and auto-start should be tested across upgrade, uninstall/reinstall, and clean install.
- REORDER: Diagnostic export button should probably move up. It is cheap and becomes more valuable now that there are settings, keyring state, jobs, MCP config, and AI feature toggles to debug.
- SCOPE-QUESTION: Custom output folder picker says "Currently fixed to `Documents\Yoink`" in the draft, but current code resolves the Desktop known folder and writes `Desktop\Yoink`. Correct the premise before scoping.
