// Shared helper used by both the content script and the service worker.
// Loaded as a classic script (NOT an ES module) so the same file can be:
//   - listed in manifest content_scripts.js BEFORE content.js
//   - imported via importScripts() inside background.js
// Exposes everything on globalThis.STC.

(function (global) {
  "use strict";

  const SERVER = "http://127.0.0.1:5179";
  const DEFAULT_INTERVAL = 30;
  const REQUEST_TIMEOUT_MS = 10 * 60 * 1000; // 10 minutes

  // ---- Auth token (P0-1) ----------------------------------------------
  // Per-install token issued by the local server. Fetched lazily on first
  // mutating request, cached in memory + chrome.storage.local. On 403 we
  // refresh once (handles the server-reinstall-regenerated-token case).
  // /token is gated server-side by the chrome-extension:// origin, so a
  // webpage attempting CSRF can't grab it.
  const TOKEN_STORAGE_KEY = "yoink_token";
  let _cachedToken = null;
  let _tokenFetchPromise = null;

  function _readStoredToken() {
    return new Promise((r) => {
      try {
        chrome.storage.local.get({ [TOKEN_STORAGE_KEY]: null }, (i) => {
          r((i && i[TOKEN_STORAGE_KEY]) || null);
        });
      } catch { r(null); }
    });
  }
  function _writeStoredToken(t) {
    return new Promise((r) => {
      try { chrome.storage.local.set({ [TOKEN_STORAGE_KEY]: t }, () => r()); }
      catch { r(); }
    });
  }
  async function _fetchFreshToken() {
    try {
      // X-Yoink-Client is the gate header on /token. Random websites
      // can't set custom headers cross-origin without a CORS preflight,
      // which our server only ACAO-echoes for the extension/youtube
      // allowlist -- so a drive-by attacker is blocked at the browser
      // before this header even reaches the server.
      const res = await fetch(`${SERVER}/token`, {
        method: "GET",
        mode: "cors",
        credentials: "omit",
        cache: "no-store",
        headers: { "X-Yoink-Client": "yoink-extension" },
      });
      if (!res.ok) return null;
      const data = await res.json();
      return (data && typeof data.token === "string") ? data.token : null;
    } catch { return null; }
  }
  async function getToken({ refresh = false } = {}) {
    if (!refresh && _cachedToken) return _cachedToken;
    if (!refresh) {
      const stored = await _readStoredToken();
      if (stored) { _cachedToken = stored; return stored; }
    }
    // Coalesce parallel fetches so the first wave of authed requests after
    // a wake doesn't slam /token N times.
    if (!_tokenFetchPromise) {
      _tokenFetchPromise = (async () => {
        const fresh = await _fetchFreshToken();
        if (fresh) {
          _cachedToken = fresh;
          await _writeStoredToken(fresh);
        }
        const out = fresh;
        _tokenFetchPromise = null;
        return out;
      })();
    }
    return _tokenFetchPromise;
  }
  async function _authedFetch(path, init) {
    init = init || {};
    const doFetch = async (tk) => {
      const headers = Object.assign({}, init.headers || {});
      if (tk) headers["X-Yoink-Token"] = tk;
      return fetch(`${SERVER}${path}`, Object.assign({}, init, {
        headers, mode: "cors", credentials: "omit",
      }));
    };
    let token = await getToken();
    let res = await doFetch(token);
    if (res.status === 403) {
      // Server may have regenerated the token (reinstall) -- refresh once.
      token = await getToken({ refresh: true });
      res = await doFetch(token);
    }
    return res;
  }

  // Pull a YouTube video ID out of any of the URL shapes the context menu
  // can hand us. Returns null if nothing video-shaped is found.
  function extractVideoId(rawUrl) {
    let u;
    try { u = new URL(rawUrl); }
    catch { return null; }

    const host = u.hostname.replace(/^www\.|^m\./, "");

    if (host === "youtu.be") {
      const id = u.pathname.replace(/^\/+/, "").split("/")[0];
      return /^[\w-]{6,}$/.test(id) ? id : null;
    }

    if (host === "youtube.com") {
      if (u.pathname === "/watch") {
        const id = u.searchParams.get("v");
        return id && /^[\w-]{6,}$/.test(id) ? id : null;
      }
      const shorts = u.pathname.match(/^\/shorts\/([\w-]{6,})/);
      if (shorts) return shorts[1];
      const embed = u.pathname.match(/^\/embed\/([\w-]{6,})/);
      if (embed) return embed[1];
    }

    return null;
  }

  // Canonicalize to the standard watch URL the server knows how to handle.
  // Strips tracking junk (si, pp, feature, ...); preserves t (timestamp) so
  // future features can use it.
  function normalizeYouTubeUrl(rawUrl) {
    const id = extractVideoId(rawUrl);
    if (!id) return null;

    let t = null;
    try {
      const u = new URL(rawUrl);
      t = u.searchParams.get("t") || u.searchParams.get("start");
    } catch { /* ignore */ }

    let normalized = `https://www.youtube.com/watch?v=${id}`;
    if (t) normalized += `&t=${encodeURIComponent(t)}`;
    return normalized;
  }

  function getInterval() {
    return new Promise((resolve) => {
      try {
        chrome.storage.sync.get({ interval: DEFAULT_INTERVAL }, (items) => {
          let n = parseInt(items && items.interval, 10);
          if (!Number.isFinite(n) || n < 5 || n > 300) n = DEFAULT_INTERVAL;
          resolve(n);
        });
      } catch {
        resolve(DEFAULT_INTERVAL);
      }
    });
  }

  // POST /extract with a 10-minute timeout. Returns the server's JSON body
  // (success or {ok: false, error: ...}). Throws only for network failures
  // (server unreachable / aborted).
  async function postExtract(url, interval) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
    const targetUrl = `${SERVER}/extract`;
    const requestBody = { url, interval };
    try {
      let res;
      try {
        res = await _authedFetch("/extract", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(requestBody),
          signal: controller.signal,
        });
      } catch (e) {
        if (e instanceof TypeError) {
          console.error("[Yoink] server unreachable at", targetUrl, e);
        } else {
          console.error("[Yoink] fetch aborted/failed", targetUrl, e);
        }
        throw e;
      }

      const text = await res.text();
      if (!res.ok) {
        console.error("[Yoink] HTTP", res.status, "body:", text);
      }
      try {
        return JSON.parse(text);
      } catch {
        console.error("[Yoink] JSON parse error, raw text:", text);
        return { ok: false, error: "Server returned a non-JSON response." };
      }
    } finally {
      clearTimeout(timer);
    }
  }

  async function ping() {
    // /health and /ping are intentionally unauthenticated -- they're the
    // public liveness probe used by the popup, the in-page button, and
    // setup.html. Keep them token-free so a stale token can't make the
    // server look offline.
    try {
      const res = await fetch(`${SERVER}/health`, {
        method: "GET", mode: "cors", credentials: "omit", cache: "no-store",
      });
      if (!res.ok) return null;
      return await res.json();
    } catch {
      return null;
    }
  }

  // ---- Session API -----------------------------------------------------
  async function _postJson(path, body) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
    try {
      const res = await _authedFetch(path, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body || {}),
        signal: controller.signal,
      });
      return await res.json().catch(() => ({
        ok: false, error: "Server returned a non-JSON response.",
      }));
    } finally {
      clearTimeout(timer);
    }
  }

  async function _getJson(path) {
    try {
      const res = await _authedFetch(path, { method: "GET" });
      return await res.json().catch(() => ({ ok: false, error: "Bad JSON" }));
    } catch (e) {
      return { ok: false, error: String(e) };
    }
  }

  // ---- Playlist / job API (v2) ----------------------------------------
  // These four endpoints belong to the v2 playlist flow. Popup sets
  // globalThis.YOINK_USE_MOCK_API = true while the real backend is being
  // built on `codex/v2-backend-playlist`; flip it off (in popup.js) once the
  // server lands and reconcile the wire shapes here against docs/v2-api.md.
  function _useMock() {
    return !!(global.YOINK_USE_MOCK_API && global.MOCK_API);
  }
  function playlistPreview(url) {
    if (_useMock()) return global.MOCK_API.playlistPreview(url);
    return _postJson("/playlist/preview", { url });
  }
  function playlistStart(url, interval) {
    // Sprint 5: the popup sources interval from the same chrome.storage.sync
    // setting single-video uses (5-300, default 30). Backend defaults to 30
    // if interval is omitted, so this stays compatible with older callers
    // that pass only url.
    const body = (typeof interval === "number" && Number.isFinite(interval))
      ? { url, interval } : { url };
    if (_useMock()) return global.MOCK_API.playlistStart(url, interval);
    return _postJson("/playlist/start", body);
  }
  function jobStatus(jobId) {
    if (_useMock()) return global.MOCK_API.jobStatus(jobId);
    return _getJson(`/jobs/${encodeURIComponent(jobId)}`);
  }
  function jobCancel(jobId) {
    if (_useMock()) return global.MOCK_API.jobCancel(jobId);
    return _postJson(`/jobs/${encodeURIComponent(jobId)}/cancel`, {});
  }
  function jobsList() {
    if (_useMock()) return global.MOCK_API.jobsList();
    return _getJson("/jobs");
  }
  // Settings helpers — originally landed via codex/v2-sprint2 without mock
  // routing. Wrapped here so mock-mode (USE_MOCK_API = true) exercises the
  // Comment Intelligence settings surface without needing the local server.
  // Shapes mirror docs/v2-comment-intelligence.md.
  function getSettings() {
    if (_useMock()) return global.MOCK_API.getSettings();
    return _getJson("/settings");
  }
  function updateSettings(settings) {
    if (_useMock()) return global.MOCK_API.updateSettings(settings || {});
    return _postJson("/settings", settings || {});
  }
  function testAnthropicKey(anthropicKey) {
    if (_useMock()) return global.MOCK_API.testAnthropicKey(anthropicKey);
    const body = {};
    if (typeof anthropicKey === "string") body.anthropic_key = anthropicKey;
    return _postJson("/settings/test-key", body);
  }

  // ---- Smart Screenshot Picker (Sprint 3) ------------------------------
  // getScreenshotThumbnail wraps the authenticated GET /file?path=<abs-path>
  // endpoint Codex shipped on codex/v2-sprint3. Returns a string usable as
  // <img src>: a blob URL in production (created from the response body),
  // a data URL in mock mode. Callers MAY want to URL.revokeObjectURL() the
  // returned blob URL when the thumbnail is no longer needed; for the
  // popup picker we just rely on the popup unload for cleanup.
  async function getScreenshotThumbnail(path) {
    if (_useMock()) return global.MOCK_API.getScreenshotThumbnail(path);
    if (!path || typeof path !== "string") {
      throw new Error("getScreenshotThumbnail: path required");
    }
    const res = await _authedFetch(
      `/file?path=${encodeURIComponent(path)}`,
      { method: "GET" }
    );
    if (!res.ok) {
      // Surface a short, friendly reason. The server's error string is in
      // a JSON body when content-type allows; otherwise just status text.
      let detail = `${res.status}`;
      try {
        const body = await res.json();
        if (body && body.error) detail = body.error;
      } catch { /* binary or empty body */ }
      throw new Error(`thumbnail fetch failed: ${detail}`);
    }
    const ct = res.headers.get("Content-Type") || "";
    if (!/^image\//i.test(ct)) {
      throw new Error(`thumbnail fetch returned non-image content-type: ${ct}`);
    }
    const blob = await res.blob();
    return URL.createObjectURL(blob);
  }

  // ---- Pending picker stash --------------------------------------------
  // When smart_screenshot_picker_enabled is true, both background.js (queue
  // path) and content.js (in-page-button path) hand the freshly-extracted
  // corpus off to the popup via chrome.storage.local rather than auto-
  // copying. The popup's boot path reads `pending_picker` and renders the
  // picker view. Shared helper so the intercept logic stays in one place.
  //
  // We stash the multimodal paste version (corpus_md_paste) AND the file-
  // reference version (yoink_md) — yoink_md is parsed for absolute
  // screenshot paths (which get sent to /file for thumbnails); the
  // clipboard payload is built from corpus_md_paste so selected screenshots
  // retain their base64-embedded form.
  async function stashPickerCorpus(data) {
    const payload = {
      // yoinked_at marks when the corpus finished extracting (effectively
      // now, since we stash immediately after STC.postExtract resolves).
      // Popup uses this for the picker source-meta relative-time line.
      yoinked_at: new Date().toISOString(),
      title: (data && data.title) || "",
      folder: (data && data.folder) || "",
      corpus_md_paste: (data && data.corpus_md_paste) || "",
      yoink_md: (data && data.yoink_md) || "",
      topic: (data && data.topic) || null,
    };
    return new Promise((resolve) => {
      try {
        chrome.storage.local.set({ pending_picker: payload }, () => resolve(true));
      } catch (e) {
        console.error("[Yoink] stashPickerCorpus failed", e);
        resolve(false);
      }
    });
  }

  function startSession(name) { return _postJson("/session/start", { name: name || "" }); }
  function addToSession(sessionId, url, interval) {
    return _postJson("/session/add", { session_id: sessionId, url, interval });
  }
  function closeSession(sessionId) { return _postJson("/session/close", { session_id: sessionId }); }
  function cancelSession(sessionId) { return _postJson("/session/cancel", { session_id: sessionId }); }
  function listSessions() { return _getJson("/session/list"); }
  function getActiveSession() { return _getJson("/session/active"); }
  function openSession(sessionId) { return _postJson("/session/open", { session_id: sessionId }); }
  function openPromptsFile() { return _getJson("/open-prompts"); }
  function openIndex() { return _getJson("/open-index"); }
  function listRecent() { return _getJson("/recent"); }
  function openFolder(path) {
    return _getJson("/open-folder?path=" + encodeURIComponent(path));
  }

  // ---- Background-proxied versions for content scripts -----------------
  // Page-context fetches from content scripts can be intercepted by Chrome's
  // tracking protection, AV web shields, and similar client-side filters
  // (which produce ERR_BLOCKED_BY_CLIENT even for localhost). Routing through
  // the extension service worker bypasses those filters because the SW's
  // request originates from the extension origin, not the page.
  async function _proxy(type, payload) {
    let res;
    try {
      res = await chrome.runtime.sendMessage({ type, ...payload });
    } catch (e) {
      console.error("[Yoink] background proxy failed", type, e);
      throw new TypeError(`Background SW unreachable: ${e && e.message || e}`);
    }
    if (!res) throw new TypeError("No response from background service worker.");
    if (res.networkError) throw new TypeError(res.networkError);
    return res.data;
  }

  function postExtractViaBg(url, interval) {
    return _proxy("stcExtract", { url, interval });
  }
  function addToSessionViaBg(sessionId, url, interval) {
    return _proxy("stcSessionAdd", { session_id: sessionId, url, interval });
  }

  // Translate raw server errors into copy a non-technical user can act
  // on. By the time this runs the built-in 403-retry has already
  // refreshed the token once and still got rejected, so surfacing
  // "missing or invalid token" verbatim only confuses the user. Map
  // anything that smells like an auth failure to a recovery instruction.
  function friendlyError(rawMsg) {
    const msg = String(rawMsg || "Yoink hit an unknown error.");
    if (/token/i.test(msg) || /\bforbidden\b/i.test(msg)) {
      return "Yoink helper authorization changed. Try reloading the extension or restarting the Yoink helper from your Start Menu.";
    }
    return msg;
  }

  // Shared "yoinked!" notification builder. Called from both the in-page
  // YouTube button success path (content.js) and the SW-driven queue path
  // (background.js) so the first-yoink CTA fires no matter which path the
  // user takes -- previously only the SW path set the flag, and a normal
  // YouTube-button click never got the special copy.
  //
  // Returns the message string to pass to chrome.notifications.create (each
  // caller fires it via its own context-appropriate path). Atomically marks
  // has_completed_first_yoink on the first successful + copied yoink.
  async function buildYoinkedMessage(data, copied) {
    const FIRST_YOINK_MSG =
      "Your first corpus is in your clipboard. Paste in Claude to see what it can do →";
    let firstTime = false;
    try {
      const stored = await new Promise((r) => {
        try {
          chrome.storage.local.get({ has_completed_first_yoink: false }, (i) => r(i));
        } catch { r({ has_completed_first_yoink: false }); }
      });
      firstTime = !stored.has_completed_first_yoink;
      if (firstTime && copied) {
        await new Promise((r) => {
          try {
            chrome.storage.local.set({ has_completed_first_yoink: true }, () => r());
          } catch { r(); }
        });
      }
    } catch { /* fall through to topic-aware copy */ }

    if (firstTime && copied) return FIRST_YOINK_MSG;

    // Topic-aware default copy (subsequent yoinks).
    const realTopic = data && data.topic && data.topic !== "Uncategorized" ? data.topic : null;
    const topicLine = realTopic ? `Saved to: ${realTopic}. ` : "";
    const tail = "Comments and any enabled AI analyses will arrive shortly in the saved corpus file.";
    if (copied) {
      return `${topicLine}Paste with Ctrl+V in Claude or ChatGPT. ${tail}`.trim();
    }
    return `${topicLine}Clipboard was blocked — open the saved file in the yoink folder.`.trim();
  }

  global.STC = {
    SERVER,
    DEFAULT_INTERVAL,
    REQUEST_TIMEOUT_MS,
    extractVideoId,
    normalizeYouTubeUrl,
    getInterval,
    postExtract,
    ping,
    startSession,
    addToSession,
    closeSession,
    cancelSession,
    listSessions,
    getActiveSession,
    openSession,
    postExtractViaBg,
    addToSessionViaBg,
    openPromptsFile,
    openIndex,
    listRecent,
    openFolder,
    buildYoinkedMessage,
    getToken,
    friendlyError,
    playlistPreview,
    playlistStart,
    jobStatus,
    jobCancel,
    jobsList,
    getSettings,
    updateSettings,
    testAnthropicKey,
    getScreenshotThumbnail,
    stashPickerCorpus,
  };
})(typeof self !== "undefined" ? self : globalThis);
