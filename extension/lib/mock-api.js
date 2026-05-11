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
  // enabled-CI path without going through updateSettings().
  const MOCK_DEFAULT_SETTINGS = {
    comment_intelligence_enabled: false,
    anthropic_key_set: false,
  };
  // FLIP THIS to true to exercise the Comment Intelligence indicator path
  // in mock mode without persisting a fake key through updateSettings().
  const MOCK_FORCE_CI_ENABLED = false;
  let mockSettings = {
    comment_intelligence_enabled: MOCK_FORCE_CI_ENABLED
      ? true
      : MOCK_DEFAULT_SETTINGS.comment_intelligence_enabled,
    anthropic_key_set: MOCK_FORCE_CI_ENABLED
      ? true
      : MOCK_DEFAULT_SETTINGS.anthropic_key_set,
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

  async function playlistStart(url) {
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
    if (!job) return { ok: true, jobs: [] };
    return { ok: true, jobs: [_publicJob()] };
  }

  // ---- Settings endpoints (docs/v2-comment-intelligence.md) -------------

  function _settingsSnapshot() {
    return {
      comment_intelligence_enabled: mockSettings.comment_intelligence_enabled,
      anthropic_key_set: mockSavedKeyPresent,
    };
  }

  async function getSettings() {
    return { ok: true, settings: _settingsSnapshot() };
  }

  // POST /settings rules per the contract:
  // - comment_intelligence_enabled is required and must be boolean.
  // - anthropic_key omitted -> existing saved key preserved.
  // - anthropic_key non-empty string -> replaces saved key.
  // - anthropic_key null or empty string -> clears saved key.
  async function updateSettings(body) {
    const b = body || {};
    if (typeof b.comment_intelligence_enabled !== "boolean") {
      return { ok: false, error: "comment_intelligence_enabled must be boolean" };
    }
    mockSettings.comment_intelligence_enabled = b.comment_intelligence_enabled;
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
      // The contract says: "anthropic_key as a non-empty string replaces
      // the saved key" — that's POST /settings semantics. For test-key
      // alone the contract is silent on save semantics, but the success
      // example flips anthropic_key_set to true, implying a successful
      // test-key persists the key. Mirror that here.
      if (valid) mockSavedKeyPresent = true;
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

  global.MOCK_API = {
    playlistPreview,
    playlistStart,
    jobStatus,
    jobCancel,
    jobsList,
    getSettings,
    updateSettings,
    testAnthropicKey,
  };
})(typeof self !== "undefined" ? self : globalThis);
