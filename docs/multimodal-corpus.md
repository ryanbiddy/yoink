# Multimodal corpus

## What it is

The clipboard version of every yoink corpus inlines a curated subset of screenshots as base64-encoded JPEG data URIs, so a single Ctrl+V into Claude or ChatGPT delivers the full transcript **and** key images at once. Users do not have to re-upload anything.

The on-disk file at `<slug>.md` is **unchanged** -- it keeps the local relative image references (`screenshots/shot_NNNN.jpg`) so VS Code preview, Obsidian, and similar viewers render the file straight from the folder without bloating it with inline image data.

Two corpora, same content, different transports:

| Variant | Where it lives | Image refs |
|---|---|---|
| File version | `<slug>.md` on disk | `![Screenshot at 0:01:23](screenshots/shot_0042.jpg)` |
| Paste version | `corpus_md_paste` field on the JSON response, written to clipboard by the extension | `![Screenshot at 0:01:23](data:image/jpeg;base64,/9j/4AAQ...)` |

## Pipeline

The server generates the paste version after writing `<slug>.md`:

1. Read the on-disk md.
2. Find every screenshot block via regex (`### [<ts>]` followed by `![Screenshot at <ts>](screenshots/shot_NNNN.jpg)`).
3. **Curate the set.** If there are more screenshots than `clipboard_screenshot_cap` (default 4, valid range 0-12), pick that many evenly distributed indices via linear interpolation. Drop the rest of the blocks entirely from the paste md. If the cap reduces the set, the paste corpus includes a short note that the full screenshot set is still on disk.
4. **Encode each kept shot.** Open with Pillow, resize to `PASTE_SCREENSHOT_WIDTH` (default 800 px) keeping aspect ratio, re-encode as JPEG at `PASTE_SCREENSHOT_QUALITY` (default 80), base64-encode.
5. **Substitute** the data URI back into the screenshot block, preserving the timestamp caption.
6. **Prepend a header** explaining the corpus is multimodal. If the result is over `PASTE_SIZE_WARN_MB` (default 4 MB), the header also includes an "if pasting fails, open the .md directly" note.

The result is returned to the extension as `corpus_md_paste`. The extension's clipboard write paths prefer this field over `yoink_md`, falling back to `yoink_md` only when the paste version is missing -- which happens in two cases:

- Pillow is not installed (dev mode without the bundled installer).
- Generation raised an exception (logged, swallowed; the file md is still written and the user gets a working clipboard).

## Size envelope

Typical totals for the embedded portion only (default 4 shots @ 800 px wide, JPEG q=80):

| Video duration | Embedded shots | Approx clipboard size |
|---|---|---|
| <=6 min @ 30s interval | up to 4 | 0.2 - 0.5 MB |
| 30 min @ 30s interval | 4 (curated) | 0.25 - 0.6 MB |
| 90 min @ 30s interval | 4 (curated) | 0.3 - 0.7 MB |

Plus the transcript text (typically 30-200 KB depending on talkativeness). Real-world clipboards usually stay well under 1 MB at the default cap; the 4 MB warning threshold is for unusual cases like high-resolution source frames or users who raise the cap.

## Configuration

The screenshot cap is a persisted server setting: `clipboard_screenshot_cap` (default 4, min 0, max 12), surfaced on `setup.html` and accepted by `POST /settings`. Resize/quality defaults remain hardcoded in `server.py` (`PASTE_SCREENSHOT_WIDTH`, `PASTE_SCREENSHOT_QUALITY`, `PASTE_SIZE_WARN_MB`).

## Sessions

Per-video paste generation is **off** for `/session/add` calls -- the per-video clipboard message would be wasted bytes since session corpora are not copied one yoink at a time. Sessions get concatenated into one corpus at `/session/close` and that is what gets copied.

## Caveats

- **Comments arrive late.** The comments worker rewrites `<slug>.md` after the initial yoink completes, but the paste version is generated at completion time. If the user pastes immediately, the embedded comments section reads `*Fetching comments...*`. Re-yoinking refreshes.
- **Screenshot picking is opt-in.** The default curation is automatic and timestamp-based. The Smart Screenshot Picker is the user-facing path for choosing exact shots before copying.
- **No JPEG quality slider.** Power users with bandwidth to spare might want full-quality embeds. A future settings surface can expose screenshot quality and width if needed.
