"""npm collector — watches the top-N npm packages for new releases.

Data sources:
  Watchlist : https://registry.npmjs.org/download-counts/latest  (package/counts.json)
  Changes   : https://replicate.npmjs.com/registry/_changes      (CouchDB feed)
  Packument : https://registry.npmjs.org/{encoded}               (full package metadata)
"""

from __future__ import annotations

import json
import logging
import sqlite3
import tarfile
import tempfile
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from scm import db as db_module
from scm.collectors import Collector, WatchlistError
from scm.models import Release

log = logging.getLogger(__name__)

REPLICATE_ROOT = "https://replicate.npmjs.com/registry"
REGISTRY_URL = "https://registry.npmjs.org"
REPLICATE_HEADER = {"npm-replication-opt-in": "true"}
MAX_CHANGES = 5_000
PAGE_SIZE = 1_000
GAP_RESET_THRESHOLD = 20_000
INITIAL_LOOKBACK_SECONDS = 30 * 86_400  # 30 days


class NpmCollector(Collector):
    ecosystem = "npm"

    def __init__(self) -> None:
        self._watchlist: (
            dict[str, int] | None
        ) = {}  # name.lower() → rank (1-based); None = all packages
        self._last_seq: int = 0
        self._poll_epoch: float = 0.0  # Unix timestamp of last poll start
        self._new_limit: int = 0  # 0 = unlimited; only applied when _watchlist is None

    # -------------------------------------------------------------------------
    # Watchlist
    # -------------------------------------------------------------------------

    def load_watchlist(self, top_n: int, new_limit: int = 0) -> None:
        """Download download-counts tarball and build rank map from counts.json.

        When top_n == 0, skip the download entirely and set _watchlist = None
        (all packages are candidates).  new_limit caps how many releases are
        yielded per poll cycle in this mode (0 = unlimited).
        """
        self._new_limit = new_limit
        if top_n == 0:
            self._watchlist = None
            log.info(
                "npm watchlist disabled (top_n=0) — all new releases will be processed"
            )
            return
        log.info("loading npm watchlist  top_n=%d", top_n)
        tmpdir = Path(tempfile.mkdtemp())
        try:
            # 1. Fetch latest metadata for the download-counts package
            encoded = urllib.parse.quote("download-counts", safe="")
            meta_url = f"{REGISTRY_URL}/{encoded}/latest"
            try:
                with urllib.request.urlopen(meta_url, timeout=30) as resp:  # noqa: S310
                    meta = json.loads(resp.read())
                tarball_url: str = meta["dist"]["tarball"]
            except Exception as exc:
                raise WatchlistError(
                    f"failed to fetch download-counts metadata: {exc}"
                ) from exc

            # 2. Download the tarball
            tgz_path = tmpdir / "download-counts.tgz"
            try:
                with urllib.request.urlopen(tarball_url, timeout=60) as resp:  # noqa: S310
                    with tgz_path.open("wb") as fh:
                        for chunk in iter(lambda: resp.read(65536), b""):
                            fh.write(chunk)
            except Exception as exc:
                raise WatchlistError(
                    f"failed to download download-counts tarball: {exc}"
                ) from exc

            # 3. Extract counts.json (path inside tarball: package/counts.json)
            try:
                with tarfile.open(tgz_path) as tf:
                    member = tf.getmember("package/counts.json")
                    fobj = tf.extractfile(member)
                    if fobj is None:
                        raise WatchlistError(
                            "counts.json is not a regular file in tarball"
                        )
                    counts: dict[str, int] = json.loads(fobj.read())
            except (KeyError, tarfile.TarError) as exc:
                raise WatchlistError(f"failed to parse counts.json: {exc}") from exc

            # 4. Sort descending by download count, assign 1-based rank
            sorted_pkgs = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)
            self._watchlist = {
                pkg.lower(): rank
                for rank, (pkg, _) in enumerate(sorted_pkgs[:top_n], start=1)
            }
            log.info(
                "watchlist loaded  %d packages  top=%s",
                len(self._watchlist),
                sorted_pkgs[0][0] if sorted_pkgs else "?",
            )

        finally:
            import shutil

            shutil.rmtree(tmpdir, ignore_errors=True)

    # -------------------------------------------------------------------------
    # Replication helpers
    # -------------------------------------------------------------------------

    def _get_head_seq(self) -> int:
        req = urllib.request.Request(f"{REPLICATE_ROOT}/", headers=REPLICATE_HEADER)
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
            return json.loads(resp.read())["update_seq"]

    def _fetch_changes(self, since: int) -> tuple[list[dict], int]:
        url = f"{REPLICATE_ROOT}/_changes?since={since}&limit={PAGE_SIZE}"
        req = urllib.request.Request(url, headers=REPLICATE_HEADER)
        with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310
            data = json.loads(resp.read())
        return data["results"], data["last_seq"]

    def _fetch_packument(self, package: str) -> dict:
        """Fetch the full packument for a package from the npm registry."""
        encoded = urllib.parse.quote(package, safe="")
        with urllib.request.urlopen(f"{REGISTRY_URL}/{encoded}", timeout=30) as resp:  # noqa: S310
            return json.loads(resp.read())

    def _extract_metadata(self, packument: dict, version: str) -> dict:
        """Extract registry metadata for a specific version from the packument."""
        time_map: dict[str, str] = packument.get("time", {})
        release_date = time_map.get(version)

        # Get version-specific metadata if available
        version_info = packument.get("versions", {}).get(version, {})
        author_info = version_info.get("author", {})
        author = (
            author_info.get("name") if isinstance(author_info, dict) else author_info
        )

        return {
            "release_date": release_date,  # ISO8601 timestamp
            "author": author,
            "license": version_info.get("license") or packument.get("license"),
            "homepage": packument.get("homepage"),
            "repository": packument.get("repository", {}).get("url"),
            "description": version_info.get("description")
            or packument.get("description"),
        }

    def _detect_new_versions(
        self, package: str, since_epoch: float
    ) -> list[tuple[str, dict]]:
        """Return versions published after since_epoch (Unix timestamp), oldest first.

        Returns list of (version, metadata) tuples.
        """
        since_iso = datetime.fromtimestamp(since_epoch, tz=timezone.utc).isoformat()
        packument = self._fetch_packument(package)

        time_map: dict[str, str] = packument.get("time", {})
        new_versions = []
        for ver, ts in time_map.items():
            if ver in ("created", "modified"):
                continue
            if ts > since_iso:
                metadata = self._extract_metadata(packument, ver)
                new_versions.append((ver, metadata))
        new_versions.sort(key=lambda x: x[1].get("release_date", ""))
        return new_versions

    def _get_previous_version(self, package: str, new_version: str) -> str | None:
        """Return the version published just before new_version, or None."""
        encoded = urllib.parse.quote(package, safe="")
        try:
            with urllib.request.urlopen(
                f"{REGISTRY_URL}/{encoded}", timeout=30
            ) as resp:  # noqa: S310
                packument = json.loads(resp.read())
        except Exception as exc:
            log.warning("could not fetch packument for %s: %s", package, exc)
            return None

        time_map: dict[str, str] = packument.get("time", {})
        versions_with_times = sorted(
            [(v, ts) for v, ts in time_map.items() if v not in ("created", "modified")],
            key=lambda x: x[1],
        )
        version_list = [v for v, _ in versions_with_times]
        if new_version not in version_list:
            return None
        idx = version_list.index(new_version)
        return version_list[idx - 1] if idx > 0 else None

    # -------------------------------------------------------------------------
    # Poll
    # -------------------------------------------------------------------------

    def poll(self) -> Iterator[Release]:
        """Yield new Release objects for watchlisted packages since last poll.

        When _watchlist is None (top_n=0 mode), all packages that appear in
        the changes feed are yielded.
        """
        cycle_start = time.time()
        log.info("npm poll  last_seq=%d  epoch=%.0f", self._last_seq, self._poll_epoch)

        # Gap protection — on first run or after a long pause, reset to HEAD
        try:
            head_seq = self._get_head_seq()
        except Exception as exc:
            log.warning("could not fetch HEAD seq: %s — using last_seq as-is", exc)
            head_seq = self._last_seq

        gap = head_seq - self._last_seq
        if gap > GAP_RESET_THRESHOLD:
            is_first_run = self._last_seq == 0
            log.warning(
                "gap too large (%d changes) — resetting seq to HEAD %d%s",
                gap,
                head_seq,
                " (first run — falling through to full-watchlist epoch scan)"
                if is_first_run and self._watchlist is not None
                else "",
            )
            self._last_seq = head_seq
            # On genuine stalls reset the epoch so we don't re-scan history.
            # On first run with a watchlist: preserve the epoch and fall through
            # to epoch-based scanning of the full watchlist.
            # On first run in --new mode (_watchlist is None): there is no
            # watchlist to iterate over, so just move to HEAD and start picking
            # up new releases from the changes feed going forward.
            if not is_first_run:
                self._poll_epoch = cycle_start
                return

            if self._watchlist is None:
                # --new mode first run: skip epoch scan, start from HEAD
                log.info(
                    "first-run gap reset in --new mode: starting from HEAD, "
                    "no epoch scan (no watchlist to enumerate)"
                )
                self._poll_epoch = cycle_start
                return

            # First run with watchlist: skip the CouchDB changes feed (window
            # is empty at HEAD) and go straight to epoch-based detection across
            # the whole watchlist.
            log.info(
                "first-run gap reset: scanning all %d watchlisted packages via epoch",
                len(self._watchlist),
            )
            for pkg in list(self._watchlist.keys()):
                try:
                    new_versions = self._detect_new_versions(pkg, self._poll_epoch)
                except Exception as exc:
                    log.warning("could not detect new versions for %s: %s", pkg, exc)
                    continue
                rank = self._watchlist.get(pkg.lower(), 0)
                for ver, metadata in new_versions:
                    log.info("new version detected  %s@%s  rank=#%d", pkg, ver, rank)
                    yield Release(
                        ecosystem="npm",
                        package=pkg,
                        version=ver,
                        previous_version=None,
                        rank=rank,
                        discovered_at=datetime.now(timezone.utc),
                        metadata=metadata,
                    )
            self._poll_epoch = cycle_start
            return

        # Paginate through _changes
        since = self._last_seq
        accumulated = 0
        last_seq_seen = since
        seen_packages: set[str] = set()

        while accumulated < MAX_CHANGES:
            try:
                results, last_seq = self._fetch_changes(since)
            except Exception as exc:
                log.warning("_fetch_changes failed at since=%d: %s", since, exc)
                break

            for entry in results:
                pkg_id: str = entry.get("id", "")
                if pkg_id.startswith("_design/"):
                    continue
                pkg_lower = pkg_id.lower()
                # In --new mode (_watchlist is None) accept all packages;
                # otherwise filter to watchlist.
                if self._watchlist is not None and pkg_lower not in self._watchlist:
                    continue
                seen_packages.add(pkg_id)

            last_seq_seen = last_seq
            accumulated += len(results)
            if len(results) < PAGE_SIZE:
                break
            since = last_seq

        log.info(
            "changes processed: %d  matching packages: %d",
            accumulated,
            len(seen_packages),
        )

        yielded = 0
        for pkg in seen_packages:
            try:
                new_versions = self._detect_new_versions(pkg, self._poll_epoch)
            except Exception as exc:
                log.warning("could not detect new versions for %s: %s", pkg, exc)
                continue

            rank = (
                self._watchlist.get(pkg.lower(), 0)
                if self._watchlist is not None
                else 0
            )
            for ver, metadata in new_versions:
                if self._new_limit > 0 and yielded >= self._new_limit:
                    log.info(
                        "npm new_limit=%d reached — stopping early", self._new_limit
                    )
                    self._last_seq = last_seq_seen
                    self._poll_epoch = cycle_start
                    return
                log.info("new version detected  %s@%s  rank=#%d", pkg, ver, rank)
                yield Release(
                    ecosystem="npm",
                    package=pkg,
                    version=ver,
                    previous_version=None,  # resolved by orchestrator
                    rank=rank,
                    discovered_at=datetime.now(timezone.utc),
                    metadata=metadata,
                )
                yielded += 1

        self._last_seq = last_seq_seen
        self._poll_epoch = cycle_start

    # -------------------------------------------------------------------------
    # State persistence
    # -------------------------------------------------------------------------

    def get_previous_version(self, package: str, new_version: str) -> str | None:
        """Public wrapper — satisfies Collector ABC + used by orchestrator."""
        return self._get_previous_version(package, new_version)

    def save_state(self, conn: sqlite3.Connection) -> None:
        db_module.set_collector_state(
            conn, self.ecosystem, {"seq": self._last_seq, "epoch": self._poll_epoch}
        )
        log.debug(
            "npm state saved  seq=%d  epoch=%.0f", self._last_seq, self._poll_epoch
        )

    def load_state(self, conn: sqlite3.Connection) -> None:
        state = db_module.get_collector_state(conn, self.ecosystem)
        self._last_seq = int(state.get("seq", 0))
        # On first run (no persisted epoch) seed 30 days back so historical
        # releases are visible immediately rather than requiring a full poll cycle.
        default_epoch = time.time() - INITIAL_LOOKBACK_SECONDS
        self._poll_epoch = float(state.get("epoch", default_epoch))
        log.debug(
            "npm state loaded  seq=%d  epoch=%.0f", self._last_seq, self._poll_epoch
        )
