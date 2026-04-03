"""SQLite schema + all CRUD.  Zero business logic lives here."""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from scm.models import Alert, Release, StoredArtifact, Verdict

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class DuplicateRelease(Exception):
    """Raised when (ecosystem, package, version) already exists in releases."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _current_month() -> str:
    """Return 'YYYY-MM' for the current UTC month.  Extracted for easy mocking."""
    return datetime.now(timezone.utc).strftime("%Y-%m")


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_DDL = """
CREATE TABLE IF NOT EXISTS collector_state (
    ecosystem   TEXT PRIMARY KEY,
    state_json  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS releases (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    ecosystem        TEXT    NOT NULL,
    package          TEXT    NOT NULL,
    version          TEXT    NOT NULL,
    previous_version TEXT,
    rank             INTEGER NOT NULL,
    discovered_at    TEXT    NOT NULL,
    metadata_json    TEXT,  -- Registry metadata (release_date, author, license, etc.)
    UNIQUE(ecosystem, package, version)
);

CREATE TABLE IF NOT EXISTS artifacts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    release_id  INTEGER NOT NULL REFERENCES releases(id),
    role        TEXT    NOT NULL CHECK(role IN ('old', 'new')),
    filename    TEXT    NOT NULL,
    path        TEXT    NOT NULL,
    sha256      TEXT    NOT NULL,
    size_bytes  INTEGER NOT NULL,
    UNIQUE(release_id, role)
);

CREATE TABLE IF NOT EXISTS verdicts (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    release_id        INTEGER NOT NULL REFERENCES releases(id),
    result            TEXT    NOT NULL,
    confidence        TEXT    NOT NULL DEFAULT 'unknown',
    summary           TEXT    NOT NULL DEFAULT '',
    analysis          TEXT    NOT NULL,
    analyzed_at       TEXT    NOT NULL,
    opencode_log_path TEXT    DEFAULT NULL
);

CREATE TABLE IF NOT EXISTS alerts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    verdict_id INTEGER NOT NULL REFERENCES verdicts(id),
    notifier   TEXT    NOT NULL,
    sent_at    TEXT    NOT NULL,
    success    INTEGER NOT NULL DEFAULT 0,
    detail     TEXT    NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS tweet_budget (
    id    INTEGER PRIMARY KEY CHECK (id = 1),
    month TEXT    NOT NULL,
    count INTEGER NOT NULL DEFAULT 0
);
"""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def init_db(path: Path) -> sqlite3.Connection:
    """Open (or create) the SQLite database, apply schema, return connection."""
    log.info("opening database at %s", path)
    conn = sqlite3.connect(
        str(path),
        check_same_thread=False,
        isolation_level=None,  # autocommit
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    for statement in _DDL.strip().split(";"):
        s = statement.strip()
        if s:
            conn.execute(s)
    # Non-destructive migration: add opencode_log_path if it doesn't exist yet
    try:
        conn.execute("ALTER TABLE verdicts ADD COLUMN opencode_log_path TEXT")
    except sqlite3.OperationalError:
        pass  # column already exists
    # Non-destructive migration: add metadata_json to releases if it doesn't exist yet
    try:
        conn.execute("ALTER TABLE releases ADD COLUMN metadata_json TEXT")
    except sqlite3.OperationalError:
        pass  # column already exists
    # Non-destructive migration: add UNIQUE(release_id, role) to artifacts if
    # not present.  SQLite does not support ADD CONSTRAINT, so we do this via
    # a CREATE UNIQUE INDEX which is a no-op if the index already exists.
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_artifacts_release_role"
        " ON artifacts(release_id, role)"
    )
    log.debug("schema initialised")
    return conn


def upsert_release(conn: sqlite3.Connection, release: Release) -> int:
    """Insert a new release row.  Raises DuplicateRelease if already present."""
    try:
        cur = conn.execute(
            """
            INSERT INTO releases
                (ecosystem, package, version, previous_version, rank, discovered_at, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                release.ecosystem,
                release.package,
                release.version,
                release.previous_version,
                release.rank,
                release.discovered_at.isoformat(),
                json.dumps(release.metadata) if release.metadata else None,
            ),
        )
        release_id: int = cur.lastrowid  # type: ignore[assignment]
        log.debug(
            "inserted release id=%s  %s/%s@%s",
            release_id,
            release.ecosystem,
            release.package,
            release.version,
        )
        return release_id
    except sqlite3.IntegrityError as exc:
        raise DuplicateRelease(
            f"{release.ecosystem}/{release.package}@{release.version} already recorded"
        ) from exc


def get_release_id(
    conn: sqlite3.Connection, ecosystem: str, package: str, version: str
) -> int | None:
    """Return the id of an existing release row, or None if not found."""
    row = conn.execute(
        "SELECT id FROM releases WHERE ecosystem = ? AND package = ? AND version = ?",
        (ecosystem, package, version),
    ).fetchone()
    return int(row["id"]) if row else None


def save_artifacts(
    conn: sqlite3.Connection,
    release_id: int,
    old: StoredArtifact | None,
    new: StoredArtifact,
) -> None:
    """Persist artifact rows for a release.

    Uses INSERT OR IGNORE so that rescans are idempotent.
    The old artifact is skipped when None (e.g. force-scans with no previous version).
    """
    for role, art in (("old", old), ("new", new)):
        if art is None:
            continue
        conn.execute(
            """
            INSERT OR IGNORE INTO artifacts (release_id, role, filename, path, sha256, size_bytes)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (release_id, role, art.filename, str(art.path), art.sha256, art.size_bytes),
        )
    log.debug("saved artifacts for release_id=%s", release_id)


def save_verdict(conn: sqlite3.Connection, release_id: int, verdict: Verdict) -> int:
    """Persist a verdict row, return its id."""
    cur = conn.execute(
        """
        INSERT INTO verdicts
            (release_id, result, confidence, summary, analysis,
             analyzed_at, opencode_log_path)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            release_id,
            verdict.result,
            verdict.confidence,
            verdict.summary,
            verdict.analysis,
            verdict.analyzed_at.isoformat(),
            verdict.opencode_log_path,
        ),
    )
    verdict_id: int = cur.lastrowid  # type: ignore[assignment]
    log.debug("saved verdict id=%s  result=%s", verdict_id, verdict.result)
    return verdict_id


def save_alert(conn: sqlite3.Connection, verdict_id: int, alert: Alert) -> None:
    """Persist a notification alert row."""
    conn.execute(
        """
        INSERT INTO alerts (verdict_id, notifier, sent_at, success, detail)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            verdict_id,
            alert.notifier,
            alert.sent_at.isoformat(),
            int(alert.success),
            alert.detail,
        ),
    )
    log.debug("saved alert  notifier=%s  success=%s", alert.notifier, alert.success)


def get_collector_state(conn: sqlite3.Connection, ecosystem: str) -> dict:
    """Return the persisted state dict, or {} if not yet set."""
    row = conn.execute(
        "SELECT state_json FROM collector_state WHERE ecosystem = ?", (ecosystem,)
    ).fetchone()
    return json.loads(row["state_json"]) if row else {}


def set_collector_state(conn: sqlite3.Connection, ecosystem: str, state: dict) -> None:
    """Upsert the state dict for an ecosystem."""
    conn.execute(
        """
        INSERT INTO collector_state (ecosystem, state_json, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(ecosystem) DO UPDATE SET
            state_json = excluded.state_json,
            updated_at = excluded.updated_at
        """,
        (ecosystem, json.dumps(state), datetime.now(timezone.utc).isoformat()),
    )


def increment_tweet_count(conn: sqlite3.Connection) -> int:
    """Increment this month's tweet counter; resets to 1 on a new month.  Returns new count."""
    current = _current_month()
    row = conn.execute("SELECT month, count FROM tweet_budget WHERE id = 1").fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO tweet_budget (id, month, count) VALUES (1, ?, 1)", (current,)
        )
        return 1
    if row["month"] != current:
        conn.execute(
            "UPDATE tweet_budget SET month = ?, count = 1 WHERE id = 1", (current,)
        )
        return 1
    new_count = row["count"] + 1
    conn.execute("UPDATE tweet_budget SET count = ? WHERE id = 1", (new_count,))
    return new_count


def get_tweet_count(conn: sqlite3.Connection) -> int:
    """Return this month's tweet count, or 0 if no row exists."""
    row = conn.execute("SELECT month, count FROM tweet_budget WHERE id = 1").fetchone()
    if row is None:
        return 0
    current = _current_month()
    return row["count"] if row["month"] == current else 0


def get_recent_verdicts(conn: sqlite3.Connection, limit: int = 50) -> list[dict]:
    """Return the most recent verdicts joined with release + artifact + alert data."""
    rows = conn.execute(
        """
        SELECT
            r.package, r.version, r.ecosystem, r.rank,
            v.result, v.confidence, v.summary, v.analyzed_at,
            old_a.sha256  AS old_sha256,
            new_a.sha256  AS new_sha256,
            al.detail     AS report_path
        FROM verdicts v
        JOIN releases r ON r.id = v.release_id
        LEFT JOIN artifacts old_a
               ON old_a.release_id = v.release_id AND old_a.role = 'old'
        LEFT JOIN artifacts new_a
               ON new_a.release_id = v.release_id AND new_a.role = 'new'
        LEFT JOIN alerts al
               ON al.verdict_id = v.id AND al.notifier = 'local'
        ORDER BY v.analyzed_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]
