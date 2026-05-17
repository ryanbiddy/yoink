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

// LAUNCH GATE -- flip to `true` only after Yoink-Setup-2.0.0.exe is live
// at github.com/ryanbiddy/yoink/releases/latest. Procedure (also in
// docs/build-installer.md, "Launch checklist"):
//   1. Confirm SHA256 hashes in build.ps1 match the component versions.
//   2. Run .\build.ps1 -- builds Yoink-Setup-2.0.0.exe.
//   3. Smoke-test on a clean Windows VM.
//   4. git tag v2.0.0 && git push --tags.
//   5. Create the GitHub release, attach the .exe.
//   6. Verify https://github.com/ryanbiddy/yoink/releases/latest/download/Yoink-Setup-2.0.0.exe
//      resolves to the file.
//   7. Flip this flag to `true` and commit.
//   8. Republish the extension to the Chrome Web Store.
//
// Until then, the download button on setup.html shows "Coming soon" and
// the click handler is no-op'd so first-wave users don't hit a 404.
const INSTALLER_PUBLISHED = false;

// ---- Constants -----------------------------------------------------------
const SERVER = "http://127.0.0.1:5179";
// /health is the canonical liveness probe (added as an alias for /ping in v1).
const PING_PATH = "/health";
const POLL_MS = 2000;
const AUTO_YOINK_TTL_MS = 60_000;

// ---- DOM handles ---------------------------------------------------------
const params = new URLSearchParams(location.search);
const source = params.get("source") || "install";
const requestedHash = location.hash || "";
const isSettingsMode = source === "popup"
  || requestedHash === "#mcp-settings"
  || requestedHash === "#skill-settings";
const firstSettingsSection = document.getElementById("comment-intelligence");

const step1 = document.getElementById("step-1");
const step2 = document.getElementById("step-2");
const step3 = document.getElementById("step-3");
const step4 = document.getElementById("step-4");

const getStartedBtn = document.getElementById("get-started-btn");
const skipInstall = document.getElementById("skip-install");
const downloadBtn = document.getElementById("download-btn");

const statusBlock = document.getElementById("status-block");
const statusText = document.getElementById("status-text");
const statusInstructions = document.getElementById("status-instructions");

const pageTitle = document.getElementById("page-title");
const pageLede = document.getElementById("page-lede");

const suggestedThumb = document.getElementById("suggested-thumb");
const suggestedTitle = document.getElementById("suggested-title");
const suggestedByline = document.getElementById("suggested-byline");
const yoinkSuggestedBtn = document.getElementById("yoink-suggested-btn");
const ciEnabled = document.getElementById("ci-enabled");
const ciKeyInput = document.getElementById("anthropic-key");
const ciStatus = document.getElementById("ci-status");
const ciSaveBtn = document.getElementById("ci-save-btn");
const ciTestBtn = document.getElementById("ci-test-btn");
const ciClearBtn = document.getElementById("ci-clear-btn");
const aiCostEstimate = document.getElementById("ai-cost-estimate");
const hookTypeEnabled = document.getElementById("hook-type-enabled");
const smartScreenshotPickerEnabled = document.getElementById("smart-screenshot-picker-enabled");
const clipboardScreenshotCap = document.getElementById("clipboard-screenshot-cap");
const mcpStdioPath = document.getElementById("mcp-stdio-path");
const mcpHttpUrl = document.getElementById("mcp-http-url");
const mcpHttpToken = document.getElementById("mcp-http-token");
const mcpConfigEls = {
  claude: document.getElementById("mcp-config-claude"),
  chatgpt: document.getElementById("mcp-config-chatgpt"),
  cursor: document.getElementById("mcp-config-cursor"),
  generic: document.getElementById("mcp-config-generic"),
};
const mcpCopyButtons = Array.from(document.querySelectorAll("[data-copy-client]"));
const skillSystemPrompt = document.getElementById("skill-system-prompt");
const skillPromptCopyBtn = document.getElementById("skill-prompt-copy");

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
  } else if (isSettingsMode) {
    // Returning users opened Settings / Agent Integration from the popup.
    // Keep the install walkthrough out of the way and land directly on the
    // settings surface they came here to manage.
    step1.classList.add("hidden");
    step2.classList.add("hidden");
    step3.classList.add("hidden");
    if (firstSettingsSection) markCurrent(firstSettingsSection);
    requestAnimationFrame(() => {
      const target = requestedHash
        ? document.getElementById(requestedHash.replace(/^#/, ""))
        : firstSettingsSection;
      if (target) target.scrollIntoView({ behavior: "smooth", block: "start" });
    });
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
    pageTitle.textContent = isSettingsMode ? "Yoink settings." : "Yoink is ready.";
    pageLede.textContent = isSettingsMode
      ? "Manage local AI features and agent integration."
      : "The local helper is running. Yoink any YouTube video to begin.";
    return;
  }
  if (source === "offline") {
    pageTitle.textContent = "Yoink isn't running yet.";
    pageLede.textContent =
      "Start the Yoink helper and this page will detect it automatically.";
  } else if (isSettingsMode) {
    pageTitle.textContent = "Yoink settings.";
    pageLede.textContent =
      "Start the local helper to manage settings and agent integration.";
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
  setCIControlsEnabled(true);
  loadAIPricing();
  loadCISettings();
  loadMCPConfig();
  loadSkillSystemPrompt();
  markDone(step3);
  if (isSettingsMode) return;
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

// ---- Comment Intelligence settings --------------------------------------
let ciLoaded = false;
let aiSettings = null;
let aiPricing = null;

function setCIStatus(text, mode) {
  if (!ciStatus) return;
  ciStatus.textContent = text;
  ciStatus.classList.remove("ok", "warn");
  if (mode) ciStatus.classList.add(mode);
}

function dollars(n) {
  if (!Number.isFinite(n)) return "$0.00";
  if (n < 0.01) return `$${n.toFixed(3)}`;
  return `$${n.toFixed(2)}`;
}

function readClipboardScreenshotCap() {
  if (!clipboardScreenshotCap) return 4;
  const parsed = Number.parseInt(clipboardScreenshotCap.value, 10);
  if (!Number.isFinite(parsed)) return 4;
  return Math.max(0, Math.min(12, parsed));
}

function renderAICostEstimate() {
  if (!aiCostEstimate) return;
  const hasKey = !!(
    (aiSettings && aiSettings.anthropic_key_set)
    || (ciKeyInput && ciKeyInput.value.trim())
  );
  const ciOn = !!(ciEnabled && ciEnabled.checked);
  const hookOn = !!(hookTypeEnabled && hookTypeEnabled.checked);
  if (!hasKey || !aiPricing || (!ciOn && !hookOn)) {
    aiCostEstimate.classList.add("hidden");
    aiCostEstimate.textContent = "";
    return;
  }

  const est = aiPricing.est_per_video || {};
  const parts = [];
  if (ciOn) parts.push(`Comment Intelligence ${dollars(Number(est.ci || 0))}`);
  if (hookOn) parts.push(`Hook Type ${dollars(Number(est.hook || 0))}`);
  const total = ciOn && hookOn
    ? Number(est.both || 0)
    : Number((ciOn ? est.ci : est.hook) || 0);
  const model = aiPricing.display_model || aiPricing.model || "Anthropic";
  aiCostEstimate.innerHTML = [
    `≈ ${dollars(total)} estimated per video`,
    `<small>${parts.join(" + ")} · ${model} estimate, actual token usage may vary.</small>`,
  ].join("");
  aiCostEstimate.classList.remove("hidden");
}

function setCIControlsEnabled(enabled) {
  for (const el of [
    ciEnabled,
    ciKeyInput,
    ciSaveBtn,
    ciTestBtn,
    ciClearBtn,
    hookTypeEnabled,
    smartScreenshotPickerEnabled,
    clipboardScreenshotCap,
  ]) {
    if (el) el.disabled = !enabled;
  }
  if (!enabled) setCIStatus("Start Yoink to manage settings.", "warn");
  if (!enabled && aiCostEstimate) aiCostEstimate.classList.add("hidden");
}

function renderCISettings(settings) {
  if (!settings) return;
  aiSettings = settings;
  if (ciEnabled) ciEnabled.checked = !!settings.comment_intelligence_enabled;
  if (hookTypeEnabled) hookTypeEnabled.checked = !!settings.hook_type_enabled;
  if (smartScreenshotPickerEnabled) {
    smartScreenshotPickerEnabled.checked = !!settings.smart_screenshot_picker_enabled;
  }
  if (clipboardScreenshotCap) {
    const cap = Number.isFinite(Number(settings.clipboard_screenshot_cap))
      ? Number(settings.clipboard_screenshot_cap)
      : 4;
    clipboardScreenshotCap.value = String(Math.max(0, Math.min(12, cap)));
  }
  if (ciKeyInput) {
    ciKeyInput.value = "";
    ciKeyInput.dataset.dirty = "false";
    ciKeyInput.placeholder = settings.anthropic_key_set
      ? "Key saved - enter a new key to replace"
      : "sk-ant-...";
  }
  setCIStatus(settings.anthropic_key_set ? "Key set" : "Key not set.",
              settings.anthropic_key_set ? "ok" : "warn");
  renderAICostEstimate();
}

async function fetchPricingWithToken(token) {
  return fetch(`${SERVER}/settings/pricing`, {
    method: "GET",
    mode: "cors",
    cache: "no-store",
    headers: token ? { "X-Yoink-Token": token } : {},
  });
}

async function loadAIPricing() {
  if (!window.STC || !STC.getToken) return;
  try {
    let token = await STC.getToken();
    let res = await fetchPricingWithToken(token);
    if (res.status === 403) {
      token = await STC.getToken({ refresh: true });
      res = await fetchPricingWithToken(token);
    }
    const body = await res.json();
    if (res.ok && body && body.ok && body.pricing) {
      aiPricing = body.pricing;
      renderAICostEstimate();
    }
  } catch {
    // Cost visibility is a trust affordance, not a setup blocker.
  }
}

async function loadCISettings() {
  if (ciLoaded || !window.STC || !STC.getSettings) return;
  try {
    const res = await STC.getSettings();
    if (!res || !res.ok) {
      setCIStatus((res && res.error) || "Settings unavailable", "warn");
      return;
    }
    ciLoaded = true;
    renderCISettings(res.settings);
  } catch {
    setCIStatus("Settings unavailable", "warn");
  }
}

if (ciKeyInput) {
  ciKeyInput.addEventListener("input", () => {
    ciKeyInput.dataset.dirty = "true";
    renderAICostEstimate();
  });
}

for (const toggle of [ciEnabled, hookTypeEnabled]) {
  if (toggle) toggle.addEventListener("change", renderAICostEstimate);
}

if (ciSaveBtn) {
  ciSaveBtn.addEventListener("click", async () => {
    if (!window.STC || !STC.updateSettings) return;
    const body = {
      comment_intelligence_enabled: !!(ciEnabled && ciEnabled.checked),
      hook_type_enabled: !!(hookTypeEnabled && hookTypeEnabled.checked),
      smart_screenshot_picker_enabled: !!(
        smartScreenshotPickerEnabled && smartScreenshotPickerEnabled.checked
      ),
      clipboard_screenshot_cap: readClipboardScreenshotCap(),
    };
    const rawKey = ciKeyInput ? ciKeyInput.value.trim() : "";
    const keyDirty = ciKeyInput && ciKeyInput.dataset.dirty === "true";
    if (rawKey || keyDirty) body.anthropic_key = rawKey || null;

    setCIStatus("Saving...", null);
    ciSaveBtn.disabled = true;
    try {
      const res = await STC.updateSettings(body);
      if (!res || !res.ok) {
        setCIStatus((res && res.error) || "Save failed", "warn");
        return;
      }
      renderCISettings(res.settings);
    } catch {
      setCIStatus("Save failed", "warn");
    } finally {
      ciSaveBtn.disabled = false;
    }
  });
}

if (ciTestBtn) {
  ciTestBtn.addEventListener("click", async () => {
    if (!window.STC || !STC.testAnthropicKey) return;
    const rawKey = ciKeyInput ? ciKeyInput.value.trim() : "";
    setCIStatus("Testing key...", null);
    ciTestBtn.disabled = true;
    try {
      const res = await STC.testAnthropicKey(rawKey || undefined);
      if (res && res.valid) {
        setCIStatus("Key test passed", "ok");
        return;
      }
      setCIStatus(`Last test failed: ${(res && res.error) || "unknown error"}`, "warn");
    } catch {
      setCIStatus("Last test failed: server unavailable", "warn");
    } finally {
      ciTestBtn.disabled = false;
    }
  });
}

if (ciClearBtn) {
  ciClearBtn.addEventListener("click", async () => {
    if (!window.STC || !STC.updateSettings) return;
    const confirmed = window.confirm(
      "Clear the saved Anthropic API key from this computer?"
    );
    if (!confirmed) return;

    setCIStatus("Clearing key...", null);
    ciClearBtn.disabled = true;
    try {
      const res = await STC.updateSettings({
        comment_intelligence_enabled: !!(ciEnabled && ciEnabled.checked),
        hook_type_enabled: !!(hookTypeEnabled && hookTypeEnabled.checked),
        smart_screenshot_picker_enabled: !!(
          smartScreenshotPickerEnabled && smartScreenshotPickerEnabled.checked
        ),
        clipboard_screenshot_cap: readClipboardScreenshotCap(),
        anthropic_key: null,
      });
      if (!res || !res.ok) {
        setCIStatus((res && res.error) || "Clear failed", "warn");
        return;
      }
      if (ciKeyInput) {
        ciKeyInput.value = "";
        ciKeyInput.dataset.dirty = "false";
        ciKeyInput.placeholder = "sk-ant-...";
      }
      aiSettings = Object.assign({}, aiSettings || {}, { anthropic_key_set: false });
      setCIStatus("Key not set.", "warn");
      renderAICostEstimate();
    } catch {
      setCIStatus("Clear failed", "warn");
    } finally {
      ciClearBtn.disabled = false;
    }
  });
}

// ---- MCP config snippets -------------------------------------------------
let mcpSnippets = {};

function jsonPretty(obj) {
  return JSON.stringify(obj, null, 2);
}

function stdioServerConfig(config) {
  return {
    mcpServers: {
      yoink: {
        command: config.stdio.command,
        args: config.stdio.args,
      },
    },
  };
}

function buildMcpSnippets(config, token) {
  const stdio = stdioServerConfig(config);
  const http = {
    url: config.http.url,
    headers: { "X-Yoink-Token": token || "<token>" },
  };
  return {
    claude: jsonPretty(stdio),
    chatgpt: jsonPretty({
      name: "yoink",
      transport: "stdio",
      command: config.stdio.command,
      args: config.stdio.args,
    }),
    cursor: jsonPretty(stdio),
    generic: [
      "STDIO:",
      jsonPretty(stdio),
      "",
      "HTTP:",
      jsonPretty(http),
    ].join("\n"),
  };
}

function renderMcpConfig(config, token) {
  if (!config || !config.ok) return;
  const stdioText = [config.stdio.command].concat(config.stdio.args || []).join(" ");
  if (mcpStdioPath) mcpStdioPath.textContent = stdioText;
  if (mcpHttpUrl) mcpHttpUrl.textContent = config.http.url;
  if (mcpHttpToken) {
    mcpHttpToken.textContent = token ? `X-Yoink-Token: ${token}` : "Token unavailable.";
  }
  mcpSnippets = buildMcpSnippets(config, token);
  for (const [client, el] of Object.entries(mcpConfigEls)) {
    if (el) el.textContent = mcpSnippets[client] || "";
  }
}

function scrollToRequestedAnchor() {
  if (!requestedHash) return;
  const target = document.getElementById(requestedHash.replace(/^#/, ""));
  if (!target) return;
  requestAnimationFrame(() => {
    target.scrollIntoView({ behavior: "smooth", block: "start" });
  });
}

async function fetchMcpConfigWithToken(token) {
  return fetch(`${SERVER}/mcp/v1/config`, {
    method: "GET",
    mode: "cors",
    cache: "no-store",
    headers: token ? { "X-Yoink-Token": token } : {},
  });
}

async function loadMCPConfig() {
  if (!window.STC || !STC.getToken) return;
  try {
    let token = await STC.getToken();
    let res = await fetchMcpConfigWithToken(token);
    if (res.status === 403) {
      token = await STC.getToken({ refresh: true });
      res = await fetchMcpConfigWithToken(token);
    }
    const config = await res.json();
    if (!res.ok || !config || !config.ok) throw new Error("MCP config unavailable");
    renderMcpConfig(config, token);
    scrollToRequestedAnchor();
  } catch {
    const msg = "MCP config unavailable. Make sure Yoink Server is running.";
    if (mcpStdioPath) mcpStdioPath.textContent = msg;
    if (mcpHttpUrl) mcpHttpUrl.textContent = msg;
  }
}

for (const btn of mcpCopyButtons) {
  btn.addEventListener("click", async () => {
    const client = btn.getAttribute("data-copy-client");
    const text = mcpSnippets[client];
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
      const old = btn.textContent;
      btn.textContent = "Copied";
      setTimeout(() => { btn.textContent = old; }, 1200);
    } catch {
      const old = btn.textContent;
      btn.textContent = "Copy failed";
      setTimeout(() => { btn.textContent = old; }, 1200);
    }
  });
}

// ---- Yoink Operator Skill fallback prompt --------------------------------
async function fetchSkillPromptWithToken(token) {
  return fetch(`${SERVER}/skill/system-prompt`, {
    method: "GET",
    mode: "cors",
    cache: "no-store",
    headers: token ? { "X-Yoink-Token": token } : {},
  });
}

async function loadSkillSystemPrompt() {
  if (!skillSystemPrompt || !window.STC || !STC.getToken) return;
  try {
    let token = await STC.getToken();
    let res = await fetchSkillPromptWithToken(token);
    if (res.status === 403) {
      token = await STC.getToken({ refresh: true });
      res = await fetchSkillPromptWithToken(token);
    }
    if (!res.ok) throw new Error("skill prompt unavailable");
    skillSystemPrompt.value = await res.text();
  } catch {
    skillSystemPrompt.value = "System prompt unavailable. Make sure Yoink Server is running.";
  }
}

if (skillPromptCopyBtn) {
  skillPromptCopyBtn.addEventListener("click", async () => {
    const text = skillSystemPrompt ? skillSystemPrompt.value : "";
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
      const old = skillPromptCopyBtn.textContent;
      skillPromptCopyBtn.textContent = "Copied";
      setTimeout(() => { skillPromptCopyBtn.textContent = old; }, 1200);
    } catch {
      const old = skillPromptCopyBtn.textContent;
      skillPromptCopyBtn.textContent = "Copy failed";
      setTimeout(() => { skillPromptCopyBtn.textContent = old; }, 1200);
    }
  });
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

// ---- Pre-launch download button state -----------------------------------
function applyDownloadState() {
  if (INSTALLER_PUBLISHED || !downloadBtn) return;
  downloadBtn.classList.add("disabled");
  downloadBtn.setAttribute("aria-disabled", "true");
  downloadBtn.textContent = "Coming soon";
  downloadBtn.title = "Installer publishes at launch.";
  // Stop the link from navigating to a not-yet-existing release page.
  downloadBtn.addEventListener("click", (ev) => ev.preventDefault());
  downloadBtn.removeAttribute("href");
}

// ---- Boot ----------------------------------------------------------------
setCIControlsEnabled(false);
applyDownloadState();
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
