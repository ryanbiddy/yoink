# `assets/store/` — Chrome Web Store assets

Generated placeholders for the v1 submission. Refine in Figma or Canva before submitting.

## Files

| File | Dimensions | Web Store role | Required? |
| --- | --- | --- | --- |
| `promo-small-440x280.png` | 440 × 280 | Small promo tile (search & category cards) | **Required** |
| `promo-large-920x680.png` | 920 × 680 | Large promo tile (featured placements) | Recommended |
| `promo-marquee-1400x560.png` | 1400 × 560 | Marquee promo tile (top of category page) | Optional |

Screenshots aren't generated — they need to be captured from the live product. See `docs/screenshot-list.md` for the 5 required screenshots and capture spec.

## Composition

All three placeholder tiles share the same composition:

- Black background (`#0F0F11`) with a subtle bottom-right glow
- White **YOINK** wordmark on the left (from `assets/wordmark-dark.png`)
- Tagline below: "The missing layer between YouTube and your AI."
- Sub-line: "Local-first. Free. Open source."
- Mock Yoink pill button (top-right) with sparkle accent and "Yoink" label
- Hint below the button: "↑ One click under any YouTube video"

## What to refine before submitting

1. **Real screenshot in the right pane.** Replace the mock pill with an actual cropped screenshot of the in-page Yoink button on a real YouTube video.
2. **Brand polish.** The mock pill uses a generic yellow `#FFD354` — swap to the canonical brand accent if it's defined elsewhere.
3. **Marquee left whitespace.** The 1400 × 560 tile has empty mid-canvas space — Figma can fill it with a video timeline strip or sample yoink markdown preview.
4. **Tagline kerning.** PIL doesn't kern beautifully. The hand-set version in Figma will look noticeably better at the small (440 × 280) size.

## Regenerating the placeholders

```powershell
python C:\Users\hello\OneDrive\Desktop\yt-extractor\assets\build_store_assets.py
```

The script also regenerates `extension/icons/icon-{16,32,48,128}.png` from `assets/logo-mark.png`.

## Asset sources

- **Wordmark** — `assets/wordmark-dark.png` (white wordmark, transparent background, designed for dark surfaces)
- **Mark** — `assets/logo-mark.png` (square symbol, used for all four extension icon sizes)
- **Full logo** — `assets/logo.png` (wordmark + mark together, currently unused in the placeholders but available for redesign)
