# Multimodal corpus

## What it is

The clipboard version of every yoink corpus inlines screenshots as base64-encoded JPEG data URIs, so a single Ctrl+V into Claude or ChatGPT delivers the full transcript **and** the images at once. Users don't have to re-upload anything.

The on-disk file at `<slug>.md` is **unchanged** -- it keeps the local relative image references (`screenshots/shot_NNNN.jpg`) so VS Code preview, Obsidian, and similar viewers render the file straight from the folder without bloating it with ~1 MB of inline image data.

Two corpora, same content, different transports:

| Variant | Where it lives | Image refs |
|---|---|---|
| File version | `<slug>.md` on disk | `![Screenshot at 0:01:23](screenshots/shot_0042.jpg)` |
| Paste version | `corpus_md_paste` field on the JSON response, written to clipboard by the extension | `![Screenshot at 0:01:23](data:image/jpeg;base64,/9j/4AAQ...)` |

## Pipeline

The server generates the paste version after writing `<slug>.md`:

1. Read the on-disk md.
2. Find every screenshot block via regex (`### [<ts>]` followed by `![Screenshot at <ts>](screenshots/shot_NNNN.jpg)`).
3. **Curate the set.** If there are more than `PASTE_MAX_SCREENSHOTS` (default 12), pick that many evenly-distributed indices via linear interpolation -- always landing on the first and last shot exactly. Drop the rest of the blocks entirely from the paste md (better than silently shrinking some images and not others).
4. **Encode each kept shot.** Open with Pillow, resize to `PASTE_SCREENSHOT_WIDTH` (default 800 px) keeping aspect ratio, re-encode as JPEG at `PASTE_SCREENSHOT_QUALITY` (default 80), base64-encode.
5. **Substitute** the data URI back into the screenshot block, preserving the timestamp caption.
6. **Prepend a header** explaining the corpus is multimodal. If the result is over `PASTE_SIZE_WARN_MB` (default 4 MB), the header also includes a "if pasting fails, open the .md directly" note.

The result is returned to the extension as `corpus_md_paste`. The extension's clipboard write paths (in both `background.js` and `content.js`) prefer this field over `yoink_md`, falling back to `yoink_md` only when the paste version is missing -- which happens in two cases:

- Pillow isn't installed (dev mode without the bundled installer).
- Generation raised an exception (logged, swallowed; the file md is still written and the user gets a working clipboard).

## Size envelope

Typical totals for the embedded portion only (12 shots @ 800 px wide, JPEG q=80):

| Video duration | Embedded shots | Approx clipboard size |
|---|---|---|
| ≤6 min @ 30s interval | up to 12 | 0.6 - 1.2 MB |
| 30 min @ 30s interval | 12 (curated) | 0.7 - 1.5 MB |
| 90 min @ 30s interval | 12 (curated) | 0.8 - 1.6 MB |

Plus the transcript text (typically 30-200 KB depending on talkativeness). Real-world clipboards land at 1-2 MB for almost everything; the 4 MB warning threshold is for unusual cases like very high-resolution source frames.

## Configuration

The defaults are hardcoded in `server.py` (`PASTE_MAX_SCREENSHOTS`, `PASTE_SCREENSHOT_WIDTH`, `PASTE_SCREENSHOT_QUALITY`, `PASTE_SIZE_WARN_MB`). The `extension/config.js` file mirrors them on the client side as a future-proof surface for v1.1's planned client-to-server config sync. For v1, editing `extension/config.js` does **not** change behavior -- mirror any change in `server.py` for it to take effect.

## Sessions

Per-video paste generation is **off** for `/session/add` calls -- the per-video clipboard message would be wasted bytes since session corpora aren't copied one yoink at a time. Sessions get concatenated into one corpus at `/session/close` and that's what gets copied. v1.1 will extend the multimodal pipeline to session corpora once the per-video flow is proven in the wild.

## Caveats and v1.1

- **Comments arrive late.** The comments worker rewrites `<slug>.md` after the initial yoink completes, but the paste version is generated at completion time. If the user pastes immediately, the embedded comments section reads `*Fetching comments...*`. Re-yoinking refreshes. v1.1 plan: regenerate the paste version when comments arrive and push to the extension via `chrome.storage.local`.
- **No user-controlled screenshot picking.** The 12-shot curation is automatic and timestamp-based. v1.1 plan: a "pick screenshots" panel in the popup lets the user keep / drop specific shots before copying.
- **No JPEG quality slider.** Power users with bandwidth to spare might want full-quality embeds. v1.1 plan: surface `screenshotQuality` and `screenshotWidth` in the popup interval section.
