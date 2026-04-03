"""Tests for scm.orchestrator — _process_release, run(), and run_multi()."""

from __future__ import annotations

import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from scm.db import DuplicateRelease, init_db
from scm.models import Alert, Release, StoredArtifact, Verdict
from scm.orchestrator import _process_release, run, run_multi
from scm.storage import DownloadError

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_release(**kwargs) -> Release:
    defaults = dict(
        ecosystem="npm",
        package="express",
        version="5.0.0",
        previous_version=None,
        rank=1,
        discovered_at=datetime.now(timezone.utc),
    )
    return Release(**{**defaults, **kwargs})


def _make_artifact(version: str) -> StoredArtifact:
    return StoredArtifact(
        ecosystem="npm",
        package="express",
        version=version,
        filename=f"express-{version}.tgz",
        path=Path(f"/tmp/express-{version}.tgz"),
        sha256="a" * 64,
        size_bytes=512,
    )


def _make_verdict(release: Release) -> Verdict:
    return Verdict(
        release=release,
        old_artifact=_make_artifact("4.19.0"),
        new_artifact=_make_artifact("5.0.0"),
        result="benign",
        confidence="high",
        summary="clean",
        analysis="no issues",
        analyzed_at=datetime.now(timezone.utc),
    )


def _make_conn(tmp_path: Path) -> sqlite3.Connection:
    return init_db(tmp_path / "test.db")


def _make_collector(previous_version: str | None = "4.19.0") -> MagicMock:
    collector = MagicMock()
    collector.ecosystem = "npm"
    collector.get_previous_version.return_value = previous_version
    collector.poll.return_value = iter([])
    return collector


def _make_notifier(name: str = "local") -> MagicMock:
    notifier = MagicMock()
    notifier.name = name
    notifier.notify.return_value = Alert(
        verdict=MagicMock(),
        notifier=name,
        sent_at=datetime.now(timezone.utc),
        success=True,
        detail="/tmp/report.md",
    )
    return notifier


# ---------------------------------------------------------------------------
# _process_release
# ---------------------------------------------------------------------------


def test_process_release_skips_when_no_previous_version(tmp_path):
    release = _make_release()
    collector = _make_collector(previous_version=None)
    notifier = _make_notifier()
    conn = _make_conn(tmp_path)

    _process_release(release, collector, [notifier], conn, analyze_timeout=30)

    # No download should have been attempted
    notifier.notify.assert_not_called()


def test_process_release_skips_duplicate(tmp_path, mocker):
    release = _make_release()
    collector = _make_collector(previous_version="4.19.0")
    notifier = _make_notifier()
    conn = _make_conn(tmp_path)

    mocker.patch(
        "scm.orchestrator.db_module.upsert_release",
        side_effect=DuplicateRelease("already exists"),
    )

    _process_release(release, collector, [notifier], conn, analyze_timeout=30)
    notifier.notify.assert_not_called()


def test_process_release_full_pipeline(tmp_path, mocker):
    release = _make_release()
    collector = _make_collector(previous_version="4.19.0")
    notifier = _make_notifier()
    conn = _make_conn(tmp_path)

    old_art = _make_artifact("4.19.0")
    new_art = _make_artifact("5.0.0")
    verdict = _make_verdict(release)

    mocker.patch("scm.orchestrator.db_module.upsert_release", return_value=1)
    mocker.patch(
        "scm.orchestrator.storage_module.download_tarball",
        side_effect=[old_art, new_art],
    )
    mocker.patch("scm.orchestrator.db_module.save_artifacts")
    mocker.patch("scm.orchestrator.tempfile.mkdtemp", return_value=str(tmp_path / "td"))
    (tmp_path / "td").mkdir()
    mocker.patch("scm.orchestrator.shutil.rmtree")
    mocker.patch(
        "scm.orchestrator.extractor_module.safe_extract",
        side_effect=[tmp_path / "td" / "old", tmp_path / "td" / "new"],
    )
    mocker.patch(
        "scm.orchestrator.extractor_module.collect_files",
        return_value={},
    )
    mocker.patch("scm.orchestrator.analyzer_module.analyze", return_value=verdict)
    mocker.patch("scm.orchestrator.db_module.save_verdict", return_value=1)
    mocker.patch("scm.orchestrator.db_module.save_alert")

    _process_release(release, collector, [notifier], conn, analyze_timeout=30)

    notifier.notify.assert_called_once()


def test_process_release_download_error_propagates(tmp_path, mocker):
    release = _make_release()
    collector = _make_collector(previous_version="4.19.0")
    notifier = _make_notifier()
    conn = _make_conn(tmp_path)

    mocker.patch("scm.orchestrator.db_module.upsert_release", return_value=1)
    mocker.patch(
        "scm.orchestrator.storage_module.download_tarball",
        side_effect=DownloadError("connection refused"),
    )

    with pytest.raises(DownloadError):
        _process_release(release, collector, [notifier], conn, analyze_timeout=30)

    notifier.notify.assert_not_called()


def test_process_release_notifier_exception_does_not_propagate(tmp_path, mocker):
    """A notifier that raises must not crash the whole release."""
    release = _make_release()
    collector = _make_collector(previous_version="4.19.0")
    notifier = _make_notifier()
    notifier.notify.side_effect = RuntimeError("notifier exploded")
    conn = _make_conn(tmp_path)

    old_art = _make_artifact("4.19.0")
    new_art = _make_artifact("5.0.0")
    verdict = _make_verdict(release)

    mocker.patch("scm.orchestrator.db_module.upsert_release", return_value=1)
    mocker.patch(
        "scm.orchestrator.storage_module.download_tarball",
        side_effect=[old_art, new_art],
    )
    mocker.patch("scm.orchestrator.db_module.save_artifacts")
    mocker.patch(
        "scm.orchestrator.tempfile.mkdtemp", return_value=str(tmp_path / "td2")
    )
    (tmp_path / "td2").mkdir()
    mocker.patch("scm.orchestrator.shutil.rmtree")
    mocker.patch(
        "scm.orchestrator.extractor_module.safe_extract",
        side_effect=[tmp_path / "td2" / "old", tmp_path / "td2" / "new"],
    )
    mocker.patch("scm.orchestrator.extractor_module.collect_files", return_value={})
    mocker.patch("scm.orchestrator.analyzer_module.analyze", return_value=verdict)
    mocker.patch("scm.orchestrator.db_module.save_verdict", return_value=1)

    # Should not raise
    _process_release(release, collector, [notifier], conn, analyze_timeout=30)


def test_process_release_sets_previous_version_on_release(tmp_path, mocker):
    release = _make_release()
    assert release.previous_version is None

    collector = _make_collector(previous_version="4.19.0")
    notifier = _make_notifier()
    conn = _make_conn(tmp_path)

    old_art = _make_artifact("4.19.0")
    new_art = _make_artifact("5.0.0")
    verdict = _make_verdict(release)

    mocker.patch("scm.orchestrator.db_module.upsert_release", return_value=1)
    mocker.patch(
        "scm.orchestrator.storage_module.download_tarball",
        side_effect=[old_art, new_art],
    )
    mocker.patch("scm.orchestrator.db_module.save_artifacts")
    mocker.patch(
        "scm.orchestrator.tempfile.mkdtemp", return_value=str(tmp_path / "td3")
    )
    (tmp_path / "td3").mkdir()
    mocker.patch("scm.orchestrator.shutil.rmtree")
    mocker.patch(
        "scm.orchestrator.extractor_module.safe_extract",
        side_effect=[tmp_path / "td3" / "old", tmp_path / "td3" / "new"],
    )
    mocker.patch("scm.orchestrator.extractor_module.collect_files", return_value={})
    mocker.patch("scm.orchestrator.analyzer_module.analyze", return_value=verdict)
    mocker.patch("scm.orchestrator.db_module.save_verdict", return_value=1)
    mocker.patch("scm.orchestrator.db_module.save_alert")

    _process_release(release, collector, [notifier], conn, analyze_timeout=30)
    assert release.previous_version == "4.19.0"


def test_process_release_force_skips_previous_version_lookup(tmp_path, mocker):
    """When force=True, get_previous_version is never called and a verdict is saved."""
    release = _make_release()
    collector = _make_collector(previous_version=None)  # would normally cause skip
    notifier = _make_notifier()
    conn = _make_conn(tmp_path)

    new_art = _make_artifact("5.0.0")
    verdict = Verdict(
        release=release,
        old_artifact=None,
        new_artifact=new_art,
        result="benign",
        confidence="high",
        summary="clean",
        analysis="no issues",
        analyzed_at=datetime.now(timezone.utc),
    )

    mocker.patch("scm.orchestrator.db_module.upsert_release", return_value=1)
    mocker.patch(
        "scm.orchestrator.storage_module.download_tarball",
        return_value=new_art,
    )
    mocker.patch("scm.orchestrator.db_module.save_artifacts")
    mocker.patch(
        "scm.orchestrator.tempfile.mkdtemp", return_value=str(tmp_path / "td_f")
    )
    (tmp_path / "td_f").mkdir()
    mocker.patch("scm.orchestrator.shutil.rmtree")
    mocker.patch(
        "scm.orchestrator.extractor_module.safe_extract",
        return_value=tmp_path / "td_f" / "new",
    )
    mocker.patch("scm.orchestrator.extractor_module.collect_files", return_value={})
    mocker.patch("scm.orchestrator.analyzer_module.analyze", return_value=verdict)
    mock_save_verdict = mocker.patch(
        "scm.orchestrator.db_module.save_verdict", return_value=1
    )
    mocker.patch("scm.orchestrator.db_module.save_alert")

    _process_release(
        release, collector, [notifier], conn, analyze_timeout=30, force=True
    )

    # get_previous_version must NOT have been called
    collector.get_previous_version.assert_not_called()
    # verdict must still be saved
    mock_save_verdict.assert_called_once()
    notifier.notify.assert_called_once()


# ---------------------------------------------------------------------------
# run()
# ---------------------------------------------------------------------------


def test_run_once_mode_exits_after_one_poll(tmp_path, mocker):
    release = _make_release()
    collector = _make_collector(previous_version="4.19.0")
    collector.poll.return_value = iter([])

    conn = _make_conn(tmp_path)
    mocker.patch("time.sleep")  # should never be called

    run(
        collector=collector,
        notifiers=[],
        conn=conn,
        once=True,
        top_n=10,
        analyze_timeout=30,
        workers=2,
    )

    collector.poll.assert_called_once()
    collector.save_state.assert_called_once()


def test_run_calls_load_state_at_startup(tmp_path, mocker):
    collector = _make_collector()
    collector.poll.return_value = iter([])
    conn = _make_conn(tmp_path)
    mocker.patch("time.sleep")

    run(
        collector=collector,
        notifiers=[],
        conn=conn,
        once=True,
        top_n=10,
        analyze_timeout=30,
    )
    collector.load_state.assert_called_once_with(conn)


def test_run_processes_releases_from_poll(tmp_path, mocker):
    release = _make_release()
    collector = _make_collector(previous_version="4.19.0")
    collector.poll.return_value = iter([release])
    conn = _make_conn(tmp_path)

    mock_process = mocker.patch("scm.orchestrator._process_release")

    run(
        collector=collector,
        notifiers=[],
        conn=conn,
        once=True,
        top_n=10,
        analyze_timeout=30,
        workers=1,
    )

    mock_process.assert_called_once()


def test_run_saves_state_even_on_empty_poll(tmp_path, mocker):
    collector = _make_collector()
    collector.poll.return_value = iter([])
    conn = _make_conn(tmp_path)

    run(
        collector=collector,
        notifiers=[],
        conn=conn,
        once=True,
        top_n=10,
        analyze_timeout=30,
    )
    collector.save_state.assert_called_once_with(conn)


def test_process_release_calls_download_tarball_with_ecosystem(tmp_path, mocker):
    """download_tarball must be called with the release ecosystem, not hardcoded 'npm'."""
    release = _make_release(ecosystem="pypi")
    collector = _make_collector(previous_version="2.31.0")
    collector.ecosystem = "pypi"
    notifier = _make_notifier()
    conn = _make_conn(tmp_path)

    old_art = _make_artifact("2.31.0")
    new_art = _make_artifact("5.0.0")
    verdict = _make_verdict(release)

    mocker.patch("scm.orchestrator.db_module.upsert_release", return_value=1)
    mock_dl = mocker.patch(
        "scm.orchestrator.storage_module.download_tarball",
        side_effect=[old_art, new_art],
    )
    mocker.patch("scm.orchestrator.db_module.save_artifacts")
    mocker.patch(
        "scm.orchestrator.tempfile.mkdtemp", return_value=str(tmp_path / "td4")
    )
    (tmp_path / "td4").mkdir()
    mocker.patch("scm.orchestrator.shutil.rmtree")
    mocker.patch(
        "scm.orchestrator.extractor_module.safe_extract",
        side_effect=[tmp_path / "td4" / "old", tmp_path / "td4" / "new"],
    )
    mocker.patch("scm.orchestrator.extractor_module.collect_files", return_value={})
    mocker.patch("scm.orchestrator.analyzer_module.analyze", return_value=verdict)
    mocker.patch("scm.orchestrator.db_module.save_verdict", return_value=1)
    mocker.patch("scm.orchestrator.db_module.save_alert")

    _process_release(release, collector, [notifier], conn, analyze_timeout=30)

    # Both calls must use ecosystem="pypi"
    for call_args in mock_dl.call_args_list:
        assert call_args.args[0] == "pypi"


# ---------------------------------------------------------------------------
# run_multi()
# ---------------------------------------------------------------------------


def _make_patched_run(events: list[str], eco: str, delay: float = 0.0):
    """Return a side_effect function that records which ecosystem ran."""

    def _fake_run(**kwargs):
        if delay:
            time.sleep(delay)
        events.append(eco)

    return _fake_run


def test_run_multi_no_collectors_is_noop(tmp_path, mocker):
    """run_multi with an empty collector list must return without error."""
    mock_run = mocker.patch("scm.orchestrator.run")
    run_multi(collectors=[], notifiers=[], db_path=tmp_path / "test.db")
    mock_run.assert_not_called()


def test_run_multi_spawns_one_thread_per_collector(tmp_path, mocker):
    """One thread per collector; both complete before run_multi returns."""
    db_path = tmp_path / "test.db"
    init_db(db_path).close()

    npm_collector = MagicMock()
    npm_collector.ecosystem = "npm"
    pypi_collector = MagicMock()
    pypi_collector.ecosystem = "pypi"

    ran: list[str] = []

    def fake_run(collector, **kwargs):
        ran.append(collector.ecosystem)
        collector.save_state(kwargs["conn"])

    mocker.patch("scm.orchestrator.run", side_effect=fake_run)

    run_multi(
        collectors=[npm_collector, pypi_collector],
        notifiers=[],
        db_path=db_path,
        once=True,
        top_n=10,
    )

    assert sorted(ran) == ["npm", "pypi"]


def test_run_multi_each_thread_gets_own_connection(tmp_path, mocker):
    """Each collector thread must open its own DB connection (not share one)."""
    db_path = tmp_path / "test.db"
    init_db(db_path).close()

    connections: list[int] = []

    def fake_run(collector, conn, **kwargs):
        connections.append(id(conn))

    mocker.patch("scm.orchestrator.run", side_effect=fake_run)

    c1, c2 = MagicMock(), MagicMock()
    c1.ecosystem = "npm"
    c2.ecosystem = "pypi"

    run_multi(
        collectors=[c1, c2],
        notifiers=[],
        db_path=db_path,
        once=True,
        top_n=10,
    )

    # Two distinct connection objects
    assert len(connections) == 2
    assert connections[0] != connections[1]


def test_run_multi_threads_run_in_parallel(tmp_path, mocker):
    """Collectors must overlap in time (parallel), not run back-to-back."""
    db_path = tmp_path / "test.db"
    init_db(db_path).close()

    start_times: dict[str, float] = {}
    finish_times: dict[str, float] = {}
    lock = threading.Lock()

    def fake_run(collector, **kwargs):
        eco = collector.ecosystem
        with lock:
            start_times[eco] = time.monotonic()
        time.sleep(0.15)
        with lock:
            finish_times[eco] = time.monotonic()

    mocker.patch("scm.orchestrator.run", side_effect=fake_run)

    c1, c2 = MagicMock(), MagicMock()
    c1.ecosystem = "npm"
    c2.ecosystem = "pypi"

    t0 = time.monotonic()
    run_multi(collectors=[c1, c2], notifiers=[], db_path=db_path, once=True, top_n=10)
    elapsed = time.monotonic() - t0

    # Sequential would take ≥ 0.30 s; parallel should finish in < 0.28 s
    assert elapsed < 0.28, f"run_multi took {elapsed:.3f}s — likely sequential"
    # Both collectors actually ran
    assert "npm" in finish_times and "pypi" in finish_times


def test_run_multi_one_collector_error_does_not_block_others(tmp_path, mocker):
    """An exception in one collector thread must not prevent the other from completing."""
    db_path = tmp_path / "test.db"
    init_db(db_path).close()

    completed: list[str] = []

    def fake_run(collector, **kwargs):
        eco = collector.ecosystem
        if eco == "npm":
            raise RuntimeError("npm exploded")
        completed.append(eco)

    mocker.patch("scm.orchestrator.run", side_effect=fake_run)

    c1, c2 = MagicMock(), MagicMock()
    c1.ecosystem = "npm"
    c2.ecosystem = "pypi"

    # Should not raise
    run_multi(collectors=[c1, c2], notifiers=[], db_path=db_path, once=True, top_n=10)

    assert "pypi" in completed
