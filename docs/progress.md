# Yoink Progress Log

## Weekend 1 — v1 build (this weekend)

### Done
- Rebrand from yt-extractor to Yoink
- Full v1 corpus format (metadata, thumbnail, description, tags, transcript, screenshots, comments, channel context)
- Two destination buttons (Claude + ChatGPT)
- Prompt library with 8 starter prompts
- Polish pass on error messages and notifications
- Chrome Web Store assets prepped (icons, listing draft, screenshot list)
- README, BACKLOG, STYLE, REQUIREMENTS docs

### Ships next weekend
- Inno Setup one-click installer for Windows
- 60-90 second demo video
- Chrome Web Store submission
- Final landing page copy at yoink.video

### Ships at launch (2-3 weekends out)
- Public Show HN post
- Launch tweet thread
- Product Hunt submission (optional)
- Chrome Web Store live (gated on review approval)

### Known issues / rough edges to address during the week

- **Page-context blocker on the dev machine.** Some client-side filter on this user's setup (Chrome tracking protection or AV web shield, reproduced even in Incognito) intercepts content-script fetches to `127.0.0.1:5179` with `ERR_BLOCKED_BY_CLIENT`. Worked around by routing all loopback fetches through the background service worker (`STC.postExtractViaBg` / `STC.addToSessionViaBg`). Worth re-testing on a clean Windows install before the installer ships — if the BG-proxy approach isn't needed for most users, the direct path could come back as the default.
- **ffmpeg PATH after `winget install`.** PATH only refreshes for shells started after the install. The installer needs to either bundle ffmpeg or guarantee a PATH refresh before launching the server for the first time. Currently documented in REQUIREMENTS.md but easy to miss.
- **Elevated `pythonw` collisions.** If the server is started from an elevated shell once, the spawned process can persist with admin-only termination, and Windows allows a second non-elevated instance to bind the same port — they then race for incoming requests. The installer should refuse to start from an elevated context (or kill any existing `pythonw server.py` on launch).
- **Clipboard payload doesn't include the comments.** Comments fetch is async and rewrites `yoink.md` on disk after the response is returned. The clipboard captures the response (initial md with the "Fetching comments…" placeholder), so the pasted corpus doesn't include comments unless the user re-copies from the file. v2 polish: re-copy to clipboard automatically when the comments thread finishes if the popup or YouTube tab is still focused.
- **Theme-aware toolbar icon.** `manifest.action.theme_icons` is wired with light/dark variants, but both currently resolve to the white Y (because most users are dark-mode and the black Y disappeared in their toolbars). Light-mode users will see a white Y on a light toolbar — also invisible. Re-cut the black variant before ship and restore proper theme split.
- **Topic classifier scores ties poorly.** Substring scoring with no tie-breaker means a video that hits one keyword in two categories lands in whichever was defined first. Fine for v1 but produces surprises on edge content. v2: per-keyword weights or simple TF-IDF.
- **Session-end no longer auto-opens Claude.** This is intentional (the destination buttons let the user pick), but the popup-end notification toast may not be obvious enough. Watch for confused user reports.
- **`/open-folder` sandbox is path-based.** Resolves the requested folder against `Desktop\Yoink\` via `relative_to`. Works on Windows but is brittle if the user moves the output root via a future setting. Tighten to a configurable allowlist when that setting lands.
- **`combined.md` references in archived sessions.** `_build_corpus` was updated to read `yoink.md`, but any session created before this build still has per-video `combined.md` files. The corpus build silently shows "yoink.md not found" for those entries. v1.0.1 patch: fall back to combined.md for legacy sessions.
