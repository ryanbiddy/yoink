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
  const progressWarningsEl = document.getElementById("pl-progress-warnings");
  const phaseRow = document.getElementById("pl-phase-row");
  const cancelBtnEl = document.getElementById("pl-cancel-btn");

  // Done panel
  const donePanel = document.getElementById("pl-done-panel");
  const doneSummary = document.getElementById("pl-done-summary");
  const doneMeta = document.getElementById("pl-done-meta");
  const doneMessageEl = document.getElementById("pl-done-message");
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
      const res = await STC.playlistStart(previewedUrl);
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
  function startPolling() {
    stopPolling();
    pollOnce();
    pollTimer = setInterval(pollOnce, POLL_MS);
  }
  function stopPolling() {
    if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
  }

  async function pollOnce() {
    if (!activeJobId) return;
    let res;
    try {
      res = await STC.jobStatus(activeJobId);
    } catch (e) {
      // Transient network blip; let the next tick try again.
      console.warn("[playlist] jobStatus failed", e);
      return;
    }
    if (!res || !res.ok || !res.job) {
      stopPolling();
      enterFailed((res && res.error) || "Status check failed.");
      return;
    }
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
  // Start in single-video mode (default). Panel visibility inside playlist
  // mode is controlled entirely from here.
  showOnly(inputPanel);
})();
