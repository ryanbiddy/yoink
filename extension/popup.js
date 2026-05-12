// Popup script. STC.* helpers come from lib/extract.js loaded just before.

// ---- v2 dev flag ----------------------------------------------------------
// FLIP TO FALSE WHEN CODEX BACKEND LANDS.
// Routes STC.playlist* / STC.jobStatus / STC.jobCancel through the local mock
// layer (lib/mock-api.js) instead of the real server. See docs/v2-api.md
// (lands on codex/v2-backend-playlist) for the contract these mocks shadow.
const USE_MOCK_API = true;
globalThis.YOINK_USE_MOCK_API = USE_MOCK_API;

const DEFAULT_INTERVAL = 30;
const CORPUS_WARN_CHARS = 500_000;

// Sprint 3: Comment Intelligence + Hook Type background-work indicator.
// Both features land their analysis after extraction completes; Hook Type
// now waits for comments too (post-Sprint-3-backend decision), so the
// "still running" copy is the same for both. Returns the user-facing
// string given the settings snapshot, or "" if neither feature is on.
// Used by the playlist done panel AND the picker's done state.
function buildBackgroundAiIndicator(settings) {
  if (!settings) return "";
  const ci = !!settings.comment_intelligence_enabled;
  const hook = !!settings.hook_type_enabled;
  // Skip the indicator entirely if no key is set — the features won't run.
  if ((ci || hook) && settings.anthropic_key_set === false) return "";
  if (ci && hook) {
    return "Comment Intelligence and Hook Type are still running in the " +
      "background — re-open per-video .md files in a few minutes for the " +
      "full analysis.";
  }
  if (ci) {
    return "Comment Intelligence is still running in the background — " +
      "re-open per-video .md files in a few minutes for analysis.";
  }
  if (hook) {
    return "Hook Type analysis is still running in the background — " +
      "re-open per-video .md files in a few minutes.";
  }
  return "";
}

// ---- DOM handles ----------------------------------------------------------
const dot = document.getElementById("dot");
const status = document.getElementById("status");
const intervalInput = document.getElementById("interval");
const saved = document.getElementById("saved");

const sessionInactive = document.getElementById("session-inactive");
const sessionActive = document.getElementById("session-active");
const startSection = document.getElementById("start-section");
const sessionNameInput = document.getElementById("session-name");
const startBtn = document.getElementById("start-session");
const recentSessionsEl = document.getElementById("recent-sessions");

const activeNameEl = document.getElementById("active-name");
const activeMetaEl = document.getElementById("active-meta");
const recentAdditionsEl = document.getElementById("recent-additions");
const promptsEl = document.getElementById("prompts");
const endBtn = document.getElementById("end-session");
const cancelBtn = document.getElementById("cancel-session");
const sessionWarn = document.getElementById("session-warn");

const currentEl = document.getElementById("current-job");
const queueEl = document.getElementById("queue-depth");
const clearBtn = document.getElementById("clear-queue");

// ---- Server status --------------------------------------------------------
const statusHelp = document.getElementById("status-help");
const sendClaudeBtn = document.getElementById("send-claude");
const sendChatgptBtn = document.getElementById("send-chatgpt");
const destHint = document.getElementById("dest-hint");
const DEST_HINT_DEFAULT = destHint ? destHint.textContent : "";
const DEST_DISABLED_TIP = "Server must be running to yoink";

function setDestButtonsEnabled(enabled) {
  for (const b of [sendClaudeBtn, sendChatgptBtn]) {
    if (!b) continue;
    b.disabled = !enabled;
    b.title = enabled ? "" : DEST_DISABLED_TIP;
  }
  if (destHint) {
    destHint.textContent = enabled
      ? DEST_HINT_DEFAULT
      : "Start the Yoink helper to enable these.";
  }
}

async function ping() {
  const data = await STC.ping();
  if (data && data.ok) {
    dot.classList.remove("down"); dot.classList.add("up");
    status.textContent = "Yoink is running.";
    if (statusHelp) statusHelp.classList.add("hidden");
    setDestButtonsEnabled(true);
  } else {
    dot.classList.remove("up"); dot.classList.add("down");
    status.textContent = "Yoink server offline";
    if (statusHelp) statusHelp.classList.remove("hidden");
    setDestButtonsEnabled(false);
  }
}

if (statusHelp) {
  statusHelp.addEventListener("click", (ev) => {
    ev.preventDefault();
    chrome.tabs.create({
      url: chrome.runtime.getURL("setup.html?source=offline"),
      active: true,
    });
    window.close();
  });
}

// ---- Interval setting -----------------------------------------------------
function loadInterval() {
  chrome.storage.sync.get({ interval: DEFAULT_INTERVAL }, (items) => {
    let n = parseInt(items.interval, 10);
    if (!Number.isFinite(n) || n < 5 || n > 300) n = DEFAULT_INTERVAL;
    intervalInput.value = n;
  });
}
let saveTimer = null;
function showSaved() {
  saved.classList.add("show");
  if (saveTimer) clearTimeout(saveTimer);
  saveTimer = setTimeout(() => saved.classList.remove("show"), 1200);
}
intervalInput.addEventListener("change", () => {
  let n = parseInt(intervalInput.value, 10);
  if (!Number.isFinite(n)) n = DEFAULT_INTERVAL;
  n = Math.max(5, Math.min(300, n));
  intervalInput.value = n;
  chrome.storage.sync.set({ interval: n }, showSaved);
});

// ---- Session UI -----------------------------------------------------------
let activeSession = null;

function fmtCount(n, noun) {
  return `${n} ${noun}${n === 1 ? "" : "s"}`;
}

function shortLabel(url) {
  try {
    const u = new URL(url);
    const id = u.searchParams.get("v");
    return id ? `youtu.be/${id}` : url;
  } catch { return url; }
}

function renderActive(session) {
  activeSession = session;
  if (!session) {
    sessionActive.classList.add("hidden");
    sessionInactive.classList.remove("hidden");
    return;
  }
  sessionInactive.classList.add("hidden");
  sessionActive.classList.remove("hidden");
  activeNameEl.textContent = session.name || session.id;
  activeMetaEl.textContent = `${fmtCount(session.video_count || 0, "video")} added`;

  const recent = session.recent || [];
  recentAdditionsEl.innerHTML = "";
  if (!recent.length) {
    const empty = document.createElement("div");
    empty.className = "panel-muted";
    empty.style.cssText = "font-size:11px;padding:4px 6px";
    empty.textContent = "No videos yet.";
    recentAdditionsEl.appendChild(empty);
  } else {
    for (const v of recent) {
      const item = document.createElement("div");
      item.className = "recent-item";
      item.title = v.url || "";
      item.textContent = v.title || shortLabel(v.url || "");
      recentAdditionsEl.appendChild(item);
    }
  }
}

async function refreshActiveFromServer() {
  // Ask the background to repull from the server and update storage.
  // Background also fires the storage.onChanged event; we just need to read it.
  try {
    await chrome.runtime.sendMessage({ type: "refreshActiveSession" });
  } catch { /* ignore — fall back to local storage */ }
  const s = await readActiveFromStorage();
  renderActive(s);
}

function readActiveFromStorage() {
  return new Promise((resolve) => {
    chrome.storage.local.get({ active_session: null }, (items) => {
      resolve(items.active_session || null);
    });
  });
}

chrome.storage.onChanged.addListener((changes, area) => {
  if (area === "local" && changes.active_session) {
    renderActive(changes.active_session.newValue || null);
  }
});

// ---- Start session --------------------------------------------------------
startBtn.addEventListener("click", async () => {
  const name = (sessionNameInput.value || "").trim();
  startBtn.disabled = true;
  startBtn.textContent = "Starting...";
  try {
    const res = await STC.startSession(name);
    if (!res || !res.ok) {
      const msg = (res && res.error) || "Failed to start session.";
      alert(msg); // simple inline error — popup is a tight space
      return;
    }
    sessionNameInput.value = "";
    if (startSection.hasAttribute("open")) startSection.removeAttribute("open");
    await refreshActiveFromServer();
  } finally {
    startBtn.disabled = false;
    startBtn.textContent = "Start session";
  }
});

// ---- Cancel session -------------------------------------------------------
cancelBtn.addEventListener("click", async () => {
  if (!activeSession) return;
  if (!confirm(`Cancel session "${activeSession.name}"? Files stay on disk; no corpus will be generated.`)) return;
  cancelBtn.disabled = true;
  try {
    await STC.cancelSession(activeSession.id);
    await refreshActiveFromServer();
    await loadRecentSessions();
  } finally {
    cancelBtn.disabled = false;
  }
});

// ---- End session ----------------------------------------------------------
endBtn.addEventListener("click", async () => {
  if (!activeSession) return;
  const id = activeSession.id;
  const name = activeSession.name || id;

  endBtn.disabled = true;
  endBtn.textContent = "Closing...";
  sessionWarn.classList.add("hidden");

  let res;
  try {
    res = await STC.closeSession(id);
  } catch (e) {
    alert(`Couldn't reach server: ${e}`);
    endBtn.disabled = false;
    endBtn.textContent = "End session";
    return;
  }

  if (!res || !res.ok) {
    alert((res && res.error) || "Failed to close session.");
    endBtn.disabled = false;
    endBtn.textContent = "End session";
    return;
  }

  // Copy via background (offscreen). Popups can call navigator.clipboard
  // directly too — try that first for the fast path, then fall back.
  let copied = false;
  try {
    await navigator.clipboard.writeText(res.corpus_md);
    copied = true;
  } catch {
    try {
      const r = await chrome.runtime.sendMessage({ type: "copyToClipboard", text: res.corpus_md });
      copied = !!(r && r.ok);
    } catch { /* leave copied false */ }
  }

  // Notify. The destination buttons up top let the user pick where to paste,
  // so we don't auto-open a tab here — that would force Claude.
  const lines = `${fmtCount(res.video_count, "video")}, ${fmtCount(res.caption_count || 0, "caption line")}`;
  const note = copied
    ? `Session yoinked! ${lines}. Pick a destination above and paste.`
    : `Session closed. ${lines}. Clipboard failed — corpus.md is in the session folder (already open in Explorer).`;
  await chrome.runtime.sendMessage({ type: "notify", title: "Research session yoinked", message: note });
  if (copied) showToast("Session yoinked! Pick a destination above.");

  // Large-corpus warning
  if ((res.corpus_md || "").length > CORPUS_WARN_CHARS) {
    sessionWarn.classList.remove("hidden");
    sessionWarn.innerHTML =
      `Corpus is ${(res.corpus_md.length / 1000).toFixed(0)}K characters — may exceed the ` +
      `paste-friendly size. Drag <code>corpus.md</code> into Claude or ChatGPT instead.<br>` +
      `<button id="open-folder" class="secondary" style="margin-top:6px">Open session folder</button>`;
    document.getElementById("open-folder").addEventListener("click", () => {
      STC.openSession(id);
    });
  }

  endBtn.disabled = false;
  endBtn.textContent = "End session";
  await refreshActiveFromServer();
  await loadRecentSessions();
});

// ---- Recent sessions ------------------------------------------------------
async function loadRecentSessions() {
  const res = await STC.listSessions();
  recentSessionsEl.innerHTML = "";
  const all = (res && res.sessions) ? res.sessions : [];
  // Show last 5 closed/cancelled (skip the open one since it's shown above).
  const past = all.filter((s) => s.status !== "open").slice(0, 5);
  if (!past.length) {
    const empty = document.createElement("div");
    empty.className = "panel-muted";
    empty.style.cssText = "font-size:11px;padding:4px 6px";
    empty.textContent = "No past sessions yet.";
    recentSessionsEl.appendChild(empty);
    return;
  }
  for (const s of past) {
    const item = document.createElement("div");
    item.className = "recent-item";
    const date = (s.created_at || "").slice(0, 10);
    item.innerHTML = `<span>${escapeHtml(s.name || s.session_id)}</span>` +
                     `<span class="meta">${s.video_count} · ${s.status} · ${date}</span>`;
    item.title = s.folder || "";
    item.addEventListener("click", () => STC.openSession(s.session_id));
    recentSessionsEl.appendChild(item);
  }
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  }[c]));
}

// ---- Prompt library -------------------------------------------------------
// prompts.json is read fresh each time the popup opens. Users can edit it
// directly (see README) and the change shows up next time the popup is opened.
const quickPromptsEl = document.getElementById("quick-prompts");
const popupToast = document.getElementById("popup-toast");

function showToast(message) {
  if (!popupToast) return;
  popupToast.textContent = message;
  popupToast.classList.add("show");
  clearTimeout(showToast._t);
  showToast._t = setTimeout(() => popupToast.classList.remove("show"), 1800);
}

async function fetchPrompts() {
  try {
    const res = await fetch(chrome.runtime.getURL("prompts.json"));
    return await res.json();
  } catch (e) {
    console.warn("[popup] prompts.json missing or invalid", e);
    return [];
  }
}

function renderPromptList(targetEl, prompts) {
  targetEl.innerHTML = "";
  if (!prompts.length) {
    const empty = document.createElement("div");
    empty.className = "panel-muted";
    empty.style.cssText = "font-size:11px;padding:4px 6px";
    empty.textContent = "No prompts defined. Edit prompts.json to add some.";
    targetEl.appendChild(empty);
    return;
  }
  for (const p of prompts) {
    const body = p.prompt || p.text || "";
    const row = document.createElement("div");
    row.className = "prompt-item";

    const label = document.createElement("span");
    label.className = "prompt-label";
    label.title = body;
    label.textContent = p.label || p.id || "(untitled)";

    const btn = document.createElement("button");
    btn.className = "copy-btn";
    btn.textContent = "Copy";
    btn.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(body);
        btn.textContent = "Copied";
        btn.classList.add("copied");
        showToast("Prompt copied! Paste in Claude after the corpus.");
        setTimeout(() => {
          btn.textContent = "Copy";
          btn.classList.remove("copied");
        }, 1500);
      } catch (e) {
        btn.textContent = "Failed";
      }
    });

    row.appendChild(label);
    row.appendChild(btn);
    targetEl.appendChild(row);
  }
}

async function loadPrompts() {
  const prompts = await fetchPrompts();
  // Always-visible Quick Prompts panel.
  if (quickPromptsEl) renderPromptList(quickPromptsEl, prompts);
  // Session panel (only shown when a session is active).
  if (promptsEl) renderPromptList(promptsEl, prompts);
}

// ---- Background queue panel ----------------------------------------------
async function refreshQueue() {
  try {
    const s = await chrome.storage.session.get({
      busy: false, current: null, queue: [],
    });
    if (s.busy && s.current) {
      const label = s.current.title || shortLabel(s.current.url);
      const verb = s.current.kind === "session_add" ? "Adding" : "Extracting";
      currentEl.textContent = `${verb}: ${label}`;
      currentEl.classList.remove("panel-muted");
    } else {
      currentEl.textContent = "Idle.";
      currentEl.classList.add("panel-muted");
    }
    const depth = (s.queue || []).length;
    queueEl.textContent = `${depth} video${depth === 1 ? "" : "s"} queued.`;
    queueEl.classList.toggle("panel-muted", depth === 0);
    clearBtn.disabled = depth === 0;
  } catch (e) {
    console.warn("[popup] refreshQueue failed", e);
  }
}
clearBtn.addEventListener("click", () => {
  clearBtn.disabled = true;
  chrome.runtime.sendMessage({ type: "clearQueue" }, () => refreshQueue());
});

// ---- Recent yoinks --------------------------------------------------------
const recentYoinksEl = document.getElementById("recent-yoinks");

async function loadRecentYoinks() {
  if (!recentYoinksEl) return;
  let recent = [];
  try {
    const res = await STC.listRecent();
    recent = (res && res.recent) || [];
  } catch { /* server may be down — leave the placeholder */ }
  recentYoinksEl.innerHTML = "";
  if (!recent.length) {
    const empty = document.createElement("div");
    empty.className = "panel-muted";
    empty.style.cssText = "font-size:11px;padding:4px 6px";
    empty.textContent = "No yoinks yet.";
    recentYoinksEl.appendChild(empty);
    return;
  }
  for (const r of recent) {
    const item = document.createElement("div");
    item.className = "recent-item";
    item.title = r.folder || "";
    item.innerHTML = `<span>${escapeHtml(r.title || "(untitled)")}</span>` +
                     `<span class="meta">${escapeHtml(r.topic || "—")}</span>`;
    item.addEventListener("click", () => {
      if (r.folder) STC.openFolder(r.folder);
    });
    recentYoinksEl.appendChild(item);
  }
}

// ---- Destination buttons --------------------------------------------------
const CLAUDE_URL = "https://claude.ai/new";
const CHATGPT_URL = "https://chat.openai.com/?model=gpt-4o";

function openDestination(url, label) {
  chrome.tabs.create({ url, active: true });
  showToast(`Yoinked! Paste with Ctrl+V in ${label}.`);
}

document.getElementById("send-claude").addEventListener("click", () => {
  openDestination(CLAUDE_URL, "Claude");
});
document.getElementById("send-chatgpt").addEventListener("click", () => {
  openDestination(CHATGPT_URL, "ChatGPT");
});

// ---- View all yoinks ------------------------------------------------------
// Opens _all-yoinks-index.md in the user's default markdown viewer.
document.getElementById("open-index").addEventListener("click", async (ev) => {
  ev.preventDefault();
  try {
    const res = await STC.openIndex();
    if (!res || res.ok === false) {
      showToast("Couldn't open the yoinks index — server may be down.");
    }
  } catch {
    showToast("Couldn't open the yoinks index — server may be down.");
  }
});

// ---- Settings link (Sprint 2) ---------------------------------------------
// Lives in the popup footer so it's visible in both single-video and playlist
// modes. setup.html is the canonical settings surface (Codex's lane), so we
// just open it in a new tab — never duplicate the form inside the popup.
const openSettingsLink = document.getElementById("open-settings");
if (openSettingsLink) {
  openSettingsLink.addEventListener("click", (ev) => {
    ev.preventDefault();
    chrome.tabs.create({
      url: chrome.runtime.getURL("setup.html?source=popup"),
      active: true,
    });
    window.close();
  });
}

// ---- MCP setup link (Sprint 4) --------------------------------------------
// Deep-links to the MCP section of setup.html. Setup.html ships an id
// "mcp-settings" anchor; the section content (Claude Desktop / Cursor /
// generic HTTP config snippets) is rendered by Codex's setup.js.
const openMcpLink = document.getElementById("open-mcp-setup");
if (openMcpLink) {
  openMcpLink.addEventListener("click", (ev) => {
    ev.preventDefault();
    chrome.tabs.create({
      url: chrome.runtime.getURL("setup.html?source=popup#mcp-settings"),
      active: true,
    });
    window.close();
  });
}

// ---- Boot -----------------------------------------------------------------
ping();
loadInterval();
loadPrompts();
refreshQueue();
refreshActiveFromServer();
loadRecentSessions();
loadRecentYoinks();

const queueTimer = setInterval(refreshQueue, 1000);
const pingTimer = setInterval(ping, 3000);
const sessionTimer = setInterval(async () => {
  // Periodically pull active session from server in case background updated
  // it while popup was open (e.g. context-menu add finished).
  try { await chrome.runtime.sendMessage({ type: "refreshActiveSession" }); }
  catch { /* ignore */ }
}, 2000);
window.addEventListener("unload", () => {
  clearInterval(queueTimer);
  clearInterval(pingTimer);
  clearInterval(sessionTimer);
});

// =====================================================================
// v2 — Playlist mode
// =====================================================================
// Self-contained: only touches its own DOM (#mode-playlist + .mode-btn) and
// the #mode-single wrapper visibility. The single-video flow above runs
// untouched whenever mode = "single".
// ---------------------------------------------------------------------

(function setupPlaylistMode() {
  const POLL_MS = 1000;
  const PLAYLIST_CAP = 10;
  const PHASES = ["metadata", "download", "screenshots", "comments"];

  // Inline SVG placeholder for thumbs the i.ytimg.com fetch couldn't load
  // (mock IDs, age-restricted, or offline). Keeps the row layout stable.
  const PLACEHOLDER_THUMB =
    "data:image/svg+xml;utf8," +
    encodeURIComponent(
      "<svg xmlns='http://www.w3.org/2000/svg' width='80' height='45' viewBox='0 0 80 45'>" +
        "<rect width='80' height='45' fill='#3a3a3f'/>" +
        "<text x='40' y='28' fill='#888' font-size='11' text-anchor='middle' " +
        "font-family='sans-serif'>YT</text></svg>"
    );

  const modeSingleEl = document.getElementById("mode-single");
  const modePlaylistEl = document.getElementById("mode-playlist");
  const modeBtns = document.querySelectorAll(".mode-btn[data-mode]");

  // Input panel
  const inputPanel = document.getElementById("pl-input-panel");
  const urlInput = document.getElementById("pl-url");
  const previewBtn = document.getElementById("pl-preview-btn");
  const inputError = document.getElementById("pl-input-error");

  // Preview panel
  const previewPanel = document.getElementById("pl-preview-panel");
  const previewPlaylistTitleEl = document.getElementById("pl-preview-playlist-title");
  const previewSubtitleEl = document.getElementById("pl-preview-subtitle");
  const previewWarningsEl = document.getElementById("pl-preview-warnings");
  const previewListEl = document.getElementById("pl-preview-list");
  const startBtn = document.getElementById("pl-start-btn");

  // Progress panel
  const progressPanel = document.getElementById("pl-progress-panel");
  const progressPlaylistTitleEl = document.getElementById("pl-progress-playlist-title");
  const progressFill = document.getElementById("pl-progress-fill");
  const progressText = document.getElementById("pl-progress-text");
  const progressMessageEl = document.getElementById("pl-progress-message");
  const progressCiEl = document.getElementById("pl-progress-ci");
  const progressDisconnectEl = document.getElementById("pl-progress-disconnect");
  const progressWarningsEl = document.getElementById("pl-progress-warnings");
  const phaseRow = document.getElementById("pl-phase-row");
  const cancelBtnEl = document.getElementById("pl-cancel-btn");

  // Done panel
  const donePanel = document.getElementById("pl-done-panel");
  const doneSummary = document.getElementById("pl-done-summary");
  const doneMeta = document.getElementById("pl-done-meta");
  const doneMessageEl = document.getElementById("pl-done-message");
  const doneCiEl = document.getElementById("pl-done-ci");
  const doneWarningsEl = document.getElementById("pl-done-warnings");
  const doneFailedListEl = document.getElementById("pl-done-failed-list");
  const openFolderBtn = document.getElementById("pl-open-folder-btn");
  const startAnotherBtn = document.getElementById("pl-start-another-btn");

  // Cancelled panel
  const cancelledPanel = document.getElementById("pl-cancelled-panel");
  const cancelledSummaryEl = document.getElementById("pl-cancelled-summary");
  const cancelledMetaEl = document.getElementById("pl-cancelled-meta");
  const cancelledMessageEl = document.getElementById("pl-cancelled-message");
  const cancelledWarningsEl = document.getElementById("pl-cancelled-warnings");
  const cancelledFolderBtn = document.getElementById("pl-cancelled-folder-btn");
  const cancelledRestartBtn = document.getElementById("pl-cancelled-restart-btn");

  // Failed panel
  const failedPanel = document.getElementById("pl-failed-panel");
  const failedMsg = document.getElementById("pl-failed-msg");
  const failedFolderBtn = document.getElementById("pl-failed-folder-btn");
  const failedRestartBtn = document.getElementById("pl-failed-restart-btn");

  // ---- State -----------------------------------------------------------
  let previewedUrl = null;
  let previewedPlaylist = null;       // unwrapped res.playlist from /playlist/preview
  let activeJobId = null;
  let pollTimer = null;
  let resultPayload = null;           // job.result on completion
  let lastJob = null;                 // most recent job object (any state)
  // Sprint 2: one-time GET /settings snapshot. Only the
  // comment_intelligence_enabled flag is consumed in the popup today; we
  // cache the whole settings object so future read-only reads are free.
  let cachedSettings = null;

  // Fire-and-forget on IIFE boot. The CI indicator's render guards against
  // cachedSettings still being null (treats it as "not enabled") so a slow
  // settings response can't block the progress UI.
  (async function loadSettings() {
    try {
      const res = await STC.getSettings();
      if (res && res.ok && res.settings) cachedSettings = res.settings;
    } catch { /* settings fetch is non-fatal */ }
  })();

  // ---- mode switching --------------------------------------------------
  function setMode(mode) {
    for (const b of modeBtns) {
      const isActive = b.dataset.mode === mode;
      b.classList.toggle("active", isActive);
      b.setAttribute("aria-selected", isActive ? "true" : "false");
    }
    if (mode === "playlist") {
      modeSingleEl.classList.add("hidden");
      modePlaylistEl.classList.remove("hidden");
    } else {
      modePlaylistEl.classList.add("hidden");
      modeSingleEl.classList.remove("hidden");
    }
  }
  for (const b of modeBtns) {
    b.addEventListener("click", () => {
      if (b.disabled) return;
      setMode(b.dataset.mode);
    });
  }

  // ---- helpers ---------------------------------------------------------
  function fmtDuration(seconds) {
    if (seconds == null) return "—";
    const s = Math.max(0, parseInt(seconds, 10) || 0);
    const m = Math.floor(s / 60);
    const r = s % 60;
    return `${m}:${String(r).padStart(2, "0")}`;
  }

  function fmtNullable(v) { return (v == null || v === "") ? "—" : v; }

  function showOnly(panel) {
    for (const el of [inputPanel, previewPanel, progressPanel, donePanel, cancelledPanel, failedPanel]) {
      if (!el) continue;
      el.classList.toggle("hidden", el !== panel);
    }
  }

  function showError(msg) {
    inputError.textContent = msg;
    inputError.classList.remove("hidden");
  }
  function clearError() {
    inputError.textContent = "";
    inputError.classList.add("hidden");
  }

  // Render the contract's `warnings: [...]` array into a strip element.
  // Hides the strip when the array is empty/missing.
  function renderWarnings(stripEl, warnings) {
    if (!stripEl) return;
    const list = Array.isArray(warnings) ? warnings : [];
    if (!list.length) {
      stripEl.textContent = "";
      stripEl.classList.add("hidden");
      return;
    }
    // Multi-warning support: join with " · ". The contract only specifies
    // "playlist exceeds cap" today but the shape is an array — handle N.
    stripEl.textContent = list.join(" · ");
    stripEl.classList.remove("hidden");
  }

  function setText(el, text) {
    if (!el) return;
    el.textContent = text || "";
    el.classList.toggle("hidden", !text);
  }

  function resetPlaylistUI() {
    stopPolling();
    activeJobId = null;
    resultPayload = null;
    lastJob = null;
    previewedUrl = null;
    previewedPlaylist = null;
    urlInput.value = "";
    clearError();
    previewListEl.innerHTML = "";
    renderWarnings(previewWarningsEl, []);
    renderWarnings(progressWarningsEl, []);
    renderWarnings(doneWarningsEl, []);
    renderWarnings(cancelledWarningsEl, []);
    setText(previewPlaylistTitleEl, "");
    setText(previewSubtitleEl, "");
    setText(progressPlaylistTitleEl, "");
    setText(progressMessageEl, "");
    setText(doneMessageEl, "");
    setText(cancelledMessageEl, "");
    setText(cancelledMetaEl, "");
    doneFailedListEl.innerHTML = "";
    doneFailedListEl.classList.add("hidden");
    progressFill.style.width = "0%";
    progressText.textContent = "Queued…";
    progressCiEl.classList.add("hidden");
    progressDisconnectEl.classList.add("hidden");
    progressDisconnectEl.textContent = "";
    doneCiEl.classList.add("hidden");
    doneCiEl.textContent = "";
    for (const chip of phaseRow.querySelectorAll(".pl-phase-chip")) {
      chip.classList.remove("active", "done");
    }
    showOnly(inputPanel);
  }

  // ---- preview ---------------------------------------------------------
  function isLikelyPlaylistUrl(s) {
    // Light client-side guard — the backend is authoritative. Just enough to
    // catch an obvious mis-paste so we don't 500ms-spin on garbage.
    if (!s) return false;
    try {
      const u = new URL(s);
      if (!/youtube\.com$|youtu\.be$/.test(u.hostname.replace(/^www\.|^m\./, ""))) return false;
      return u.searchParams.has("list") || u.pathname.includes("/playlist");
    } catch {
      return false;
    }
  }

  previewBtn.addEventListener("click", async () => {
    const raw = (urlInput.value || "").trim();
    clearError();
    if (!isLikelyPlaylistUrl(raw)) {
      showError("That doesn't look like a YouTube playlist URL.");
      return;
    }
    previewBtn.disabled = true;
    previewBtn.textContent = "Previewing…";
    try {
      const res = await STC.playlistPreview(raw);
      if (!res || !res.ok || !res.playlist) {
        showError((res && res.error) || "Couldn't preview that playlist.");
        return;
      }
      previewedUrl = raw;
      previewedPlaylist = res.playlist;
      renderPreview(res.playlist);
      showOnly(previewPanel);
    } catch (e) {
      showError(`Preview failed: ${e && e.message || e}`);
    } finally {
      previewBtn.disabled = false;
      previewBtn.textContent = "Preview";
    }
  });

  function renderPreview(playlist) {
    // Playlist heading line + uploader.
    previewPlaylistTitleEl.textContent = playlist.title || "(untitled playlist)";
    const uploader = fmtNullable(playlist.uploader);
    const vc = playlist.video_count != null ? playlist.video_count : (playlist.videos || []).length;
    const willProc = playlist.will_process_count != null ? playlist.will_process_count : (playlist.videos || []).length;
    previewSubtitleEl.textContent = `${uploader} · ${willProc} of ${vc} videos`;

    // Message (e.g., "Playlist has 12 videos -- yoinking the first 10.")
    // is displayed as the warnings strip when present alongside warnings,
    // otherwise we surface it inside the warnings strip too — it carries
    // the same "be aware of the cap" signal as the warnings list.
    // Per the contract, prefer `message` (human copy) when both exist;
    // fall back to the raw warnings list when only warnings are present.
    const warnings = playlist.warnings || [];
    if (playlist.message) {
      renderWarnings(previewWarningsEl, [playlist.message]);
    } else {
      renderWarnings(previewWarningsEl, warnings);
    }

    // Video list — contract shape: {index, id, url, title, channel, duration_seconds}
    previewListEl.innerHTML = "";
    for (const v of (playlist.videos || [])) {
      const row = document.createElement("div");
      row.className = "pl-video";

      // Thumb from YouTube's standard mqdefault path. The onerror swap is
      // what handles mock IDs (which 404) and the offline case.
      const img = document.createElement("img");
      img.className = "pl-thumb";
      img.alt = "";
      if (v.id) img.src = `https://i.ytimg.com/vi/${encodeURIComponent(v.id)}/mqdefault.jpg`;
      else img.src = PLACEHOLDER_THUMB;
      img.addEventListener("error", () => {
        if (img.src !== PLACEHOLDER_THUMB) img.src = PLACEHOLDER_THUMB;
      });
      row.appendChild(img);

      const meta = document.createElement("div");
      meta.className = "pl-meta";
      const title = document.createElement("div");
      title.className = "pl-title";
      title.textContent = v.title || "(untitled)";
      const sub = document.createElement("div");
      sub.className = "pl-duration";
      // channel and duration_seconds may both be null per the contract.
      // Use the nullable-format helper instead of erroring.
      const channelLabel = fmtNullable(v.channel);
      const durationLabel = fmtDuration(v.duration_seconds);
      sub.textContent = `${channelLabel} · ${durationLabel}`;
      meta.appendChild(title);
      meta.appendChild(sub);
      row.appendChild(meta);

      previewListEl.appendChild(row);
    }

    startBtn.disabled = !(playlist.videos && playlist.videos.length);
  }

  // ---- start -----------------------------------------------------------
  startBtn.addEventListener("click", async () => {
    if (!previewedUrl) return;
    startBtn.disabled = true;
    startBtn.textContent = "Starting…";
    try {
      // Sprint 5: source interval from the same chrome.storage.sync setting
      // single-video uses, so the popup's interval slider actually applies
      // to playlist jobs. Backend defaults to 30 if we sent nothing.
      const interval = await STC.getInterval();
      const res = await STC.playlistStart(previewedUrl, interval);
      // Contract: returns both top-level job_id and nested job.
      if (!res || !res.ok || !res.job_id) {
        showError((res && res.error) || "Couldn't start playlist yoink.");
        showOnly(inputPanel);
        return;
      }
      activeJobId = res.job_id;
      lastJob = res.job || null;

      // Pre-paint the progress panel from the start response so we don't
      // wait a poll tick for the title/warnings/message to appear.
      progressFill.style.width = "0%";
      const total = (res.job && res.job.videos_total) ||
        (previewedPlaylist && previewedPlaylist.will_process_count) ||
        PLAYLIST_CAP;
      progressText.textContent = `Queued — ${total} videos`;
      progressPlaylistTitleEl.textContent =
        (res.job && res.job.playlist_title) ||
        (previewedPlaylist && previewedPlaylist.title) || "";
      progressPlaylistTitleEl.classList.toggle("hidden", !progressPlaylistTitleEl.textContent);
      setText(progressMessageEl, (res.job && res.job.message) || "");
      renderWarnings(progressWarningsEl, (res.job && res.job.warnings) || []);

      for (const chip of phaseRow.querySelectorAll(".pl-phase-chip")) {
        chip.classList.remove("active", "done");
      }
      showOnly(progressPanel);
      startPolling();
    } catch (e) {
      showError(`Start failed: ${e && e.message || e}`);
      showOnly(inputPanel);
    } finally {
      startBtn.disabled = false;
      startBtn.textContent = "Yoink playlist";
    }
  });

  // ---- polling ---------------------------------------------------------
  // Sprint 5: polling becomes self-healing. A transient network blip used to
  // silently swallow errors and let the progress panel freeze. Now we count
  // consecutive failures; after STALL_THRESHOLD the panel shows a banner
  // and the poll cadence downshifts to SLOW_POLL_MS so a recovered helper
  // auto-reconnects without burning the user's network. A single successful
  // poll resets both the counter and the cadence.
  const STALL_THRESHOLD = 5;     // consecutive failures before banner shows
  const SLOW_POLL_MS = 10_000;   // recovery cadence once stalled
  let pollFailures = 0;
  let pollCadence = POLL_MS;     // current interval between pollOnce ticks

  function startPolling() {
    stopPolling();
    pollFailures = 0;
    pollCadence = POLL_MS;
    progressDisconnectEl.classList.add("hidden");
    progressDisconnectEl.textContent = "";
    pollOnce();
    pollTimer = setInterval(pollOnce, pollCadence);
  }
  function stopPolling() {
    if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
  }

  function _setPollCadence(ms) {
    if (ms === pollCadence) return;
    pollCadence = ms;
    if (pollTimer) {
      clearInterval(pollTimer);
      pollTimer = setInterval(pollOnce, pollCadence);
    }
  }

  function _onPollSuccess() {
    if (pollFailures === 0 && pollCadence === POLL_MS) return;
    pollFailures = 0;
    progressDisconnectEl.classList.add("hidden");
    progressDisconnectEl.textContent = "";
    _setPollCadence(POLL_MS);
  }

  function _onPollFailure(reason) {
    pollFailures++;
    if (pollFailures < STALL_THRESHOLD) return;
    // First time we cross the threshold, paint the banner and downshift.
    // Keep painting on subsequent failures so the message reason stays fresh.
    progressDisconnectEl.textContent =
      "Yoink helper disconnected — check that the helper is running. " +
      "Retrying in the background…";
    progressDisconnectEl.classList.remove("hidden");
    _setPollCadence(SLOW_POLL_MS);
    if (reason) console.warn("[playlist] poll stalled:", reason);
  }

  async function pollOnce() {
    if (!activeJobId) return;
    let res;
    try {
      res = await STC.jobStatus(activeJobId);
    } catch (e) {
      _onPollFailure(e);
      return;
    }
    if (!res || !res.ok) {
      // ok:false with a non-recoverable error -> fail the job. But a
      // transient {ok: false} without a recognisable error string is also
      // treated as a poll failure (helper restarted mid-call, body parse
      // failed, etc) — give it the stall budget before declaring failure.
      const err = res && res.error;
      if (err && /not found|invalid/i.test(String(err))) {
        stopPolling();
        enterFailed(err);
        return;
      }
      _onPollFailure(err || "no response");
      return;
    }
    if (!res.job) {
      _onPollFailure("missing job field");
      return;
    }
    _onPollSuccess();
    const job = res.job;
    lastJob = job;
    renderProgress(job);

    if (job.state === "completed") {
      stopPolling();
      resultPayload = job.result || null;
      await onCompleted(job);
    } else if (job.state === "cancelled") {
      stopPolling();
      onCancelled(job);
    } else if (job.state === "failed") {
      stopPolling();
      enterFailed(job.error || "Playlist yoink failed.");
    }
  }

  function renderProgress(job) {
    const total = job.videos_total ||
      (previewedPlaylist && previewedPlaylist.will_process_count) ||
      PLAYLIST_CAP;
    const done = job.videos_done || 0;
    const failed = job.videos_failed || 0;
    // Progress = successful + failed (both consume a "slot"), so the bar
    // doesn't stall when a video fails.
    const advanced = Math.min(total, done + failed);
    const pct = total > 0 ? Math.min(100, Math.round((advanced / total) * 100)) : 0;
    progressFill.style.width = `${pct}%`;

    if (job.state === "queued") {
      progressText.textContent = `Queued — ${total} videos`;
    } else if (job.current_video) {
      const title = job.current_video.title || "(untitled)";
      const idx = job.current_video.index || (advanced + 1);
      progressText.textContent = `Video ${idx} of ${total}: ${title}`;
    } else if (job.state === "running") {
      progressText.textContent = `${done} of ${total} videos done`;
    }

    setText(progressMessageEl, job.message || "");
    renderWarnings(progressWarningsEl, job.warnings || []);
    if (job.playlist_title) {
      progressPlaylistTitleEl.textContent = job.playlist_title;
      progressPlaylistTitleEl.classList.remove("hidden");
    }

    // Sprint 2: Comment Intelligence indicator. Only when the current video
    // is actually in the comments phase AND the user has CI enabled.
    // Comments phase runs in the background; we tell the user it won't
    // block playlist progress so they don't think the bar has stalled.
    const ciEnabled = !!(cachedSettings && cachedSettings.comment_intelligence_enabled);
    const inCommentsPhase = job.current_video_phase === "comments";
    progressCiEl.classList.toggle("hidden", !(ciEnabled && inCommentsPhase));

    // Phase chips: highlight active, mark prior phases done.
    const activeIdx = PHASES.indexOf(job.current_video_phase);
    for (const chip of phaseRow.querySelectorAll(".pl-phase-chip")) {
      const phase = chip.dataset.phase;
      const idx = PHASES.indexOf(phase);
      chip.classList.remove("active", "done");
      if (activeIdx < 0) continue;
      if (idx < activeIdx) chip.classList.add("done");
      else if (idx === activeIdx) chip.classList.add("active");
    }
  }

  // ---- cancel ----------------------------------------------------------
  cancelBtnEl.addEventListener("click", async () => {
    if (!activeJobId) return;
    cancelBtnEl.disabled = true;
    cancelBtnEl.textContent = "Cancelling…";
    try {
      const res = await STC.jobCancel(activeJobId);
      // Contract: cancel returns the full updated job. If we got it, render
      // the cancelled view immediately instead of waiting for the next poll
      // tick — same data, faster transition.
      if (res && res.ok && res.job) {
        stopPolling();
        lastJob = res.job;
        onCancelled(res.job);
      }
      // If res.ok is false (e.g. "job is already finished"), let the next
      // poll tick observe the real terminal state.
    } catch (e) {
      console.warn("[playlist] cancel failed", e);
    } finally {
      // Reset button state in case the panel hasn't flipped yet.
      setTimeout(() => {
        cancelBtnEl.disabled = false;
        cancelBtnEl.textContent = "Cancel";
      }, 1500);
    }
  });

  function onCancelled(job) {
    const done = job.videos_done || 0;
    const failed = job.videos_failed || 0;
    const total = job.videos_total || PLAYLIST_CAP;
    cancelledSummaryEl.textContent = "Cancelled.";
    setText(cancelledMetaEl, `${done} of ${total} videos completed${failed ? ` · ${failed} failed` : ""}`);
    setText(cancelledMessageEl, job.message || "");
    renderWarnings(cancelledWarningsEl, job.warnings || []);
    showOnly(cancelledPanel);
  }

  // ---- completion ------------------------------------------------------
  async function onCompleted(job) {
    const result = job.result || {};
    let copied = false;
    const corpusText = result.combined_md_text || "";
    if (corpusText) {
      try {
        await navigator.clipboard.writeText(corpusText);
        copied = true;
      } catch {
        try {
          const r = await chrome.runtime.sendMessage({
            type: "copyToClipboard",
            text: corpusText,
          });
          copied = !!(r && r.ok);
        } catch { /* leave copied=false */ }
      }
    }

    const perVideo = result.per_video || [];
    const successCount = perVideo.filter((p) => p.ok).length;
    const failedVideos = perVideo.filter((p) => p.ok === false);
    const kb = corpusText ? (corpusText.length / 1024).toFixed(1) : "0";

    doneSummary.textContent = copied
      ? "Done — corpus copied to clipboard"
      : "Done — clipboard blocked, open the folder";

    const totalProcessed = perVideo.length || job.videos_total || 0;
    const metaBits = [`${successCount} of ${totalProcessed} videos`, `${kb} KB combined`];
    if (failedVideos.length) metaBits.splice(1, 0, `${failedVideos.length} failed`);
    doneMeta.textContent = metaBits.join(" · ");

    setText(doneMessageEl, job.message || "");
    renderWarnings(doneWarningsEl, job.warnings || []);
    renderFailedList(failedVideos);

    // Sprint 3: CI/Hook still-running indicator. Shown when either feature
    // is enabled (and the user has a key set); the per-video .md files
    // keep updating on disk after the playlist transitions to completed.
    const aiCopy = buildBackgroundAiIndicator(cachedSettings);
    doneCiEl.textContent = aiCopy;
    doneCiEl.classList.toggle("hidden", !aiCopy);

    showOnly(donePanel);

    if (copied) showToast("Playlist yoinked! Paste in Claude or ChatGPT.");
  }

  function renderFailedList(failed) {
    doneFailedListEl.innerHTML = "";
    if (!failed.length) {
      doneFailedListEl.classList.add("hidden");
      return;
    }
    for (const f of failed) {
      const item = document.createElement("div");
      item.className = "pl-failed-item";

      const titleLine = document.createElement("div");
      const icon = document.createElement("span");
      icon.className = "pl-failed-icon";
      icon.textContent = "⚠";
      const titleSpan = document.createElement("span");
      titleSpan.className = "pl-failed-title";
      titleSpan.textContent = `#${f.index} ${f.title || "(untitled)"}`;
      titleLine.appendChild(icon);
      titleLine.appendChild(titleSpan);
      item.appendChild(titleLine);

      const errLine = document.createElement("div");
      errLine.className = "pl-failed-error";
      errLine.textContent = f.error || "Failed (no detail provided).";
      item.appendChild(errLine);

      doneFailedListEl.appendChild(item);
    }
    doneFailedListEl.classList.remove("hidden");
  }

  // Sprint 2: Open Folder targets job.session_folder, which the contract
  // guarantees is populated from `queued` onwards and through every terminal
  // state (cancelled, failed, completed). Fall back to result.combined_md_path
  // only defensively (older job snapshots before Sprint 2).
  async function openSessionFolder() {
    const path =
      (lastJob && lastJob.session_folder) ||
      (resultPayload && resultPayload.combined_md_path) ||
      null;
    if (!path) {
      showToast("No folder path available.");
      return;
    }
    try {
      // openFolder is the existing v1 server endpoint — in mock mode the
      // server may not be running, in which case the toast below is the
      // recovery.
      const res = await STC.openFolder(path);
      if (!res || res.ok === false) showToast("Couldn't open folder — server may be offline.");
    } catch {
      showToast("Couldn't open folder — server may be offline.");
    }
  }
  openFolderBtn.addEventListener("click", openSessionFolder);
  cancelledFolderBtn.addEventListener("click", openSessionFolder);
  failedFolderBtn.addEventListener("click", openSessionFolder);

  startAnotherBtn.addEventListener("click", resetPlaylistUI);
  cancelledRestartBtn.addEventListener("click", resetPlaylistUI);
  failedRestartBtn.addEventListener("click", resetPlaylistUI);

  function enterFailed(msg) {
    failedMsg.textContent = msg;
    showOnly(failedPanel);
  }

  // ---- boot ------------------------------------------------------------
  // Sprint 5: try to recover an in-flight playlist job before settling into
  // the default input view. If the user closed the popup mid-job, the helper
  // is still running the work in-process and `GET /jobs` will return it.
  // When we find a non-terminal job we flip into playlist mode, repaint the
  // progress panel from the snapshot, and resume polling. If none found,
  // default to the input panel as before.
  showOnly(inputPanel);
  (async function recoverActiveJob() {
    let res;
    try {
      res = await STC.jobsList();
    } catch { return; }
    if (!res || !res.ok || !Array.isArray(res.jobs)) return;
    const active = res.jobs
      .filter((j) => j && (j.state === "queued" || j.state === "running"))
      .sort((a, b) => String(b.updated_at || "").localeCompare(String(a.updated_at || "")))[0];
    if (!active || !active.id) return;
    activeJobId = active.id;
    lastJob = active;
    progressFill.style.width = "0%";
    for (const chip of phaseRow.querySelectorAll(".pl-phase-chip")) {
      chip.classList.remove("active", "done");
    }
    renderProgress(active);
    showOnly(progressPanel);
    setMode("playlist");
    startPolling();
  })();
})();

// =====================================================================
// v3 — Smart Screenshot Picker
// =====================================================================
// Activates when chrome.storage.local.pending_picker is set by the
// background or content-script intercept. When active, the picker view
// owns the popup surface (mode selector and both mode panels hide). On
// Copy/Cancel: writes the chosen corpus to clipboard, clears the pending
// state, opens Claude, and closes the popup — same end behavior as v1
// auto-copy, just user-mediated.
// ---------------------------------------------------------------------

(function setupPickerMode() {
  const pickerMode = document.getElementById("picker-mode");
  const modeSelectorWrap = document.getElementById("mode-selector-wrap");
  const modeSingleEl = document.getElementById("mode-single");
  const modePlaylistEl = document.getElementById("mode-playlist");
  const pickerTitleEl = document.getElementById("picker-title");
  const pickerSourceMetaEl = document.getElementById("picker-source-meta");
  const pickerCountEl = document.getElementById("picker-count");
  const pickerSelectAllLink = document.getElementById("picker-select-all");
  const pickerGridEl = document.getElementById("picker-grid");
  const pickerErrorEl = document.getElementById("picker-error");
  const pickerDoneIndicatorEl = document.getElementById("picker-done-indicator");
  const pickerCancelBtn = document.getElementById("picker-cancel-btn");
  const pickerCopyBtn = document.getElementById("picker-copy-btn");

  // ---- State ----
  let pendingPicker = null;
  let screenshots = [];      // [{ alt, path }] parsed from yoink_md
  let selectedSet = new Set(); // 0-based indices selected for copy
  let cachedSettings = null;
  let thumbCache = new Map();  // path -> blob/data URL (avoid refetching)

  // One-time settings snapshot for the CI/Hook done indicator. Same
  // pattern as the playlist controller (its own fetch is isolated from
  // this one — cheap enough that the duplication is worth the
  // encapsulation).
  (async function loadSettings() {
    try {
      const res = await STC.getSettings();
      if (res && res.ok && res.settings) cachedSettings = res.settings;
    } catch { /* settings fetch is non-fatal */ }
  })();

  // ---- Visibility ----
  function showPicker() {
    pickerMode.classList.remove("hidden");
    modeSelectorWrap.classList.add("hidden");
    modeSingleEl.classList.add("hidden");
    modePlaylistEl.classList.add("hidden");
  }
  function hidePicker() {
    pickerMode.classList.add("hidden");
    modeSelectorWrap.classList.remove("hidden");
    // Whichever mode the user was in before the picker activated isn't
    // tracked — restoring to single-video matches the v1 boot default.
    modeSingleEl.classList.remove("hidden");
    modePlaylistEl.classList.add("hidden");
  }

  // ---- Parse screenshots from yoink_md ----
  // Match the file-reference markdown: ![alt](C:/.../shot_0001.jpg).
  // Tolerant of leading whitespace; rejects data: URLs (those belong to
  // the multimodal paste corpus, not the canonical screenshot list).
  function parseScreenshots(yoinkMd) {
    if (!yoinkMd) return [];
    const out = [];
    const re = /!\[([^\]]*)\]\(([^)]+)\)/g;
    let m;
    while ((m = re.exec(yoinkMd)) !== null) {
      const src = m[2].trim();
      if (src.startsWith("data:")) continue;
      out.push({ alt: m[1] || "", path: src });
    }
    return out;
  }

  // Build a filtered corpus by removing image lines at the given drop
  // indices. Operates on whichever corpus the user wants to send to
  // clipboard — for the multimodal-paste case (corpus_md_paste) this
  // preserves the base64-embedded form for KEPT screenshots while
  // dropping unselected ones. Image lines are matched in source order
  // and aligned to the yoink_md parse positionally.
  function buildFilteredCorpus(sourceCorpus, dropIndices) {
    if (!sourceCorpus) return "";
    if (!dropIndices || !dropIndices.length) return sourceCorpus;
    const lines = sourceCorpus.split(/\r?\n/);
    const imgLineIndices = [];
    const re = /!\[[^\]]*\]\([^)]+\)/;
    for (let i = 0; i < lines.length; i++) {
      if (re.test(lines[i])) imgLineIndices.push(i);
    }
    const dropSet = new Set();
    for (const idx of dropIndices) {
      if (idx >= 0 && idx < imgLineIndices.length) {
        dropSet.add(imgLineIndices[idx]);
      }
    }
    return lines.filter((_, i) => !dropSet.has(i)).join("\n");
  }

  // ---- Rendering ----
  function updateCount() {
    pickerCountEl.textContent =
      `${selectedSet.size} of ${screenshots.length} selected`;
    pickerSelectAllLink.textContent =
      selectedSet.size === screenshots.length ? "Deselect all" : "Select all";
    pickerCopyBtn.disabled = false; // 0-selected is a valid (text-only) copy
  }

  function renderGrid() {
    pickerGridEl.innerHTML = "";
    if (!screenshots.length) {
      const empty = document.createElement("div");
      empty.className = "picker-empty";
      empty.textContent = "No screenshots found in this yoink.";
      pickerGridEl.appendChild(empty);
      pickerCopyBtn.disabled = false; // copy the unmodified corpus
      pickerSelectAllLink.classList.add("hidden");
      return;
    }
    pickerSelectAllLink.classList.remove("hidden");
    for (let i = 0; i < screenshots.length; i++) {
      const s = screenshots[i];
      const tile = document.createElement("div");
      tile.className = "picker-thumb loading";
      if (selectedSet.has(i)) tile.classList.add("selected");
      tile.dataset.index = String(i);
      tile.title = s.alt || s.path;

      const img = document.createElement("img");
      img.alt = s.alt || "";
      // Lazy-load thumbnails. The grid is typically <20 items; firing
      // them all in parallel is fine for local server load.
      _loadThumb(s.path).then((src) => {
        img.src = src;
        tile.classList.remove("loading");
      }).catch((err) => {
        tile.classList.remove("loading");
        console.warn("[picker] thumb load failed", s.path, err);
        // Leave the diagonal-stripe loading background visible so the
        // tile is still clickable (user can include/exclude even when
        // the thumb didn't render).
      });
      tile.appendChild(img);

      const idxBadge = document.createElement("div");
      idxBadge.className = "picker-thumb-index";
      idxBadge.textContent = String(i + 1);
      tile.appendChild(idxBadge);

      const check = document.createElement("div");
      check.className = "picker-thumb-check";
      check.textContent = "✓";
      tile.appendChild(check);

      tile.addEventListener("click", () => toggleIndex(i));
      pickerGridEl.appendChild(tile);
    }
  }

  async function _loadThumb(path) {
    if (thumbCache.has(path)) return thumbCache.get(path);
    const src = await STC.getScreenshotThumbnail(path);
    thumbCache.set(path, src);
    return src;
  }

  // Sprint 4 (1c): real-mode thumbnails come back as blob: URLs from
  // URL.createObjectURL(). Revoke them on picker exit so they don't sit
  // in memory until popup unload. Mock-mode entries are data: URLs — no
  // revocation needed (and revoking a data URL is a no-op anyway, but
  // skipping the call keeps the loop cheap on large grids).
  function _revokeThumbBlobs() {
    for (const src of thumbCache.values()) {
      if (typeof src === "string" && src.startsWith("blob:")) {
        try { URL.revokeObjectURL(src); }
        catch (e) { console.warn("[picker] revoke failed", e); }
      }
    }
    thumbCache.clear();
  }

  function toggleIndex(i) {
    if (selectedSet.has(i)) selectedSet.delete(i);
    else selectedSet.add(i);
    const tile = pickerGridEl.querySelector(`[data-index="${i}"]`);
    if (tile) tile.classList.toggle("selected", selectedSet.has(i));
    updateCount();
  }

  pickerSelectAllLink.addEventListener("click", () => {
    if (selectedSet.size === screenshots.length) {
      selectedSet.clear();
    } else {
      selectedSet = new Set(screenshots.map((_, i) => i));
    }
    for (const tile of pickerGridEl.querySelectorAll(".picker-thumb")) {
      const i = parseInt(tile.dataset.index, 10);
      tile.classList.toggle("selected", selectedSet.has(i));
    }
    updateCount();
  });

  // ---- Activation ----
  function showError(msg) {
    pickerErrorEl.textContent = msg || "";
    pickerErrorEl.classList.toggle("hidden", !msg);
  }

  // Sprint 4 (1b): relative-time helper for the picker source meta line.
  // Two-yoinks-back-to-back disambiguation: when pending_picker gets
  // overwritten, the user sees "just now" vs "3m ago" so they know which
  // yoink they're looking at. Falls back to ISO date for older stashes
  // (shouldn't happen in practice — pending_picker is cleared on copy).
  function formatRelativeTime(iso) {
    if (!iso) return "";
    const t = Date.parse(iso);
    if (Number.isNaN(t)) return "";
    const diffSec = Math.max(0, Math.round((Date.now() - t) / 1000));
    if (diffSec < 10) return "just now";
    if (diffSec < 60) return `${diffSec}s ago`;
    if (diffSec < 3600) {
      const m = Math.round(diffSec / 60);
      return `${m}m ago`;
    }
    if (diffSec < 86400) {
      const h = Math.round(diffSec / 3600);
      return `${h}h ago`;
    }
    return iso.slice(0, 10); // YYYY-MM-DD fallback
  }

  function activate(payload) {
    pendingPicker = payload || null;
    if (!pendingPicker) { hidePicker(); return; }
    showError("");
    pickerTitleEl.textContent = pendingPicker.title || "Untitled video";
    const rel = formatRelativeTime(pendingPicker.yoinked_at);
    pickerSourceMetaEl.textContent = rel ? `Yoinked ${rel}` : "";
    screenshots = parseScreenshots(pendingPicker.yoink_md);
    selectedSet = new Set(screenshots.map((_, i) => i)); // default all selected
    _revokeThumbBlobs(); // release any prior-payload blobs before reusing
    renderGrid();
    updateCount();

    // CI/Hook indicator on the picker (single-video done surface).
    const aiCopy = buildBackgroundAiIndicator(cachedSettings);
    pickerDoneIndicatorEl.textContent = aiCopy;
    pickerDoneIndicatorEl.classList.toggle("hidden", !aiCopy);

    showPicker();
  }

  // ---- Finish actions (Copy / Cancel) ----
  async function _writeClipboard(text) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch {
      try {
        const r = await chrome.runtime.sendMessage({
          type: "copyToClipboard", text,
        });
        return !!(r && r.ok);
      } catch { return false; }
    }
  }

  async function _clearPending() {
    return new Promise((resolve) => {
      try {
        chrome.storage.local.remove("pending_picker", () => resolve());
      } catch { resolve(); }
    });
  }

  async function _finish(kind /* "copy" | "cancel" */) {
    if (!pendingPicker) { hidePicker(); return; }
    pickerCopyBtn.disabled = true;
    pickerCancelBtn.disabled = true;
    pickerCopyBtn.textContent = kind === "copy" ? "Copying…" : "Copying…";

    // Source corpus: prefer multimodal paste so KEPT screenshots stay
    // base64-embedded. Falls back to yoink_md when corpus_md_paste isn't
    // present (dev mode without Pillow, etc).
    const sourceCorpus =
      pendingPicker.corpus_md_paste || pendingPicker.yoink_md || "";

    let clipboardText;
    if (kind === "copy") {
      const dropIndices = [];
      for (let i = 0; i < screenshots.length; i++) {
        if (!selectedSet.has(i)) dropIndices.push(i);
      }
      clipboardText = buildFilteredCorpus(sourceCorpus, dropIndices);
    } else {
      // Cancel = copy unmodified corpus (matches v1 default behavior).
      clipboardText = sourceCorpus;
    }

    const copied = await _writeClipboard(clipboardText);
    await _clearPending();
    _revokeThumbBlobs(); // Sprint 4 (1c): release blob URLs from getScreenshotThumbnail

    // Open Claude tab to match the v1 auto-copy flow, then close popup.
    try {
      await chrome.tabs.create({ url: "https://claude.ai/new", active: true });
    } catch (e) {
      console.warn("[picker] tab create failed", e);
    }

    // Sprint 4 (1a): route through STC.buildYoinkedMessage so the picker
    // path gets the same first-yoink CTA treatment as v1 auto-copy. The
    // helper atomically flips has_completed_first_yoink on first success
    // and returns either the first-yoink CTA copy or the topic-aware
    // subsequent copy. We pass a minimal data-shape (only `.topic` is
    // consumed by the helper) reconstituted from the stashed payload.
    try {
      const data = { topic: pendingPicker && pendingPicker.topic };
      const message = await STC.buildYoinkedMessage(data, copied);
      // Title surfaces the picker-specific detail (how many screenshots
      // were kept) so the user-visible signal isn't lost when the message
      // body becomes the standard CTA/topic copy.
      const titleSuffix = kind === "copy"
        ? ` (${selectedSet.size} of ${screenshots.length} screenshots)`
        : "";
      await chrome.runtime.sendMessage({
        type: "notify",
        title: copied ? `Yoinked!${titleSuffix}` : "Yoink ready (clipboard blocked)",
        message,
      });
    } catch (e) {
      // notify is fire-and-forget; log but don't surface to user
      console.warn("[picker] notify failed", e);
    }

    window.close();
  }

  pickerCopyBtn.addEventListener("click", () => _finish("copy"));
  pickerCancelBtn.addEventListener("click", () => _finish("cancel"));

  // ---- Boot ----
  // On popup open, check if a picker is waiting. Also subscribe to
  // storage changes so an open popup auto-switches if a fresh yoink
  // arrives mid-session.
  function _readPending() {
    return new Promise((resolve) => {
      try {
        chrome.storage.local.get({ pending_picker: null }, (items) => {
          resolve((items && items.pending_picker) || null);
        });
      } catch { resolve(null); }
    });
  }
  (async function bootPicker() {
    const p = await _readPending();
    if (p) activate(p);
  })();

  chrome.storage.onChanged.addListener((changes, area) => {
    if (area !== "local" || !changes.pending_picker) return;
    const next = changes.pending_picker.newValue;
    if (next) activate(next);
    else { _revokeThumbBlobs(); hidePicker(); }
  });
})();
