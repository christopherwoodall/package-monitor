"""Read-only DB queries for the dashboard.

All functions accept a sqlite3.Connection and return plain dicts / lists of dicts.
No business logic lives here — only SQL.
"""

from __future__ import annotations

import json
import logging
import sqlite3

log = logging.getLogger(__name__)


def get_stats(conn: sqlite3.Connection) -> dict:
    """Return aggregate counts for the summary cards.

    Returns:
        {
            total_scans: int,
            malicious: int,
            benign: int,
            unknown: int,
            error: int,
            packages_watched: int,
            ecosystems: int,
        }
    """
    row = conn.execute("""
        SELECT
            COUNT(*)                                          AS total_scans,
            SUM(CASE WHEN result = 'malicious' THEN 1 ELSE 0 END) AS malicious,
            SUM(CASE WHEN result = 'benign'    THEN 1 ELSE 0 END) AS benign,
            SUM(CASE WHEN result = 'unknown'   THEN 1 ELSE 0 END) AS unknown,
            SUM(CASE WHEN result = 'error'     THEN 1 ELSE 0 END) AS error
        FROM verdicts
        """).fetchone()

    pkg_row = conn.execute(
        "SELECT COUNT(DISTINCT package || '|' || ecosystem) AS packages_watched FROM releases"
    ).fetchone()

    eco_row = conn.execute(
        "SELECT COUNT(DISTINCT ecosystem) AS ecosystems FROM releases"
    ).fetchone()

    return {
        "total_scans": row["total_scans"] or 0,
        "malicious": row["malicious"] or 0,
        "benign": row["benign"] or 0,
        "unknown": row["unknown"] or 0,
        "error": row["error"] or 0,
        "packages_watched": pkg_row["packages_watched"] or 0,
        "ecosystems": eco_row["ecosystems"] or 0,
    }


def get_verdicts_paginated(
    conn: sqlite3.Connection,
    offset: int = 0,
    limit: int = 50,
    ecosystem: str | None = None,
    result: str | None = None,
) -> list[dict]:
    """Return verdicts with optional ecosystem/result filters, newest first.

    Returns a list of dicts with keys:
        package, version, ecosystem, rank, result, confidence, summary,
        analyzed_at, old_sha256, new_sha256, report_path
    """
    conditions: list[str] = []
    params: list[object] = []

    if ecosystem:
        conditions.append("r.ecosystem = ?")
        params.append(ecosystem)
    if result:
        conditions.append("v.result = ?")
        params.append(result)

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    params.extend([limit, offset])

    rows = conn.execute(
        f"""
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
        {where}
        ORDER BY v.analyzed_at DESC
        LIMIT ? OFFSET ?
        """,
        params,
    ).fetchall()

    return [dict(r) for r in rows]


def get_latest_per_package(
    conn: sqlite3.Connection,
    offset: int = 0,
    limit: int = 50,
    ecosystem: str | None = None,
    result: str | None = None,
) -> list[dict]:
    """Return one row per (ecosystem, package) — the most recent verdict only.

    Ordered by analyzed_at DESC so the most recently scanned packages appear
    first.  Supports the same ecosystem/result filters as get_verdicts_paginated.

    Returns a list of dicts with keys:
        package, version, ecosystem, rank, result, confidence, summary,
        analyzed_at, old_sha256, new_sha256, new_path, report_path
    """
    conditions: list[str] = []
    params: list[object] = []

    if ecosystem:
        conditions.append("r.ecosystem = ?")
        params.append(ecosystem)
    if result:
        conditions.append("v.result = ?")
        params.append(result)

    # The query already has a WHERE clause (for the subquery filter), so
    # extra conditions must be appended with AND, not a second WHERE.
    extra_filter = ("AND " + " AND ".join(conditions)) if conditions else ""

    # Subquery picks the max verdict id per (ecosystem, package) — that is the
    # most recent verdict because verdict ids are autoincrement and verdicts are
    # only ever appended, never updated.
    params.extend([limit, offset])

    rows = conn.execute(
        f"""
        SELECT
            r.package, r.version, r.ecosystem, r.rank,
            v.result, v.confidence, v.summary, v.analyzed_at,
            old_a.sha256  AS old_sha256,
            new_a.sha256  AS new_sha256,
            new_a.path    AS new_path,
            al.detail     AS report_path,
            v.opencode_log_path AS log_path
        FROM verdicts v
        JOIN releases r ON r.id = v.release_id
        LEFT JOIN artifacts old_a
               ON old_a.release_id = v.release_id AND old_a.role = 'old'
        LEFT JOIN artifacts new_a
               ON new_a.release_id = v.release_id AND new_a.role = 'new'
        LEFT JOIN alerts al
               ON al.verdict_id = v.id AND al.notifier = 'local'
        WHERE v.id IN (
            SELECT MAX(v2.id)
            FROM verdicts v2
            JOIN releases r2 ON r2.id = v2.release_id
            GROUP BY r2.ecosystem, r2.package
        )
        {extra_filter}
        ORDER BY v.analyzed_at DESC
        LIMIT ? OFFSET ?
        """,
        params,
    ).fetchall()

    return [dict(r) for r in rows]


def get_package_history(
    conn: sqlite3.Connection, ecosystem: str, package: str
) -> list[dict]:
    """Return all verdicts for one package, newest first.

    Returns same column set as get_verdicts_paginated, plus new_path for
    the tarball download action.
    """
    rows = conn.execute(
        """
        SELECT
            v.id          AS verdict_id,
            r.package, r.version, r.ecosystem, r.rank,
            r.metadata_json,
            v.result, v.confidence, v.summary, v.analyzed_at,
            old_a.sha256  AS old_sha256,
            new_a.sha256  AS new_sha256,
            new_a.path    AS new_path,
            al.detail     AS report_path,
            v.opencode_log_path AS log_path
        FROM verdicts v
        JOIN releases r ON r.id = v.release_id
        LEFT JOIN artifacts old_a
               ON old_a.release_id = v.release_id AND old_a.role = 'old'
        LEFT JOIN artifacts new_a
               ON new_a.release_id = v.release_id AND new_a.role = 'new'
        LEFT JOIN alerts al
               ON al.verdict_id = v.id AND al.notifier = 'local'
        WHERE r.ecosystem = ? AND r.package = ?
        ORDER BY v.analyzed_at DESC
        """,
        (ecosystem, package),
    ).fetchall()

    results = []
    for r in rows:
        row_dict = dict(r)
        # Parse metadata JSON
        metadata_json = row_dict.pop("metadata_json", None)
        if metadata_json:
            try:
                row_dict["metadata"] = json.loads(metadata_json)
            except json.JSONDecodeError:
                row_dict["metadata"] = {}
        else:
            row_dict["metadata"] = {}
        results.append(row_dict)

    return results


def get_ecosystem_breakdown(conn: sqlite3.Connection) -> list[dict]:
    """Return per-ecosystem scan counts.

    Returns list of {ecosystem, count, malicious_count}.
    """
    rows = conn.execute("""
        SELECT
            r.ecosystem,
            COUNT(*) AS count,
            SUM(CASE WHEN v.result = 'malicious' THEN 1 ELSE 0 END) AS malicious_count
        FROM verdicts v
        JOIN releases r ON r.id = v.release_id
        GROUP BY r.ecosystem
        ORDER BY count DESC
        """).fetchall()

    return [dict(r) for r in rows]


def delete_verdict(conn: sqlite3.Connection, verdict_id: int) -> bool:
    """Delete a verdict by its ID.

    Deletes associated alerts first (alerts.verdict_id has a foreign key
    constraint to verdicts.id without ON DELETE CASCADE), then deletes
    the verdict row.

    Returns True if a row was deleted, False if no such verdict exists.
    """
    # Delete associated alerts first to satisfy FK constraint
    conn.execute("DELETE FROM alerts WHERE verdict_id = ?", (verdict_id,))
    # Now delete the verdict
    cur = conn.execute("DELETE FROM verdicts WHERE id = ?", (verdict_id,))
    return cur.rowcount > 0


def get_collector_states(conn: sqlite3.Connection) -> list[dict]:
    """Return last-poll info per ecosystem from the collector_state table.

    Returns list of {ecosystem, updated_at, state_json}.
    """
    rows = conn.execute(
        "SELECT ecosystem, updated_at, state_json FROM collector_state ORDER BY ecosystem"
    ).fetchall()

    return [dict(r) for r in rows]
