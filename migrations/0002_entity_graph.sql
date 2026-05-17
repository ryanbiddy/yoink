-- Yoink library index -- entity graph (v2, Sprint 16 / A2 minimal).
-- Applied by index._run_migrations() on top of 0001. Adds the entities +
-- entity_mentions tables that back the entity extraction worker and the
-- find_mentions MCP tool. Sentiment / temporal / co-occurrence columns are
-- intentionally omitted -- those ride Sprint 16.5.

CREATE TABLE entities (
    entity_id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    name_normalized TEXT NOT NULL,
    type TEXT NOT NULL,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    mention_count INTEGER DEFAULT 0,
    -- (name_normalized, type) is the dedup key: INSERT OR IGNORE folds a
    -- repeated entity into the existing row. `type` is one of person / tool
    -- / product / topic / company / other, enforced in Python (no CHECK).
    UNIQUE (name_normalized, type)
);

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

CREATE INDEX idx_entities_normalized ON entities(name_normalized);
CREATE INDEX idx_entities_type ON entities(type);
CREATE INDEX idx_entity_mentions_entity ON entity_mentions(entity_id);
CREATE INDEX idx_entity_mentions_video ON entity_mentions(video_id);
