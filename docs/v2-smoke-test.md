# Yoink v2 definitive smoke-test checklist

Use this as the one pre-launch checklist. Run on a clean Windows user profile if possible, then repeat the core extraction subset on the normal dev machine.

Total checkpoints: 91.

## 1. Core extraction - single-video v1 regression

1. [ ] Install helper and extension fresh - success: setup page detects helper green and popup status is green.
2. [ ] Open a normal public YouTube watch URL and click the in-page Yoink button - success: extraction completes, Claude/ChatGPT opens, clipboard contains a corpus.
3. [ ] Confirm saved files for the single video - success: topic folder contains `<slug>.md`, `<slug>.json`, `metadata.json`, transcript, screenshots, and thumbnail.
4. [ ] Paste clipboard into Claude or ChatGPT - success: title, metadata, transcript, selected screenshots, comments, and footer render without broken markdown.
5. [ ] Click "Send to Claude" from popup on a video page - success: same single-video flow works and first-yoink CTA still appears only once after reset.
6. [ ] Click "Send to ChatGPT" from popup on a video page - success: same corpus lands on clipboard and ChatGPT opens.
7. [ ] Right-click a YouTube thumbnail/video link and yoink it - success: extraction completes without opening the video page.
8. [ ] Try 3 public Shorts URLs - success: `/shorts/<id>` canonicalizes to `watch?v=<id>`, transcript/metadata extraction works when YouTube exposes them, screenshot extraction handles the short duration, and failures are friendly rather than stack traces.
9. [ ] Try malformed and non-YouTube URLs through server routes or UI - success: rejected with clear "youtube.com or youtu.be" copy.

## 2. Playlist Mode end-to-end

10. [ ] Preview a valid playlist over 10 videos - success: preview shows first 10 and warns that the playlist exceeds the cap.
11. [ ] Start a playlist job - success: `POST /playlist/start` returns quickly and popup enters live progress.
12. [ ] Let a 2-3 video playlist complete - success: state becomes `completed`, `videos_done` matches successes, combined clipboard corpus is text-only.
13. [ ] Inspect playlist session folder - success: `_sessions/<playlist-slug>/corpus.md` exists and per-video folders retain screenshots on disk.
14. [ ] Include one unavailable/private/deleted video if possible - success: job continues, increments `videos_failed`, and completes if at least one video succeeds.
15. [ ] Run a playlist where zero videos can extract - success: final state is `failed` with a friendly zero-success error.
16. [ ] Cancel during metadata/download/screenshots phase - success: active subprocess stops quickly, job state stays `cancelled`, partial outputs remain.
17. [ ] Close and reopen popup during a running playlist - success: popup recovers from `/jobs` and resumes the active job view.
18. [ ] Use `/jobs?kind=playlist` and `/jobs?kind=single` manually - success: filter returns only matching jobs, sorted newest first.

## 3. AI features - CI, Hook Type, taxonomy capture

19. [ ] Enable Comment Intelligence with no key - success: normal yoink still works and CI silently skips.
20. [ ] Save a valid Anthropic key and test it - success: setup shows key set/test passed and `GET /settings` never returns the key.
21. [ ] Yoink a video with at least 5 comments and CI enabled - success: CI section appears in the per-video `.md` after comments finish.
22. [ ] Enable Hook Type and yoink a video - success: Hook Analysis appears after the metadata block with one allowed category and explanation.
23. [ ] Confirm Hook Type waits for comments - success: top comment is available to the hook worker when comments exist.
24. [ ] Force or simulate a 401 Anthropic response - success: saved key is cleared, `anthropic_key_set` becomes false, future AI work skips until re-save.
25. [ ] Trigger Hook Type on two different videos - success: `%LOCALAPPDATA%\Yoink\taxonomy.json` contains two records with `video_id`, `hook_type`, `hook_explanation`, `channel`, `title`, `classified_at`.
26. [ ] Re-trigger Hook Type on the same video - success: taxonomy record is updated in place, not duplicated.
27. [ ] Corrupt `taxonomy.json` and restart/trigger Hook Type - success: helper logs warning, starts fresh, and does not crash.
28. [ ] Query taxonomy through HTTP and MCP - success: `/taxonomy`, MCP `get_taxonomy`, `channel`, `hook_type`, and combined filters return newest-first rows without duplicates.

## 4. Setup / settings / key handling

29. [ ] First-run install path - success: extension opens `setup.html?source=install`, shows intro/install/verify/try steps.
30. [ ] Offline path - success: stop helper, click Yoink, setup opens at verify step and does not require user to understand Python.
31. [ ] Installer download gate before publication - success: if `INSTALLER_PUBLISHED=false`, download button cannot send users to a 404.
32. [ ] Installer download gate after publication - success: after flip, button downloads the exact release asset.
33. [ ] Clear key button - success: confirmation appears, key clears from Credential Manager/keyring, input empties, status says key not set.
34. [ ] Keyring fresh install - success: with no settings key and no credential entry, settings works and reports `anthropic_key_set=false`.
35. [ ] Keyring migration install - success: a legacy plaintext `anthropic_key` in settings.json migrates to keyring and is removed from settings.json on startup.
36. [ ] Missing/unavailable keyring in dev mode - success: helper starts, but saving a non-empty key fails clearly instead of falling back to plaintext.
37. [ ] Toggle CI, Hook Type, and Smart Screenshot Picker - success: settings persist across helper restart and popup/setup reload.
38. [ ] AI cost estimator - success: with a key present, CI/Hook toggles show the correct per-video estimate from `/settings/pricing`; with no key or no paid toggles it stays hidden.
39. [ ] Deep link to `setup.html?source=popup#mcp-settings` - success: page scrolls to Agent Integration and is not hijacked by Step 4 auto-scroll.

## 5. Smart Screenshot Picker

40. [ ] Enable picker and yoink a normal video - success: popup shows thumbnail grid after extraction.
41. [ ] Pick zero screenshots - success: clipboard corpus remains valid text and UI gives a clear empty-selection state.
42. [ ] Pick one screenshot - success: clipboard includes exactly that screenshot.
43. [ ] Pick many screenshots - success: UI remains responsive and clipboard respects configured max/context constraints.
44. [ ] Serve thumbnails through `/file` - success: authenticated image URLs load; invalid token, outside-root path, non-image, bad magic bytes, and >10 MB file are rejected.
45. [ ] Run playlist with picker enabled - success: playlist clipboard remains text-only and no picker is shown.

## 6. MCP - stdio and HTTP

46. [ ] Copy Claude Desktop stdio config from setup - success: command points to installed `python.exe` and `yoink_mcp.py`.
47. [ ] Smoke-test Claude Desktop stdio - success: client lists all 12 tools and `list_recent_yoinks` works.
48. [ ] Smoke-test Cursor stdio - success: client lists all 12 tools and `search_yoinks` works.
49. [ ] Call `yoink_video` through MCP - success: returns `ok`, `slug`, `folder`, `corpus_md`, and screenshots.
50. [ ] Call `yoink_playlist` then `get_job_status` - success: job appears in `/jobs` and completes/cancels consistently.
51. [ ] Call `get_yoink_corpus` on a sidecar-backed yoink - success: returns `video_id` and `video_url`.
52. [ ] Call `get_yoink_corpus` on legacy/missing sidecar - success: returns `video_id:null` and `video_url:null` without crashing.
53. [ ] Call `get_taxonomy` through MCP - success: no-arg, `channel`, `hook_type`, and combined filters match `/taxonomy`.
54. [ ] Call `get_citation_map` through MCP - success: `transcript_citations` and `screenshot_citations` are both non-empty for a yoink with screenshots.
55. [ ] Call `get_yoink_health` through MCP - success: returns a dict with five status fields, none null.
56. [ ] Trigger MCP rate limits - success: extraction tools fail friendly after 5/minute, AI tools after 10/minute, and citation/health tools after 60/minute.
57. [ ] Smoke-test HTTP JSON-RPC helper - success: token-gated initialize/tools/list/tools/call return MCP-style envelopes.

## 7. XSS / security

58. [ ] Yoink a video with hostile title/description/comment text containing HTML/script - success: popup/setup/picker render text safely; no script executes.
59. [ ] Try malicious `/file` paths - success: `..`, relative paths, symlink escapes, outside-root files, and wrong magic bytes are rejected.
60. [ ] Try unauthenticated mutating requests - success: all POST routes and private GET routes return 403 before reading body.
61. [ ] Try `/token` from a normal web origin - success: browser CORS/preflight blocks or server returns forbidden; token is not exposed.
62. [ ] Confirm token is never in query strings - success: network history shows `X-Yoink-Token` header only.
63. [ ] Check logs after key operations - success: no Anthropic key, token, or full auth header appears in `server.log`.

## 8. Recovery + resilience

64. [ ] Stop helper while popup is open - success: disconnect banner appears after grace period and buttons do not look active.
65. [ ] Leave helper offline for the auto-open threshold - success: setup opens once, then rate-limit prevents repeated tab spam.
66. [ ] Restart helper while popup is open - success: popup reconnects, banner clears, status dots turn green.
67. [ ] Kill helper mid-playlist and restart - success: `/jobs` returns the job as `failed` with `error="server restarted"` from `index.db`.
68. [ ] Confirm last-yoink affordance for playlist completion - success: popup surfaces recent completed playlist job.
69. [ ] Confirm last-yoink affordance for single-video completion - success: popup surfaces recent single job after `/extract`.
70. [ ] Confirm active-playlist pill while switching modes - success: pill shows status and click returns to playlist view.
71. [ ] Cancel an active backfill - success: `/index/backfill-cancel` returns `cancelled:true`, banner clears when status becomes complete, and already-indexed yoinks remain searchable.

## 9. Index foundation

72. [ ] Boot helper with no `index.db` - success: backfill scan runs, `/index/backfill-status` reports complete, and all on-disk corpora appear in Recent yoinks or indexed search surfaces.
73. [ ] Boot helper with corrupt `index.db` (truncate to 0 bytes) - success: file is renamed to `index.db.corrupt-<timestamp>`, fresh backfill runs, recovers all yoinks, and `/health` reports `index_recovering:true` during recovery.
74. [ ] Boot helper with `jobs.json` and `taxonomy.json` present - success: both migrate into `index.db`, `.migrated` files appear, and no data is lost.
75. [ ] Yoink three new videos - success: each appears incrementally in Recent yoinks or indexed search surfaces without a full re-scan.
76. [ ] Search yoinks against a corpus of 50+ - success: returns in under 500ms.

## 10. Windows path handling

77. [ ] OneDrive Desktop redirection - success: Yoink output root resolves to the actual known Desktop path, not a guessed `%USERPROFILE%\Desktop`.
78. [ ] Video title is a Windows reserved name (`CON`, `AUX`, `LPT1`) - success: folder slug is safe and extraction completes.
79. [ ] Very long video title - success: folder/file creation succeeds or fails gracefully without path traversal.
80. [ ] Desktop on network/synced drive - success: extraction either completes or reports a clear file-write error.
81. [ ] Start Menu shortcuts - success: Yoink Server, Stop Yoink Server, Yoink folder, and Uninstall Yoink entries work.
82. [ ] Auto-start on Windows login - success: server starts hidden after sign-in and popup is green within 30 seconds.
83. [ ] Uninstall - success: files, Start Menu shortcuts, Run key, and helper process are removed/cleared cleanly.

## 11. Pre-launch packaging gates

84. [ ] `USE_MOCK_API` / mock mode is off for production extension - success: popup talks to real helper, not fixtures.
85. [ ] `INSTALLER_PUBLISHED` is flipped only after GitHub release asset exists - success: setup download URL resolves to `Yoink-Setup-2.0.0.exe`.
86. [ ] Manifest version and installer version are aligned - success: Chrome Web Store package, installer, and `server.py VERSION` match launch plan.
87. [ ] Direct-download hashes are locked - success: Python, ffmpeg, and get-pip hashes in `build.ps1` are non-empty and verified during build.
88. [ ] Build installer from clean cache - success: `.\build.ps1 -Clean` outputs `build\Yoink-Setup-2.0.0.exe` and hash checks pass.
89. [ ] Clean Windows VM install - success: unsigned SmartScreen path is understandable, installer runs without admin, helper starts hidden.
90. [ ] Chrome Web Store package uses production domain/copy - success: footer, setup, README, store listing, and landing links point at `ryanbiddy.com/yoink` or the chosen canonical URL.
91. [ ] Final docs pass - success: README, security, build-installer, v2 docs, and store listing no longer describe pre-Sprint-7 behavior.
