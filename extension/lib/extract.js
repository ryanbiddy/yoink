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
    console.log("[Yoink] POST", targetUrl, requestBody);
    try {
      let res;
      try {
        res = await fetch(targetUrl, {
          method: "POST",
          mode: "cors",
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
    try {
      const res = await fetch(`${SERVER}/ping`, { method: "GET", mode: "cors" });
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
      const res = await fetch(`${SERVER}${path}`, {
        method: "POST",
        mode: "cors",
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
      const res = await fetch(`${SERVER}${path}`, { method: "GET", mode: "cors" });
      return await res.json().catch(() => ({ ok: false, error: "Bad JSON" }));
    } catch (e) {
      return { ok: false, error: String(e) };
    }
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
    listRecent,
    openFolder,
  };
})(typeof self !== "undefined" ? self : globalThis);
