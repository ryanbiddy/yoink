-- Yoink library index -- initial schema (v1).
-- Applied by index._run_migrations(). Each migration file is named
-- NNNN_description.sql; the leading integer is its schema version.

CREATE TABLE schema_version (
    version    INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE TABLE yoinks (
    video_id          TEXT PRIMARY KEY,
    slug              TEXT NOT NULL UNIQUE,
    channel           TEXT,
    title             TEXT,
    topic             TEXT,
    hook_type         TEXT,
    yoinked_at        TEXT NOT NULL,
    corpus_path       TEXT NOT NULL,
    sidecar_path      TEXT NOT NULL,
    health_score_json TEXT,
    metadata_json     TEXT
);

-- Standalone FTS5 table (NOT external-content). The original spec used
-- content='yoinks', content_rowid='rowid', but the yoinks table has no
-- `content` column, so an external-content table referencing it would be
-- invalid. A standalone table stores its own text; `video_id` is UNINDEXED
-- so a MATCH result maps straight back to the yoinks row by primary key
-- with no rowid bookkeeping. See index.py upsert_yoink / search.
CREATE VIRTUAL TABLE yoinks_fts USING fts5(
    video_id UNINDEXED,
    slug,
    channel,
    title,
    topic,
    hook_type,
    content
);

CREATE TABLE jobs (
    job_id        TEXT PRIMARY KEY,
    kind          TEXT NOT NULL,            -- 'single' | 'playlist'
    status        TEXT NOT NULL,            -- pending|running|completed|failed|cancelled
    slug          TEXT,
    title         TEXT,
    error         TEXT,
    started_at    TEXT,
    updated_at    TEXT NOT NULL,
    metadata_json TEXT                      -- per-job payload; never combined_md_text
);

CREATE TABLE taxonomy (
    video_id         TEXT PRIMARY KEY,
    hook_type        TEXT NOT NULL,
    hook_explanation TEXT,
    channel          TEXT,
    title            TEXT,
    classified_at    TEXT NOT NULL
);

CREATE TABLE citations (
    citation_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id          TEXT NOT NULL,
    kind              TEXT NOT NULL,        -- 'transcript_chunk' | 'screenshot'
    seq               INTEGER NOT NULL,     -- order within the video
    timestamp_start   REAL,
    timestamp_end     REAL,
    text              TEXT,
    file_path         TEXT,
    youtube_deep_link TEXT NOT NULL,
    FOREIGN KEY (video_id) REFERENCES yoinks(video_id) ON DELETE CASCADE
);

CREATE INDEX idx_yoinks_yoinked_at ON yoinks(yoinked_at DESC);
CREATE INDEX idx_yoinks_channel    ON yoinks(channel);
CREATE INDEX idx_yoinks_hook_type  ON yoinks(hook_type);
CREATE INDEX idx_jobs_updated_at   ON jobs(updated_at DESC);
CREATE INDEX idx_jobs_status       ON jobs(status);
CREATE INDEX idx_taxonomy_hook_type ON taxonomy(hook_type);
CREATE INDEX idx_citations_video_id ON citations(video_id, seq);

-- Idempotent citation regeneration: a re-yoink rewrites the same
-- (video_id, kind, seq) rows via INSERT OR REPLACE rather than duplicating.
CREATE UNIQUE INDEX idx_citations_unique ON citations(video_id, kind, seq);
