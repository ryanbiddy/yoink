# Yoink
*The missing layer between YouTube and your AI.*

Yoink any YouTube video into Claude or ChatGPT — full transcript, screenshots, and metadata in one structured doc.

## Why Yoink

You see a video that's clearly working. You want to know why. So you paste the YouTube link into Claude and hope it can "watch" it. It can't. It hallucinates. You give up and watch the whole thing on 2x while taking notes by hand.

Yoink fixes that. Click the button under any YouTube video and you get the full transcript, timestamped screenshots, top comments, channel context, and video metadata — automatically copied to your clipboard, ready for Claude, ChatGPT, or your notes app of choice.

## Features (v1)

- One-click "Yoink" button under every YouTube video
- Right-click any thumbnail to yoink without opening the video
- Full timestamped transcript with chapter awareness
- Timestamped screenshots throughout the video
- Top 50 comments with author and like count
- Full video metadata (views, likes, tags, description, upload date)
- Thumbnail image
- Channel context (subscriber count, recent videos)
- Auto topic-classification into folders on disk
- Editable prompt library for fast follow-up analysis
- Two destination buttons: Send to Claude, Send to ChatGPT
- Local-first, no cloud, no accounts, fully open source

## Install

1. **Download the installer** -- grab `Yoink-Setup-1.0.0.exe` from the [latest release](https://github.com/ryanbiddy/yoink/releases/latest). Windows only for v1; macOS in v1.5.
2. **Run it.** Defaults install to `%LOCALAPPDATA%\Yoink\` (no admin required). The "Launch Yoink Server now" checkbox on the finish page starts the helper immediately, and an autostart entry runs it on every Windows login. A toast confirms when it's running.
3. **Install the extension** from the Chrome Web Store. The first time you launch the popup it'll detect the helper and the indicator will go green within a couple of seconds.

If the indicator stays orange, open the Start Menu, search "Yoink", and click **Yoink Server**. Stop it the same way via **Stop Yoink Server**. Uninstall removes everything including the autostart entry.

For developers running from source, see [REQUIREMENTS.md](./REQUIREMENTS.md). Build the installer locally with `./build.ps1` -- see [docs/build-installer.md](./docs/build-installer.md).

## How it works

1. Click "Yoink" under any YouTube video
2. Yoink extracts transcript, screenshots, comments, metadata
3. Markdown corpus copied to clipboard, opens Claude or ChatGPT
4. Paste, run a prompt, get analysis

## Customizing the prompt library

The Yoink popup ships with eleven starter prompts ("Decode the hook", "Outline the structure", "Format as Twitter thread", and so on). They live in `extension/prompts.json` and are fully editable — add your own, delete the ones you don't use, rename labels, anything goes.

In v1 the prompt file lives inside the unpacked extension, so editing it requires a dev install. The in-popup "Edit prompts" link is hidden in v1 because the on-disk path it opened only exists in dev mode -- a unified prompt store between extension and server is tracked for v1.1.

The format is:

```json
[
  { "id": "my-prompt", "label": "Short button label", "prompt": "The full prompt body..." }
]
```

Changes take effect the next time you open the popup — no extension reload required if you're running from a `chrome://extensions/`-loaded unpacked copy.

## Customizing topic folders

Videos are auto-sorted into topic folders under `Desktop\Yoink\` based on keyword matches against `topics.json` in the project root. Edit that file to add, remove, or rename topics, or to tweak the keyword lists. New keywords apply on the next Yoink.

## Roadmap

See [BACKLOG.md](./BACKLOG.md) for v2/v3 plans.

## License

MIT. See LICENSE.

---

*Yoink is part of the [ReplayRyan](https://replayryan.com) family of tools.*
