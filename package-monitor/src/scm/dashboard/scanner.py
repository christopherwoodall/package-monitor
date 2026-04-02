"""ScanManager — runs one collector thread per ecosystem in the background.

Used by the Flask dashboard to kick off on-demand scans without blocking
HTTP request handling.
"""

from __future__ import annotations

import collections
import concurrent.futures
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Deque

from scm import db as db_module
from scm import orchestrator
from scm import plugins
from scm.models import Release

if TYPE_CHECKING:
    from scm.scanners import Scanner

log = logging.getLogger(__name__)

_MAX_LOG_LINES = 500
_MAX_HISTORY = 20


class ScanManager:
    """Manages a single background scan across one or more ecosystems.

    Only one scan may be active at a time.  Call :meth:`start` to kick off a
    new scan; it returns ``False`` immediately if a scan is already running.

    All attributes accessed from the Flask request thread are protected by
    ``_lock``.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._status: str = "idle"  # "idle" | "running" | "error"
        self._ecosystems: list[str] = []
        self._processed: int = 0
        self._errors: int = 0
        self._releases_found: int = 0
        self._started_at: datetime | None = None
        self._finished_at: datetime | None = None
        self._log_lines: Deque[str] = collections.deque(maxlen=_MAX_LOG_LINES)
        self._threads: list[threading.Thread] = []
        self._history: list[dict] = []  # capped at _MAX_HISTORY entries

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(
        self,
        db_path: Path,
        ecosystems: list[str],
        top_n: int = 1000,
        workers: int = 4,
        analyze_timeout: int = 300,
        notifier_names: list[str] | None = None,
        analyzer_model: str | None = None,
        analyzer_prompt: str | None = None,
        enabled_scanners: list[str] | None = None,
        scanner_config: dict | None = None,
    ) -> bool:
        """Launch one background thread per ecosystem.

        Args:
            db_path:          Path to the SQLite database.
            ecosystems:       List of ecosystem names to scan.
            top_n:            Number of top packages to watch per ecosystem.
            workers:          Parallel release-processing threads per ecosystem.
            analyze_timeout:  Per-release opencode timeout in seconds.
            notifier_names:   Notifier names to use (default: ["local"]).
            analyzer_model:   If set, passed as ``--model`` to opencode.
            analyzer_prompt:  If set, overrides the default analysis prompt.
            enabled_scanners: Names of scanners to enable (default: all registered).
            scanner_config:   Per-scanner configuration dicts.

        Returns:
            True  — scan started successfully.
            False — a scan is already running; request ignored.
        """
        with self._lock:
            if self._status == "running":
                log.warning("scan already running — ignoring start request")
                return False

            self._status = "running"
            self._ecosystems = list(ecosystems)
            self._processed = 0
            self._errors = 0
            self._releases_found = 0
            self._started_at = datetime.now(timezone.utc)
            self._finished_at = None
            self._log_lines.clear()
            self._threads = []

        if notifier_names is None:
            notifier_names = ["local"]

        self._append_log(
            f"Scan started — ecosystems: {', '.join(ecosystems)}"
            f"  notifiers: {', '.join(notifier_names)}"
        )

        # Load collector classes from entry_points
        available = plugins.load_collectors()
        available_notifiers = plugins.load_notifiers()
        available_scanners = plugins.load_scanners()

        missing = [e for e in ecosystems if e not in available]
        if missing:
            msg = f"Unknown ecosystems: {', '.join(missing)}"
            self._append_log(f"ERROR: {msg}")
            with self._lock:
                self._status = "error"
                self._finished_at = datetime.now(timezone.utc)
            log.error(msg)
            return False

        # Build and configure scanner instances
        _scanner_cfg = scanner_config or {}
        _enabled = (
            enabled_scanners
            if enabled_scanners is not None
            else list(available_scanners.keys())
        )
        scanners: list[Scanner] = []
        for sname in _enabled:
            if sname not in available_scanners:
                log.warning("scanner %r not registered — skipping", sname)
                continue
            try:
                s = available_scanners[sname]()
                opts = _scanner_cfg.get(sname, {})
                if opts:
                    s.configure(opts)
                scanners.append(s)
            except Exception as exc:  # noqa: BLE001
                log.warning("failed to init scanner %r: %s", sname, exc)

        # Build collector instances and load watchlists
        collectors = []
        for eco in ecosystems:
            try:
                collector = available[eco]()
                collector.load_watchlist(top_n)
                collectors.append(collector)
                self._append_log(
                    f"[{eco}] watchlist loaded "
                    f"({'all packages' if top_n == 0 else f'top {top_n}'})"
                )
            except Exception as exc:
                self._append_log(f"[{eco}] ERROR loading watchlist: {exc}")
                log.exception("[%s] failed to load watchlist: %s", eco, exc)
                with self._lock:
                    self._status = "error"
                    self._finished_at = datetime.now(timezone.utc)
                return False

        # Build notifier list
        notifiers = []
        for name in notifier_names:
            if name in available_notifiers:
                try:
                    notifiers.append(available_notifiers[name]())
                except Exception as exc:
                    self._append_log(
                        f"WARNING: failed to init notifier '{name}': {exc}"
                    )
                    log.warning("failed to init notifier '%s': %s", name, exc)
            else:
                self._append_log(f"WARNING: notifier '{name}' not found — skipping")
                log.warning("notifier '%s' not registered", name)

        # Spawn one thread per collector
        supervisor = threading.Thread(
            target=self._supervisor,
            args=(
                collectors,
                notifiers,
                db_path,
                workers,
                analyze_timeout,
                analyzer_model,
                analyzer_prompt,
                scanners,
            ),
            name="scan-supervisor",
            daemon=True,
        )
        supervisor.start()
        log.info("scan supervisor started")
        return True

    def force_scan_package(
        self,
        db_path: Path,
        ecosystem: str,
        package: str,
        version: str,
        workers: int = 4,
        analyze_timeout: int = 300,
        notifier_names: list[str] | None = None,
        analyzer_model: str | None = None,
        analyzer_prompt: str | None = None,
        enabled_scanners: list[str] | None = None,
        scanner_config: dict | None = None,
    ) -> bool:
        """Force a scan of a single specific package@version.

        Bypasses the collector poll entirely — directly runs the release
        pipeline for the named package.

        Returns:
            True  — scan started.
            False — a scan is already running.
        """
        with self._lock:
            if self._status == "running":
                log.warning("scan already running — ignoring force-scan request")
                return False

            self._status = "running"
            self._ecosystems = [ecosystem]
            self._processed = 0
            self._errors = 0
            self._releases_found = 1
            self._started_at = datetime.now(timezone.utc)
            self._finished_at = None
            self._log_lines.clear()
            self._threads = []

        if notifier_names is None:
            notifier_names = ["local"]

        self._append_log(
            f"Force scan — {ecosystem}/{package}@{version}"
            f"  notifiers: {', '.join(notifier_names)}"
        )

        available_collectors = plugins.load_collectors()
        available_notifiers = plugins.load_notifiers()
        available_scanners_map = plugins.load_scanners()

        if ecosystem not in available_collectors:
            msg = f"Unknown ecosystem: {ecosystem}"
            self._append_log(f"ERROR: {msg}")
            with self._lock:
                self._status = "error"
                self._finished_at = datetime.now(timezone.utc)
            log.error(msg)
            return False

        notifiers = []
        for name in notifier_names:
            if name in available_notifiers:
                try:
                    notifiers.append(available_notifiers[name]())
                except Exception as exc:
                    self._append_log(
                        f"WARNING: failed to init notifier '{name}': {exc}"
                    )
                    log.warning("failed to init notifier '%s': %s", name, exc)

        # Build and configure scanner instances
        _scanner_cfg = scanner_config or {}
        _enabled = (
            enabled_scanners
            if enabled_scanners is not None
            else list(available_scanners_map.keys())
        )
        force_scanners: list[Scanner] = []
        for sname in _enabled:
            if sname not in available_scanners_map:
                log.warning("scanner %r not registered — skipping", sname)
                continue
            try:
                s = available_scanners_map[sname]()
                opts = _scanner_cfg.get(sname, {})
                if opts:
                    s.configure(opts)
                force_scanners.append(s)
            except Exception as exc:  # noqa: BLE001
                log.warning("failed to init scanner %r: %s", sname, exc)

        release = Release(
            ecosystem=ecosystem,
            package=package,
            version=version,
            previous_version=None,
            rank=0,
            discovered_at=datetime.now(timezone.utc),
        )

        collector = available_collectors[ecosystem]()

        t = threading.Thread(
            target=self._run_force,
            args=(
                collector,
                release,
                notifiers,
                db_path,
                analyze_timeout,
                analyzer_model,
                analyzer_prompt,
                force_scanners,
            ),
            name=f"force-scan-{ecosystem}-{package}",
            daemon=True,
        )
        with self._lock:
            self._threads.append(t)
        t.start()
        return True

    def status(self) -> dict:
        """Return a JSON-serialisable status snapshot."""
        with self._lock:
            return {
                "status": self._status,
                "ecosystems": list(self._ecosystems),
                "processed": self._processed,
                "errors": self._errors,
                "releases_found": self._releases_found,
                "started_at": (
                    self._started_at.isoformat() if self._started_at else None
                ),
                "finished_at": (
                    self._finished_at.isoformat() if self._finished_at else None
                ),
                "log_lines": list(self._log_lines),
            }

    def history(self) -> list[dict]:
        """Return past scan summaries, newest first (capped at _MAX_HISTORY)."""
        with self._lock:
            return list(reversed(self._history))

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _append_log(self, message: str) -> None:
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
        line = f"[{ts}] {message}"
        with self._lock:
            self._log_lines.append(line)
        log.debug("scan log: %s", message)

    def _record_history(self) -> None:
        """Append current run summary to history ring buffer (call under no lock)."""
        with self._lock:
            entry = {
                "started_at": (
                    self._started_at.isoformat() if self._started_at else None
                ),
                "finished_at": (
                    self._finished_at.isoformat() if self._finished_at else None
                ),
                "ecosystems": list(self._ecosystems),
                "processed": self._processed,
                "releases_found": self._releases_found,
                "errors": self._errors,
                "status": self._status,
            }
            self._history.append(entry)
            if len(self._history) > _MAX_HISTORY:
                self._history = self._history[-_MAX_HISTORY:]

    def _supervisor(
        self,
        collectors: list,
        notifiers: list,
        db_path: Path,
        workers: int,
        analyze_timeout: int,
        analyzer_model: str | None = None,
        analyzer_prompt: str | None = None,
        scanners: list | None = None,
    ) -> None:
        """Run in a background thread; spawns per-ecosystem threads and waits."""
        threads: list[threading.Thread] = []

        for collector in collectors:
            eco = collector.ecosystem
            t = threading.Thread(
                target=self._run_one,
                args=(
                    collector,
                    notifiers,
                    db_path,
                    workers,
                    analyze_timeout,
                    analyzer_model,
                    analyzer_prompt,
                    scanners,
                ),
                name=f"scan-{eco}",
                daemon=True,
            )
            threads.append(t)
            with self._lock:
                self._threads.append(t)

        for t in threads:
            t.start()

        for t in threads:
            t.join()

        with self._lock:
            all_ok = self._errors == 0
            self._status = "idle" if all_ok else "error"
            self._finished_at = datetime.now(timezone.utc)

        self._append_log(
            f"Scan complete — releases_found={self._releases_found}"
            f"  processed={self._processed}  errors={self._errors}"
        )
        log.info(
            "scan complete  releases_found=%d  processed=%d  errors=%d",
            self._releases_found,
            self._processed,
            self._errors,
        )
        self._record_history()

    def _run_one(
        self,
        collector,
        notifiers: list,
        db_path: Path,
        workers: int,
        analyze_timeout: int,
        analyzer_model: str | None = None,
        analyzer_prompt: str | None = None,
        scanners: list | None = None,
    ) -> None:
        """Run a single collector (once=True) in its own thread with its own DB conn."""
        eco = collector.ecosystem
        self._append_log(f"[{eco}] starting poll")
        conn = db_module.init_db(db_path)
        try:
            # Load state so poll() has the correct seq/epoch
            collector.load_state(conn)

            releases = list(collector.poll())
            collector.save_state(conn)

            n = len(releases)
            with self._lock:
                self._releases_found += n
            self._append_log(f"[{eco}] poll complete — {n} release(s) found")
            log.info("[%s] poll yielded %d release(s)", eco, n)

            if releases:
                with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
                    futures = {
                        pool.submit(
                            orchestrator._process_release,
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
                    for future in concurrent.futures.as_completed(futures):
                        r = futures[future]
                        exc = future.exception()
                        if exc is not None:
                            with self._lock:
                                self._errors += 1
                            self._append_log(
                                f"[{eco}] ERROR processing {r.package}@{r.version}: {exc}"
                            )
                            log.exception(
                                "[%s] release %s@%s failed: %s",
                                eco,
                                r.package,
                                r.version,
                                exc,
                            )
                        else:
                            with self._lock:
                                self._processed += 1
                            self._append_log(
                                f"[{eco}] processed {r.package}@{r.version}"
                            )

        except Exception as exc:
            with self._lock:
                self._errors += 1
            self._append_log(f"[{eco}] ERROR: {exc}")
            log.exception("[%s] scan thread raised: %s", eco, exc)
        finally:
            conn.close()

    def _run_force(
        self,
        collector,
        release: Release,
        notifiers: list,
        db_path: Path,
        analyze_timeout: int,
        analyzer_model: str | None = None,
        analyzer_prompt: str | None = None,
        scanners: list | None = None,
    ) -> None:
        """Run the full pipeline for a single forced release."""
        eco = release.ecosystem
        pkg = release.package
        ver = release.version
        self._append_log(f"[{eco}] force-scanning {pkg}@{ver}")
        conn = db_module.init_db(db_path)
        try:
            orchestrator._process_release(
                release,
                collector,
                notifiers,
                conn,
                analyze_timeout,
                analyzer_model=analyzer_model,
                analyzer_prompt=analyzer_prompt,
                scanners=scanners,
            )
            with self._lock:
                self._processed += 1
                self._status = "idle"
                self._finished_at = datetime.now(timezone.utc)
            self._append_log(f"[{eco}] force scan complete — {pkg}@{ver}")
            log.info("[%s] force scan complete  %s@%s", eco, pkg, ver)
        except Exception as exc:
            with self._lock:
                self._errors += 1
                self._status = "error"
                self._finished_at = datetime.now(timezone.utc)
            self._append_log(f"[{eco}] ERROR force scanning {pkg}@{ver}: {exc}")
            log.exception("[%s] force scan %s@%s failed: %s", eco, pkg, ver, exc)
        finally:
            conn.close()
            self._record_history()
