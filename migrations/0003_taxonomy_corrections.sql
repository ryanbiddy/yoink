-- Yoink library index -- taxonomy corrections (v3, Sprint 17 / A3).
-- Applied by index._run_migrations() on top of 0002. Backs the
-- self-calibrating Hook Type classifier: user corrections are stored in
-- their own table (separation of concerns -- corrections become a labeled
-- dataset asset, BACKLOG v2.5), and the taxonomy table gains a confidence
-- score from the classifier.

CREATE TABLE taxonomy_corrections (
    correction_id INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id TEXT NOT NULL,
    original_hook_type TEXT NOT NULL,
    corrected_hook_type TEXT NOT NULL,
    user_reason TEXT,
    corrected_at TEXT NOT NULL,
    channel TEXT,                    -- denormalized for similarity matching
    topic TEXT,                      -- denormalized for similarity matching
    FOREIGN KEY (video_id) REFERENCES yoinks(video_id) ON DELETE CASCADE
);

CREATE INDEX idx_taxonomy_corrections_video ON taxonomy_corrections(video_id);
CREATE INDEX idx_taxonomy_corrections_channel ON taxonomy_corrections(channel);
CREATE INDEX idx_taxonomy_corrections_topic ON taxonomy_corrections(topic);
CREATE INDEX idx_taxonomy_corrections_corrected_at
    ON taxonomy_corrections(corrected_at DESC);

-- Classifier confidence (1-5). Nullable: pre-Sprint-17 taxonomy rows keep
-- NULL until the video is re-classified.
ALTER TABLE taxonomy ADD COLUMN confidence INTEGER;
