"""Backfill registry metadata for historical releases.

One-time script to populate the metadata_json column for releases that
were scanned before metadata collection was implemented.

Usage:
    uv run package-monitor-backfill-metadata [--db scm.db] [--rate-limit-ms 100]
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from scm import db as db_module

log = logging.getLogger(__name__)

NPM_REGISTRY_URL = "https://registry.npmjs.org"
PYPI_JSON_API = "https://pypi.org/pypi"


def fetch_npm_metadata(package: str, version: str) -> dict[str, Any] | None:
    """Fetch metadata for an npm package version. Returns None on 404."""
    encoded = urllib.parse.quote(package, safe="")
    url = f"{NPM_REGISTRY_URL}/{encoded}"

    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            packument = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            log.warning("npm package not found: %s@%s", package, version)
            return None
        raise

    time_map: dict[str, str] = packument.get("time", {})
    release_date = time_map.get(version)

    version_info = packument.get("versions", {}).get(version, {})
    author_info = version_info.get("author", {})
    author = author_info.get("name") if isinstance(author_info, dict) else author_info

    return {
        "release_date": release_date,
        "author": author,
        "license": version_info.get("license") or packument.get("license"),
        "homepage": packument.get("homepage"),
        "repository": packument.get("repository", {}).get("url"),
        "description": version_info.get("description") or packument.get("description"),
        "backfill_status": "success",
    }


def fetch_pypi_metadata(package: str, version: str) -> dict[str, Any] | None:
    """Fetch metadata for a PyPI package version. Returns None on 404."""
    pkg_encoded = urllib.parse.quote(package.lower(), safe="")
    ver_encoded = urllib.parse.quote(version, safe="")
    url = f"{PYPI_JSON_API}/{pkg_encoded}/{ver_encoded}/json"

    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            meta = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            log.warning("PyPI package not found: %s@%s", package, version)
            return None
        raise

    info = meta.get("info", {})
    files = meta.get("urls", [])

    # Get release date from first sdist upload time
    release_date = None
    for f in files:
        if f.get("packagetype") == "sdist":
            release_date = f.get("upload_time_iso_8601") or f.get("upload_time")
            break

    return {
        "release_date": release_date,
        "author": info.get("author"),
        "author_email": info.get("author_email"),
        "license": info.get("license"),
        "summary": info.get("summary"),
        "home_page": info.get("home_page"),
        "project_urls": info.get("project_urls"),
        "requires_python": info.get("requires_python"),
        "backfill_status": "success",
    }


def backfill_release(
    conn: sqlite3.Connection,
    release_id: int,
    ecosystem: str,
    package: str,
    version: str,
    rate_limit_ms: int,
) -> bool:
    """Backfill metadata for a single release. Returns True if successful."""
    try:
        if ecosystem == "npm":
            metadata = fetch_npm_metadata(package, version)
        elif ecosystem == "pypi":
            metadata = fetch_pypi_metadata(package, version)
        else:
            log.warning("Unknown ecosystem: %s", ecosystem)
            return False

        if metadata is None:
            # Package not found (404) - mark as failed
            metadata = {
                "backfill_status": "failed",
                "error": "Package not found in registry",
            }

        conn.execute(
            "UPDATE releases SET metadata_json = ? WHERE id = ?",
            (json.dumps(metadata), release_id),
        )
        conn.commit()

        if rate_limit_ms > 0:
            time.sleep(rate_limit_ms / 1000.0)

        return metadata.get("backfill_status") == "success"

    except Exception as exc:
        log.error("Failed to backfill %s/%s@%s: %s", ecosystem, package, version, exc)
        # Mark as failed
        try:
            conn.execute(
                "UPDATE releases SET metadata_json = ? WHERE id = ?",
                (
                    json.dumps({"backfill_status": "failed", "error": str(exc)}),
                    release_id,
                ),
            )
            conn.commit()
        except Exception:
            pass
        return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Backfill registry metadata for historical releases"
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=Path("scm.db"),
        help="Path to SQLite database (default: scm.db)",
    )
    parser.add_argument(
        "--rate-limit-ms",
        type=int,
        default=100,
        help="Milliseconds to wait between registry requests (default: 100)",
    )
    parser.add_argument(
        "--ecosystem",
        choices=["npm", "pypi"],
        help="Only backfill specific ecosystem",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable verbose logging",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    if not args.db.exists():
        log.error("Database not found: %s", args.db)
        return 1

    conn = db_module.init_db(args.db)

    # Get releases without metadata
    query = """
        SELECT id, ecosystem, package, version
        FROM releases
        WHERE metadata_json IS NULL
    """
    params = []
    if args.ecosystem:
        query += " AND ecosystem = ?"
        params.append(args.ecosystem)

    rows = conn.execute(query, params).fetchall()

    if not rows:
        log.info("No releases need backfilling. All releases already have metadata.")
        return 0

    log.info("Found %d releases to backfill", len(rows))

    success_count = 0
    fail_count = 0

    for i, row in enumerate(rows, 1):
        release_id = row["id"]
        ecosystem = row["ecosystem"]
        package = row["package"]
        version = row["version"]

        log.info(
            "[%d/%d] Backfilling %s/%s@%s", i, len(rows), ecosystem, package, version
        )

        if backfill_release(
            conn, release_id, ecosystem, package, version, args.rate_limit_ms
        ):
            success_count += 1
        else:
            fail_count += 1

    conn.close()

    log.info("Backfill complete: %d succeeded, %d failed", success_count, fail_count)
    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
