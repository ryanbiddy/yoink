# Yoink Library Index Schema

Status: implemented through Sprint 16
Database: `%LOCALAPPDATA%\Yoink\index.db`
Scope: SQLite library index, FTS5 search, job/taxonomy migration, citations, health scores, entity graph, and backfill.

## Overview

Sprint 15 adds a local SQLite library index at `%LOCALAPPDATA%\Yoink\index.db`. The index is a local-only database: it is never uploaded, synced to a Yoink cloud service, or used for telemetry. It accelerates library operations that previously depended on scanning the Yoink output tree or reading separate JSON files. In practice, it becomes the durable home for searchable yoink metadata, FTS5 content, job records, Hook Type taxonomy rows, citation maps, and extraction health snapshots.

The index replaces or absorbs these older persistence patterns:

- Scan-based search/recent code paths for indexed consumers such as MCP library tools and new popup index surfaces.
- `%LOCALAPPDATA%\Yoink\jobs.json` for persisted job records.
- `%LOCALAPPDATA%\Yoink\taxonomy.json` for Hook Type taxonomy capture.
- Ad hoc citation reconstruction from markdown when an agent needs timestamped deep links.
- Ad hoc entity lookup across markdown when an agent needs "where did people mention X?" results.

The corpus markdown, sidecar JSON, screenshots, transcript files, and thumbnails still remain on disk in the Yoink output root. `index.db` points at those files; it does not replace the user-visible corpus folders.

## Schema Version And Migrations

Migrations live in `migrations/` next to `index.py`. Each migration file is named:

```text
NNNN_description.sql
```

The leading integer is the schema version. `index._run_migrations()` discovers those files, sorts them numerically, applies every version greater than the highest row in `schema_version`, inserts a `schema_version` row, and commits the migration. Re-running against an up-to-date database is a no-op.

Authoring a migration:

1. Create a new `migrations/NNNN_short_name.sql` file with the next integer.
2. Keep it idempotent at the application level: migrations are not re-run after their version is recorded.
3. Prefer additive changes for launch users. If data migration is needed, include it in the SQL or add a guarded Python migration step in `index.py`.
4. Update this document with new tables, indexes, and migration behavior.
5. Smoke a fresh database and an existing database before shipping.

## Tables

### `schema_version`

Tracks applied migration files.

| Column | Type | Nullability | Stores |
|---|---|---|---|
| `version` | INTEGER PRIMARY KEY | required | Migration version number from the filename prefix. |
| `applied_at` | TEXT | required | Local ISO-style timestamp when the migration was applied. |

Notes:

- Version `1` is created by `migrations/0001_initial_schema.sql`.
- The current schema version is `MAX(version)`.

### `yoinks`

Primary metadata table for one saved single-video yoink.

| Column | Type | Nullability | Stores |
|---|---|---|---|
| `video_id` | TEXT PRIMARY KEY | required | YouTube video ID. Also the join key for FTS, citations, health, and taxonomy. |
| `slug` | TEXT UNIQUE | required | Folder slug, usually the saved folder name. |
| `channel` | TEXT | nullable | Channel/uploader name from the sidecar. |
| `title` | TEXT | nullable | Video title from the sidecar. |
| `topic` | TEXT | nullable | Yoink topic folder name. |
| `hook_type` | TEXT | nullable | Latest Hook Type category when available. |
| `yoinked_at` | TEXT | required | Timestamp from the sidecar, or current local timestamp during indexing. |
| `corpus_path` | TEXT | required | Absolute path to the per-video markdown corpus. |
| `sidecar_path` | TEXT | required | Absolute path to the per-video JSON sidecar. |
| `health_score_json` | TEXT | nullable | JSON-encoded health dict for popup/MCP health surfaces. |
| `metadata_json` | TEXT | nullable | JSON snapshot of URL, duration, views, likes, and upload date. |

Query behavior:

- `video_id` is the durable identity. Re-yoinking the same video updates the row.
- `slug` stays unique so UI and agent tools can resolve the saved folder.
- `yoinked_at` backs newest-first recent-library queries.

### `yoinks_fts`

Full-text search table for saved corpora.

```sql
CREATE VIRTUAL TABLE yoinks_fts USING fts5(
    video_id UNINDEXED,
    slug,
    channel,
    title,
    topic,
    hook_type,
    content
);
```

This is a standalone FTS5 table, not an external-content table. The original design considered `content='yoinks'`, but the `yoinks` table does not have a corpus body column, so an external-content FTS table would be invalid. Instead, `yoinks_fts` stores its own indexed text.

| Column | Type | Nullability | Stores |
|---|---|---|---|
| `video_id` | UNINDEXED | required | Join key back to `yoinks.video_id`; not tokenized for search. |
| `slug` | indexed text | nullable | Folder slug, searchable. |
| `channel` | indexed text | nullable | Channel name, searchable. |
| `title` | indexed text | nullable | Video title, searchable. |
| `topic` | indexed text | nullable | Topic name, searchable. |
| `hook_type` | indexed text | nullable | Hook category, searchable/filterable. |
| `content` | indexed text | nullable | Markdown corpus text used for FTS snippets and ranking. |

Update behavior:

- FTS5 has no normal `UPSERT`.
- `index.upsert_yoink()` deletes the old FTS row by `video_id`, then inserts a fresh row.
- Search uses sanitized tokens joined into an FTS5 `MATCH` query and returns a snippet plus `bm25()` score.

### `jobs`

Durable job table for playlist and single-video job records.

| Column | Type | Nullability | Stores |
|---|---|---|---|
| `job_id` | TEXT PRIMARY KEY | required | Public job ID, same as the `id` field returned by `/jobs`. |
| `kind` | TEXT | required | `single` or `playlist`. |
| `status` | TEXT | required | Job state: `pending`, `running`, `completed`, `failed`, or `cancelled`. |
| `slug` | TEXT | nullable | Session or output folder slug when available. |
| `title` | TEXT | nullable | Video title or playlist title. |
| `error` | TEXT | nullable | Friendly terminal error, if any. |
| `started_at` | TEXT | nullable | Job start timestamp. |
| `updated_at` | TEXT | required | Last update timestamp; drives newest-first ordering. |
| `metadata_json` | TEXT | nullable | JSON-encoded public job object, with corpus text stripped. |

Important size rule:

- `metadata_json` must not contain `combined_md_text` or `corpus_md_paste`.
- Full corpus text is read from the on-disk corpus or MCP corpus tool when needed.
- This prevents `index.db` and `/jobs` from ballooning with base64 clipboard payloads.

Retention:

- `index.prune_jobs()` keeps the 200 most-recent terminal jobs.
- Non-terminal jobs are retained so a restart can mark them failed with `error="server restarted"`.
- The prune runs opportunistically every 50 job writes.

### `taxonomy`

Hook Type taxonomy capture table.

| Column | Type | Nullability | Stores |
|---|---|---|---|
| `video_id` | TEXT PRIMARY KEY | required | YouTube video ID; dedupe key. |
| `hook_type` | TEXT | required | One of the 9 Hook Type categories. |
| `hook_explanation` | TEXT | nullable | One- or two-sentence explanation from Hook Type analysis. |
| `channel` | TEXT | nullable | Channel/uploader name. |
| `title` | TEXT | nullable | Video title. |
| `classified_at` | TEXT | required | Timestamp of the classification. |

Behavior:

- `INSERT OR REPLACE` dedupes by `video_id`.
- Re-classifying the same video updates the row instead of appending a duplicate.
- `/taxonomy` and MCP `get_taxonomy` sort by `classified_at DESC` and support optional `channel`, `hook_type`, and `limit` filtering.

### `citations`

Pre-computed timestamp citation map for each indexed yoink.

| Column | Type | Nullability | Stores |
|---|---|---|---|
| `citation_id` | INTEGER PRIMARY KEY AUTOINCREMENT | required | Internal row ID. |
| `video_id` | TEXT | required | Foreign key to `yoinks.video_id`; cascade deletes with the yoink row. |
| `kind` | TEXT | required | `transcript_chunk` or `screenshot`. |
| `seq` | INTEGER | required | Order within the video for that citation kind. |
| `timestamp_start` | REAL | nullable | Start time in seconds. |
| `timestamp_end` | REAL | nullable | End time in seconds for transcript chunks. |
| `text` | TEXT | nullable | Transcript text for transcript citations. |
| `file_path` | TEXT | nullable | Absolute screenshot path for screenshot citations. |
| `youtube_deep_link` | TEXT | required | YouTube URL with `t=<seconds>s`. |

Uniqueness:

- `idx_citations_unique` enforces one row per `(video_id, kind, seq)`.
- Re-yoinking regenerates citations with `INSERT OR REPLACE`, so rows update in place instead of duplicating.

MCP surfaces:

- `get_citation_map(slug)` returns transcript citations and screenshot citations separately.
- `get_yoink_corpus(slug)` also includes the raw citations list as an additive optional field.

### Entity graph (Sprint 16, migration 0002)

Sprint 16 adds a minimal entity graph for agent lookup. Entity extraction runs on new yoinks when the user has a configured Anthropic API key and entity extraction is enabled through the AI-feature path. Existing yoinks are not retroactively backfilled in Sprint 16; re-yoinking an older video is the way to populate entity rows for it.

Sentiment is intentionally omitted in this version. Mention sentiment, temporal trends, co-occurrence, cross-creator citation graph, and user-correctable disambiguation are Sprint 16.5 / Sprint 17.5 follow-ups.

Disambiguation is automatic in Sprint 16: names cluster by `name_normalized` plus `type`. There is no user-correctable override yet. That means "Claude" the AI tool and "Claude" the person can collide unless the extractor assigns different types.

Sidecar status:

- Per-video sidecars include `entity_extraction_status`.
- Expected values are `completed`, `failed`, or `skipped`.
- `skipped` is used when no Anthropic API key is available or entity extraction is otherwise not enabled.
- A failed API call or parse failure should mark the sidecar `failed` and log a short reason without leaking keys.

#### `entities`

Canonical entity table. One row represents an entity name/type pair.

```sql
CREATE TABLE entities (
    entity_id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    name_normalized TEXT NOT NULL,
    type TEXT NOT NULL,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    mention_count INTEGER DEFAULT 0,
    UNIQUE (name_normalized, type)
);
```

| Column | Type | Nullability | Stores |
|---|---|---|---|
| `entity_id` | INTEGER PRIMARY KEY AUTOINCREMENT | required | Internal entity ID. |
| `name` | TEXT | required | Display name returned by extraction, preserving readable casing. |
| `name_normalized` | TEXT | required | Lowercased, punctuation/whitespace-stripped matching key. |
| `type` | TEXT | required | Python-enforced type: `person`, `tool`, `product`, `topic`, `company`, or `other`. |
| `first_seen` | TEXT | required | First timestamp this entity was inserted into the index. |
| `last_seen` | TEXT | required | Most recent timestamp this entity was mentioned. |
| `mention_count` | INTEGER | optional default `0` | Aggregate mention count across indexed yoinks. |

Uniqueness:

- `UNIQUE (name_normalized, type)` dedupes entities across videos.
- The backend can `INSERT OR IGNORE`, then `SELECT` by `(name_normalized, type)` to get the canonical `entity_id`.
- There is intentionally no SQL `CHECK` constraint on `type`; Python enforces the allowed values.

#### `entity_mentions`

Mention table linking entities to videos and timestamped context.

```sql
CREATE TABLE entity_mentions (
    mention_id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id INTEGER NOT NULL,
    video_id TEXT NOT NULL,
    source TEXT NOT NULL,
    timestamp REAL,
    context TEXT,
    FOREIGN KEY (entity_id) REFERENCES entities(entity_id) ON DELETE CASCADE,
    FOREIGN KEY (video_id) REFERENCES yoinks(video_id) ON DELETE CASCADE
);
```

| Column | Type | Nullability | Stores |
|---|---|---|---|
| `mention_id` | INTEGER PRIMARY KEY AUTOINCREMENT | required | Internal mention ID. |
| `entity_id` | INTEGER | required | Foreign key to `entities.entity_id`. |
| `video_id` | TEXT | required | Foreign key to `yoinks.video_id`. |
| `source` | TEXT | required | Source surface for the mention, initially `transcript`. |
| `timestamp` | REAL | nullable | Mention timestamp in seconds, when the extractor can tie it to transcript time. |
| `context` | TEXT | nullable | Short local context string around the mention. |

Query behavior:

- MCP `find_mentions(entity, limit)` normalizes the input name, joins `entities`, `entity_mentions`, and `yoinks`, and returns newest mention rows.
- Results include `video_id`, `slug`, `title`, `channel`, `source`, `timestamp`, `context`, and a YouTube `deep_link`.
- Sprint 16 orders mentions by `entity_mentions.mention_id DESC`, which approximates newest indexed mentions first.
- Lazy backfill: rows exist only for yoinks processed after Sprint 16 entity extraction lands.

## Indexes

| Index | Table | Supports |
|---|---|---|
| `idx_yoinks_yoinked_at` | `yoinks(yoinked_at DESC)` | Recent library queries. |
| `idx_yoinks_channel` | `yoinks(channel)` | Channel-filtered search or future channel views. |
| `idx_yoinks_hook_type` | `yoinks(hook_type)` | Hook Type filters and future taxonomy views. |
| `idx_jobs_updated_at` | `jobs(updated_at DESC)` | Newest-first `/jobs` recovery and popup state. |
| `idx_jobs_status` | `jobs(status)` | Terminal-job retention and future status filters. |
| `idx_taxonomy_hook_type` | `taxonomy(hook_type)` | Hook Type taxonomy filters. |
| `idx_citations_video_id` | `citations(video_id, seq)` | Citation-map lookup for one video. |
| `idx_citations_unique` | `citations(video_id, kind, seq)` | Idempotent citation regeneration. |
| `idx_entities_normalized` | `entities(name_normalized)` | `find_mentions` lookup by normalized entity name. |
| `idx_entities_type` | `entities(type)` | Future filters by entity type. |
| `idx_entity_mentions_entity` | `entity_mentions(entity_id)` | Mentions lookup for one entity. |
| `idx_entity_mentions_video` | `entity_mentions(video_id)` | Cleanup/join lookup for one video. |

The FTS5 table maintains its own search index internally.

## Migration From v2.0 File-Based State

Sprint 15 folds existing file-based state into `index.db` on helper startup.

### `jobs.json`

Legacy path: `%LOCALAPPDATA%\Yoink\jobs.json`

Migration behavior:

1. If `jobs.json` exists, Yoink parses it.
2. Each valid job object is mapped into the `jobs` table.
3. `combined_md_text` and `corpus_md_paste` are dropped during mapping.
4. The source file is renamed to `jobs.json.migrated`.
5. If parsing or import fails, the source file is left in place and the helper continues booting.

After migration, job persistence writes to `index.db`, not `jobs.json`.

### `taxonomy.json`

Legacy path: `%LOCALAPPDATA%\Yoink\taxonomy.json`

Migration behavior:

1. If `taxonomy.json` exists, Yoink parses it as a JSON array.
2. Rows with a non-empty `video_id` are inserted into `taxonomy`.
3. Existing rows dedupe by `video_id`.
4. The source file is renamed to `taxonomy.json.migrated`.
5. If parsing or import fails, the source file is left in place and the helper continues booting.

After migration, taxonomy capture writes to `index.db`, not `taxonomy.json`.

## Corruption Recovery

`index.Index.open_or_recover(path)` handles corrupt or unreadable SQLite files.

Recovery behavior:

1. Open `index.db` and run migrations.
2. If SQLite raises a database error, quarantine the file as `index.db.corrupt-<timestamp>`.
3. Delete any stale `index.db-wal` and `index.db-shm` siblings.
4. Create a fresh database and rerun migrations.
5. Set the process-level recovery flag so `/health` reports `index_recovering: true`.
6. Start the backfill scan.
7. Clear `index_recovering` when backfill finishes.

If quarantine fails, Yoink attempts to delete the corrupt file and still starts fresh. Recovery should not prevent the helper from binding or answering `/health`.

## Backfill

The backfill scanner indexes existing on-disk corpora into `index.db`.

Backfill triggers:

- First helper boot after `index.db` is created.
- Helper boot after `index.db` corruption recovery.
- Any boot where existing corpora are missing from the index.

Backfill behavior:

- Runs in a background daemon thread named `index-backfill`.
- Scans the Yoink output root recursively for folders with a resolvable corpus markdown file, including per-video session folders when they have their own corpus.
- Skips non-corpus folders through the corpus resolver.
- Reads each folder's `<slug>.json` sidecar when available.
- Skips folders with no `video_id`, because `video_id` is the primary key.
- Skips already-indexed `video_id`s for idempotency.
- Upserts the `yoinks` row, refreshes the FTS row, writes citation rows, and stores health JSON.
- Logs individual failures and continues.

Progress endpoint:

```http
GET /index/backfill-status
```

Response shape:

```json
{ "ok": true, "state": "running", "current": 47, "total": 200 }
```

Cancel endpoint:

```http
POST /index/backfill-cancel
X-Yoink-Token: <token>
```

Response shape:

```json
{ "ok": true, "cancelled": true }
```

States:

- `idle`: helper has not started a backfill during this process.
- `running`: scan is active.
- `complete`: scan finished or was cancelled.

Backfill is idempotent. Cancelling a scan leaves already-indexed rows in place; the next helper boot or future scan can pick up missing corpora.
