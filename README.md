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

One-click installer ships in v1 launch (target: 2 weeks). Manual setup until then — see [REQUIREMENTS.md](./REQUIREMENTS.md).

## How it works

1. Click "Yoink" under any YouTube video
2. Yoink extracts transcript, screenshots, comments, metadata
3. Markdown corpus copied to clipboard, opens Claude or ChatGPT
4. Paste, run a prompt, get analysis

## Customizing the prompt library

The Yoink popup ships with eight starter prompts ("Decode the hook", "Outline the structure", and so on). They live in `extension/prompts.json` and are fully editable — add your own, delete the ones you don't use, rename labels, anything goes.

Open the file via the **Edit prompts ▸** link at the bottom of the popup's prompt panel (it pops Explorer at the file). Or edit `extension/prompts.json` directly. The format is:

```json
[
  { "id": "my-prompt", "label": "Short button label", "prompt": "The full prompt body..." }
]
```

Changes take effect the next time you open the popup — no extension reload required.

## Customizing topic folders

Videos are auto-sorted into topic folders under `Desktop\Yoink\` based on keyword matches against `topics.json` in the project root. Edit that file to add, remove, or rename topics, or to tweak the keyword lists. New keywords apply on the next Yoink.

## Roadmap

See [BACKLOG.md](./BACKLOG.md) for v2/v3 plans.

## License

MIT. See LICENSE.

---

*Yoink is part of the [ReplayRyan](https://replayryan.com) family of tools.*
