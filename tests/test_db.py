"""Tests for scm.db — schema, CRUD, tweet budget."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from scm.db import (
    DuplicateRelease,
    _current_month,
    get_collector_state,
    get_recent_verdicts,
    get_release_id,
    get_tweet_count,
    increment_tweet_count,
    init_db,
    save_alert,
    save_artifacts,
    save_verdict,
    set_collector_state,
    upsert_release,
)
from scm.models import Alert, Release, StoredArtifact, Verdict


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_release(**kwargs) -> Release:
    defaults = dict(
        ecosystem="npm",
        package="lodash",
        version="4.17.21",
        previous_version="4.17.20",
        rank=3,
        discovered_at=datetime.now(timezone.utc),
    )
    return Release(**{**defaults, **kwargs})


def _make_artifact(version: str = "4.17.21", role: str = "new") -> StoredArtifact:
    return StoredArtifact(
        ecosystem="npm",
        package="lodash",
        version=version,
        filename=f"lodash-{version}.tgz",
        path=Path(f"/tmp/lodash-{version}.tgz"),
        sha256="a" * 64,
        size_bytes=1024,
    )


def _make_verdict(release: Release) -> Verdict:
    return Verdict(
        release=release,
        old_artifact=_make_artifact("4.17.20"),
        new_artifact=_make_artifact("4.17.21"),
        result="benign",
        confidence="high",
        summary="clean version bump",
        analysis="no suspicious changes",
        analyzed_at=datetime.now(timezone.utc),
    )


# ---------------------------------------------------------------------------
# init_db
# ---------------------------------------------------------------------------


def test_init_db_creates_file(tmp_path):
    path = tmp_path / "test.db"
    conn = init_db(path)
    assert path.exists()
    conn.close()


def test_init_db_returns_connection(tmp_path):
    conn = init_db(tmp_path / "test.db")
    assert isinstance(conn, sqlite3.Connection)
    conn.close()


def test_init_db_idempotent(tmp_path):
    path = tmp_path / "test.db"
    c1 = init_db(path)
    c1.close()
    c2 = init_db(path)  # second call must not raise
    c2.close()


def test_init_db_wal_mode(db_conn):
    row = db_conn.execute("PRAGMA journal_mode").fetchone()
    assert row[0] == "wal"


def test_init_db_foreign_keys_on(db_conn):
    row = db_conn.execute("PRAGMA foreign_keys").fetchone()
    assert row[0] == 1


# ---------------------------------------------------------------------------
# upsert_release
# ---------------------------------------------------------------------------


def test_upsert_release_returns_id(db_conn):
    r = _make_release()
    rid = upsert_release(db_conn, r)
    assert isinstance(rid, int)
    assert rid >= 1


def test_upsert_release_duplicate_raises(db_conn):
    r = _make_release()
    upsert_release(db_conn, r)
    with pytest.raises(DuplicateRelease):
        upsert_release(db_conn, r)


def test_upsert_release_different_versions_ok(db_conn):
    r1 = _make_release(version="1.0.0")
    r2 = _make_release(version="2.0.0")
    id1 = upsert_release(db_conn, r1)
    id2 = upsert_release(db_conn, r2)
    assert id1 != id2


def test_upsert_release_persists_fields(db_conn):
    r = _make_release(package="express", version="5.0.0", rank=7)
    rid = upsert_release(db_conn, r)
    row = db_conn.execute("SELECT * FROM releases WHERE id = ?", (rid,)).fetchone()
    assert row["package"] == "express"
    assert row["version"] == "5.0.0"
    assert row["rank"] == 7


# ---------------------------------------------------------------------------
# save_artifacts
# ---------------------------------------------------------------------------


def test_save_artifacts_writes_two_rows(db_conn):
    r = _make_release()
    rid = upsert_release(db_conn, r)
    old = _make_artifact("4.17.20")
    new = _make_artifact("4.17.21")
    save_artifacts(db_conn, rid, old, new)
    rows = db_conn.execute(
        "SELECT role FROM artifacts WHERE release_id = ?", (rid,)
    ).fetchall()
    roles = {row["role"] for row in rows}
    assert roles == {"old", "new"}


def test_save_artifacts_persists_sha256(db_conn):
    r = _make_release()
    rid = upsert_release(db_conn, r)
    old = _make_artifact("4.17.20")
    old.sha256 = "deadbeef" * 8
    new = _make_artifact("4.17.21")
    save_artifacts(db_conn, rid, old, new)
    row = db_conn.execute(
        "SELECT sha256 FROM artifacts WHERE release_id = ? AND role = 'old'", (rid,)
    ).fetchone()
    assert row["sha256"] == "deadbeef" * 8


def test_save_artifacts_is_idempotent_on_rescan(db_conn):
    """Calling save_artifacts twice for the same release_id must not create
    duplicate rows (INSERT OR IGNORE semantics).
    """
    r = _make_release()
    rid = upsert_release(db_conn, r)
    old = _make_artifact("4.17.20")
    new = _make_artifact("4.17.21")

    save_artifacts(db_conn, rid, old, new)
    save_artifacts(db_conn, rid, old, new)  # second call — must be ignored

    rows = db_conn.execute(
        "SELECT role FROM artifacts WHERE release_id = ?", (rid,)
    ).fetchall()
    assert len(rows) == 2, "expected exactly 2 artifact rows, not duplicates"


# ---------------------------------------------------------------------------
# save_verdict
# ---------------------------------------------------------------------------


def test_save_verdict_returns_id(db_conn):
    r = _make_release()
    rid = upsert_release(db_conn, r)
    v = _make_verdict(r)
    vid = save_verdict(db_conn, rid, v)
    assert isinstance(vid, int)
    assert vid >= 1


def test_save_verdict_persists_result(db_conn):
    r = _make_release()
    rid = upsert_release(db_conn, r)
    v = _make_verdict(r)
    vid = save_verdict(db_conn, rid, v)
    row = db_conn.execute(
        "SELECT result, confidence FROM verdicts WHERE id = ?", (vid,)
    ).fetchone()
    assert row["result"] == "benign"
    assert row["confidence"] == "high"


# ---------------------------------------------------------------------------
# save_alert
# ---------------------------------------------------------------------------


def test_save_alert_persists_notifier(db_conn):
    r = _make_release()
    rid = upsert_release(db_conn, r)
    v = _make_verdict(r)
    vid = save_verdict(db_conn, rid, v)
    alert = Alert(
        verdict=v,
        notifier="local",
        sent_at=datetime.now(timezone.utc),
        success=True,
        detail="/tmp/report.md",
    )
    save_alert(db_conn, vid, alert)
    row = db_conn.execute(
        "SELECT notifier, success FROM alerts WHERE verdict_id = ?", (vid,)
    ).fetchone()
    assert row["notifier"] == "local"
    assert row["success"] == 1


# ---------------------------------------------------------------------------
# collector state
# ---------------------------------------------------------------------------


def test_get_collector_state_returns_empty_dict_initially(db_conn):
    state = get_collector_state(db_conn, "npm")
    assert state == {}


def test_set_and_get_collector_state_roundtrip(db_conn):
    set_collector_state(db_conn, "npm", {"seq": 999, "epoch": 1234.5})
    state = get_collector_state(db_conn, "npm")
    assert state["seq"] == 999
    assert state["epoch"] == 1234.5


def test_set_collector_state_upserts(db_conn):
    set_collector_state(db_conn, "npm", {"seq": 1})
    set_collector_state(db_conn, "npm", {"seq": 2})
    state = get_collector_state(db_conn, "npm")
    assert state["seq"] == 2


# ---------------------------------------------------------------------------
# tweet budget
# ---------------------------------------------------------------------------


def test_get_tweet_count_zero_initially(db_conn):
    assert get_tweet_count(db_conn) == 0


def test_increment_tweet_count_first_call_returns_one(db_conn):
    count = increment_tweet_count(db_conn)
    assert count == 1


def test_increment_tweet_count_accumulates(db_conn):
    increment_tweet_count(db_conn)
    increment_tweet_count(db_conn)
    count = increment_tweet_count(db_conn)
    assert count == 3
    assert get_tweet_count(db_conn) == 3


def test_tweet_count_resets_on_new_month(db_conn, mocker):
    mocker.patch("scm.db._current_month", return_value="2026-01")
    increment_tweet_count(db_conn)
    increment_tweet_count(db_conn)
    # Advance to new month
    mocker.patch("scm.db._current_month", return_value="2026-02")
    count = increment_tweet_count(db_conn)
    assert count == 1
    assert get_tweet_count(db_conn) == 1


def test_get_tweet_count_returns_zero_after_month_rollover(db_conn, mocker):
    mocker.patch("scm.db._current_month", return_value="2026-01")
    increment_tweet_count(db_conn)
    mocker.patch("scm.db._current_month", return_value="2026-02")
    assert get_tweet_count(db_conn) == 0


def test_current_month_format():
    month = _current_month()
    parts = month.split("-")
    assert len(parts) == 2
    assert len(parts[0]) == 4  # YYYY
    assert len(parts[1]) == 2  # MM


# ---------------------------------------------------------------------------
# get_recent_verdicts
# ---------------------------------------------------------------------------


def test_get_recent_verdicts_empty(db_conn):
    rows = get_recent_verdicts(db_conn)
    assert rows == []


def test_get_recent_verdicts_returns_dicts(db_conn):
    r = _make_release()
    rid = upsert_release(db_conn, r)
    old = _make_artifact("4.17.20")
    new = _make_artifact("4.17.21")
    save_artifacts(db_conn, rid, old, new)
    v = _make_verdict(r)
    save_verdict(db_conn, rid, v)
    rows = get_recent_verdicts(db_conn)
    assert len(rows) == 1
    assert rows[0]["package"] == "lodash"
    assert rows[0]["result"] == "benign"


# ---------------------------------------------------------------------------
# get_release_id
# ---------------------------------------------------------------------------


def test_get_release_id_returns_id_for_existing_release(db_conn):
    r = _make_release()
    rid = upsert_release(db_conn, r)
    found = get_release_id(db_conn, r.ecosystem, r.package, r.version)
    assert found == rid


def test_get_release_id_returns_none_for_missing_release(db_conn):
    result = get_release_id(db_conn, "npm", "does-not-exist", "1.0.0")
    assert result is None


def test_get_release_id_is_specific_to_ecosystem(db_conn):
    r = _make_release(ecosystem="npm")
    rid = upsert_release(db_conn, r)
    # Same package/version, different ecosystem should not match
    result = get_release_id(db_conn, "pypi", r.package, r.version)
    assert result is None
    # Correct ecosystem should match
    result2 = get_release_id(db_conn, "npm", r.package, r.version)
    assert result2 == rid


# ---------------------------------------------------------------------------
# save_verdict — opencode_log_path
# ---------------------------------------------------------------------------


def test_save_verdict_persists_opencode_log_path(db_conn):
    r = _make_release()
    rid = upsert_release(db_conn, r)
    v = _make_verdict(r)
    v.opencode_log_path = "/home/user/.local/share/opencode/log/2026-04-02T053609.log"
    vid = save_verdict(db_conn, rid, v)
    row = db_conn.execute(
        "SELECT opencode_log_path FROM verdicts WHERE id = ?", (vid,)
    ).fetchone()
    assert row["opencode_log_path"] == v.opencode_log_path


def test_save_verdict_opencode_log_path_defaults_to_none(db_conn):
    r = _make_release()
    rid = upsert_release(db_conn, r)
    v = _make_verdict(r)
    assert v.opencode_log_path is None
    vid = save_verdict(db_conn, rid, v)
    row = db_conn.execute(
        "SELECT opencode_log_path FROM verdicts WHERE id = ?", (vid,)
    ).fetchone()
    assert row["opencode_log_path"] is None


# ---------------------------------------------------------------------------
# init_db migration — opencode_log_path column
# ---------------------------------------------------------------------------


def test_init_db_migration_adds_opencode_log_path_column(tmp_path):
    """init_db must non-destructively add opencode_log_path to an old DB."""
    import sqlite3 as _sqlite3

    db_file = tmp_path / "old.db"
    # Create a DB with the old verdicts schema (no opencode_log_path column)
    raw = _sqlite3.connect(str(db_file))
    raw.execute(
        """
        CREATE TABLE verdicts (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            release_id     INTEGER NOT NULL,
            result         TEXT    NOT NULL,
            confidence     TEXT    NOT NULL DEFAULT 'unknown',
            summary        TEXT    NOT NULL DEFAULT '',
            analysis       TEXT    NOT NULL,
            analyzed_at    TEXT    NOT NULL
        )
        """
    )
    raw.commit()
    raw.close()

    # init_db should add the column without raising
    conn = init_db(db_file)
    # Verify the column exists
    cols = [row[1] for row in conn.execute("PRAGMA table_info(verdicts)").fetchall()]
    assert "opencode_log_path" in cols
    conn.close()
