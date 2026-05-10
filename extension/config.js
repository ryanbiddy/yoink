// Yoink extension config surface.
//
// v1 hardcodes these defaults server-side -- this object exists so v1.1
// can wire client-to-server config sync without a structural change.
// Editing values here today does NOT affect behavior; mirror any change
// in server.py (PASTE_MAX_SCREENSHOTS, PASTE_SCREENSHOT_WIDTH,
// PASTE_SCREENSHOT_QUALITY, PASTE_SIZE_WARN_MB) for the change to take
// effect.
//
// Loaded as a classic script. Exposes globalThis.YOINK_CONFIG.

(function (global) {
  "use strict";
  global.YOINK_CONFIG = {
    pasteCorpus: {
      // When true, the clipboard version inlines screenshots as base64
      // data URIs so a single Ctrl+V into Claude/ChatGPT delivers
      // transcript + images. The on-disk <slug>.md keeps local image
      // refs regardless of this flag.
      embedScreenshots: true,
      // Cap on screenshots embedded in the clipboard version. Even an
      // hour-long video at a 30s interval (~120 shots) gets curated
      // down to this many evenly across the timeline.
      maxScreenshots: 12,
      // Resize width before re-encoding to JPEG. Maintains aspect ratio.
      screenshotWidth: 800,
      // JPEG quality for embedded screenshots. 80 keeps file size small
      // without visible artifacts at 800px.
      screenshotQuality: 80,
      // Size threshold for the "this paste is large" warning in the
      // corpus header.
      sizeWarningThresholdMB: 4,
    },
  };
})(typeof self !== "undefined" ? self : globalThis);
