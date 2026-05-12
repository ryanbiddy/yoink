// Yoink v2 mock API layer.
//
// Phase 1 stand-in for the playlist endpoints Codex is implementing on
// `codex/v2-backend-playlist`. The popup routes through STC.playlist* /
// STC.jobStatus / STC.jobCancel / STC.jobsList, which dispatches to this
// layer when globalThis.YOINK_USE_MOCK_API is true.
//
// Shapes mirror docs/v2-api.md on codex/v2-backend-playlist (Sprint 1
// reconciliation). Top-level wrappers: { ok, playlist }, { ok, job, job_id? },
// { ok, jobs }. When the real backend lands, flip USE_MOCK_API in popup.js
// to false; the contract is what's authoritative.

(function (global) {
  "use strict";

  const MOCK_JOB_ID = "job_mock_20260510_143012_a1b2c3";
  const MOCK_TOTAL_VIDEOS = 10;           // post-cap (videos_total in job)
  const MOCK_VIDEO_COUNT = 12;            // pre-cap source playlist size
  const MOCK_SECONDS_PER_VIDEO = 3;
  const MOCK_PHASES = ["metadata", "download", "screenshots", "comments"];
  const PREVIEW_DELAY_MS = 500;
  const MOCK_PLAYLIST_TITLE = "Creator Strategy Interviews";
  const MOCK_PLAYLIST_UPLOADER = "Example Channel";
  const MOCK_SESSION_FOLDER =
    "C:\\Users\\Ryan\\Desktop\\Yoink\\_sessions\\creator-strategy-interviews";

  // Persistent mock-settings state. Survives mock job lifecycle but is reset
  // on popup close (module scope). Flip the constants below to test the
  // enabled-feature paths without going through updateSettings().
  // Settings shape matches docs/v2-comment-intelligence.md (Sprint 3
  // extension: hook_type_enabled + smart_screenshot_picker_enabled).
  const MOCK_DEFAULT_SETTINGS = {
    comment_intelligence_enabled: false,
    hook_type_enabled: false,
    smart_screenshot_picker_enabled: false,
    anthropic_key_set: false,
  };
  // FLIP these to true to exercise their respective UI paths in mock mode
  // without persisting a fake key through updateSettings.
  // Note: any FORCE flag that needs a key (CI / Hook Type) also flips
  // anthropic_key_set to true so the "key required" gate is satisfied.
  const MOCK_FORCE_CI_ENABLED = false;
  const MOCK_FORCE_HOOK_TYPE_ENABLED = false;
  const MOCK_FORCE_SCREENSHOT_PICKER = false;
  // Sprint 6 recovery-flow fixtures. The mock job-state machine resets when
  // the popup closes, so item-3 ("last yoink completed" affordance on boot)
  // and item-4 ("playlist running" pill in single-video) are normally
  // impossible to exercise from scratch — flip these to inject fake state
  // into jobsList() at popup boot. Set at most one to true at a time.
  const MOCK_FORCE_RECOVERY_RUNNING = false;     // simulates close+reopen mid-job
  const MOCK_FORCE_RECOVERY_COMPLETED = false;   // simulates last-yoink affordance (playlist)
  // Sprint 7: simulates a recent kind="single" record returned by /jobs.
  // Per Codex's Sprint 7 contract, /extract writes a side-effect single job
  // record that surfaces in /jobs alongside playlists.
  const MOCK_FORCE_RECOVERY_SINGLE_COMPLETED = false;
  const _needsKey =
    MOCK_FORCE_CI_ENABLED || MOCK_FORCE_HOOK_TYPE_ENABLED;
  let mockSettings = {
    comment_intelligence_enabled:
      MOCK_FORCE_CI_ENABLED || MOCK_DEFAULT_SETTINGS.comment_intelligence_enabled,
    hook_type_enabled:
      MOCK_FORCE_HOOK_TYPE_ENABLED || MOCK_DEFAULT_SETTINGS.hook_type_enabled,
    smart_screenshot_picker_enabled:
      MOCK_FORCE_SCREENSHOT_PICKER || MOCK_DEFAULT_SETTINGS.smart_screenshot_picker_enabled,
    anthropic_key_set: _needsKey || MOCK_DEFAULT_SETTINGS.anthropic_key_set,
  };
  // Keys are never echoed back. We store presence-only, mirroring the real
  // server's `anthropic_key_set` boolean.
  let mockSavedKeyPresent = mockSettings.anthropic_key_set;
  // Bake a per-video failure on video index 3 so the completion view's
  // per-video failure surface is always exercised in mock mode.
  const MOCK_FAILED_VIDEO_INDEX = 3;
  const MOCK_FAILED_VIDEO_ERROR = "video unavailable (mock — age-restricted)";

  // Mock video metadata. `id` is the contract's video id (used by the UI to
  // build thumbnail URLs via i.ytimg.com — fallback handled client-side).
  // channel/duration_seconds may be null per the contract's
  // "preview metadata completeness" open question — we exercise both null
  // cases here so the UI's null-handling stays honest.
  const MOCK_VIDEOS = [
    { id: "dQw4w9WgXcQ", title: "How transformers actually work — a visual primer",
      channel: "Example Channel", duration_seconds: 765 },
    { id: "jNQXAC9IVRw", title: "Attention is all you need, explained line-by-line",
      channel: "Example Channel", duration_seconds: 1242 },
    { id: "9bZkp7q19f0", title: "Why RLHF eats compute — a back-of-envelope",
      channel: null, duration_seconds: 488 },        // null channel
    { id: "kJQP7kiw5Fk", title: "The Anthropic interpretability stack tour",
      channel: "Example Channel", duration_seconds: null },  // null duration
    { id: "fJ9rUzIMcZQ", title: "Sparse autoencoders demystified",
      channel: "Example Channel", duration_seconds: 934 },
    { id: "RgKAFK5djSk", title: "Feature circuits and why they matter",
      channel: "Example Channel", duration_seconds: 612 },
    { id: "OPf0YbXqDm0", title: "Constitutional AI in 12 minutes",
      channel: "Example Channel", duration_seconds: 731 },
    { id: "CevxZvSJLk8", title: "Long-context evaluation pitfalls",
      channel: "Example Channel", duration_seconds: 1018 },
    { id: "hT_nvWreIhg", title: "What 1M-token context actually buys you",
      channel: "Example Channel", duration_seconds: 1456 },
    { id: "60ItHLz5WEA", title: "Tool use loops that don't spiral",
      channel: "Example Channel", duration_seconds: 823 },
  ];

  // ---- Job state ---------------------------------------------------------
  // Reset on each playlistStart. Lives in module scope; popup is short-lived
  // so this is fine — a fresh popup open after a completed mock job returns
  // it from jobsList for recovery, but jobStatus on the same id after popup
  // close still works for one session lifetime.
  let job = null;

  function _resetJob(sourceUrl) {
    job = {
      id: MOCK_JOB_ID,
      kind: "playlist",
      state: "queued",
      source_url: sourceUrl || "https://www.youtube.com/playlist?list=PLmock",
      // Sprint 7: contract field. Null for playlist jobs (title belongs to
      // single-video records); kept here so _publicJob emits it consistently.
      title: null,
      playlist_title: MOCK_PLAYLIST_TITLE,
      // session_folder is populated from `queued` onwards and stays
      // populated through every terminal state (including cancelled and
      // failed). See docs/v2-api.md "Field rules".
      session_folder: MOCK_SESSION_FOLDER,
      videos_total: MOCK_TOTAL_VIDEOS,
      videos_done: 0,
      videos_failed: 0,
      current_video: null,
      current_video_phase: null,
      started_at: null,
      updated_at: _isoNow(),
      completed_at: null,
      error: null,
      result: null,
      warnings: ["playlist exceeds cap"],
      message: `Playlist has ${MOCK_VIDEO_COUNT} videos -- yoinking the first ${MOCK_TOTAL_VIDEOS}.`,

      // private bookkeeping (stripped before returning)
      _statusFirstCall: true,
      _runStartAt: null,
      _cancelRequested: false,
    };
  }

  function _isoNow() { return new Date().toISOString(); }

  function _delay(ms) { return new Promise((r) => setTimeout(r, ms)); }

  // Strip private fields (underscore-prefixed) so we return only contract
  // fields — keeps the boundary between bookkeeping and wire shape clean.
  function _publicJob() {
    const out = {};
    for (const k of Object.keys(job)) {
      if (!k.startsWith("_")) out[k] = job[k];
    }
    return out;
  }

  // ---- Public mock endpoints --------------------------------------------

  async function playlistPreview(url) {
    await _delay(PREVIEW_DELAY_MS);
    const cap = MOCK_TOTAL_VIDEOS;
    const videoCount = MOCK_VIDEO_COUNT;
    const truncated = videoCount > cap;
    const willProcessCount = Math.min(videoCount, cap);
    const warnings = truncated ? ["playlist exceeds cap"] : [];
    const message = truncated
      ? `Playlist has ${videoCount} videos -- yoinking the first ${cap}.`
      : `Playlist has ${videoCount} video${videoCount === 1 ? "" : "s"}.`;
    const videos = MOCK_VIDEOS.slice(0, willProcessCount).map((v, i) => ({
      index: i + 1,
      id: v.id,
      url: `https://www.youtube.com/watch?v=${v.id}`,
      title: v.title,
      channel: v.channel,
      duration_seconds: v.duration_seconds,
    }));
    return {
      ok: true,
      playlist: {
        url: url || "https://www.youtube.com/playlist?list=PLmock",
        title: MOCK_PLAYLIST_TITLE,
        uploader: MOCK_PLAYLIST_UPLOADER,
        video_count: videoCount,
        cap,
        will_process_count: willProcessCount,
        truncated,
        message,
        warnings,
        videos,
      },
    };
  }

  async function playlistStart(url, _interval) {
    // _interval is accepted for signature parity with the real backend but
    // ignored — mock job timing is wall-clock based, not screenshot-derived.
    _resetJob(url);
    // Contract returns BOTH top-level job_id AND nested job — preserves
    // backward compat for any client that grabs job_id without unwrapping.
    return {
      ok: true,
      job_id: MOCK_JOB_ID,
      job: _publicJob(),
    };
  }

  function jobStatus(jobId) {
    // Sprint 6 fixture: if the running-recovery flag is set, also serve
    // jobStatus for the fixture id so polling doesn't immediately
    // terminate with "job not found". The fixture state is frozen — it
    // exists to exercise the recovery UI + active-playlist pill, not to
    // simulate progress.
    if (MOCK_FORCE_RECOVERY_RUNNING && jobId === "fixture_running_job") {
      return Promise.resolve({ ok: true, job: _fixtureRunningJob() });
    }
    if (!job || jobId !== MOCK_JOB_ID) {
      return Promise.resolve({ ok: false, error: "job not found" });
    }

    // Cancellation wins over progress unless already completed.
    if (job._cancelRequested && job.state !== "completed") {
      _applyCancel();
      return Promise.resolve({ ok: true, job: _publicJob() });
    }

    // First poll: still queued. Second poll onwards: kick off the run clock.
    if (job._statusFirstCall) {
      job._statusFirstCall = false;
      job.updated_at = _isoNow();
      return Promise.resolve({ ok: true, job: _publicJob() });
    }

    if (!job._runStartAt) {
      job._runStartAt = Date.now();
      job.state = "running";
      job.started_at = _isoNow();
    }

    const elapsedSec = (Date.now() - job._runStartAt) / 1000;
    const videosDone = Math.min(
      MOCK_TOTAL_VIDEOS,
      Math.floor(elapsedSec / MOCK_SECONDS_PER_VIDEO)
    );

    if (videosDone >= MOCK_TOTAL_VIDEOS) {
      if (job.state !== "completed") {
        _applyCompletion(videosDone);
      }
      job.updated_at = _isoNow();
      return Promise.resolve({ ok: true, job: _publicJob() });
    }

    _applyRunningSnapshot(videosDone, elapsedSec);
    job.updated_at = _isoNow();
    return Promise.resolve({ ok: true, job: _publicJob() });
  }

  async function jobCancel(jobId) {
    // Sprint 9: handle cancel for the running-recovery fixture so QA can
    // exit the recovered-state UI in mock mode. Synthesized statelessly —
    // the cancel response itself carries state="cancelled", which is what
    // the popup's cancel handler renders via onCancelled(). The popup
    // stops polling on cancel, so the fixture's running-state jobStatus
    // path doesn't get hit again.
    if (MOCK_FORCE_RECOVERY_RUNNING && jobId === "fixture_running_job") {
      const cancelled = _fixtureRunningJob();
      cancelled.state = "cancelled";
      cancelled.completed_at = _isoNow();
      cancelled.updated_at = _isoNow();
      cancelled.current_video = null;
      cancelled.current_video_phase = null;
      cancelled.message =
        "Playlist job cancelled. Partial outputs were left on disk.";
      return { ok: true, job: cancelled };
    }
    if (!job || jobId !== MOCK_JOB_ID) {
      return { ok: false, error: "job not found" };
    }
    if (job.state === "completed" || job.state === "cancelled" || job.state === "failed") {
      return { ok: false, error: "job is already finished" };
    }
    job._cancelRequested = true;
    // Per contract, cancel response returns the full updated job. We apply
    // the cancellation transition immediately so the response reflects the
    // terminal state.
    _applyCancel();
    return { ok: true, job: _publicJob() };
  }

  async function jobsList() {
    if (!job) {
      // No live mock job. Sprint 6/7 fixtures synthesize a job purely so
      // the UI can exercise recovery paths that depend on jobsList
      // returning something from before the popup opened. Only one
      // fixture flag should be true at a time — they're ordered here so
      // the running fixture wins (most useful for testing the pill).
      if (MOCK_FORCE_RECOVERY_RUNNING) return { ok: true, jobs: [_fixtureRunningJob()] };
      if (MOCK_FORCE_RECOVERY_COMPLETED) return { ok: true, jobs: [_fixtureCompletedJob()] };
      if (MOCK_FORCE_RECOVERY_SINGLE_COMPLETED) {
        return { ok: true, jobs: [_fixtureSingleCompletedJob()] };
      }
      return { ok: true, jobs: [] };
    }
    return { ok: true, jobs: [_publicJob()] };
  }

  // --- Sprint 6 recovery fixtures ---------------------------------------
  // _fixtureRunningJob: a frozen running playlist job. Paired with
  // MOCK_FORCE_RECOVERY_RUNNING, lets the popup boot into the recovery
  // path (auto-switch to playlist mode + progress panel) without going
  // through playlistStart. jobStatus(fixture_running_job) keeps serving
  // the same frozen state on every poll tick so the running UI + pill
  // remain interactive; the fixture does not progress on its own.
  // Sprint 9: cancel from the recovered-state UI now works — jobCancel
  // recognizes the fixture id and returns a synthesized cancelled job,
  // which the popup renders via onCancelled. Reload the popup with the
  // flag flipped off to clear the fixture.
  function _fixtureRunningJob() {
    const now = new Date().toISOString();
    return {
      id: "fixture_running_job",
      kind: "playlist",
      state: "running",
      source_url: "https://www.youtube.com/playlist?list=PLfixture",
      title: null,
      playlist_title: MOCK_PLAYLIST_TITLE,
      session_folder: MOCK_SESSION_FOLDER,
      videos_total: MOCK_TOTAL_VIDEOS,
      videos_done: 4,
      videos_failed: 0,
      current_video: { index: 5, title: MOCK_VIDEOS[4].title,
                       url: `https://www.youtube.com/watch?v=${MOCK_VIDEOS[4].id}` },
      current_video_phase: "screenshots",
      started_at: now,
      updated_at: now,
      completed_at: null,
      error: null,
      result: null,
      warnings: ["playlist exceeds cap"],
      message: "Yoinking video 5 of 10.",
    };
  }

  // _fixtureCompletedJob: a recently-finished playlist job. Pairing with
  // MOCK_FORCE_RECOVERY_COMPLETED exercises the "Last playlist: ..."
  // affordance on the playlist input panel. completed_at is 5 minutes
  // ago so it falls inside the popup's 30-minute "recent" window.
  function _fixtureCompletedJob() {
    const completedAt = new Date(Date.now() - 5 * 60 * 1000).toISOString();
    return {
      id: "fixture_completed_job",
      kind: "playlist",
      state: "completed",
      source_url: "https://www.youtube.com/playlist?list=PLfixturedone",
      title: null,
      playlist_title: MOCK_PLAYLIST_TITLE,
      session_folder: MOCK_SESSION_FOLDER,
      videos_total: MOCK_TOTAL_VIDEOS,
      videos_done: 9,
      videos_failed: 1,
      current_video: null,
      current_video_phase: null,
      started_at: completedAt,
      updated_at: completedAt,
      completed_at: completedAt,
      error: null,
      result: null, // result is null in jobsList — only /jobs/<id> returns the full result
      warnings: ["playlist exceeds cap"],
      message: "Playlist complete.",
    };
  }

  // _fixtureSingleCompletedJob (Sprint 7): a recently-finished kind="single"
  // job. Pairs with MOCK_FORCE_RECOVERY_SINGLE_COMPLETED to exercise the
  // single-video branch of the "Last yoink: ..." affordance. Shape per
  // docs/v2-api.md GET /jobs example: title populated, playlist_title null,
  // videos_total: 1, videos_done: 1. completed_at within the 30-minute
  // window.
  function _fixtureSingleCompletedJob() {
    const completedAt = new Date(Date.now() - 3 * 60 * 1000).toISOString();
    const folder =
      "C:\\Users\\Ryan\\Desktop\\Yoink\\Creator Research\\" +
      "a-practical-guide-to-creator-research";
    return {
      id: "fixture_single_completed_job",
      kind: "single",
      state: "completed",
      source_url: "https://www.youtube.com/watch?v=abc123DEF45",
      title: "A practical guide to creator research",
      playlist_title: null,
      session_folder: folder,
      videos_total: 1,
      videos_done: 1,
      videos_failed: 0,
      current_video: null,
      current_video_phase: null,
      started_at: completedAt,
      updated_at: completedAt,
      completed_at: completedAt,
      error: null,
      result: null, // jobsList omits result; only /jobs/<id> returns it
      warnings: [],
      message: "Single-video yoink complete.",
    };
  }

  // ---- Settings endpoints (docs/v2-comment-intelligence.md) -------------

  function _settingsSnapshot() {
    return {
      comment_intelligence_enabled: mockSettings.comment_intelligence_enabled,
      hook_type_enabled: mockSettings.hook_type_enabled,
      smart_screenshot_picker_enabled: mockSettings.smart_screenshot_picker_enabled,
      anthropic_key_set: mockSavedKeyPresent,
    };
  }

  async function getSettings() {
    return { ok: true, settings: _settingsSnapshot() };
  }

  // POST /settings rules per the Sprint 3 contract:
  // - comment_intelligence_enabled, hook_type_enabled,
  //   smart_screenshot_picker_enabled are optional booleans. Omitted fields
  //   keep their existing value.
  // - anthropic_key omitted -> existing saved key preserved.
  // - anthropic_key non-empty string -> replaces saved key.
  // - anthropic_key null or empty string -> clears saved key.
  async function updateSettings(body) {
    const b = body || {};
    const flagFields = [
      "comment_intelligence_enabled",
      "hook_type_enabled",
      "smart_screenshot_picker_enabled",
    ];
    for (const f of flagFields) {
      if (!Object.prototype.hasOwnProperty.call(b, f)) continue;
      if (typeof b[f] !== "boolean") {
        return { ok: false, error: `${f} must be boolean` };
      }
      mockSettings[f] = b[f];
    }
    if (Object.prototype.hasOwnProperty.call(b, "anthropic_key")) {
      const k = b.anthropic_key;
      if (k === null || k === "") {
        mockSavedKeyPresent = false;
      } else if (typeof k === "string" && k.length > 0) {
        mockSavedKeyPresent = true;
      }
    }
    // The contract requires the response to mirror GET /settings exactly.
    return { ok: true, settings: _settingsSnapshot() };
  }

  // POST /settings/test-key — pretend to send "hi" to Anthropic. Accepts an
  // unsaved key (in body.anthropic_key) or {} to test the saved key.
  // Mock heuristic: keys starting with "sk-ant-" are considered valid; the
  // saved-key path is "valid" iff there's a saved key.
  async function testAnthropicKey(rawArg) {
    let testKey = null;
    if (typeof rawArg === "string") testKey = rawArg;
    else if (rawArg && typeof rawArg.anthropic_key === "string") testKey = rawArg.anthropic_key;

    let valid;
    let error = null;
    if (testKey != null) {
      valid = testKey.startsWith("sk-ant-") && testKey.length > 12;
      if (!valid) error = "invalid x-api-key";
      // Sprint 5: the real /settings/test-key endpoint validates without
      // persisting (setup.html POSTs /settings separately to save). Mock
      // previously flipped mockSavedKeyPresent on a valid test — that drift
      // misled setup.html testing in mock mode. The mock now mirrors
      // production: a successful test does NOT save the key.
    } else {
      // Test the saved key.
      valid = mockSavedKeyPresent;
      if (!valid) error = "no saved key";
    }
    return {
      ok: true,
      valid,
      error,
      settings: _settingsSnapshot(),
    };
  }

  // ---- State transition helpers -----------------------------------------

  function _applyRunningSnapshot(videosDone, elapsedSec) {
    const idx = videosDone; // 0-based index of the in-flight video
    const v = MOCK_VIDEOS[idx];
    job.current_video = {
      index: idx + 1,
      title: v.title,
      url: `https://www.youtube.com/watch?v=${v.id}`,
    };
    const intoVideo = elapsedSec % MOCK_SECONDS_PER_VIDEO;
    const phaseIdx = Math.min(
      MOCK_PHASES.length - 1,
      Math.floor((intoVideo / MOCK_SECONDS_PER_VIDEO) * MOCK_PHASES.length)
    );
    job.current_video_phase = MOCK_PHASES[phaseIdx];
    // videos_done is the count of *successful* completions; videos_failed
    // tracks the baked-in failure once we've passed its index.
    const failedSeen = idx > (MOCK_FAILED_VIDEO_INDEX - 1) ? 1 : 0;
    job.videos_done = Math.max(0, videosDone - failedSeen);
    job.videos_failed = failedSeen;
    job.message = `Yoinking video ${idx + 1} of ${MOCK_TOTAL_VIDEOS}.`;
  }

  function _applyCompletion(videosDone) {
    job.state = "completed";
    job.current_video = null;
    job.current_video_phase = null;
    job.videos_done = videosDone - 1; // one baked-in failure
    job.videos_failed = 1;
    job.completed_at = _isoNow();
    job.result = _buildMockResult();
    job.message = "Playlist complete.";
  }

  function _applyCancel() {
    job.state = "cancelled";
    job.current_video = null;
    job.current_video_phase = null;
    job.completed_at = _isoNow();
    job.message = "Playlist job cancelled. Partial outputs were left on disk.";
  }

  function _buildMockResult() {
    const baseFolder =
      "C:\\Users\\you\\Desktop\\Yoink\\_sessions\\creator-strategy-interviews";
    const per_video = MOCK_VIDEOS.map((v, i) => {
      const idx = i + 1;
      const folder = `${baseFolder}\\video-${idx}`;
      const isFailure = idx === MOCK_FAILED_VIDEO_INDEX;
      return {
        index: idx,
        title: v.title,
        url: `https://www.youtube.com/watch?v=${v.id}`,
        folder,
        md_path: `${folder}\\video-${idx}.md`,
        json_path: `${folder}\\video-${idx}.json`,
        ok: !isFailure,
        error: isFailure ? MOCK_FAILED_VIDEO_ERROR : null,
      };
    });
    const combined_md_path = `${baseFolder}\\corpus.md`;
    const combined_md_text =
      `# Playlist Corpus: ${MOCK_PLAYLIST_TITLE}\n\n` +
      per_video
        .filter((p) => p.ok)
        .map(
          (p) =>
            `## ${p.index}. ${p.title}\n\n` +
            `_(mock corpus body — real combined.md from the backend will go here)_\n`
        )
        .join("\n") +
      "\n";
    return { combined_md_path, combined_md_text, per_video };
  }

  // ---- Screenshot thumbnail mock (GET /file?path=) ----------------------
  // Returns a small inline SVG data URL keyed by the file basename, so that
  // the picker grid shows visually distinct thumbnails in mock mode without
  // network egress. Path is hashed into a hue so the same path stably renders
  // the same color (helps the user visually identify "the cyan one") in QA.
  function _hash(s) {
    let h = 0;
    for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) | 0;
    return Math.abs(h);
  }
  function _basename(p) {
    if (!p) return "screenshot";
    const m = String(p).match(/[^\\/]+$/);
    return m ? m[0] : String(p);
  }
  async function getScreenshotThumbnail(path) {
    // Real backend returns a blob URL via getScreenshotThumbnail. The mock
    // returns a data URL — both work as <img src>.
    const hue = _hash(path || "") % 360;
    const label = _basename(path).replace(/\.[^.]+$/, "").slice(0, 14);
    const svg =
      `<svg xmlns='http://www.w3.org/2000/svg' width='120' height='68' viewBox='0 0 120 68'>` +
        `<rect width='120' height='68' fill='hsl(${hue}, 30%, 28%)'/>` +
        `<rect x='3' y='3' width='114' height='62' fill='none' stroke='hsl(${hue}, 40%, 50%)' stroke-width='1'/>` +
        `<text x='60' y='38' fill='hsl(${hue}, 25%, 88%)' font-size='10' text-anchor='middle' ` +
        `font-family='monospace'>${label}</text>` +
      `</svg>`;
    return "data:image/svg+xml;utf8," + encodeURIComponent(svg);
  }

  global.MOCK_API = {
    playlistPreview,
    playlistStart,
    jobStatus,
    jobCancel,
    jobsList,
    getSettings,
    updateSettings,
    testAnthropicKey,
    getScreenshotThumbnail,
  };
})(typeof self !== "undefined" ? self : globalThis);
