"""Yoink local library index -- SQLite + FTS5.

Replaces the O(n) full-disk-scan code paths (search_yoinks, list_recent, the
post-extraction `_all-yoinks-index.md` rebuild) with an incremental SQLite
index, and absorbs jobs.json / taxonomy.json into queryable tables.

The database lives at ``%LOCALAPPDATA%\\Yoink\\index.db``. Both ``sqlite3``
and the FTS5 extension ship in the Python standard library, so this adds no
new dependency.

This module is self-contained: it owns the schema, the migration runner, and
all query helpers. server.py and yoink_mcp_tools.py call into ``Index`` and
never touch the database directly.

Schema / migrations: see the ``migrations/`` directory. ``_run_migrations``
applies any pending ``NNNN_*.sql`` file in numeric order and is idempotent.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from pathlib import Path

log = logging.getLogger("yoink.index")

_MIGRATIONS_DIR = Path(__file__).parent.resolve() / "migrations"

# Columns of the `yoinks` table, in declaration order. video_id is the
# primary key and is handled separately in the upsert.
_YOINK_COLUMNS = (
    "video_id", "slug", "channel", "title", "topic", "hook_type",
    "yoinked_at", "corpus_path", "sidecar_path", "health_score_json",
    "metadata_json",
)

_JOB_COLUMNS = (
    "job_id", "kind", "status", "slug", "title", "error",
    "started_at", "updated_at", "metadata_json",
)

_TERMINAL_JOB_STATES = ("completed", "failed", "cancelled")


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())


# --------------------------------------------------------------------------
# Migration framework
# --------------------------------------------------------------------------
def _discover_migrations() -> list[tuple[int, Path]]:
    """Return (version, path) for every migrations/NNNN_*.sql file, sorted by
    version ascending."""
    out: list[tuple[int, Path]] = []
    if not _MIGRATIONS_DIR.is_dir():
        return out
    for path in _MIGRATIONS_DIR.glob("*.sql"):
        stem = path.name.split("_", 1)[0]
        try:
            version = int(stem)
        except ValueError:
            log.warning("ignoring migration with non-numeric prefix: %s", path.name)
            continue
        out.append((version, path))
    out.sort(key=lambda item: item[0])
    return out


def _current_schema_version(conn: sqlite3.Connection) -> int:
    """Return the highest applied schema version, or 0 on a fresh database."""
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_version'"
    ).fetchone()
    if row is None:
        return 0
    row = conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
    return int(row["v"]) if row and row["v"] is not None else 0


def _run_migrations(conn: sqlite3.Connection) -> int:
    """Apply every pending migration in numeric order. Idempotent: re-running
    against an up-to-date database is a no-op. Returns the resulting version.

    Each migration file is applied as one script followed by a
    schema_version row insert and a commit, so a successfully applied
    migration is never re-run."""
    current = _current_schema_version(conn)
    applied = current
    for version, path in _discover_migrations():
        if version <= current:
            continue
        log.info("applying index migration %04d (%s)", version, path.name)
        sql = path.read_text(encoding="utf-8")
        try:
            conn.executescript(sql)
            conn.execute(
                "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
                (version, _now_iso()),
            )
            conn.commit()
        except sqlite3.Error:
            conn.rollback()
            log.exception("index migration %04d failed", version)
            raise
        applied = version
    return applied


# --------------------------------------------------------------------------
# FTS query sanitisation
# --------------------------------------------------------------------------
import re as _re

_FTS_TERM_RE = _re.compile(r"[A-Za-z0-9_]+")


def _fts_query(raw: str) -> str:
    """Turn an arbitrary user string into a safe FTS5 MATCH expression.

    FTS5 MATCH has its own grammar (quotes, NEAR, column filters, ``*``),
    and a raw user string can be a syntax error. We extract bare word
    tokens, quote each one, and AND them together. A trailing ``*`` is kept
    on the last token for prefix matching so partial words still hit."""
    terms = _FTS_TERM_RE.findall(raw or "")
    if not terms:
        return ""
    quoted = [f'"{t}"' for t in terms]
    # Prefix-match the final term so "hook" matches "hooks".
    quoted[-1] = quoted[-1][:-1] + '"*'
    return " ".join(quoted)


# --------------------------------------------------------------------------
# Entity graph (Sprint 16)
# --------------------------------------------------------------------------
# Allowed entity `type` values. Constrained here, not by a SQL CHECK: an
# unknown type from the extraction worker is folded to 'other', not rejected.
ENTITY_TYPES = ("person", "tool", "product", "topic", "company", "other")


def normalize_entity_name(name: str) -> str:
    """The matching key for the entities table: the name lowercased with all
    punctuation and whitespace removed. 'GPT-4o' -> 'gpt4o',
    'New York' -> 'newyork'. str.isalnum() is unicode-aware, so accented
    letters survive while spaces and punctuation drop out."""
    return "".join(ch for ch in str(name or "").lower() if ch.isalnum())


def _entity_deep_link(video_id: str, seconds) -> str:
    """A timestamped watch URL for an entity mention. Mirrors server.py's
    _youtube_deep_link; duplicated here so index.py stays self-contained."""
    vid = (video_id or "").strip()
    try:
        t = max(0, int(float(seconds)))
    except (TypeError, ValueError):
        t = 0
    return f"https://youtube.com/watch?v={vid}&t={t}s"


# --------------------------------------------------------------------------
# Index
# --------------------------------------------------------------------------
class Index:
    """Connection wrapper around index.db. Thread-safe: every public method
    serialises through a single re-entrant lock, which keeps SQLite write
    semantics simple for the helper's many worker threads.

    Open with ``Index.open(path)`` (runs migrations) or
    ``Index.open_or_recover(path)`` (also handles a corrupt file). The
    instance is usable as a context manager."""

    def __init__(self, conn: sqlite3.Connection, path: Path):
        self._conn = conn
        self._path = path
        self._lock = threading.RLock()
        self._insert_count = 0

    # ---- lifecycle -------------------------------------------------------
    @classmethod
    def open(cls, path) -> "Index":
        """Open (creating if needed) the index database and run migrations."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path), check_same_thread=False)
        # sqlite3.connect() is lazy -- a corrupt file only errors on the
        # first real operation below. If anything fails, close the
        # connection so the file handle is released and open_or_recover()
        # can rename the corrupt file aside.
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            _run_migrations(conn)
        except Exception:
            conn.close()
            raise
        return cls(conn, path)

    @classmethod
    def open_or_recover(cls, path) -> tuple["Index", bool]:
        """Like ``open``, but if the file is a corrupt SQLite database, rename
        it aside (``index.db.corrupt-<ts>``) and start fresh.

        Returns ``(index, recovered)`` where ``recovered`` is True if the old
        file had to be quarantined -- the caller should then trigger a full
        backfill scan and surface ``index_recovering`` in /health."""
        path = Path(path)
        try:
            return cls.open(path), False
        except sqlite3.DatabaseError:
            log.error("index.db is corrupt or unreadable -- quarantining and "
                      "rebuilding from disk")
            if path.exists():
                quarantine = path.with_name(
                    f"{path.name}.corrupt-{time.strftime('%Y%m%d-%H%M%S')}"
                )
                try:
                    path.replace(quarantine)
                    log.error("corrupt index quarantined at %s", quarantine)
                except OSError:
                    log.exception("could not rename corrupt index; deleting")
                    try:
                        path.unlink(missing_ok=True)
                    except OSError:
                        log.exception("could not delete corrupt index either")
            # WAL/shm siblings of the corrupt file would poison the new DB.
            for suffix in ("-wal", "-shm"):
                sibling = path.with_name(path.name + suffix)
                try:
                    sibling.unlink(missing_ok=True)
                except OSError:
                    pass
            return cls.open(path), True

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __enter__(self) -> "Index":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ---- yoinks ----------------------------------------------------------
    def upsert_yoink(self, record: dict, *, content: str = "") -> None:
        """Insert or update one yoink row, and refresh its FTS5 entry.

        ``record`` carries the ``yoinks`` columns. ``content`` is the corpus
        text indexed for full-text search (not stored in the yoinks table)."""
        video_id = record.get("video_id")
        if not video_id:
            raise ValueError("upsert_yoink: record requires a video_id")
        values = [record.get(col) for col in _YOINK_COLUMNS]
        placeholders = ", ".join("?" * len(_YOINK_COLUMNS))
        update_set = ", ".join(
            f"{col}=excluded.{col}" for col in _YOINK_COLUMNS if col != "video_id"
        )
        with self._lock:
            self._conn.execute(
                f"INSERT INTO yoinks ({', '.join(_YOINK_COLUMNS)}) "
                f"VALUES ({placeholders}) "
                f"ON CONFLICT(video_id) DO UPDATE SET {update_set}",
                values,
            )
            # FTS5 has no UPSERT; delete-then-insert keeps it in sync.
            self._conn.execute("DELETE FROM yoinks_fts WHERE video_id=?", (video_id,))
            self._conn.execute(
                "INSERT INTO yoinks_fts "
                "(video_id, slug, channel, title, topic, hook_type, content) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (video_id, record.get("slug"), record.get("channel"),
                 record.get("title"), record.get("topic"),
                 record.get("hook_type"), content or ""),
            )
            self._conn.commit()

    def delete_yoink(self, video_id: str) -> None:
        """Delete a yoink and its citations (FK cascade) and FTS row."""
        with self._lock:
            self._conn.execute("DELETE FROM yoinks WHERE video_id=?", (video_id,))
            self._conn.execute("DELETE FROM yoinks_fts WHERE video_id=?", (video_id,))
            self._conn.commit()

    def get_yoink(self, video_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM yoinks WHERE video_id=?", (video_id,)
            ).fetchone()
        return dict(row) if row else None

    def has_yoink(self, video_id: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM yoinks WHERE video_id=?", (video_id,)
            ).fetchone()
        return row is not None

    def has_slug(self, slug: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM yoinks WHERE slug=?", (slug,)
            ).fetchone()
        return row is not None

    def all_video_ids(self) -> set[str]:
        with self._lock:
            rows = self._conn.execute("SELECT video_id FROM yoinks").fetchall()
        return {r["video_id"] for r in rows}

    def search(self, query: str, limit: int = 10, *,
               channel: str | None = None,
               hook_type: str | None = None) -> list[dict]:
        """Full-text search across indexed corpora. Returns yoink rows ranked
        by FTS5 bm25 (best first), optionally filtered by channel/hook_type."""
        match = _fts_query(query)
        if not match:
            return []
        # snippet() column index 6 == the `content` column of yoinks_fts
        # (0:video_id 1:slug 2:channel 3:title 4:topic 5:hook_type 6:content).
        # Each result row carries `_snippet` (a match excerpt) and `_score`
        # (bm25; lower is a better match) alongside the yoinks columns.
        sql = ("SELECT y.*, "
               "snippet(yoinks_fts, 6, '', '', '…', 12) AS _snippet, "
               "bm25(yoinks_fts) AS _score "
               "FROM yoinks_fts f "
               "JOIN yoinks y ON y.video_id = f.video_id "
               "WHERE yoinks_fts MATCH ? ")
        params: list = [match]
        if channel:
            sql += "AND y.channel = ? "
            params.append(channel)
        if hook_type:
            sql += "AND y.hook_type = ? "
            params.append(hook_type)
        sql += "ORDER BY bm25(yoinks_fts) LIMIT ?"
        params.append(max(1, int(limit)))
        with self._lock:
            try:
                rows = self._conn.execute(sql, params).fetchall()
            except sqlite3.OperationalError:
                # Defensive: a MATCH expression FTS5 still rejects.
                log.warning("FTS search rejected query %r", query)
                return []
        return [dict(r) for r in rows]

    def list_recent(self, limit: int = 20) -> list[dict]:
        """Most-recently-yoinked rows, newest first."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM yoinks ORDER BY yoinked_at DESC LIMIT ?",
                (max(1, int(limit)),),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_health(self, video_id: str) -> dict | None:
        """Return the parsed health-score dict for a video, or None."""
        with self._lock:
            row = self._conn.execute(
                "SELECT health_score_json FROM yoinks WHERE video_id=?",
                (video_id,),
            ).fetchone()
        if not row or not row["health_score_json"]:
            return None
        try:
            return json.loads(row["health_score_json"])
        except (json.JSONDecodeError, TypeError):
            return None

    # ---- citations -------------------------------------------------------
    def insert_citations(self, video_id: str, citations: list[dict]) -> int:
        """Bulk insert citation rows. Idempotent per (video_id, kind, seq):
        re-yoinking a video rewrites its rows via INSERT OR REPLACE. Returns
        the number of rows written."""
        rows = [
            (video_id, c.get("kind"), c.get("seq"),
             c.get("timestamp_start"), c.get("timestamp_end"),
             c.get("text"), c.get("file_path"), c.get("youtube_deep_link"))
            for c in citations
        ]
        if not rows:
            return 0
        with self._lock:
            self._conn.executemany(
                "INSERT OR REPLACE INTO citations "
                "(video_id, kind, seq, timestamp_start, timestamp_end, "
                " text, file_path, youtube_deep_link) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
            self._conn.commit()
        return len(rows)

    def get_citations(self, video_id: str) -> list[dict]:
        """All citations for a video, ordered by kind then seq."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM citations WHERE video_id=? ORDER BY kind, seq",
                (video_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    # ---- jobs ------------------------------------------------------------
    def upsert_job(self, record: dict) -> None:
        """Insert or update one job row. ``metadata_json`` must already be a
        JSON string and must NOT contain combined_md_text / corpus payloads."""
        job_id = record.get("job_id")
        if not job_id:
            raise ValueError("upsert_job: record requires a job_id")
        record = dict(record)
        record.setdefault("updated_at", _now_iso())
        values = [record.get(col) for col in _JOB_COLUMNS]
        placeholders = ", ".join("?" * len(_JOB_COLUMNS))
        update_set = ", ".join(
            f"{col}=excluded.{col}" for col in _JOB_COLUMNS if col != "job_id"
        )
        with self._lock:
            self._conn.execute(
                f"INSERT INTO jobs ({', '.join(_JOB_COLUMNS)}) "
                f"VALUES ({placeholders}) "
                f"ON CONFLICT(job_id) DO UPDATE SET {update_set}",
                values,
            )
            self._conn.commit()
            self._insert_count += 1
        # Opportunistic retention: prune terminal jobs every 50 writes.
        if self._insert_count % 50 == 0:
            self.prune_jobs()

    def get_job(self, job_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM jobs WHERE job_id=?", (job_id,)
            ).fetchone()
        return dict(row) if row else None

    def list_jobs(self, *, kind: str | None = None,
                  limit: int = 100) -> list[dict]:
        """Jobs newest-first, optionally filtered by kind."""
        sql = "SELECT * FROM jobs "
        params: list = []
        if kind:
            sql += "WHERE kind=? "
            params.append(kind)
        sql += "ORDER BY updated_at DESC LIMIT ?"
        params.append(max(1, int(limit)))
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def prune_jobs(self, keep_terminal: int = 200) -> int:
        """Keep at most ``keep_terminal`` most-recent terminal jobs; non-
        terminal jobs (pending/running) are always retained. Returns the
        number of rows deleted."""
        placeholders = ", ".join("?" * len(_TERMINAL_JOB_STATES))
        with self._lock:
            cur = self._conn.execute(
                f"DELETE FROM jobs WHERE status IN ({placeholders}) "
                f"AND job_id NOT IN ("
                f"  SELECT job_id FROM jobs WHERE status IN ({placeholders}) "
                f"  ORDER BY updated_at DESC LIMIT ?"
                f")",
                (*_TERMINAL_JOB_STATES, *_TERMINAL_JOB_STATES, keep_terminal),
            )
            self._conn.commit()
            return cur.rowcount

    # ---- taxonomy --------------------------------------------------------
    def upsert_taxonomy(self, record: dict) -> None:
        """Insert or replace one taxonomy row, deduplicated by video_id."""
        video_id = record.get("video_id")
        if not video_id:
            raise ValueError("upsert_taxonomy: record requires a video_id")
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO taxonomy "
                "(video_id, hook_type, hook_explanation, channel, title, "
                " classified_at) VALUES (?, ?, ?, ?, ?, ?)",
                (video_id, record.get("hook_type"),
                 record.get("hook_explanation"), record.get("channel"),
                 record.get("title"),
                 record.get("classified_at") or _now_iso()),
            )
            self._conn.commit()

    def query_taxonomy(self, *, channel: str | None = None,
                       hook_type: str | None = None,
                       limit: int = 50) -> list[dict]:
        """Taxonomy rows, newest classification first, optional filters."""
        sql = "SELECT * FROM taxonomy "
        clauses: list[str] = []
        params: list = []
        if channel:
            # Case-insensitive to match the pre-index taxonomy query.
            clauses.append("channel = ? COLLATE NOCASE")
            params.append(channel)
        if hook_type:
            clauses.append("hook_type = ?")
            params.append(hook_type)
        if clauses:
            sql += "WHERE " + " AND ".join(clauses) + " "
        sql += "ORDER BY classified_at DESC LIMIT ?"
        params.append(max(1, int(limit)))
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    # ---- entities (Sprint 16) -------------------------------------------
    def record_entities(self, video_id: str, entities: list[dict], *,
                         source: str = "transcript") -> int:
        """Write one extraction's worth of entities + mentions for a video.

        Idempotent per video: a re-yoink first drops the video's previous
        entity_mentions (and rolls their mention_count back) so re-running
        the extraction worker never double-counts. Each entity is deduped on
        (name_normalized, type) via INSERT OR IGNORE; an unrecognised type
        folds to 'other'. The whole read-modify-write is one transaction.

        ``entities`` is the worker's parsed list of
        ``{name, type, mentions: [{timestamp, context}]}`` dicts. Returns the
        number of mention rows written."""
        if not video_id:
            raise ValueError("record_entities: video_id is required")
        now = _now_iso()
        written = 0
        with self._lock:
            try:
                # Idempotent re-yoink: clear this video's prior mentions and
                # decrement the affected entities' denormalised counters.
                prior = self._conn.execute(
                    "SELECT entity_id, COUNT(*) AS n FROM entity_mentions "
                    "WHERE video_id=? GROUP BY entity_id", (video_id,)
                ).fetchall()
                if prior:
                    self._conn.execute(
                        "DELETE FROM entity_mentions WHERE video_id=?", (video_id,)
                    )
                    for r in prior:
                        self._conn.execute(
                            "UPDATE entities "
                            "SET mention_count = MAX(0, mention_count - ?) "
                            "WHERE entity_id=?", (r["n"], r["entity_id"])
                        )
                for ent in entities or []:
                    if not isinstance(ent, dict):
                        continue
                    name = str(ent.get("name") or "").strip()
                    norm = normalize_entity_name(name)
                    if not name or not norm:
                        continue
                    etype = str(ent.get("type") or "other").strip().lower()
                    if etype not in ENTITY_TYPES:
                        etype = "other"
                    self._conn.execute(
                        "INSERT OR IGNORE INTO entities "
                        "(name, name_normalized, type, first_seen, last_seen, "
                        " mention_count) VALUES (?, ?, ?, ?, ?, 0)",
                        (name, norm, etype, now, now),
                    )
                    row = self._conn.execute(
                        "SELECT entity_id FROM entities "
                        "WHERE name_normalized=? AND type=?", (norm, etype)
                    ).fetchone()
                    if row is None:
                        continue
                    entity_id = row["entity_id"]
                    added = 0
                    for m in ent.get("mentions") or []:
                        if not isinstance(m, dict):
                            continue
                        ts = m.get("timestamp")
                        try:
                            ts = float(ts) if ts is not None else None
                        except (TypeError, ValueError):
                            ts = None
                        ctx = m.get("context")
                        ctx = str(ctx)[:500] if ctx else None
                        self._conn.execute(
                            "INSERT INTO entity_mentions "
                            "(entity_id, video_id, source, timestamp, context) "
                            "VALUES (?, ?, ?, ?, ?)",
                            (entity_id, video_id, source, ts, ctx),
                        )
                        added += 1
                    if added:
                        self._conn.execute(
                            "UPDATE entities "
                            "SET mention_count = mention_count + ?, last_seen=? "
                            "WHERE entity_id=?", (added, now, entity_id)
                        )
                    written += added
                self._conn.commit()
            except sqlite3.Error:
                self._conn.rollback()
                raise
        return written

    def find_mentions(self, name: str, limit: int = 50) -> list[dict]:
        """Every recorded mention of an entity, newest first.

        Matches on the normalised name (case/punctuation-insensitive) across
        every type the name was tagged as, joining through to the yoink for
        its slug/title/channel. Each row carries a timestamped deep link.
        Returns [] for an unknown entity."""
        norm = normalize_entity_name(name)
        if not norm:
            return []
        sql = (
            "SELECT y.video_id AS video_id, y.slug AS slug, y.title AS title, "
            "       y.channel AS channel, em.source AS source, "
            "       em.timestamp AS timestamp, em.context AS context "
            "FROM entity_mentions em "
            "JOIN entities e ON e.entity_id = em.entity_id "
            "JOIN yoinks   y ON y.video_id  = em.video_id "
            "WHERE e.name_normalized = ? "
            "ORDER BY em.mention_id DESC LIMIT ?"
        )
        with self._lock:
            rows = self._conn.execute(sql, (norm, max(1, int(limit)))).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["deep_link"] = _entity_deep_link(d.get("video_id"), d.get("timestamp"))
            out.append(d)
        return out
