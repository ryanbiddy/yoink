// Setup page — drives the install + first-run flow.
//
// Two entry points (distinguished by ?source=...):
//   ?source=install  — opened by background.js after a fresh install. Shows
//                      all four steps top-to-bottom.
//   ?source=offline  — opened by content.js when the user clicks Yoink on
//                      YouTube but the local server is unreachable. Skips
//                      the welcome step and jumps straight to verify.
//
// The "verify" step polls the local server every POLL_MS until it answers,
// then unlocks step 4. Polling stops once the server is up.

// ---- Suggested video for step 4. Edit here to swap. ----------------------
// (Channel-friendly default: a short, popular Lenny's Podcast clip.)
const SUGGESTED_VIDEO = {
  // YouTube watch URL. Used both as the link target and to derive the ID.
  url: "https://www.youtube.com/watch?v=8rABwKRsec4",
  title: "Andrej Karpathy on AGI, hiring, and the future of programming",
  byline: "Lenny's Podcast",
};

// ---- Constants -----------------------------------------------------------
const SERVER = "http://127.0.0.1:5179";
const PING_PATH = "/ping"; // server exposes /ping (not /health)
const POLL_MS = 2000;
const AUTO_YOINK_TTL_MS = 60_000;

// ---- DOM handles ---------------------------------------------------------
const params = new URLSearchParams(location.search);
const source = params.get("source") || "install";

const step1 = document.getElementById("step-1");
const step2 = document.getElementById("step-2");
const step3 = document.getElementById("step-3");
const step4 = document.getElementById("step-4");

const getStartedBtn = document.getElementById("get-started-btn");
const skipInstall = document.getElementById("skip-install");

const statusBlock = document.getElementById("status-block");
const statusText = document.getElementById("status-text");
const statusInstructions = document.getElementById("status-instructions");

const pageTitle = document.getElementById("page-title");
const pageLede = document.getElementById("page-lede");

const suggestedThumb = document.getElementById("suggested-thumb");
const suggestedTitle = document.getElementById("suggested-title");
const suggestedByline = document.getElementById("suggested-byline");
const yoinkSuggestedBtn = document.getElementById("yoink-suggested-btn");

// ---- Suggested-video population -----------------------------------------
function videoIdFromUrl(url) {
  try {
    const u = new URL(url);
    if (u.hostname.replace(/^www\.|^m\./, "") === "youtu.be") {
      return u.pathname.replace(/^\/+/, "").split("/")[0] || null;
    }
    return u.searchParams.get("v");
  } catch {
    return null;
  }
}

const suggestedVideoId = videoIdFromUrl(SUGGESTED_VIDEO.url);
if (suggestedVideoId) {
  suggestedThumb.src = `https://i.ytimg.com/vi/${suggestedVideoId}/hqdefault.jpg`;
}
suggestedTitle.textContent = SUGGESTED_VIDEO.title;
suggestedByline.textContent = SUGGESTED_VIDEO.byline;

// ---- Source-driven layout ------------------------------------------------
function applySource() {
  if (source === "offline") {
    // Skip the welcome + install steps; user already has the extension.
    step1.classList.add("hidden");
    step2.classList.add("hidden");
    markCurrent(step3);
  } else {
    markCurrent(step1);
  }
  // Header copy is driven by current status, not just source. Initial state
  // is "checking" -- updateHeader gets called again on every status change.
  updateHeader("checking");
}

// Keep page title/lede in sync with the live status. Without this, the
// source=offline path stayed on "Yoink isn't running yet" even after the
// status block flipped green, so the page contradicted itself.
function updateHeader(status) {
  if (status === "running") {
    pageTitle.textContent = "Yoink is ready.";
    pageLede.textContent =
      "The local helper is running. Yoink any YouTube video to begin.";
    return;
  }
  if (source === "offline") {
    pageTitle.textContent = "Yoink isn't running yet.";
    pageLede.textContent =
      "Start the Yoink helper and this page will detect it automatically.";
  } else {
    pageTitle.textContent = "Let's get you set up.";
    pageLede.textContent =
      "Two minutes. Then you'll be yoinking videos straight into Claude.";
  }
}

function markCurrent(stepEl) {
  for (const el of [step1, step2, step3, step4]) {
    el.classList.remove("is-current");
  }
  if (stepEl) stepEl.classList.add("is-current");
}

function markDone(stepEl) {
  stepEl.classList.add("is-done");
  stepEl.classList.remove("is-current");
}

// ---- Step nav ------------------------------------------------------------
getStartedBtn.addEventListener("click", () => {
  markDone(step1);
  markCurrent(step2);
  step2.scrollIntoView({ behavior: "smooth", block: "start" });
});

skipInstall.addEventListener("click", (ev) => {
  ev.preventDefault();
  markDone(step2);
  markCurrent(step3);
  step3.scrollIntoView({ behavior: "smooth", block: "start" });
});

// ---- Step 3: live server polling ----------------------------------------
let pollTimer = null;
let polling = false;

function setStatus(state, text) {
  statusBlock.classList.remove("is-checking", "is-running", "is-down");
  statusBlock.classList.add(`is-${state}`);
  statusText.textContent = text;
}

async function pingOnce() {
  try {
    const ctrl = new AbortController();
    const t = setTimeout(() => ctrl.abort(), 1500);
    const res = await fetch(SERVER + PING_PATH, {
      method: "GET",
      mode: "cors",
      cache: "no-store",
      signal: ctrl.signal,
    });
    clearTimeout(t);
    return res.ok;
  } catch {
    return false;
  }
}

async function tickPoll() {
  const up = await pingOnce();
  if (up) {
    onServerUp();
    return;
  }
  // Switch from "checking" to "down" instructions only after the first
  // failed probe, so the user sees a quick spinner first instead of an
  // immediate scary "not running" message.
  if (statusBlock.classList.contains("is-checking")) {
    setStatus("down", "Yoink isn't running yet");
    statusInstructions.classList.remove("hidden");
    updateHeader("down");
  }
}

function startPolling() {
  if (polling) return;
  polling = true;
  setStatus("checking", "Checking for Yoink...");
  statusInstructions.classList.add("hidden");
  // Fire one immediately so a running server flips green right away.
  tickPoll();
  pollTimer = setInterval(tickPoll, POLL_MS);
}

function stopPolling() {
  polling = false;
  if (pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
}

function onServerUp() {
  stopPolling();
  setStatus("running", "Yoink is running ✓");
  statusInstructions.classList.add("hidden");
  updateHeader("running");
  markDone(step3);
  if (step4.classList.contains("hidden")) {
    step4.classList.remove("hidden");
    markCurrent(step4);
    // Defer scroll so the layout settles before we move the viewport.
    requestAnimationFrame(() => {
      step4.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  } else {
    markCurrent(step4);
  }
}

// ---- Step 4: hand off to YouTube + auto-trigger Yoink -------------------
yoinkSuggestedBtn.addEventListener("click", async () => {
  // Stash a flag the YouTube content script will read on injection so it
  // auto-clicks the Yoink button. TTL guards against the user opening the
  // page later from history and getting a surprise yoink.
  if (suggestedVideoId) {
    try {
      await chrome.storage.local.set({
        auto_yoink: { videoId: suggestedVideoId, ts: Date.now() },
      });
    } catch (e) {
      console.warn("[stc] auto_yoink set failed", e);
    }
  }
  try {
    await chrome.tabs.create({ url: SUGGESTED_VIDEO.url, active: true });
  } catch {
    window.open(SUGGESTED_VIDEO.url, "_blank", "noopener");
  }
});

// ---- Boot ----------------------------------------------------------------
applySource();
startPolling();

// If the tab gets backgrounded for a while we don't burn cycles polling,
// but we resume the moment it's visible again so a user who tabbed away to
// run the installer sees the green check immediately on return.
document.addEventListener("visibilitychange", () => {
  if (document.hidden) {
    if (statusBlock.classList.contains("is-running")) return;
    stopPolling();
  } else {
    if (!statusBlock.classList.contains("is-running")) startPolling();
  }
});
