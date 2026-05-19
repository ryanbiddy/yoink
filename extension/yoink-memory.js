// Yoink Memory page. Runs as an extension page and talks to the local helper.

const LIMIT = 50;
const FILTER_STORAGE_KEY = "yoink_memory_filters";
const SERVER = (globalThis.STC && STC.SERVER) || "http://127.0.0.1:5179";
const HEALTH_FIELDS = [
  "transcript",
  "screenshots",
  "comments",
  "hook",
  "comment_intelligence",
];

const els = {
  count: document.getElementById("count-label"),
  offline: document.getElementById("offline-banner"),
  results: document.getElementById("results"),
  prev: document.getElementById("prev-page"),
  next: document.getElementById("next-page"),
  page: document.getElementById("page-label"),
  toast: document.getElementById("toast"),
  clear: document.getElementById("clear-filters"),
  search: document.getElementById("filter-search"),
  channel: document.getElementById("filter-channel"),
  topic: document.getElementById("filter-topic"),
  hook: document.getElementById("filter-hook"),
  dateFrom: document.getElementById("filter-date-from"),
  dateTo: document.getElementById("filter-date-to"),
};

const state = {
  filters: {
    q: "",
    channel: "",
    topic: "",
    hook_type: "",
    date_from: "",
    date_to: "",
  },
  offset: 0,
  total: 0,
  totalAll: 0,
  rows: [],
  loading: false,
  menu: null,
  toastTimer: null,
};
const thumbnailCache = new Map();

function debounce(fn, ms) {
  let timer = null;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), ms);
  };
}

function storageGet(defaults) {
  return new Promise((resolve) => {
    try {
      chrome.storage.local.get(defaults, (items) => resolve(items || defaults));
    } catch {
      resolve(defaults);
    }
  });
}

function storageSet(items) {
  return new Promise((resolve) => {
    try { chrome.storage.local.set(items, resolve); }
    catch { resolve(); }
  });
}

async function authedFetch(path, init = {}) {
  const doFetch = async (token) => {
    const headers = Object.assign({}, init.headers || {});
    if (token) headers["X-Yoink-Token"] = token;
    return fetch(`${SERVER}${path}`, Object.assign({}, init, {
      headers,
      mode: "cors",
      credentials: "omit",
      cache: init.cache || "no-store",
    }));
  };

  let token = STC.getToken ? await STC.getToken() : null;
  let res = await doFetch(token);
  if (res.status === 403 && STC.getToken) {
    token = await STC.getToken({ refresh: true });
    res = await doFetch(token);
  }
  return { res, token };
}

async function authedJson(path, init = {}) {
  const { res } = await authedFetch(path, init);
  let body = null;
  try { body = await res.json(); } catch { /* empty or non-JSON body */ }
  if (!res.ok || !body) {
    const detail = body && body.error ? body.error : `HTTP ${res.status}`;
    const err = new Error(detail);
    err.status = res.status;
    throw err;
  }
  return body;
}

function readFiltersFromInputs() {
  state.filters = {
    q: els.search.value.trim(),
    channel: els.channel.value.trim(),
    topic: els.topic.value.trim(),
    hook_type: els.hook.value,
    date_from: els.dateFrom.value,
    date_to: els.dateTo.value,
  };
}

function applyFiltersToInputs() {
  els.search.value = state.filters.q || "";
  els.channel.value = state.filters.channel || "";
  els.topic.value = state.filters.topic || "";
  els.hook.value = state.filters.hook_type || "";
  els.dateFrom.value = state.filters.date_from || "";
  els.dateTo.value = state.filters.date_to || "";
}

function filtersActive() {
  return Object.values(state.filters).some((v) => !!v);
}

async function persistFilters() {
  await storageSet({ [FILTER_STORAGE_KEY]: state.filters });
}

function queryString() {
  const params = new URLSearchParams();
  params.set("limit", String(LIMIT));
  params.set("offset", String(state.offset));
  for (const [key, value] of Object.entries(state.filters)) {
    if (value) params.set(key, value);
  }
  return params.toString();
}

function normalizeRows(body) {
  const data = body.data && typeof body.data === "object" ? body.data : {};
  const rows = body.results || body.yoinks || body.items || body.recent
    || data.results || data.yoinks || data.items || [];
  return Array.isArray(rows) ? rows : [];
}

function normalizeSearchResponse(body) {
  const data = body.data && typeof body.data === "object" ? body.data : {};
  const rows = normalizeRows(body);
  const total = Number(
    body.total ?? body.matching ?? body.count
    ?? data.total ?? data.matching ?? data.count ?? rows.length
  );
  const totalAll = Number(
    body.total_all ?? body.library_total ?? body.all_total
    ?? data.total_all ?? data.library_total ?? data.all_total ?? total
  );
  return {
    rows,
    total: Number.isFinite(total) ? total : rows.length,
    totalAll: Number.isFinite(totalAll) ? totalAll : total,
  };
}

function rowVideoId(row) {
  return row.video_id || (row.url && STC.extractVideoId(row.url)) || "";
}

function rowFolder(row) {
  return row.folder || row.output_folder || row.session_folder || "";
}

function rowUrl(row) {
  return row.url
    || row.video_url
    || row.source_url
    || (row.metadata && row.metadata.url)
    || (rowVideoId(row) ? `https://www.youtube.com/watch?v=${rowVideoId(row)}` : "");
}

function rowThumbnailPath(row) {
  return row.thumbnail_path
    || row.thumbnail
    || row.thumbnail_file
    || (row.metadata && row.metadata.thumbnail_path)
    || "";
}

function formatHookType(value) {
  return String(value || "")
    .replace(/_/g, " ")
    .replace(/\b\w/g, (ch) => ch.toUpperCase());
}

function formatDate(value) {
  if (!value) return "";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return String(value).slice(0, 10);
  return d.toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}

function topEntityNames(row) {
  const raw = Array.isArray(row.top_entities) ? row.top_entities : [];
  return raw
    .map((entity) => {
      if (typeof entity === "string") return entity.trim();
      if (entity && typeof entity === "object") {
        return String(entity.name || entity.label || "").trim();
      }
      return "";
    })
    .filter(Boolean)
    .slice(0, 5);
}

function healthLabel(key) {
  return String(key || "")
    .replace(/_/g, " ")
    .replace(/\b\w/g, (ch) => ch.toUpperCase());
}

function normalizeHealthValue(value) {
  if (value == null) return { status: "skipped", reason: "" };
  if (typeof value === "string") return { status: value, reason: "" };
  if (typeof value === "boolean") {
    return { status: value ? "ok" : "missing", reason: "" };
  }
  if (typeof value === "object") {
    return {
      status: String(value.status || value.state || value.result || (value.ok ? "ok" : "skipped")),
      reason: String(value.reason || value.error || value.message || ""),
    };
  }
  return { status: String(value), reason: "" };
}

function healthDotClass(status) {
  const s = String(status || "").toLowerCase();
  if (["ok", "success", "complete", "completed", "available", "present", "pass"].includes(s)) {
    return "ok";
  }
  if (["missing", "failed", "error", "warning", "warn", "blocked", "unavailable"].includes(s)) {
    return "missing";
  }
  return "skipped";
}

function healthEntries(health) {
  if (!health || typeof health !== "object") return [];
  const keys = [];
  for (const key of HEALTH_FIELDS) {
    if (Object.prototype.hasOwnProperty.call(health, key)) keys.push(key);
  }
  for (const key of Object.keys(health)) {
    if (!keys.includes(key)) keys.push(key);
  }
  return keys.slice(0, 5).map((key) => {
    const normalized = normalizeHealthValue(health[key]);
    return { key, label: healthLabel(key), ...normalized };
  });
}

function renderHealth(health) {
  const entries = healthEntries(health);
  if (!entries.length) return null;

  const row = document.createElement("span");
  row.className = "health-row";
  row.title = entries.map((entry) => {
    const reason = entry.reason ? ` - ${entry.reason}` : "";
    return `${entry.label}: ${entry.status || "skipped"}${reason}`;
  }).join("\n");
  row.setAttribute("aria-label", row.title);

  for (const entry of entries) {
    const dot = document.createElement("span");
    dot.className = `health-dot ${healthDotClass(entry.status)}`;
    row.appendChild(dot);
  }
  return row;
}

async function thumbnailUrl(row) {
  const path = rowThumbnailPath(row);
  if (!path) return "";
  if (thumbnailCache.has(path)) return thumbnailCache.get(path);
  const { res } = await authedFetch(`/file?path=${encodeURIComponent(path)}`, {
    method: "GET",
  });
  if (!res.ok) return "";
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  thumbnailCache.set(path, url);
  return url;
}

function renderThumb(row) {
  const path = rowThumbnailPath(row);
  const placeholder = document.createElement("div");
  placeholder.className = "thumb-placeholder";
  placeholder.textContent = "[]";
  placeholder.title = "No thumbnail";
  if (!path) return placeholder;

  const img = document.createElement("img");
  img.className = "thumb";
  img.alt = "";
  img.loading = "lazy";
  img.decoding = "async";
  img.addEventListener("error", () => img.replaceWith(placeholder));
  thumbnailUrl(row).then((url) => {
    if (url) img.src = url;
    else img.replaceWith(placeholder);
  });
  return img;
}

function renderHook(row) {
  const hookType = row.hook_type
    || row.corrected_hook_type
    || (row.hook && (row.hook.hook_type || row.hook.type));
  if (!hookType) return null;
  const confidence = Number(
    row.hook_type_confidence
    ?? row.hook_confidence
    ?? row.confidence
    ?? (row.hook && row.hook.confidence)
  );
  const chip = document.createElement("span");
  chip.className = "hook-chip";
  if (Number.isFinite(confidence) && confidence <= 2) chip.classList.add("warning");
  chip.textContent = Number.isFinite(confidence)
    ? `${formatHookType(hookType)} · confidence ${confidence}/5`
    : formatHookType(hookType);
  return chip;
}

function renderEntities(row) {
  const count = Number(row.entity_count);
  if (!Number.isFinite(count) || count <= 0) return null;
  const pill = document.createElement("span");
  pill.className = "entity-pill";
  pill.textContent = `${count} ${count === 1 ? "entity" : "entities"}`;
  const names = topEntityNames(row);
  pill.title = names.length ? names.join("\n") : pill.textContent;
  return pill;
}

function closeMenu() {
  if (state.menu) {
    state.menu.remove();
    state.menu = null;
  }
}

function menuButton(label, onClick, className = "") {
  const btn = document.createElement("button");
  btn.type = "button";
  btn.textContent = label;
  if (className) btn.className = className;
  btn.addEventListener("click", async (ev) => {
    ev.stopPropagation();
    closeMenu();
    await onClick();
  });
  return btn;
}

async function openFolder(row) {
  const folder = rowFolder(row);
  if (!folder) {
    showToast("Folder path unavailable.");
    return;
  }
  try {
    const res = await STC.openFolder(folder);
    if (!res || res.ok === false) throw new Error((res && res.error) || "open failed");
  } catch {
    showToast("Couldn't open folder. Is the helper running?");
  }
}

function openYouTube(row) {
  const url = rowUrl(row);
  if (!url) {
    showToast("YouTube URL unavailable.");
    return;
  }
  chrome.tabs.create({ url, active: true });
}

async function reYoink(row) {
  const url = rowUrl(row);
  if (!url) {
    showToast("YouTube URL unavailable.");
    return;
  }
  showToast("Re-yoinking...");
  try {
    const res = await STC.postExtract(url, STC.DEFAULT_INTERVAL || 30);
    if (!res || res.ok === false) throw new Error((res && res.error) || "yoink failed");
    showToast("Re-yoink complete.");
    await loadResults();
  } catch (e) {
    showToast((e && e.message) || "Re-yoink failed.");
  }
}

async function deleteRow(row) {
  const videoId = rowVideoId(row);
  if (!videoId) {
    showToast("Video ID unavailable.");
    return;
  }
  try {
    const res = await authedJson("/memory/delete", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ video_id: videoId }),
    });
    if (res && res.ok === false) throw new Error(res.error || "delete failed");
    state.rows = state.rows.filter((r) => rowVideoId(r) !== videoId);
    state.total = Math.max(0, state.total - 1);
    renderResults();
    showToast("Deleted - undo within 30 days from trash", {
      label: "Undo",
      onClick: () => restoreRow(videoId),
    });
  } catch (e) {
    showToast((e && e.message) || "Delete failed.");
  }
}

async function restoreRow(videoId) {
  try {
    const res = await authedJson("/memory/restore", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ video_id: videoId }),
    });
    if (res && res.ok === false) throw new Error(res.error || "restore failed");
    showToast("Restored.");
    await loadResults();
  } catch (e) {
    showToast((e && e.message) || "Restore failed.");
  }
}

function renderMenu(row, host) {
  closeMenu();
  const menu = document.createElement("div");
  menu.className = "menu";
  menu.appendChild(menuButton("Open folder", () => openFolder(row)));
  menu.appendChild(menuButton("Open on YouTube", () => openYouTube(row)));
  menu.appendChild(menuButton("Re-yoink", () => reYoink(row)));
  menu.appendChild(menuButton("Delete", () => deleteRow(row), "danger"));
  host.appendChild(menu);
  state.menu = menu;
}

function renderRow(row) {
  const item = document.createElement("article");
  item.className = "memory-row";
  item.tabIndex = 0;
  item.title = rowFolder(row);

  item.appendChild(renderThumb(row));

  const main = document.createElement("div");
  main.className = "row-main";

  const title = document.createElement("div");
  title.className = "row-title";
  title.textContent = row.title || row.slug || "(untitled)";

  const meta = document.createElement("div");
  meta.className = "row-meta";
  const metaBits = [
    row.channel || "Unknown channel",
    row.topic || "Uncategorized",
    formatDate(row.yoinked_at || row.created_at || row.classified_at),
  ].filter(Boolean);
  meta.textContent = metaBits.join(" · ");

  const tags = document.createElement("div");
  tags.className = "row-tags";
  for (const el of [renderHook(row), renderEntities(row), renderHealth(row.health)]) {
    if (el) tags.appendChild(el);
  }
  if (!tags.childNodes.length) {
    const empty = document.createElement("span");
    empty.textContent = "No analysis tags yet";
    tags.appendChild(empty);
  }

  main.appendChild(title);
  main.appendChild(meta);
  main.appendChild(tags);

  const actions = document.createElement("div");
  actions.className = "row-actions";
  const action = document.createElement("button");
  action.type = "button";
  action.className = "action-button";
  action.textContent = "⋯";
  action.setAttribute("aria-label", "Yoink actions");
  action.addEventListener("click", (ev) => {
    ev.stopPropagation();
    if (state.menu && actions.contains(state.menu)) closeMenu();
    else renderMenu(row, actions);
  });
  actions.appendChild(action);

  item.appendChild(main);
  item.appendChild(actions);

  item.addEventListener("click", () => openFolder(row));
  item.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter" || ev.key === " ") {
      ev.preventDefault();
      openFolder(row);
    }
  });

  return item;
}

function renderState(kind) {
  els.results.innerHTML = "";
  const card = document.createElement("div");
  card.className = "state-card";
  const inner = document.createElement("div");

  const title = document.createElement("strong");
  const body = document.createElement("div");
  const button = document.createElement("button");
  button.type = "button";
  button.className = "ghost-button";

  if (kind === "empty-filtered") {
    title.textContent = "No yoinks match your filters.";
    body.textContent = "Try broadening the search or clearing filters.";
    button.textContent = "Clear filters";
    button.addEventListener("click", clearFilters);
    inner.append(title, body, button);
  } else if (kind === "empty-all") {
    title.textContent = "You haven't yoinked anything yet.";
    body.textContent = "Open the extension on YouTube and yoink your first video.";
    button.textContent = "Open the extension";
    button.addEventListener("click", () => {
      if (chrome.action && chrome.action.openPopup) chrome.action.openPopup();
    });
    inner.append(title, body, button);
  } else {
    title.textContent = "Couldn't load Yoink Memory.";
    body.textContent = "Check that the Yoink helper is running, then try again.";
    button.textContent = "Retry";
    button.addEventListener("click", loadResults);
    inner.append(title, body, button);
  }

  card.appendChild(inner);
  els.results.appendChild(card);
}

function renderCounts() {
  if (filtersActive() && state.totalAll && state.totalAll !== state.total) {
    els.count.textContent = `${state.total} / ${state.totalAll} yoinks`;
  } else {
    els.count.textContent = `${state.total} ${state.total === 1 ? "yoink" : "yoinks"}`;
  }
}

function renderPagination() {
  const totalPages = Math.max(1, Math.ceil(state.total / LIMIT));
  const page = Math.min(totalPages, Math.floor(state.offset / LIMIT) + 1);
  els.page.textContent = `Page ${page} of ${totalPages}`;
  els.prev.disabled = page <= 1 || state.loading;
  els.next.disabled = page >= totalPages || state.loading;
}

function renderResults() {
  renderCounts();
  renderPagination();
  els.results.innerHTML = "";
  if (!state.rows.length) {
    renderState(filtersActive() ? "empty-filtered" : "empty-all");
    return;
  }
  for (const row of state.rows) {
    els.results.appendChild(renderRow(row));
  }
}

function setOffline(isOffline) {
  els.offline.classList.toggle("hidden", !isOffline);
}

async function loadResults() {
  closeMenu();
  state.loading = true;
  renderPagination();
  els.results.innerHTML = '<div class="loading-row">Loading Yoink Memory...</div>';
  try {
    const body = await authedJson(`/memory/search?${queryString()}`, { method: "GET" });
    const normalized = normalizeSearchResponse(body);
    state.rows = normalized.rows;
    state.total = normalized.total;
    state.totalAll = normalized.totalAll;
    setOffline(false);
    renderResults();
  } catch (e) {
    state.rows = [];
    state.total = 0;
    state.totalAll = 0;
    setOffline(!e || e.status === 0 || e.status >= 500 || e instanceof TypeError);
    renderCounts();
    renderPagination();
    renderState("error");
  } finally {
    state.loading = false;
    renderPagination();
  }
}

function showToast(message, action = null) {
  clearTimeout(state.toastTimer);
  els.toast.innerHTML = "";
  const text = document.createElement("span");
  text.textContent = message;
  els.toast.appendChild(text);
  if (action) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.textContent = action.label;
    btn.addEventListener("click", async () => {
      els.toast.classList.add("hidden");
      await action.onClick();
    });
    els.toast.appendChild(btn);
  }
  els.toast.classList.remove("hidden");
  state.toastTimer = setTimeout(() => {
    els.toast.classList.add("hidden");
  }, action ? 8000 : 3200);
}

async function onFilterChange(resetPage = true) {
  readFiltersFromInputs();
  if (resetPage) state.offset = 0;
  await persistFilters();
  await loadResults();
}

async function clearFilters() {
  state.filters = {
    q: "",
    channel: "",
    topic: "",
    hook_type: "",
    date_from: "",
    date_to: "",
  };
  state.offset = 0;
  applyFiltersToInputs();
  await persistFilters();
  await loadResults();
}

function bindEvents() {
  const debounced = debounce(() => onFilterChange(true), 300);
  els.search.addEventListener("input", debounced);
  for (const el of [els.channel, els.topic]) {
    el.addEventListener("input", debounced);
  }
  for (const el of [els.hook, els.dateFrom, els.dateTo]) {
    el.addEventListener("change", () => onFilterChange(true));
  }
  els.clear.addEventListener("click", clearFilters);
  els.prev.addEventListener("click", async () => {
    if (state.offset <= 0) return;
    state.offset = Math.max(0, state.offset - LIMIT);
    await loadResults();
  });
  els.next.addEventListener("click", async () => {
    if (state.offset + LIMIT >= state.total) return;
    state.offset += LIMIT;
    await loadResults();
  });
  document.addEventListener("click", (ev) => {
    if (state.menu && !state.menu.contains(ev.target)) closeMenu();
  });
  document.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape") closeMenu();
  });
}

async function boot() {
  bindEvents();
  const stored = await storageGet({ [FILTER_STORAGE_KEY]: state.filters });
  if (stored && stored[FILTER_STORAGE_KEY]) {
    state.filters = Object.assign({}, state.filters, stored[FILTER_STORAGE_KEY]);
  }
  applyFiltersToInputs();
  await loadResults();
}

boot();
