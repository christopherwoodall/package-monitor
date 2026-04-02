"""Tests for scm.plugins — load_collectors, load_notifiers, load_scanners."""

from __future__ import annotations

from importlib.metadata import EntryPoint
from unittest.mock import MagicMock, patch

import pytest

from scm.collectors import Collector
from scm.notifiers import Notifier
from scm.scanners import Scanner
from scm.plugins import load_collectors, load_notifiers, load_scanners


# ---------------------------------------------------------------------------
# Helpers — stub Collector / Notifier for fake entry_points
# ---------------------------------------------------------------------------


class _StubCollector(Collector):
    ecosystem = "stub"

    def load_watchlist(self, top_n: int) -> None:
        pass

    def poll(self):
        return iter([])

    def save_state(self, conn) -> None:
        pass

    def load_state(self, conn) -> None:
        pass


class _StubNotifier(Notifier):
    name = "stub"

    def notify(self, verdict, conn):
        pass


class _StubScanner(Scanner):
    name = "stub"

    def scan(self, new_root, changed_files, added_files):
        return ""


def _make_ep(name: str, cls) -> MagicMock:
    ep = MagicMock(spec=EntryPoint)
    ep.name = name
    ep.load.return_value = cls
    return ep


# ---------------------------------------------------------------------------
# load_collectors
# ---------------------------------------------------------------------------


def test_load_collectors_returns_dict_keyed_by_name(mocker):
    ep = _make_ep("stub", _StubCollector)
    mocker.patch(
        "scm.plugins.entry_points",
        return_value=[ep],
    )
    result = load_collectors()
    assert "stub" in result
    assert result["stub"] is _StubCollector


def test_load_collectors_empty_when_no_entry_points(mocker):
    mocker.patch("scm.plugins.entry_points", return_value=[])
    result = load_collectors()
    assert result == {}


def test_load_collectors_skips_broken_entry_point(mocker):
    good_ep = _make_ep("good", _StubCollector)
    bad_ep = MagicMock(spec=EntryPoint)
    bad_ep.name = "bad"
    bad_ep.load.side_effect = ImportError("missing dependency")

    mocker.patch("scm.plugins.entry_points", return_value=[good_ep, bad_ep])
    result = load_collectors()
    assert "good" in result
    assert "bad" not in result


def test_load_collectors_multiple_plugins(mocker):
    class _Another(Collector):
        ecosystem = "another"

        def load_watchlist(self, top_n):
            pass

        def poll(self):
            return iter([])

        def save_state(self, conn):
            pass

        def load_state(self, conn):
            pass

    ep1 = _make_ep("stub", _StubCollector)
    ep2 = _make_ep("another", _Another)
    mocker.patch("scm.plugins.entry_points", return_value=[ep1, ep2])
    result = load_collectors()
    assert len(result) == 2
    assert "stub" in result
    assert "another" in result


# ---------------------------------------------------------------------------
# load_notifiers
# ---------------------------------------------------------------------------


def test_load_notifiers_returns_dict_keyed_by_name(mocker):
    ep = _make_ep("stub", _StubNotifier)
    mocker.patch("scm.plugins.entry_points", return_value=[ep])
    result = load_notifiers()
    assert "stub" in result
    assert result["stub"] is _StubNotifier


def test_load_notifiers_empty_when_no_entry_points(mocker):
    mocker.patch("scm.plugins.entry_points", return_value=[])
    result = load_notifiers()
    assert result == {}


def test_load_notifiers_skips_broken_entry_point(mocker):
    good_ep = _make_ep("good", _StubNotifier)
    bad_ep = MagicMock(spec=EntryPoint)
    bad_ep.name = "bad"
    bad_ep.load.side_effect = ImportError("broken")
    mocker.patch("scm.plugins.entry_points", return_value=[good_ep, bad_ep])
    result = load_notifiers()
    assert "good" in result
    assert "bad" not in result


# ---------------------------------------------------------------------------
# entry_points group argument
# ---------------------------------------------------------------------------


def test_load_collectors_uses_correct_group(mocker):
    mock_ep = mocker.patch("scm.plugins.entry_points", return_value=[])
    load_collectors()
    mock_ep.assert_called_once_with(group="package_monitor.collectors")


def test_load_notifiers_uses_correct_group(mocker):
    mock_ep = mocker.patch("scm.plugins.entry_points", return_value=[])
    load_notifiers()
    mock_ep.assert_called_once_with(group="package_monitor.notifiers")


# ---------------------------------------------------------------------------
# load_scanners
# ---------------------------------------------------------------------------


def test_load_scanners_returns_dict_keyed_by_name(mocker):
    ep = _make_ep("stub", _StubScanner)
    mocker.patch("scm.plugins.entry_points", return_value=[ep])
    result = load_scanners()
    assert "stub" in result
    assert result["stub"] is _StubScanner


def test_load_scanners_empty_when_no_entry_points(mocker):
    mocker.patch("scm.plugins.entry_points", return_value=[])
    result = load_scanners()
    assert result == {}


def test_load_scanners_skips_broken_entry_point(mocker):
    good_ep = _make_ep("good", _StubScanner)
    bad_ep = MagicMock(spec=EntryPoint)
    bad_ep.name = "bad"
    bad_ep.load.side_effect = ImportError("missing dependency")

    mocker.patch("scm.plugins.entry_points", return_value=[good_ep, bad_ep])
    result = load_scanners()
    assert "good" in result
    assert "bad" not in result


def test_load_scanners_uses_correct_group(mocker):
    mock_ep = mocker.patch("scm.plugins.entry_points", return_value=[])
    load_scanners()
    mock_ep.assert_called_once_with(group="package_monitor.scanners")
