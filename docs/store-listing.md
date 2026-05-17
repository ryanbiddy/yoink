# Chrome Web Store Listing — Yoink

## Title (45 chars max)
Yoink — any YouTube video into your AI

<!-- 39 chars. -->

## Short description (132 chars max)
The YouTube layer for any AI. Yoink any video into Claude, ChatGPT, or your agent — transcript, screenshots, comments, all of it.

<!-- 131 chars. -->

## Long description

You see a video that's clearly working. You want to know why. So you paste the link into Claude and hope it can "watch" it. It can't. It hallucinates titles, invents quotes, and gives up on anything past the first paragraph. You give up too — and watch the whole thing on 2x speed while taking notes by hand.

Yoink is the missing layer between YouTube and your AI.

Click the **Yoink** button under any YouTube video and you get a single, structured markdown corpus auto-loaded onto your clipboard:

- The full timestamped transcript, chapter-aware
- Timestamped screenshots throughout the video, with a paste-safe subset embedded in the clipboard
- The top 50 comments, with author and like count
- Full video metadata — views, likes, tags, description, upload date
- The thumbnail
- Channel context — subscriber count and the channel's last 5 videos

Paste it into Claude or ChatGPT. Ask "Decode the hook," "Outline the structure," "What would make this 2x better." Yoink ships with a built-in 11-prompt starter library.

### Three ways to use Yoink

**As a Chrome extension (the one-click flow):**
One click on the in-page button, your clipboard fills with a structured corpus, the chat opens. Paste, prompt, analyze.

**As an MCP server (the agent flow):**
Yoink ships a local MCP server with 10 tools your AI agent can call directly: `yoink_video`, `yoink_playlist`, `list_recent_yoinks`, `search_yoinks`, `analyze_comments`, `classify_hook`, `get_taxonomy`, and more. Tell Claude "yoink that video and decode the hook" — it does both. No clipboard step. Officially tested with Claude Desktop and Cursor; community-reported support for ChatGPT Desktop, Continue, and Cline.

**As an operator Skill (Claude Code, OpenClaw, Hermes, Cursor, and more):**
Yoink includes a portable `SKILL.md` using the agentskills.io open standard. Drop it into your agent client to add citation discipline, operator-grade YouTube analysis, hook-autopsy tweet mode, and the Yoink Hook Type taxonomy on top of the MCP tools.

### Why people use Yoink

- **One click in, ready to analyze.** No copy-pasting transcripts. No exporting screenshots one at a time. No describing the video to your AI in your own words.
- **The full picture, not just the words.** Comments, channel context, and screenshots ride along with the transcript so your AI has actual context.
- **Playlist mode.** Paste a YouTube playlist URL, yoink up to 10 videos at a time. Combined corpus + per-video files on disk, ready for cross-video analysis.
- **Auto-organized on disk.** Yoinks land in topic folders on your Desktop (Social Media Research, AI and ML, Productivity, etc.) — fully editable via `topics.json`.
- **Two destinations.** Send to Claude or Send to ChatGPT, your call.
- **Local-first.** No accounts, no cloud, no required telemetry. Yoink runs a tiny local helper on `127.0.0.1` to do the extraction.
- **Free and open source.** MIT-licensed. Read the code, fork it, modify it.

### Optional AI-powered features (BYO Anthropic API key)

Yoink stays free and local-only by default. Two optional analysis features call the Anthropic API using your own API key (stored securely via Windows Credential Manager — never in plaintext):

- **Comment Intelligence** — clusters comment themes, extracts mentioned products, flags notable disagreements
- **Hook Type classification** — classifies each video's opening style across 9 hook categories

Both are off by default. Enable per feature on the setup page. Your API key never leaves your machine except for the Comment Intelligence and Hook Type calls made to Anthropic on your behalf. Yoink itself collects nothing.

### Optional local feature: Smart Screenshot Picker

Opt-in grid for choosing which screenshots make the clipboard. Stays fully local — no API key, no network calls.

### Privacy summary

- The core Yoink flow (extraction + clipboard) is fully local. Nothing leaves your machine except the YouTube fetch.
- Optional AI features (Comment Intelligence, Hook Type) send video text to Anthropic with your own API key — only when you enable them.
- No analytics, telemetry, or remote logging in v1.0 or v2.0. Opt-in install-success telemetry is on the v1.1 roadmap.
- Full details: see the privacy policy linked below.

### How it works

1. Install the extension and run the local Yoink helper (a small Python server bundled with the install).
2. Open any YouTube video. Click the **Yoink** button under the player (or right-click any thumbnail).
3. Yoink extracts transcript, screenshots, comments, channel context, and metadata into one `<video-slug>.md` file (e.g. `karpathy-deep-dive-into-llms.md`) under `Desktop\Yoink\<topic>\<slug>\`. A structured `<slug>.json` sidecar lands beside it for programmatic use. A master `_all-yoinks-index.md` at the root tracks every yoink you've done.
4. The clipboard version is **multimodal**: same content as the on-disk file, but with 4 screenshots inlined as base64 images by default (configurable 0-12). Paste into Claude or ChatGPT and the AI sees the transcript text **and** key screenshots in one shot — no separate uploads. The full screenshot set stays on disk.

### Playlist Mode

Paste any YouTube playlist URL and Yoink processes up to 10 videos in sequence. Live progress, cancel mid-flight, partial-failure tolerance (one bad video doesn't kill the playlist). Combined corpus copied to clipboard (text-only — fits Claude/ChatGPT context); per-video corpora with screenshots remain on disk.

### Customization

- Edit `topics.json` (in your install folder) to add or rename topic folders and tweak keyword rules. Reloaded on the next yoink — no restart needed.
- The popup ships with 11 starter prompts. v1.1 adds an inline editor; until then the prompts are part of the extension package.
- Toggle Comment Intelligence, Hook Type, and Smart Screenshot Picker on the setup page.

### Requirements

- Windows 10 / 11 (Mac on the v1.1 roadmap)
- Chrome or Chromium-based browser (Edge, Brave, Comet, Opera GX, Vivaldi)
- The Yoink helper server (~120 MB installed; bundles Python 3.11.9 + yt-dlp + ffmpeg 7.1 + keyring + MCP SDK)

### What's next

Yoink v2 ships the MCP server and AI features. The roadmap (in BACKLOG.md on GitHub) includes Channel Decoder mode, Niche Corpus mode, Critique-against-corpus, and Notion / Obsidian integrations. Open issues and PRs welcome.

---

*Yoink is part of the [ReplayRyan](https://replayryan.com) family of tools.*

## Category
Productivity

## Language
English (United States)

## Support email
yoink@replayryan.com

> **Note:** This support email must be deliverable before submission. If `yoink@replayryan.com` isn't yet set up as a forwarder, swap to a working personal address until it is.

## Privacy policy URL
https://ryanbiddy.com/yoink/privacy

> **Note:** This URL must resolve before submission. The Web Store reviewer will fetch it.
>
> The v2 privacy policy is **drafted** at `docs/privacy-policy.md` in
> this repo. To go live, publish that file's content (rendered as a web
> page) at `https://ryanbiddy.com/yoink/privacy`. It already covers the
> required points:
> 1. Core extraction is fully local. Yoink itself collects nothing.
> 2. Optional AI features (Comment Intelligence, Hook Type) call the Anthropic API with the user's own API key when enabled by the user. Smart Screenshot Picker is opt-in but stays fully local — it does not call Anthropic.
> 3. The user's API key is stored in Windows Credential Manager and never transmitted anywhere except to Anthropic in the headers of those API calls.
> 4. No analytics, telemetry, or remote logging.

## Website
https://ryanbiddy.com/yoink

## Permissions justification

The Web Store will ask why each permission is requested. Pre-drafted answers:

- **`clipboardWrite`** — Yoink writes the extracted markdown corpus to the user's clipboard so they can paste it into Claude or ChatGPT.
- **`notifications`** — surfaces success and error toasts (e.g., "Yoinked! Saved to: Social Media Research.") so the user knows when an extraction completes.
- **`storage`** — persists user settings (screenshot interval, clipboard screenshot cap, active research session, queue state, last-yoink affordance state) across browser sessions.
- **`contextMenus`** — adds "Yoink this video" / "Yoink this page" / "Yoink into session" entries on YouTube right-click menus.
- **`activeTab`** — reads the current YouTube URL when the user clicks the extension action.
- **`offscreen`** — the MV3 service worker uses an offscreen document to access the clipboard API (the only supported path in MV3).
- **Host permissions:**
  - `https://*.youtube.com/*` — content script injects the in-page Yoink button under every video
  - `http://127.0.0.1:5179/*`, `http://localhost:5179/*` — the extension communicates with the local Yoink helper server

## Single purpose statement

Yoink has one purpose: extract a structured markdown corpus (transcript, screenshots, comments, metadata) from a YouTube video and make it available to the user's AI of choice — either via clipboard (Chrome extension flow) or via MCP tools (agent flow).

## Pre-submission checklist

- [ ] All 5 screenshots captured at 1280x800 (see `docs/screenshot-list.md`)
- [ ] Promo tiles finalized in Figma (placeholders in `assets/store/`)
- [ ] Privacy policy live at https://ryanbiddy.com/yoink/privacy with v2 accurate language (covers BYO Anthropic key + keyring storage)
- [ ] Support email yoink@replayryan.com receiving mail
- [ ] ryanbiddy.com/yoink landing page live (or holding page is fine for v2)
- [ ] Extension version in `manifest.json` matches release tag (2.0.0 for v2 launch)
- [ ] `USE_MOCK_API = false` in `extension/popup.js`
- [ ] `INSTALLER_PUBLISHED = true` in `extension/setup.js`
- [ ] Final `.zip` of the `extension/` folder produced (no dev artifacts, no `MOCK_FORCE_*` flags committed as true)
- [ ] Tested install + first yoink on a clean Chrome profile
- [ ] Tested MCP setup flow on a clean Claude Desktop install (the v2 launch headliner)
