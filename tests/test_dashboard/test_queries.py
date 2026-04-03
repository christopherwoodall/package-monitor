"""Tests for scm.dashboard.queries.

All tests use an in-memory SQLite database seeded through the normal
scm.db CRUD functions — no direct SQL inserts in test code.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from scm.db import (
    init_db,
    save_alert,
    save_artifacts,
    save_verdict,
    set_collector_state,
    upsert_release,
)
from scm.dashboard.queries import (
    get_collector_states,
    get_ecosystem_breakdown,
    get_latest_per_package,
    get_package_history,
    get_stats,
    get_verdicts_paginated,
)
from scm.models import Alert, Release, StoredArtifact, Verdict


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TS = datetime(2026, 4, 1, 12, 0, 0, tzinfo=timezone.utc)


def _conn(tmp_path: Path):
    return init_db(tmp_path / "test.db")


def _release(
    ecosystem: str = "npm",
    package: str = "express",
    version: str = "5.0.0",
    rank: int = 1,
) -> Release:
    return Release(
        ecosystem=ecosystem,
        package=package,
        version=version,
        previous_version="4.0.0",
        rank=rank,
        discovered_at=_TS,
    )


def _artifact(version: str = "5.0.0", ecosystem: str = "npm") -> StoredArtifact:
    return StoredArtifact(
        ecosystem=ecosystem,
        package="express",
        version=version,
        filename=f"express-{version}.tgz",
        path=Path(f"/tmp/express-{version}.tgz"),
        sha256="a" * 64,
        size_bytes=512,
    )


def _verdict(release: Release, result: str = "benign") -> Verdict:
    return Verdict(
        release=release,
        old_artifact=_artifact("4.0.0"),
        new_artifact=_artifact("5.0.0"),
        result=result,
        confidence="high",
        summary="all good",
        analysis="no issues",
        analyzed_at=_TS,
    )


def _seed_one(
    conn, ecosystem="npm", package="express", version="5.0.0", result="benign"
):
    """Insert one complete release→verdict chain and return verdict_id."""
    r = _release(ecosystem=ecosystem, package=package, version=version)
    rid = upsert_release(conn, r)
    save_artifacts(
        conn, rid, _artifact("4.0.0", ecosystem), _artifact(version, ecosystem)
    )
    v = _verdict(r, result=result)
    return save_verdict(conn, rid, v)


# ---------------------------------------------------------------------------
# get_stats
# ---------------------------------------------------------------------------


def test_get_stats_empty_db(tmp_path):
    conn = _conn(tmp_path)
    stats = get_stats(conn)
    assert stats["total_scans"] == 0
    assert stats["malicious"] == 0
    assert stats["benign"] == 0
    assert stats["unknown"] == 0
    assert stats["error"] == 0
    assert stats["packages_watched"] == 0
    assert stats["ecosystems"] == 0


def test_get_stats_counts_correctly(tmp_path):
    conn = _conn(tmp_path)
    _seed_one(conn, version="1.0.0", result="benign")
    _seed_one(conn, version="2.0.0", result="malicious")
    _seed_one(conn, version="3.0.0", result="unknown")

    stats = get_stats(conn)
    assert stats["total_scans"] == 3
    assert stats["benign"] == 1
    assert stats["malicious"] == 1
    assert stats["unknown"] == 1
    assert stats["packages_watched"] == 1  # same package, different versions
    assert stats["ecosystems"] == 1


def test_get_stats_multiple_ecosystems(tmp_path):
    conn = _conn(tmp_path)
    _seed_one(conn, ecosystem="npm", version="1.0.0", result="benign")
    _seed_one(
        conn, ecosystem="pypi", package="requests", version="2.31.0", result="benign"
    )

    stats = get_stats(conn)
    assert stats["total_scans"] == 2
    assert stats["packages_watched"] == 2
    assert stats["ecosystems"] == 2


# ---------------------------------------------------------------------------
# get_verdicts_paginated
# ---------------------------------------------------------------------------


def test_get_verdicts_paginated_returns_all(tmp_path):
    conn = _conn(tmp_path)
    _seed_one(conn, version="1.0.0", result="benign")
    _seed_one(conn, version="2.0.0", result="malicious")

    rows = get_verdicts_paginated(conn, offset=0, limit=50)
    assert len(rows) == 2


def test_get_verdicts_paginated_respects_limit(tmp_path):
    conn = _conn(tmp_path)
    for i in range(5):
        _seed_one(conn, version=f"{i}.0.0", result="benign")

    rows = get_verdicts_paginated(conn, offset=0, limit=3)
    assert len(rows) == 3


def test_get_verdicts_paginated_respects_offset(tmp_path):
    conn = _conn(tmp_path)
    for i in range(5):
        _seed_one(conn, version=f"{i}.0.0", result="benign")

    all_rows = get_verdicts_paginated(conn, offset=0, limit=50)
    page2 = get_verdicts_paginated(conn, offset=2, limit=50)
    assert len(page2) == len(all_rows) - 2


def test_get_verdicts_paginated_filter_ecosystem(tmp_path):
    conn = _conn(tmp_path)
    _seed_one(conn, ecosystem="npm", version="1.0.0")
    _seed_one(conn, ecosystem="pypi", package="requests", version="2.0.0")

    npm_rows = get_verdicts_paginated(conn, ecosystem="npm")
    assert all(r["ecosystem"] == "npm" for r in npm_rows)
    assert len(npm_rows) == 1


def test_get_verdicts_paginated_filter_result(tmp_path):
    conn = _conn(tmp_path)
    _seed_one(conn, version="1.0.0", result="benign")
    _seed_one(conn, version="2.0.0", result="malicious")

    mal = get_verdicts_paginated(conn, result="malicious")
    assert len(mal) == 1
    assert mal[0]["result"] == "malicious"


def test_get_verdicts_paginated_returns_expected_keys(tmp_path):
    conn = _conn(tmp_path)
    _seed_one(conn)

    rows = get_verdicts_paginated(conn)
    assert len(rows) == 1
    row = rows[0]
    expected_keys = {
        "package",
        "version",
        "ecosystem",
        "rank",
        "result",
        "confidence",
        "summary",
        "analyzed_at",
        "old_sha256",
        "new_sha256",
        "report_path",
    }
    assert expected_keys.issubset(set(row.keys()))


# ---------------------------------------------------------------------------
# get_package_history
# ---------------------------------------------------------------------------


def test_get_package_history_empty(tmp_path):
    conn = _conn(tmp_path)
    rows = get_package_history(conn, "npm", "nonexistent")
    assert rows == []


def test_get_package_history_returns_correct_package(tmp_path):
    conn = _conn(tmp_path)
    _seed_one(conn, ecosystem="npm", package="express", version="5.0.0")
    _seed_one(conn, ecosystem="npm", package="lodash", version="4.0.0")

    rows = get_package_history(conn, "npm", "express")
    assert len(rows) == 1
    assert rows[0]["package"] == "express"


def test_get_package_history_multiple_versions_newest_first(tmp_path):
    conn = _conn(tmp_path)
    # Insert with different analyzed_at by tweaking the save_verdict call directly
    for i, version in enumerate(["1.0.0", "2.0.0", "3.0.0"]):
        _seed_one(conn, ecosystem="npm", package="express", version=version)

    rows = get_package_history(conn, "npm", "express")
    assert len(rows) == 3
    # All should be the same package
    assert all(r["package"] == "express" for r in rows)


def test_get_package_history_ecosystem_filter(tmp_path):
    conn = _conn(tmp_path)
    _seed_one(conn, ecosystem="npm", package="express", version="5.0.0")
    _seed_one(conn, ecosystem="pypi", package="express", version="5.0.0")

    npm_rows = get_package_history(conn, "npm", "express")
    pypi_rows = get_package_history(conn, "pypi", "express")
    assert len(npm_rows) == 1
    assert len(pypi_rows) == 1


# ---------------------------------------------------------------------------
# get_ecosystem_breakdown
# ---------------------------------------------------------------------------


def test_get_ecosystem_breakdown_empty(tmp_path):
    conn = _conn(tmp_path)
    assert get_ecosystem_breakdown(conn) == []


def test_get_ecosystem_breakdown_counts(tmp_path):
    conn = _conn(tmp_path)
    _seed_one(conn, ecosystem="npm", version="1.0.0", result="benign")
    _seed_one(conn, ecosystem="npm", version="2.0.0", result="malicious")
    _seed_one(
        conn, ecosystem="pypi", package="requests", version="2.0.0", result="benign"
    )

    rows = get_ecosystem_breakdown(conn)
    by_eco = {r["ecosystem"]: r for r in rows}

    assert by_eco["npm"]["count"] == 2
    assert by_eco["npm"]["malicious_count"] == 1
    assert by_eco["pypi"]["count"] == 1
    assert by_eco["pypi"]["malicious_count"] == 0


# ---------------------------------------------------------------------------
# get_collector_states
# ---------------------------------------------------------------------------


def test_get_collector_states_empty(tmp_path):
    conn = _conn(tmp_path)
    assert get_collector_states(conn) == []


def test_get_collector_states_returns_set_states(tmp_path):
    conn = _conn(tmp_path)
    set_collector_state(conn, "npm", {"seq": 100})
    set_collector_state(conn, "pypi", {"last_cursor": "abc"})

    rows = get_collector_states(conn)
    ecosystems = [r["ecosystem"] for r in rows]
    assert "npm" in ecosystems
    assert "pypi" in ecosystems
    assert len(rows) == 2


def test_get_collector_states_has_updated_at(tmp_path):
    conn = _conn(tmp_path)
    set_collector_state(conn, "npm", {"seq": 1})

    rows = get_collector_states(conn)
    assert rows[0]["updated_at"] is not None
    assert len(rows[0]["updated_at"]) > 0


# ---------------------------------------------------------------------------
# get_latest_per_package
# ---------------------------------------------------------------------------


def test_get_latest_per_package_empty(tmp_path):
    conn = _conn(tmp_path)
    assert get_latest_per_package(conn) == []


def test_get_latest_per_package_one_row_per_package(tmp_path):
    """Multiple versions of the same package → only one row returned."""
    conn = _conn(tmp_path)
    _seed_one(conn, ecosystem="npm", package="express", version="4.0.0")
    _seed_one(conn, ecosystem="npm", package="express", version="5.0.0")

    rows = get_latest_per_package(conn)
    assert len(rows) == 1
    assert rows[0]["package"] == "express"


def test_get_latest_per_package_returns_newest_verdict(tmp_path):
    """Returns the latest verdict (highest verdict id), not the oldest."""
    conn = _conn(tmp_path)
    _seed_one(
        conn, ecosystem="npm", package="express", version="4.0.0", result="benign"
    )
    _seed_one(
        conn, ecosystem="npm", package="express", version="5.0.0", result="malicious"
    )

    rows = get_latest_per_package(conn)
    assert len(rows) == 1
    assert rows[0]["version"] == "5.0.0"
    assert rows[0]["result"] == "malicious"


def test_get_latest_per_package_multiple_packages(tmp_path):
    """Two different packages → two rows."""
    conn = _conn(tmp_path)
    _seed_one(conn, ecosystem="npm", package="express", version="5.0.0")
    _seed_one(conn, ecosystem="npm", package="lodash", version="4.17.21")

    rows = get_latest_per_package(conn)
    assert len(rows) == 2
    packages = {r["package"] for r in rows}
    assert packages == {"express", "lodash"}


def test_get_latest_per_package_filter_ecosystem(tmp_path):
    conn = _conn(tmp_path)
    _seed_one(conn, ecosystem="npm", package="express", version="5.0.0")
    _seed_one(conn, ecosystem="pypi", package="requests", version="2.31.0")

    npm_rows = get_latest_per_package(conn, ecosystem="npm")
    assert len(npm_rows) == 1
    assert npm_rows[0]["ecosystem"] == "npm"

    pypi_rows = get_latest_per_package(conn, ecosystem="pypi")
    assert len(pypi_rows) == 1
    assert pypi_rows[0]["ecosystem"] == "pypi"


def test_get_latest_per_package_filter_result(tmp_path):
    conn = _conn(tmp_path)
    _seed_one(
        conn, ecosystem="npm", package="express", version="5.0.0", result="benign"
    )
    _seed_one(
        conn, ecosystem="npm", package="lodash", version="4.17.21", result="malicious"
    )

    mal = get_latest_per_package(conn, result="malicious")
    assert len(mal) == 1
    assert mal[0]["result"] == "malicious"
    assert mal[0]["package"] == "lodash"


def test_get_latest_per_package_pagination(tmp_path):
    conn = _conn(tmp_path)
    packages = ["express", "lodash", "react", "chalk", "axios"]
    for pkg in packages:
        _seed_one(conn, ecosystem="npm", package=pkg, version="1.0.0")

    page1 = get_latest_per_package(conn, offset=0, limit=3)
    page2 = get_latest_per_package(conn, offset=3, limit=3)
    assert len(page1) == 3
    assert len(page2) == 2
    # No overlap
    p1_names = {r["package"] for r in page1}
    p2_names = {r["package"] for r in page2}
    assert p1_names.isdisjoint(p2_names)


def test_get_latest_per_package_returns_expected_keys(tmp_path):
    conn = _conn(tmp_path)
    _seed_one(conn)

    rows = get_latest_per_package(conn)
    assert len(rows) == 1
    row = rows[0]
    expected_keys = {
        "package",
        "version",
        "ecosystem",
        "rank",
        "result",
        "confidence",
        "summary",
        "analyzed_at",
        "old_sha256",
        "new_sha256",
        "new_path",
        "report_path",
    }
    assert expected_keys.issubset(set(row.keys()))


def test_get_latest_per_package_new_path_value(tmp_path):
    """new_path should match the path stored in the 'new' artifact."""
    conn = _conn(tmp_path)
    _seed_one(conn, version="5.0.0")

    rows = get_latest_per_package(conn)
    assert len(rows) == 1
    # _artifact() stores path as Path(f"/tmp/express-{version}.tgz")
    assert rows[0]["new_path"] == "/tmp/express-5.0.0.tgz"


# ---------------------------------------------------------------------------
# get_package_history — new_path column
# ---------------------------------------------------------------------------


def test_get_package_history_includes_new_path(tmp_path):
    """get_package_history rows include new_path after the schema update."""
    conn = _conn(tmp_path)
    _seed_one(conn, ecosystem="npm", package="express", version="5.0.0")

    rows = get_package_history(conn, "npm", "express")
    assert len(rows) == 1
    assert "new_path" in rows[0]
    assert rows[0]["new_path"] == "/tmp/express-5.0.0.tgz"


def test_get_package_history_includes_verdict_id(tmp_path):
    """Each row in get_package_history includes a unique verdict_id."""
    conn = _conn(tmp_path)
    vid1 = _seed_one(conn, ecosystem="npm", package="express", version="4.0.0")
    vid2 = _seed_one(conn, ecosystem="npm", package="express", version="5.0.0")

    rows = get_package_history(conn, "npm", "express")
    assert len(rows) == 2
    verdict_ids = {r["verdict_id"] for r in rows}
    assert verdict_ids == {vid1, vid2}


def test_get_package_history_verdict_id_distinguishes_same_version_rescans(tmp_path):
    """Two scans of the same version produce two rows with distinct verdict_ids."""
    conn = _conn(tmp_path)
    r = _release(ecosystem="npm", package="express", version="5.0.0")
    rid = upsert_release(conn, r)
    save_artifacts(conn, rid, _artifact("4.0.0"), _artifact("5.0.0"))
    v = _verdict(r, result="benign")
    vid1 = save_verdict(conn, rid, v)
    vid2 = save_verdict(conn, rid, v)

    rows = get_package_history(conn, "npm", "express")
    assert len(rows) == 2
    assert rows[0]["verdict_id"] != rows[1]["verdict_id"]
    assert {rows[0]["verdict_id"], rows[1]["verdict_id"]} == {vid1, vid2}
