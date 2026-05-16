# Yoink v2 Comment Intelligence contract

Status: implemented in `codex/v2-sprint2`; settings extended in `codex/v2-sprint3`; key storage moved to OS keyring in v2.1 Sprint 7

## Overview

Comment Intelligence is an optional BYO Anthropic-key feature. Normal Yoink extraction works without a key. When enabled, Yoink fetches the top YouTube comments as it already does in v1, then starts a separate background analysis pass that appends structured comment insight sections to the per-video corpus file.

The internal tool-facing function is named `analyze_comments(comments)`. It intentionally returns vendor-neutral structured data so Sprint 4 can expose it as an MCP tool without coupling the tool name to Anthropic.

## Settings endpoints

Both settings endpoints require the v1 auth header:

```http
X-Yoink-Token: <token>
Content-Type: application/json
```

`GET /settings`

Returns public settings only. It never returns the Anthropic API key.

```json
{
  "ok": true,
  "settings": {
    "comment_intelligence_enabled": true,
    "hook_type_enabled": false,
    "smart_screenshot_picker_enabled": false,
    "clipboard_screenshot_cap": 4,
    "anthropic_key_set": true
  }
}
```

`POST /settings`

Request body:

```json
{
  "comment_intelligence_enabled": true,
  "hook_type_enabled": false,
  "smart_screenshot_picker_enabled": false,
  "clipboard_screenshot_cap": 4,
  "anthropic_key": "sk-ant-..."
}
```

Field rules:

- `comment_intelligence_enabled`, `hook_type_enabled`, and `smart_screenshot_picker_enabled` are optional booleans. Fields omitted from a POST keep their existing value.
- `clipboard_screenshot_cap` is optional integer configuration for single-video clipboard screenshots. Valid range: `0-12`; default: `4`. It does not affect screenshots saved on disk.
- `anthropic_key` is optional. If omitted, the existing saved key is preserved.
- `anthropic_key` as a non-empty string replaces the saved key.
- `anthropic_key` as `null` or an empty string clears the saved key.
- Starting in v2.1, the key is stored in the OS keyring. On Windows this is Windows Credential Manager via the Python `keyring` package, using service `Yoink` and username `anthropic_key`. `settings.json` stores only booleans plus `anthropic_key_invalid`.
- Legacy plaintext `anthropic_key` values in `%LOCALAPPDATA%\Yoink\settings.json` are migrated to keyring on helper startup and removed from `settings.json`.

Response body matches `GET /settings`:

```json
{
  "ok": true,
  "settings": {
    "comment_intelligence_enabled": true,
    "hook_type_enabled": false,
    "smart_screenshot_picker_enabled": false,
    "clipboard_screenshot_cap": 4,
    "anthropic_key_set": true
  }
}
```

`POST /settings/test-key`

Used by setup.html's "Test key" button. The endpoint sends a tiny "hi" prompt to Anthropic from the local server. The key is never logged and is never echoed back.

Request body with an unsaved key:

```json
{ "anthropic_key": "sk-ant-..." }
```

Request body to test the saved key:

```json
{}
```

Success:

```json
{
  "ok": true,
  "valid": true,
  "error": null,
  "settings": {
    "comment_intelligence_enabled": true,
    "hook_type_enabled": false,
    "smart_screenshot_picker_enabled": false,
    "clipboard_screenshot_cap": 4,
    "anthropic_key_set": true
  }
}
```

Failed validation:

```json
{
  "ok": true,
  "valid": false,
  "error": "invalid x-api-key",
  "settings": {
    "comment_intelligence_enabled": true,
    "hook_type_enabled": false,
    "smart_screenshot_picker_enabled": false,
    "clipboard_screenshot_cap": 4,
    "anthropic_key_set": false
  }
}
```

`GET /settings/pricing`

Used by setup.html's cost estimator. It returns local pricing constants and per-video estimates for optional AI features. It does not call Anthropic and never includes the saved API key.

```json
{
  "ok": true,
  "pricing": {
    "model": "claude-haiku-4-5-20251001",
    "display_model": "Claude Haiku 4.5",
    "input_per_million": 1.0,
    "output_per_million": 5.0,
    "est_tokens": {
      "ci": { "input": 5000, "output": 500 },
      "hook": { "input": 1200, "output": 80 }
    },
    "est_per_video": {
      "ci": 0.0075,
      "hook": 0.0016,
      "both": 0.0091
    },
    "source": "https://docs.claude.com/en/docs/about-claude/pricing",
    "source_checked": "2026-05-12"
  }
}
```

The setup page shows the estimator only when a key is present and at least one paid AI feature toggle is enabled. Pricing is a transparent estimate, not a billing guarantee.

## Model choice

The first implementation uses `claude-haiku-4-5-20251001` through Anthropic's Messages API. It is the cheapest model expected to produce usable clustering and extraction quality for top-comment analysis. The model name is centralized in `server.py` as `ANTHROPIC_MODEL`.

## Invocation flow

1. `_run_extraction()` writes the normal per-video corpus with the Top Comments placeholder.
2. `_start_comments_thread()` starts the existing comments fetch in the background.
3. When comments are fetched, the comments worker rewrites the Top Comments section and updates the JSON sidecar.
4. If Comment Intelligence is enabled, a key is set, and at least 5 comments exist, Yoink starts a second background thread for analysis.
5. The analysis thread calls `analyze_comments()` with the top 50 comments.
6. When analysis finishes, Yoink inserts/replaces the Comment Intelligence block in the per-video `.md` and updates the JSON sidecar.

Playlist jobs do not wait for Comment Intelligence. The combined playlist corpus snapshots whatever sections exist when `/jobs/<id>` transitions to `completed`.

## Skip conditions

Comment Intelligence skips silently when:

- `comment_intelligence_enabled` is false.
- No Anthropic API key is set.
- The saved Anthropic key has been marked invalid after a 401.
- The video has fewer than 5 fetched comments.
- Comments are disabled or unavailable.

Skipped analysis must not turn a successful yoink into an error.

## Corpus markdown format

Comment Intelligence sections are wrapped in marker comments so re-runs are idempotent:

```markdown
<!-- yoink:comment-intelligence-start -->
## Comment Intelligence

### Top Themes
- **Learning by doing** (12 comments): Viewers are reacting to the practical workflow.
  - "This is the first explanation that made the process click."

### Mentioned Products/Tools
- **Claude** (7)
- **NotebookLM** (3)

### Notable Disagreements
- Some commenters disagree about whether the workflow is overkill for short videos.
  - "This is useful for research, but too much for quick summaries."
<!-- yoink:comment-intelligence-end -->
```

The block is inserted immediately after the existing Top Comments marker block. If markers already exist, the old block is replaced.

## Error handling

Anthropic 429, 5xx, network failures, invalid JSON, and other analysis errors:

- Log a short reason without the key.
- Write this one-line failure body inside the Comment Intelligence markers:

```markdown
## Comment Intelligence

Comment Intelligence: analysis failed - <short reason>
```

- Update the sidecar with `comment_intelligence_status: "failed"` and `comment_intelligence_error`.
- Do not retry automatically.

Anthropic 401:

- Mark the saved key invalid by clearing it from the OS keyring.
- Subsequent `GET /settings` returns `anthropic_key_set: false`.
- Future Comment Intelligence calls skip until the user saves a key again.

## Sidecar shape

The per-video JSON sidecar gets these fields:

```json
{
  "comment_intelligence_status": "not_run|fetched|failed",
  "comment_intelligence": {
    "model": "claude-haiku-4-5-20251001",
    "top_themes": [
      {
        "label": "Learning by doing",
        "description": "Viewers respond to the practical workflow.",
        "count": 12,
        "quotes": ["This made the process click."]
      }
    ],
    "mentioned_products_tools": [
      { "name": "Claude", "frequency": 7 }
    ],
    "notable_disagreements": [
      {
        "description": "Whether the workflow is worth it for short videos.",
        "sample_comments": ["Too much for quick summaries."]
      }
    ]
  },
  "comment_intelligence_error": null,
  "comment_intelligence_updated_at": "2026-05-10T14:30:12"
}
```

## Before / after example

Before analysis:

```markdown
## Top Comments

<!-- yoink:comments-start -->
**Alex** (35 likes)
> This is useful for building a research workflow.
<!-- yoink:comments-end -->
```

After analysis:

```markdown
## Top Comments

<!-- yoink:comments-start -->
**Alex** (35 likes)
> This is useful for building a research workflow.
<!-- yoink:comments-end -->

<!-- yoink:comment-intelligence-start -->
## Comment Intelligence

### Top Themes
- **Research workflows** (9 comments): Viewers are interested in turning videos into reusable research material.
  - "This is useful for building a research workflow."

### Mentioned Products/Tools
- **Claude** (4)

### Notable Disagreements
- None found.
<!-- yoink:comment-intelligence-end -->
```

## Open questions

- Should invalid-key detection surface a browser notification, or is setup.html status enough?
