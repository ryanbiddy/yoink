# Yoink v2 Hook Type contract

Status: implemented in `codex/v2-sprint3`; taxonomy capture added in v2.1 Sprint 7

## Overview

Hook Type is an optional BYO Anthropic-key feature that classifies a video's opening style after extraction. It uses the same saved Anthropic API key as Comment Intelligence, but has its own `hook_type_enabled` setting. Normal Yoink extraction works without it.

## Settings shape

`GET /settings` returns:

```json
{
  "ok": true,
  "settings": {
    "comment_intelligence_enabled": true,
    "hook_type_enabled": true,
    "smart_screenshot_picker_enabled": false,
    "anthropic_key_set": true
  }
}
```

`POST /settings` accepts partial updates. Omitted fields keep their existing values:

```json
{
  "hook_type_enabled": true,
  "smart_screenshot_picker_enabled": false
}
```

Field rules:

- `hook_type_enabled` defaults to `false`.
- `smart_screenshot_picker_enabled` defaults to `false`.
- Hook Type uses the existing Anthropic API key stored in the OS keyring.
- The key is never returned by `GET /settings`.

## Invocation flow

1. `_run_extraction()` writes the normal per-video `.md` and JSON sidecar.
2. `_start_comments_thread()` starts the existing comments fetch in the background.
3. Hook Type waits for that comments worker to finish updating the Top Comments section. It runs after comments are fetched, marked disabled, or marked unavailable.
4. If `hook_type_enabled` is true, an Anthropic key is set, and the video has a title or description, Yoink starts a Hook Type thread.
5. The thread calls `analyze_hook_type(context)` with:
   - video title
   - channel name
   - description
   - first 250 words of transcript
   - top comment, when comments were fetched
6. The analysis writes/replaces a Hook Analysis section near the top of the per-video markdown and patches the sidecar.

Playlist jobs do not wait for Hook Type. The combined playlist corpus snapshots whatever per-video hook sections exist when the job transitions to `completed`.

## Categories

The model must return exactly one lowercase snake_case category:

- `curiosity_gap`
- `question`
- `contrarian`
- `story_open`
- `promise_list`
- `demo`
- `authority`
- `stakes`
- `other`

## Corpus markdown format

Hook Type lands immediately after the existing metadata block, before the first horizontal rule:

```markdown
## Hook Analysis
<!-- HOOK_START -->
**Hook Type:** Curiosity Gap
**Analysis:** The intro promises a counter-intuitive answer that creates anticipation in the first 10 seconds.
<!-- HOOK_END -->
```

Markers make re-runs idempotent. If the markers already exist, the whole Hook Analysis block is replaced.

## Sidecar shape

The per-video JSON sidecar includes:

```json
{
  "hook_type_status": "pending|completed|failed|skipped",
  "hook_type": "curiosity_gap",
  "hook_explanation": "The intro promises a counter-intuitive answer that creates anticipation.",
  "hook_type_error": null,
  "hook_type_updated_at": "2026-05-11T09:30:00"
}
```

Initial sidecar status is:

- `pending` when Hook Type is enabled, key is set, and title or description exists.
- `skipped` otherwise.

## Skip conditions

Hook Type skips silently when:

- `hook_type_enabled` is false.
- No Anthropic API key is set.
- The saved key was marked invalid after a 401.
- The video has no title and no description.

Skipped analysis must not turn a successful yoink into an error.

## Error handling

Anthropic 429, 5xx, network failures, invalid JSON, and unexpected response shapes:

- Log a short reason without the key.
- Write this failure section in place of Hook Analysis:

```markdown
## Hook Analysis
<!-- HOOK_START -->
Hook Type: analysis failed - <short reason>
<!-- HOOK_END -->
```

- Update the sidecar with `hook_type_status: "failed"` and `hook_type_error`.
- Do not retry automatically.

Anthropic 401:

- Clear the saved key and mark it invalid.
- Subsequent `GET /settings` returns `anthropic_key_set: false`.
- Comment Intelligence and Hook Type both skip until the user saves a key again.

## Smart Screenshot Picker setting

`smart_screenshot_picker_enabled` is a backend-persisted UI preference for Claude Code's popup work. The backend does not implement the picker UI in Sprint 3. Playlists remain text-only in the clipboard regardless of this flag.

## Aggregation

Starting in v2.1, every successful Hook Type classification is captured in `%LOCALAPPDATA%\Yoink\taxonomy.json` on Windows. The file is a JSON array of records:

```json
{
  "video_id": "abc123DEF45",
  "hook_type": "curiosity_gap",
  "hook_explanation": "The opening creates anticipation by promising a counter-intuitive answer.",
  "channel": "Example Channel",
  "title": "A practical guide to creator research",
  "classified_at": "2026-05-11T09:30:00"
}
```

Records dedupe by `video_id`: re-classifying the same video updates the existing record instead of appending a duplicate. Corrupt `taxonomy.json` is logged and replaced with a fresh array. There is no backfill from existing yoinks; the taxonomy starts collecting from this version forward. Sprint 10 exposes the captured rows through token-gated `GET /taxonomy` and the MCP `get_taxonomy` tool.
