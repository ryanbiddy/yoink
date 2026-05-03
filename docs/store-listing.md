# Chrome Web Store Listing — Yoink

## Title (45 chars max)
Yoink — any YouTube video into your AI

<!-- 39 chars. -->

## Short description (132 chars max)
Yoink full transcripts, screenshots, comments, and metadata from any YouTube video into Claude or ChatGPT in one click.

<!-- 119 chars. -->

## Long description

You see a video that's clearly working. You want to know why. So you paste the link into Claude and hope it can "watch" it. It can't. It hallucinates titles, invents quotes, and gives up on anything past the first paragraph. You give up too — and watch the whole thing on 2x speed while taking notes by hand.

Yoink is the missing layer between YouTube and your AI.

Click the **Yoink** button under any YouTube video and you get a single, structured markdown corpus auto-loaded onto your clipboard:

- The full timestamped transcript, chapter-aware
- Timestamped screenshots throughout the video
- The top 50 comments, with author and like count
- Full video metadata — views, likes, tags, description, upload date
- The thumbnail
- Channel context — subscriber count and the channel's last 5 videos

Paste it into Claude or ChatGPT. Ask "Decode the hook," "Outline the structure," "What would make this 2x better." Yoink ships with a starter prompt library you can edit in one click.

### Why people use Yoink

- **One click in, ready to analyze.** No copy-pasting transcripts. No exporting screenshots one at a time. No describing the video to your AI in your own words.
- **The full picture, not just the words.** Comments, channel context, and screenshots ride along with the transcript so your AI has actual context.
- **Auto-organized on disk.** Yoinks land in topic folders on your Desktop (Social Media Research, AI and ML, Productivity, etc.) — fully editable via `topics.json`.
- **Two destinations.** Send to Claude or Send to ChatGPT, your call.
- **Local-first.** No accounts, no cloud, no telemetry. Yoink runs a tiny local helper on `127.0.0.1` to do the extraction. Nothing leaves your machine except the YouTube fetch itself.
- **Free and open source.** MIT-licensed. Read the code, fork it, modify it.

### How it works

1. Install the extension and run the local Yoink helper (a small Python server bundled with the install).
2. Open any YouTube video. Click the **Yoink** button under the player (or right-click any thumbnail).
3. Yoink extracts transcript, screenshots, comments, channel context, and metadata into one `yoink.md` file. The corpus is auto-copied to your clipboard.
4. Open Claude or ChatGPT. Paste. Run a prompt. Get analysis.

### Research sessions (multi-video corpora)

Yoinking related videos? Start a **session** in the popup, yoink as many videos as you want, then end the session — Yoink concatenates everything into one corpus and copies it to your clipboard. Perfect for niche research, competitive analysis, or trend study.

### Customization

- Edit `topics.json` to add or rename topic folders and tweak keyword rules
- Edit `prompts.json` to add your own starter prompts to the popup library
- Both reload at runtime — no extension restart needed

### Requirements

- Windows 10 / 11 (Mac and Linux on the v2 roadmap)
- Chrome or Chromium-based browser
- The Yoink helper server (~30 MB, includes yt-dlp + ffmpeg)

### What's next

Yoink is v1. The roadmap (in BACKLOG.md on GitHub) includes Channel Decoder mode, niche corpus mode, comment-theme clustering, and Notion / Obsidian integrations. Open issues and PRs welcome.

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
https://yoink.video/privacy

> **Note:** This URL must resolve before submission. The Web Store reviewer will fetch it. A short single-page policy stating "Yoink does not collect or transmit any user data — all extraction runs locally on the user's machine via the bundled helper server" is sufficient for v1.

## Website
https://yoink.video

## Permissions justification

The Web Store will ask why each permission is requested. Pre-drafted answers:

- **`clipboardWrite`** — Yoink writes the extracted markdown corpus to the user's clipboard so they can paste it into Claude or ChatGPT.
- **`notifications`** — surfaces success and error toasts (e.g., "Yoinked! Saved to: Social Media Research.") so the user knows when an extraction completes.
- **`storage`** — persists user settings (screenshot interval, active research session, queue state) across browser sessions.
- **`contextMenus`** — adds "Yoink this video" / "Yoink this page" / "Yoink into session" entries on YouTube right-click menus.
- **`activeTab`** — reads the current YouTube URL when the user clicks the extension action.
- **`offscreen`** — the MV3 service worker uses an offscreen document to access the clipboard API (the only supported path in MV3).
- **Host permissions:**
  - `https://*.youtube.com/*` — content script injects the in-page Yoink button under every video
  - `http://127.0.0.1:5179/*`, `http://localhost:5179/*` — the extension communicates with the local Yoink helper server

## Single purpose statement

Yoink has one purpose: extract a structured markdown corpus (transcript, screenshots, comments, metadata) from a YouTube video and load it onto the user's clipboard for use with an AI assistant.

## Pre-submission checklist

- [ ] All 5 screenshots captured at 1280x800 (see `docs/screenshot-list.md`)
- [ ] Promo tiles finalized in Figma (placeholders in `assets/store/`)
- [ ] Privacy policy live at https://yoink.video/privacy
- [ ] Support email yoink@replayryan.com receiving mail
- [ ] yoink.video landing page live (or holding page is fine for v1)
- [ ] Extension version in `manifest.json` matches release tag
- [ ] Final `.zip` of the `extension/` folder produced (no dev artifacts)
- [ ] Tested install + first yoink on a clean Chrome profile
