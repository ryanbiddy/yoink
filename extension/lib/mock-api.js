// Yoink v2 mock API layer.
//
// Phase 1 stand-in for the playlist endpoints Codex is implementing on
// `codex/v2-backend-playlist`. The popup routes through STC.playlist* which
// dispatches to this layer when globalThis.YOINK_USE_MOCK_API is true. When
// the real backend lands, flip USE_MOCK_API in popup.js to false and reconcile
// the shapes here against docs/v2-api.md.
//
// Exposed on globalThis.MOCK_API. Classic-script style so it can be loaded
// from popup.html alongside extract.js without an ES module shell.

(function (global) {
  "use strict";

  const MOCK_JOB_ID = "mock_job_001";
  const MOCK_TOTAL_VIDEOS = 10;
  const MOCK_SECONDS_PER_VIDEO = 3;
  const MOCK_PHASES = ["metadata", "download", "screenshots", "comments"];
  const PREVIEW_DELAY_MS = 500;

  // Inline placeholder thumb — keeps mock mode 100% offline so demos work
  // when the local server is down and there's no public network egress.
  const PLACEHOLDER_THUMB =
    "data:image/svg+xml;utf8," +
    encodeURIComponent(
      "<svg xmlns='http://www.w3.org/2000/svg' width='80' height='45' viewBox='0 0 80 45'>" +
        "<rect width='80' height='45' fill='#3a3a3f'/>" +
        "<text x='40' y='28' fill='#888' font-size='11' text-anchor='middle' " +
        "font-family='sans-serif'>YT</text></svg>"
    );

  const MOCK_VIDEOS = [
    { title: "How transformers actually work — a visual primer",
      duration_seconds: 765 },
    { title: "Attention is all you need, explained line-by-line",
      duration_seconds: 1242 },
    { title: "Why RLHF eats compute — a back-of-envelope",
      duration_seconds: 488 },
    { title: "The Anthropic interpretability stack tour",
      duration_seconds: 2103 },
    { title: "Sparse autoencoders demystified",
      duration_seconds: 934 },
    { title: "Feature circuits and why they matter",
      duration_seconds: 612 },
    { title: "Constitutional AI in 12 minutes",
      duration_seconds: 731 },
    { title: "Long-context evaluation pitfalls",
      duration_seconds: 1018 },
    { title: "What 1M-token context actually buys you",
      duration_seconds: 1456 },
    { title: "Tool use loops that don't spiral",
      duration_seconds: 823 },
  ];

  // ---- Job state ---------------------------------------------------------
  // Reset on each playlistStart. Lives in module scope; popup is short-lived
  // so this is fine — a fresh popup open after a completed mock job just
  // returns "no job" until start is called again.
  let job = null;

  function _resetJob() {
    job = {
      job_id: MOCK_JOB_ID,
      state: "queued",
      videos_total: MOCK_TOTAL_VIDEOS,
      videos_failed: 0,
      started_at: new Date().toISOString(),
      // statusFirstCall flips false after the very first poll, so call #1
      // reports "queued" and call #2 onwards reports "running" — matches the
      // real backend's queue-then-pick-up timing.
      statusFirstCall: true,
      runStartAt: null,
      cancelled: false,
      result: null,
    };
  }

  function _isoNow() { return new Date().toISOString(); }

  function _delay(ms) { return new Promise((r) => setTimeout(r, ms)); }

  // ---- Public mock endpoints --------------------------------------------

  async function playlistPreview(_url) {
    await _delay(PREVIEW_DELAY_MS);
    // Pretend the source playlist has 12 videos but we cap at 10 — exercises
    // the "X of Y — capped at 10" indicator in the popup.
    const cap = MOCK_TOTAL_VIDEOS;
    const totalInPlaylist = 12;
    const videos = MOCK_VIDEOS.slice(0, cap).map((v, i) => ({
      title: v.title,
      duration_seconds: v.duration_seconds,
      thumbnail_url: PLACEHOLDER_THUMB,
      url: `https://www.youtube.com/watch?v=mock_${String(i + 1).padStart(2, "0")}`,
    }));
    return {
      ok: true,
      videos,
      total_in_playlist: totalInPlaylist,
      cap,
    };
  }

  async function playlistStart(_url) {
    _resetJob();
    return { ok: true, job_id: MOCK_JOB_ID };
  }

  function jobStatus(jobId) {
    if (!job || jobId !== MOCK_JOB_ID) {
      return Promise.resolve({ ok: false, error: "Unknown job id (mock)" });
    }

    // Cancellation wins over progress.
    if (job.cancelled && job.state !== "completed") {
      job.state = "cancelled";
      return Promise.resolve(_renderStatus());
    }

    // First poll: still queued. Second poll onwards: kick off the run clock.
    if (job.statusFirstCall) {
      job.statusFirstCall = false;
      return Promise.resolve(_renderStatus());
    }

    if (!job.runStartAt) {
      job.runStartAt = Date.now();
      job.state = "running";
    }

    const elapsedSec = (Date.now() - job.runStartAt) / 1000;
    const videosDone = Math.min(
      MOCK_TOTAL_VIDEOS,
      Math.floor(elapsedSec / MOCK_SECONDS_PER_VIDEO)
    );

    if (videosDone >= MOCK_TOTAL_VIDEOS) {
      if (job.state !== "completed") {
        job.state = "completed";
        job.completed_at = _isoNow();
        job.result = _buildMockResult();
      }
      return Promise.resolve(_renderStatus(videosDone));
    }

    return Promise.resolve(_renderStatus(videosDone, elapsedSec));
  }

  async function jobCancel(jobId) {
    if (!job || jobId !== MOCK_JOB_ID) {
      return { ok: false, error: "Unknown job id (mock)" };
    }
    job.cancelled = true;
    return { ok: true };
  }

  // ---- Status payload builder -------------------------------------------

  function _renderStatus(videosDoneArg, elapsedSecArg) {
    const videos_done = videosDoneArg != null ? videosDoneArg : 0;

    let current_video = null;
    let current_video_phase = null;

    if (job.state === "running" && videos_done < MOCK_TOTAL_VIDEOS) {
      const idx = videos_done; // 0-based index of the in-flight video
      current_video = {
        title: MOCK_VIDEOS[idx].title,
        index: idx + 1,
      };
      const elapsedSec = elapsedSecArg != null
        ? elapsedSecArg
        : (Date.now() - (job.runStartAt || Date.now())) / 1000;
      const intoVideo = elapsedSec % MOCK_SECONDS_PER_VIDEO;
      const phaseIdx = Math.min(
        MOCK_PHASES.length - 1,
        Math.floor((intoVideo / MOCK_SECONDS_PER_VIDEO) * MOCK_PHASES.length)
      );
      current_video_phase = MOCK_PHASES[phaseIdx];
    } else if (job.state === "completed") {
      current_video_phase = "done";
    }

    return {
      ok: true,
      job_id: job.job_id,
      state: job.state,
      videos_total: job.videos_total,
      videos_done,
      videos_failed: job.videos_failed,
      current_video,
      current_video_phase,
      started_at: job.started_at,
      updated_at: _isoNow(),
      completed_at: job.completed_at || null,
      error: null,
      result: job.state === "completed" ? job.result : null,
    };
  }

  function _buildMockResult() {
    const per_video = MOCK_VIDEOS.map((v, i) => ({
      title: v.title,
      video_slug: `mock_${String(i + 1).padStart(2, "0")}`,
      folder: `C:/Users/you/yoinks/playlist_mock/${String(i + 1).padStart(2, "0")}_${
        v.title.toLowerCase().replace(/[^a-z0-9]+/g, "_").slice(0, 30)
      }`,
    }));
    const combined_md_path =
      "C:/Users/you/yoinks/playlist_mock/_combined_corpus.md";
    const combined_md_text =
      "# Playlist corpus (mock)\n\n" +
      per_video
        .map(
          (p, i) =>
            `## ${i + 1}. ${p.title}\n\n` +
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
  };
})(typeof self !== "undefined" ? self : globalThis);
