"""Tests for scm.dashboard.scanner.ScanManager."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from scm.dashboard.scanner import ScanManager, _MAX_HISTORY

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mgr() -> ScanManager:
    return ScanManager()


# ---------------------------------------------------------------------------
# history()
# ---------------------------------------------------------------------------


def test_history_empty_initially():
    mgr = _make_mgr()
    assert mgr.history() == []


def test_history_records_entry_after_scan_completion():
    mgr = _make_mgr()
    # Directly invoke _record_history after setting internal state
    from datetime import datetime, timezone

    mgr._status = "idle"
    mgr._ecosystems = ["npm"]
    mgr._processed = 2
    mgr._releases_found = 2
    mgr._errors = 0
    mgr._started_at = datetime(2026, 4, 1, tzinfo=timezone.utc)
    mgr._finished_at = datetime(2026, 4, 1, 0, 1, tzinfo=timezone.utc)

    mgr._record_history()

    entries = mgr.history()
    assert len(entries) == 1
    assert entries[0]["ecosystems"] == ["npm"]
    assert entries[0]["processed"] == 2
    assert entries[0]["releases_found"] == 2
    assert entries[0]["errors"] == 0
    assert entries[0]["status"] == "idle"


def test_history_is_capped_at_max_history():
    mgr = _make_mgr()
    for i in range(_MAX_HISTORY + 5):
        mgr._processed = i
        mgr._record_history()

    assert len(mgr.history()) == _MAX_HISTORY


def test_history_is_newest_first():
    mgr = _make_mgr()
    for i in range(3):
        mgr._processed = i
        mgr._record_history()

    # history() returns newest first (reversed from insertion order)
    entries = mgr.history()
    assert entries[0]["processed"] == 2
    assert entries[1]["processed"] == 1
    assert entries[2]["processed"] == 0


# ---------------------------------------------------------------------------
# status() — releases_found field
# ---------------------------------------------------------------------------


def test_status_includes_releases_found():
    mgr = _make_mgr()
    s = mgr.status()
    assert "releases_found" in s
    assert s["releases_found"] == 0


def test_status_releases_found_updated_during_scan():
    mgr = _make_mgr()
    with mgr._lock:
        mgr._releases_found = 7
    assert mgr.status()["releases_found"] == 7


# ---------------------------------------------------------------------------
# start() — notifier_names parameter
# ---------------------------------------------------------------------------


def test_start_uses_local_notifier_by_default(tmp_path):
    mgr = _make_mgr()

    mock_collector_cls = MagicMock()
    mock_collector = MagicMock()
    mock_collector.ecosystem = "npm"
    mock_collector_cls.return_value = mock_collector

    mock_notifier_cls = MagicMock()
    mock_notifier = MagicMock()
    mock_notifier.name = "local"
    mock_notifier_cls.return_value = mock_notifier

    with (
        patch("scm.dashboard.scanner.plugins") as mock_plugins,
        patch("scm.dashboard.scanner.threading.Thread") as mock_thread,
    ):
        mock_plugins.load_collectors.return_value = {"npm": mock_collector_cls}
        mock_plugins.load_notifiers.return_value = {"local": mock_notifier_cls}
        mock_thread_instance = MagicMock()
        mock_thread.return_value = mock_thread_instance

        db_path = tmp_path / "test.db"
        result = mgr.start(db_path=db_path, ecosystems=["npm"], top_n=10)

    assert result is True
    # local notifier should have been instantiated
    mock_notifier_cls.assert_called_once()


def test_start_rejects_unknown_ecosystem(tmp_path):
    mgr = _make_mgr()

    with patch("scm.dashboard.scanner.plugins") as mock_plugins:
        mock_plugins.load_collectors.return_value = {"npm": MagicMock()}
        mock_plugins.load_notifiers.return_value = {}

        result = mgr.start(
            db_path=tmp_path / "test.db",
            ecosystems=["rubygems"],
            top_n=10,
        )

    assert result is False
    assert mgr.status()["status"] == "error"


def test_start_returns_false_when_already_running(tmp_path):
    mgr = _make_mgr()
    with mgr._lock:
        mgr._status = "running"

    result = mgr.start(
        db_path=tmp_path / "test.db",
        ecosystems=["npm"],
        top_n=10,
    )
    assert result is False


# ---------------------------------------------------------------------------
# force_scan_package() — basic guards
# ---------------------------------------------------------------------------


def test_force_scan_returns_false_when_already_running(tmp_path):
    mgr = _make_mgr()
    with mgr._lock:
        mgr._status = "running"

    result = mgr.force_scan_package(
        db_path=tmp_path / "test.db",
        ecosystem="npm",
        package="lodash",
        version="4.17.22",
    )
    assert result is False


def test_force_scan_returns_false_for_unknown_ecosystem(tmp_path):
    mgr = _make_mgr()

    with patch("scm.dashboard.scanner.plugins") as mock_plugins:
        mock_plugins.load_collectors.return_value = {}
        mock_plugins.load_notifiers.return_value = {}

        result = mgr.force_scan_package(
            db_path=tmp_path / "test.db",
            ecosystem="rubygems",
            package="rails",
            version="7.0.0",
        )

    assert result is False
    assert mgr.status()["status"] == "error"
