# Screenshots needed for Web Store submission

All screenshots: **1280x800 PNG**. Use Cleanshot or Snagit for capture and annotation. Save into `assets/store/screenshots/` as `01-...png`, `02-...png`, etc., so they sort in upload order.

The Web Store accepts up to 5 screenshots. The first one is the most important — it appears in search results.

---

## 1. Yoink button under a YouTube video
- **Setup:** Open a public YouTube video. Pick a recent video from a creator with on-brand subject matter (e.g., social media, AI, productivity — something a target user would already be curious about).
- **Crop:** Tight on the action button row below the title. Show Like, Dislike, Share, and the **Yoink** button side by side. Include a sliver of the video thumbnail above so the context reads as YouTube.
- **Annotation:** Subtle arrow pointing at the Yoink button, label **"One click to yoink"**. Use a brand-color (yellow) annotation, not red.

## 2. The popup with two destination buttons + prompts
- **Setup:** Click the Yoink extension icon while a yoink is fresh on the clipboard. Make sure the dot is green and the status reads "Yoink is running."
- **Crop:** Full popup, no surrounding browser chrome. Trim the empty area below if the popup is shorter than 800px — pad with brand background, not white.
- **Annotation:** Highlight the **Send to Claude** / **Send to ChatGPT** buttons (light box around both) and the **Quick Prompts** panel (light box). Caption: **"Pick a destination, copy a starter prompt."**

## 3. The yoink.md file open in a markdown viewer
- **Setup:** Open the generated `yoink.md` from a recent yoink in VS Code (with the markdown preview pane) or Obsidian. Pick a video with comments enabled and a real description so all sections show populated.
- **Crop:** Show the metadata header at the top (channel, subs, views, duration, topic), then scroll-position-set so a transcript chunk and a screenshot section are visible side by side.
- **Annotation:** None — let the structure speak. The viewer's syntax highlighting + the embedded screenshot in the preview pane is the message.

## 4. Claude.ai with a yoink corpus pasted in, mid-conversation
- **Setup:** Paste a yoink corpus into a fresh Claude conversation. Ask **"Decode the hook"** (one of the bundled prompts). Wait for Claude's response.
- **Crop:** Show the user's message (corpus visible — first ~5 lines is enough to read "Yoinked with Yoink…" footer) and Claude's analysis below. Aim for the corpus header + transcript ribbon to be readable.
- **Annotation:** Caption at the bottom: **"Yoink → Claude in one click."**

## 5. The Desktop\Yoink folder with topic-organized subfolders
- **Setup:** Open File Explorer at `C:\Users\<you>\Desktop\Yoink\`. Have at least 4 topic folders populated (Social Media Research, AI and ML, Productivity and Workflows, etc.) with 2–3 yoinks inside each. Expand 2 of the topic folders so the user sees the subfolder structure.
- **Crop:** Show the folder tree from `Yoink\` down two levels. Set Explorer to "Details" view so timestamps are visible — it sells "this just happened, automatically."
- **Annotation:** Top-right callout: **"Auto-organized by topic."**

---

## Capture tips

- Use Chrome's **Device Toolbar** (`Ctrl+Shift+M`) and set viewport to 1280x800 before capturing the YouTube screenshots — guarantees consistent crop dimensions across all five.
- Light/dark mode: capture in **dark mode** (matches Yoink's brand and most users' YouTube setups).
- Personal data: blur or replace your YouTube avatar in the corner. Don't show an inbox notification badge or a personal homepage.
- Annotation style: use a single brand color (yellow `#FFD354` matches the Yoink mark accent) for arrows and boxes. No drop shadows. Sentence-case captions.

## Order of priority

If you only have time for 3, capture 1, 2, and 4 — they tell the full story (button → popup → result in Claude). 3 and 5 are supporting evidence.
