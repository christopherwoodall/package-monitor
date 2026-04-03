"""PyPI collector — watches top-N PyPI packages for new releases.

Watchlist source : https://hugovk.github.io/top-pypi-packages/top-pypi-packages-30-days.min.json
New releases     : PyPI XMLRPC API — changelog_since_serial(last_serial)
                   https://pypi.org/pypi  (xmlrpc.client.ServerProxy)
Package metadata : https://pypi.org/pypi/{package}/{version}/json

State persisted  : {"serial": int}  — last PyPI changelog serial processed.

Wheel-only releases (no sdist .tar.gz) are skipped with a warning.

Gap protection   : if current_serial - last_serial > PYPI_GAP_RESET_THRESHOLD,
                   reset to HEAD and return (avoids replaying millions of entries).
First-run        : if last_serial == 0, look back PYPI_SERIALS_PER_DAY * 30 serials
                   so the first cycle catches ~30 days of releases.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
import urllib.parse
import urllib.request
import xmlrpc.client
from datetime import datetime, timezone
from typing import Iterator

from scm import db as db_module
from scm.collectors import Collector, WatchlistError
from scm.models import Release

log = logging.getLogger(__name__)

PYPI_TOP_PACKAGES_URL = (
    "https://hugovk.github.io/top-pypi-packages/top-pypi-packages-30-days.min.json"
)
PYPI_XMLRPC_URL = "https://pypi.org/pypi"
PYPI_JSON_API = "https://pypi.org/pypi"

PYPI_SERIALS_PER_DAY = 28_880
PYPI_GAP_RESET_THRESHOLD = 200_000  # ~7 days of serials


def _extract_metadata(meta: dict) -> dict:
    """Extract registry metadata from PyPI JSON API response."""
    info = meta.get("info", {})
    files = meta.get("urls", [])

    # Get release date from first file's upload time
    release_date = None
    for f in files:
        if f.get("packagetype") == "sdist":
            release_date = f.get("upload_time_iso_8601") or f.get("upload_time")
            break

    return {
        "release_date": release_date,  # ISO8601 timestamp
        "author": info.get("author"),
        "author_email": info.get("author_email"),
        "license": info.get("license"),
        "summary": info.get("summary"),
        "home_page": info.get("home_page"),
        "project_urls": info.get("project_urls"),
        "requires_python": info.get("requires_python"),
    }


class PypiCollector(Collector):
    ecosystem = "pypi"

    def __init__(self) -> None:
        self._watchlist: (
            dict[str, int] | None
        ) = {}  # name → rank (1-based); None = all packages
        self._last_serial: int = 0
        self._new_limit: int = 0  # 0 = unlimited; only applied when _watchlist is None

    # ------------------------------------------------------------------
    # load_watchlist
    # ------------------------------------------------------------------

    def load_watchlist(self, top_n: int, new_limit: int = 0) -> None:
        """Fetch top-N PyPI packages by monthly downloads and build _watchlist.

        When top_n == 0, skip the download entirely and set _watchlist = None
        (all packages with an sdist are candidates).  new_limit caps how many
        releases are yielded per poll cycle in this mode (0 = unlimited).
        """
        self._new_limit = new_limit
        if top_n == 0:
            self._watchlist = None
            log.info(
                "PyPI watchlist disabled (top_n=0) — all new releases will be processed"
            )
            return
        log.info("loading PyPI top-%d watchlist", top_n)
        try:
            with urllib.request.urlopen(PYPI_TOP_PACKAGES_URL, timeout=30) as resp:  # noqa: S310
                data = json.loads(resp.read())
        except Exception as exc:
            raise WatchlistError(
                f"failed to fetch PyPI top-packages list: {exc}"
            ) from exc

        try:
            rows = data["rows"]
        except (KeyError, TypeError) as exc:
            raise WatchlistError(f"unexpected top-packages JSON shape: {exc}") from exc

        self._watchlist = {
            row["project"].lower(): rank
            for rank, row in enumerate(rows[:top_n], start=1)
        }
        log.info("PyPI watchlist loaded: %d packages", len(self._watchlist))

    # ------------------------------------------------------------------
    # XMLRPC helpers
    # ------------------------------------------------------------------

    def _xmlrpc_client(self) -> xmlrpc.client.ServerProxy:
        return xmlrpc.client.ServerProxy(PYPI_XMLRPC_URL)

    def _get_head_serial(self) -> int:
        client = self._xmlrpc_client()
        return client.changelog_last_serial()  # type: ignore[return-value]

    def _changelog_since(self, serial: int) -> list[tuple]:
        client = self._xmlrpc_client()
        return client.changelog_since_serial(serial)  # type: ignore[return-value]

    # ------------------------------------------------------------------
    # poll
    # ------------------------------------------------------------------

    def poll(self) -> Iterator[Release]:
        """Yield new Release objects for watchlisted packages since last poll."""
        log.info("PyPI poll  last_serial=%d", self._last_serial)

        # Fetch current HEAD serial
        try:
            current_serial: int = self._get_head_serial()
        except Exception as exc:
            log.warning("failed to fetch PyPI head serial: %s", exc)
            return

        gap = current_serial - self._last_serial

        # Gap protection — if too far behind, reset to HEAD and skip this cycle
        if self._last_serial != 0 and gap > PYPI_GAP_RESET_THRESHOLD:
            log.warning(
                "PyPI serial gap too large (%d) — resetting to HEAD %d",
                gap,
                current_serial,
            )
            self._last_serial = current_serial
            return

        # First-run: seed with 30-day lookback
        if self._last_serial == 0:
            lookback = PYPI_SERIALS_PER_DAY * 30
            self._last_serial = max(0, current_serial - lookback)
            log.info(
                "PyPI first run — seeding serial to %d (~30 days back from %d)",
                self._last_serial,
                current_serial,
            )

        # Fetch changelog entries since last_serial
        try:
            entries: list[tuple] = self._changelog_since(self._last_serial)
        except Exception as exc:
            log.warning("failed to fetch PyPI changelog: %s", exc)
            return

        log.info("PyPI changelog returned %d entries", len(entries))

        # Filter to "new release" actions and deduplicate (pkg, version)
        seen: set[tuple[str, str]] = set()
        candidates: list[tuple[str, str]] = []
        for entry in entries:
            # entry: (package_name, version, timestamp, action, serial)
            if len(entry) < 5:
                continue
            pkg_name: str = entry[0] or ""
            version: str = entry[1] or ""
            action: str = entry[3] or ""
            if action != "new release":
                continue
            key = (pkg_name.lower(), version)
            if key not in seen:
                seen.add(key)
                candidates.append((pkg_name, version))

        log.info("PyPI new-release candidates after dedup: %d", len(candidates))

        yielded = 0
        for pkg_name, version in candidates:
            pkg_lower = pkg_name.lower()
            # In --new mode (_watchlist is None) accept all packages;
            # otherwise filter to watchlist.
            if self._watchlist is not None and pkg_lower not in self._watchlist:
                continue

            # Apply new_limit cap (only meaningful when _watchlist is None)
            if self._new_limit > 0 and yielded >= self._new_limit:
                log.info("PyPI new_limit=%d reached — stopping early", self._new_limit)
                break

            rank = self._watchlist[pkg_lower] if self._watchlist is not None else 0

            # Fetch per-version JSON to verify sdist and get canonical name
            pkg_encoded = urllib.parse.quote(pkg_lower, safe="")
            ver_encoded = urllib.parse.quote(version, safe="")
            meta_url = f"{PYPI_JSON_API}/{pkg_encoded}/{ver_encoded}/json"

            try:
                with urllib.request.urlopen(meta_url, timeout=30) as resp:  # noqa: S310
                    meta = json.loads(resp.read())
            except Exception as exc:
                log.warning(
                    "failed to fetch PyPI metadata for %s@%s: %s",
                    pkg_lower,
                    version,
                    exc,
                )
                continue

            # Skip wheel-only releases
            files = meta.get("urls", [])
            has_sdist = any(f.get("packagetype") == "sdist" for f in files)
            if not has_sdist:
                log.warning(
                    "skipping %s@%s — no sdist (wheel-only release)", pkg_lower, version
                )
                continue

            canonical_name: str = meta.get("info", {}).get("name", pkg_name)
            metadata = _extract_metadata(meta)

            yield Release(
                ecosystem="pypi",
                package=canonical_name,
                version=version,
                previous_version=None,
                rank=rank,
                discovered_at=datetime.now(timezone.utc),
                metadata=metadata,
            )
            yielded += 1

        # Advance serial to HEAD after all yields
        self._last_serial = current_serial
        log.info("PyPI poll complete: yielded %d new release(s)", yielded)

    # ------------------------------------------------------------------
    # get_previous_version
    # ------------------------------------------------------------------

    def get_previous_version(self, package: str, new_version: str) -> str | None:
        """Return the version immediately before new_version, chronologically."""
        pkg_encoded = urllib.parse.quote(package.lower(), safe="")
        meta_url = f"{PYPI_JSON_API}/{pkg_encoded}/json"

        try:
            with urllib.request.urlopen(meta_url, timeout=30) as resp:  # noqa: S310
                meta = json.loads(resp.read())
        except Exception as exc:
            log.warning(
                "get_previous_version: failed to fetch PyPI metadata for %s: %s",
                package,
                exc,
            )
            return None

        releases: dict[str, list] = meta.get("releases", {})

        # Build {version: upload_timestamp} for versions that have at least one file
        version_times: list[tuple[float, str]] = []
        for ver, files in releases.items():
            if not files:
                continue
            ts_str = files[0].get("upload_time_iso_8601") or files[0].get(
                "upload_time", ""
            )
            try:
                dt = datetime.fromisoformat(ts_str.rstrip("Z")).replace(
                    tzinfo=timezone.utc
                )
                version_times.append((dt.timestamp(), ver))
            except (ValueError, AttributeError):
                pass

        version_times.sort()  # ascending by timestamp

        versions_sorted = [v for _, v in version_times]
        if new_version not in versions_sorted:
            log.debug(
                "get_previous_version: %s not found in PyPI releases for %s",
                new_version,
                package,
            )
            return None

        idx = versions_sorted.index(new_version)
        if idx == 0:
            return None
        return versions_sorted[idx - 1]

    # ------------------------------------------------------------------
    # save_state / load_state
    # ------------------------------------------------------------------

    def save_state(self, conn: sqlite3.Connection) -> None:
        db_module.set_collector_state(
            conn, self.ecosystem, {"serial": self._last_serial}
        )

    def load_state(self, conn: sqlite3.Connection) -> None:
        state = db_module.get_collector_state(conn, self.ecosystem)
        try:
            self._last_serial = int(state.get("serial", 0))
        except (TypeError, ValueError):
            self._last_serial = 0
