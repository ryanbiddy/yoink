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
  const capNotice = document.getElementById("pl-cap-notice");
  const previewListEl = document.getElementById("pl-preview-list");
  const startBtn = document.getElementById("pl-start-btn");

  // Progress panel
  const progressPanel = document.getElementById("pl-progress-panel");
  const progressFill = document.getElementById("pl-progress-fill");
  const progressText = document.getElementById("pl-progress-text");
  const phaseRow = document.getElementById("pl-phase-row");
  const cancelBtnEl = document.getElementById("pl-cancel-btn");

  // Done / cancelled / failed
  const donePanel = document.getElementById("pl-done-panel");
  const doneSummary = document.getElementById("pl-done-summary");
  const doneMeta = document.getElementById("pl-done-meta");
  const openFolderBtn = document.getElementById("pl-open-folder-btn");
  const startAnotherBtn = document.getElementById("pl-start-another-btn");
  const cancelledPanel = document.getElementById("pl-cancelled-panel");
  const cancelledRestartBtn = document.getElementById("pl-cancelled-restart-btn");
  const failedPanel = document.getElementById("pl-failed-panel");
  const failedMsg = document.getElementById("pl-failed-msg");
  const failedRestartBtn = document.getElementById("pl-failed-restart-btn");

  // State
  let previewedUrl = null;
  let previewedVideos = [];
  let activeJobId = null;
  let pollTimer = null;
  let resultPayload = null;

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
    const s = Math.max(0, parseInt(seconds, 10) || 0);
    const m = Math.floor(s / 60);
    const r = s % 60;
    return `${m}:${String(r).padStart(2, "0")}`;
  }

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

  function resetPlaylistUI() {
    stopPolling();
    activeJobId = null;
    resultPayload = null;
    previewedUrl = null;
    previewedVideos = [];
    urlInput.value = "";
    clearError();
    previewListEl.innerHTML = "";
    capNotice.classList.add("hidden");
    progressFill.style.width = "0%";
    progressText.textContent = "Queued…";
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
      if (!res || !res.ok) {
        showError((res && res.error) || "Couldn't preview that playlist.");
        return;
      }
      previewedUrl = raw;
      previewedVideos = res.videos || [];
      renderPreview(res);
      showOnly(previewPanel);
    } catch (e) {
      showError(`Preview failed: ${e && e.message || e}`);
    } finally {
      previewBtn.disabled = false;
      previewBtn.textContent = "Preview";
    }
  });

  function renderPreview(res) {
    previewListEl.innerHTML = "";
    for (const v of (res.videos || [])) {
      const row = document.createElement("div");
      row.className = "pl-video";

      const img = document.createElement("img");
      img.className = "pl-thumb";
      img.src = v.thumbnail_url || "";
      img.alt = "";
      row.appendChild(img);

      const meta = document.createElement("div");
      meta.className = "pl-meta";
      const title = document.createElement("div");
      title.className = "pl-title";
      title.textContent = v.title || "(untitled)";
      const dur = document.createElement("div");
      dur.className = "pl-duration";
      dur.textContent = fmtDuration(v.duration_seconds);
      meta.appendChild(title);
      meta.appendChild(dur);
      row.appendChild(meta);

      previewListEl.appendChild(row);
    }

    const cap = res.cap || PLAYLIST_CAP;
    const total = res.total_in_playlist != null ? res.total_in_playlist : (res.videos || []).length;
    if (total > cap) {
      capNotice.textContent = `${cap} of ${total} videos — capped at ${cap}.`;
      capNotice.classList.remove("hidden");
    } else {
      capNotice.classList.add("hidden");
    }

    startBtn.disabled = !(res.videos && res.videos.length);
  }

  // ---- start -----------------------------------------------------------
  startBtn.addEventListener("click", async () => {
    if (!previewedUrl) return;
    startBtn.disabled = true;
    startBtn.textContent = "Starting…";
    try {
      const res = await STC.playlistStart(previewedUrl);
      if (!res || !res.ok || !res.job_id) {
        showError((res && res.error) || "Couldn't start playlist yoink.");
        showOnly(inputPanel);
        return;
      }
      activeJobId = res.job_id;
      // Preset progress view with the previewed total so the user sees
      // "Video 1 of 10" immediately instead of a flicker.
      progressFill.style.width = "0%";
      progressText.textContent = `Queued — ${previewedVideos.length} videos`;
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
    let s;
    try {
      s = await STC.jobStatus(activeJobId);
    } catch (e) {
      // Transient network blip; let the next tick try again.
      console.warn("[playlist] jobStatus failed", e);
      return;
    }
    if (!s || !s.ok) {
      stopPolling();
      enterFailed((s && s.error) || "Status check failed.");
      return;
    }
    renderProgress(s);

    if (s.state === "completed") {
      stopPolling();
      resultPayload = s.result || null;
      await onCompleted(resultPayload);
    } else if (s.state === "cancelled") {
      stopPolling();
      showOnly(cancelledPanel);
    } else if (s.state === "failed") {
      stopPolling();
      enterFailed(s.error || "Playlist yoink failed.");
    }
  }

  function renderProgress(s) {
    const total = s.videos_total || previewedVideos.length || PLAYLIST_CAP;
    const done = s.videos_done || 0;
    const pct = total > 0 ? Math.min(100, Math.round((done / total) * 100)) : 0;
    progressFill.style.width = `${pct}%`;

    if (s.state === "queued") {
      progressText.textContent = `Queued — ${total} videos`;
    } else if (s.current_video) {
      const title = s.current_video.title || "(untitled)";
      const idx = s.current_video.index || (done + 1);
      progressText.textContent = `Video ${idx} of ${total}: ${title}`;
    } else if (s.state === "running") {
      progressText.textContent = `${done} of ${total} videos done`;
    }

    // Phase chips: highlight the active phase, mark prior phases done.
    const phases = ["metadata", "download", "screenshots", "comments"];
    const activeIdx = phases.indexOf(s.current_video_phase);
    for (const chip of phaseRow.querySelectorAll(".pl-phase-chip")) {
      const phase = chip.dataset.phase;
      const idx = phases.indexOf(phase);
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
      await STC.jobCancel(activeJobId);
      // Don't switch UI here — let the next poll observe state=cancelled and
      // flip the panel, so the transition stays driven by the backend.
    } catch (e) {
      console.warn("[playlist] cancel failed", e);
    } finally {
      // Reset button state in case poll hasn't flipped yet.
      setTimeout(() => {
        cancelBtnEl.disabled = false;
        cancelBtnEl.textContent = "Cancel";
      }, 1500);
    }
  });

  // ---- completion ------------------------------------------------------
  async function onCompleted(result) {
    let copied = false;
    const corpusText = (result && result.combined_md_text) || "";
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

    const videoCount = result && result.per_video ? result.per_video.length : 0;
    const kb = corpusText ? (corpusText.length / 1024).toFixed(1) : "0";
    doneSummary.textContent = copied
      ? "Done — corpus copied to clipboard"
      : "Done — clipboard blocked, open the folder";
    doneMeta.textContent = `${videoCount} videos · ${kb} KB combined`;
    showOnly(donePanel);

    if (copied) showToast("Playlist yoinked! Paste in Claude or ChatGPT.");
  }

  openFolderBtn.addEventListener("click", async () => {
    const path = resultPayload && resultPayload.combined_md_path;
    if (!path) {
      showToast("No folder path available.");
      return;
    }
    try {
      // openFolder is the existing server endpoint — in mock mode the server
      // may not be running, in which case the toast below is the recovery.
      const res = await STC.openFolder(path);
      if (!res || res.ok === false) showToast("Couldn't open folder — server may be offline.");
    } catch {
      showToast("Couldn't open folder — server may be offline.");
    }
  });

  startAnotherBtn.addEventListener("click", resetPlaylistUI);
  cancelledRestartBtn.addEventListener("click", resetPlaylistUI);
  failedRestartBtn.addEventListener("click", resetPlaylistUI);

  function enterFailed(msg) {
    failedMsg.textContent = msg;
    showOnly(failedPanel);
  }

  // ---- boot ------------------------------------------------------------
  // Start in single-video mode (default). Panel visibility is controlled
  // entirely from here.
  showOnly(inputPanel);
})();
