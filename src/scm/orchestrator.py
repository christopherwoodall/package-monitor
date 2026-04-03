"""Orchestrator — wires collector → storage → extractor → analyzer → notifiers.

Processes releases in parallel via ThreadPoolExecutor.
One failed release never stops the others.
"""

from __future__ import annotations

import logging
import shutil
import sqlite3
import tempfile
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from scm import analyzer as analyzer_module
from scm import db as db_module
from scm import extractor as extractor_module
from scm import storage as storage_module
from scm.collectors import Collector
from scm.db import DuplicateRelease
from scm.models import Release
from scm.notifiers import Notifier
from scm.storage import DownloadError

if TYPE_CHECKING:
    from scm.scanners import Scanner

log = logging.getLogger(__name__)


def _process_release(
    release: Release,
    collector: Collector,
    notifiers: list[Notifier],
    conn: sqlite3.Connection,
    analyze_timeout: int,
    *,
    analyzer_model: str | None = None,
    analyzer_prompt: str | None = None,
    scanners: list[Scanner] | None = None,
    force: bool = False,
) -> None:
    """Full pipeline for a single release.  Raises on unrecoverable error.

    When *force* is True the previous-version lookup is skipped and the
    release is analysed with new/ only (no diff).  This is used by the
    dashboard force-scan path so that a rescan always produces a verdict
    even when no previous version can be resolved from the registry.
    """
    pkg = release.package
    new_ver = release.version
    eco = release.ecosystem

    # ── resolve previous version ────────────────────────────────────────────
    if force:
        previous_version: str | None = None
        log.info(
            "[%s] force-scan %s@%s — skipping previous-version lookup",
            eco,
            pkg,
            new_ver,
        )
    else:
        previous_version = collector.get_previous_version(pkg, new_ver)
        if previous_version is None:
            log.info("[%s] skipping %s@%s — no previous version", eco, pkg, new_ver)
            return
        release.previous_version = previous_version

    # ── persist release ──────────────────────────────────────────────────────
    try:
        release_id = db_module.upsert_release(conn, release)
    except DuplicateRelease:
        existing_id = db_module.get_release_id(conn, eco, pkg, new_ver)
        if existing_id is None:
            log.warning(
                "[%s] duplicate %s@%s but release row not found — skipping",
                eco,
                pkg,
                new_ver,
            )
            return
        log.info("[%s] rescan  %s@%s  (release_id=%s)", eco, pkg, new_ver, existing_id)
        release_id = existing_id

    # ── download tarballs ────────────────────────────────────────────────────
    try:
        old_artifact = (
            storage_module.download_tarball(eco, pkg, previous_version)
            if previous_version is not None
            else None
        )
        new_artifact = storage_module.download_tarball(eco, pkg, new_ver)
    except DownloadError as exc:
        log.exception(
            "[%s] download failed  %s  %s→%s: %s",
            eco,
            pkg,
            previous_version or "(none)",
            new_ver,
            exc,
        )
        raise

    db_module.save_artifacts(conn, release_id, old_artifact, new_artifact)

    # ── extract tarballs ─────────────────────────────────────────────────────
    tmpdir = Path(tempfile.mkdtemp())
    try:
        new_dest = tmpdir / "new"
        new_dest.mkdir()
        log.debug("extracting new  %s", new_artifact.path)
        new_root = extractor_module.safe_extract(new_artifact.path, new_dest)

        old_root: Path | None = None
        old_files: dict[str, Path] = {}
        if old_artifact is not None:
            old_dest = tmpdir / "old"
            old_dest.mkdir()
            log.debug("extracting old  %s", old_artifact.path)
            old_root = extractor_module.safe_extract(old_artifact.path, old_dest)
            old_files = extractor_module.collect_files(old_root)

        new_files = extractor_module.collect_files(new_root)
        log.info(
            "extracted %s@%s → %s  (%d old files, %d new files)",
            pkg,
            previous_version or "(none)",
            new_ver,
            len(old_files),
            len(new_files),
        )

        # Derive changed / added file lists for scanners
        old_set = set(old_files)
        new_set = set(new_files)
        changed_files = sorted(
            f
            for f in old_set & new_set
            if old_files[f].read_bytes() != new_files[f].read_bytes()
        )
        added_files = sorted(new_set - old_set)

        # ── analyze (scanners + opencode) ─────────────────────────────────────
        verdict = analyzer_module.analyze(
            release=release,
            old_artifact=old_artifact,
            new_artifact=new_artifact,
            old_root=old_root,
            new_root=new_root,
            changed_files=changed_files,
            added_files=added_files,
            timeout=analyze_timeout,
            model=analyzer_model,
            prompt=analyzer_prompt,
            scanners=scanners,
        )
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
        log.debug("cleaned up tmpdir %s", tmpdir)

    verdict_id = db_module.save_verdict(conn, release_id, verdict)

    log.info(
        "[%s] %s  %s→%s  |  %s (%s)  |  rank #%d",
        eco,
        pkg,
        previous_version or "(none)",
        new_ver,
        verdict.result.upper(),
        verdict.confidence,
        release.rank,
    )

    # ── notify ───────────────────────────────────────────────────────────────
    for notifier in notifiers:
        try:
            alert = notifier.notify(verdict, conn)
            db_module.save_alert(conn, verdict_id, alert)
        except Exception as exc:
            log.exception("notifier %r raised unexpectedly: %s", notifier.name, exc)


def run(
    collector: Collector,
    notifiers: list[Notifier],
    conn: sqlite3.Connection,
    *,
    interval: int = 300,
    once: bool = False,
    top_n: int = 1000,
    analyze_timeout: int = 300,
    workers: int = 4,
    analyzer_model: str | None = None,
    analyzer_prompt: str | None = None,
    scanners: list[Scanner] | None = None,
) -> None:
    """Main loop — poll → process releases in parallel → sleep (unless once=True)."""
    collector.load_state(conn)
    log.info(
        "orchestrator started  workers=%d  top_n=%d  once=%s", workers, top_n, once
    )

    while True:
        releases = list(collector.poll())
        collector.save_state(conn)  # always persist after poll, even if releases=[]
        log.info("poll complete  %d release(s) to process", len(releases))

        if releases:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                future_to_release: dict[Future[None], Release] = {
                    pool.submit(
                        _process_release,
                        r,
                        collector,
                        notifiers,
                        conn,
                        analyze_timeout,
                        analyzer_model=analyzer_model,
                        analyzer_prompt=analyzer_prompt,
                        scanners=scanners,
                    ): r
                    for r in releases
                }
                for future in as_completed(future_to_release):
                    r = future_to_release[future]
                    exc = future.exception()
                    if exc is not None:
                        log.exception(
                            "release %s@%s failed: %s", r.package, r.version, exc
                        )

        if once:
            log.info("--once mode: exiting after first poll cycle")
            break
        log.info("sleeping %ds until next poll", interval)
        time.sleep(interval)


def _run_collector_thread(
    collector: Collector,
    notifiers: list[Notifier],
    db_path: Path,
    *,
    interval: int,
    once: bool,
    top_n: int,
    analyze_timeout: int,
    workers: int,
    analyzer_model: str | None = None,
    analyzer_prompt: str | None = None,
    scanners: list[Scanner] | None = None,
    status_callback: threading.local | None = None,
) -> None:
    """Target for each per-ecosystem thread in run_multi.

    Opens its own sqlite3 connection (connections cannot be shared across threads).
    """
    eco = collector.ecosystem
    conn = db_module.init_db(db_path)
    try:
        run(
            collector=collector,
            notifiers=notifiers,
            conn=conn,
            interval=interval,
            once=once,
            top_n=top_n,
            analyze_timeout=analyze_timeout,
            workers=workers,
            analyzer_model=analyzer_model,
            analyzer_prompt=analyzer_prompt,
            scanners=scanners,
        )
    except Exception as exc:
        log.exception("[%s] collector thread raised: %s", eco, exc)
    finally:
        conn.close()
        log.info("[%s] collector thread exiting", eco)


def run_multi(
    collectors: list[Collector],
    notifiers: list[Notifier],
    db_path: Path,
    *,
    interval: int = 300,
    once: bool = False,
    top_n: int = 1000,
    analyze_timeout: int = 300,
    workers: int = 4,
    analyzer_model: str | None = None,
    analyzer_prompt: str | None = None,
    scanners: list[Scanner] | None = None,
) -> None:
    """Run multiple collectors in parallel, each in its own thread with its own DB connection.

    Each collector independently polls its registry, processes releases, and persists
    state.  Threads are joined before this function returns (in once=True mode) or
    run until interrupted (in continuous mode).

    Args:
        collectors: One Collector instance per ecosystem to monitor.
        notifiers:  Shared list of notifiers (must be thread-safe — LocalNotifier is).
        db_path:    Path to the SQLite database file.  Each thread opens its own connection.
        interval:   Seconds to sleep between poll cycles (continuous mode only).
        once:       If True, each collector runs exactly one poll cycle then exits.
        top_n:      Top-N packages each collector should watch.
        analyze_timeout: opencode timeout per release in seconds.
        workers:    ThreadPoolExecutor workers *per collector* for release processing.
        analyzer_model: If set, passed as ``--model`` to opencode.
        analyzer_prompt: If set, overrides the default analysis prompt.
        scanners:   Optional list of configured Scanner instances.
    """
    if not collectors:
        log.warning("run_multi called with no collectors — nothing to do")
        return

    ecosystems = [c.ecosystem for c in collectors]
    log.info(
        "run_multi starting  ecosystems=%s  workers=%d  top_n=%d  once=%s",
        ecosystems,
        workers,
        top_n,
        once,
    )

    threads: list[threading.Thread] = []
    for collector in collectors:
        t = threading.Thread(
            target=_run_collector_thread,
            kwargs=dict(
                collector=collector,
                notifiers=notifiers,
                db_path=db_path,
                interval=interval,
                once=once,
                top_n=top_n,
                analyze_timeout=analyze_timeout,
                workers=workers,
                analyzer_model=analyzer_model,
                analyzer_prompt=analyzer_prompt,
                scanners=scanners,
            ),
            name=f"collector-{collector.ecosystem}",
            daemon=True,
        )
        threads.append(t)

    for t in threads:
        t.start()
        log.info("started thread %s", t.name)

    for t in threads:
        t.join()
        log.info("thread %s finished", t.name)

    log.info("run_multi complete")
