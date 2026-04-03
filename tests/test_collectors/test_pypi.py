"""Tests for scm.collectors.pypi.PypiCollector (XMLRPC serial-based)."""

from __future__ import annotations

import io
import json
import sqlite3
import time
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from scm.collectors import WatchlistError
from scm.collectors.pypi import (
    PYPI_GAP_RESET_THRESHOLD,
    PYPI_SERIALS_PER_DAY,
    PypiCollector,
)
from scm.models import Release

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_collector() -> PypiCollector:
    return PypiCollector()


class _FakeResp:
    def __init__(self, data: bytes):
        self._buf = io.BytesIO(data)

    def read(self, n: int = -1) -> bytes:
        return self._buf.read(n)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass


def _top_packages_json(packages: list[str]) -> bytes:
    """Build a top-pypi-packages JSON payload."""
    rows = [
        {"project": p, "download_count": 1_000_000 - i} for i, p in enumerate(packages)
    ]
    return json.dumps({"rows": rows}).encode()


def _pypi_version_meta(
    package: str,
    version: str,
    upload_time: str = "2024-05-01T12:00:00.000000Z",
    packagetype: str = "sdist",
    filename: str | None = None,
) -> bytes:
    if filename is None:
        ext = ".tar.gz" if packagetype == "sdist" else "-py3-none-any.whl"
        filename = f"{package}-{version}{ext}"
    meta = {
        "info": {"name": package, "version": version},
        "urls": [
            {
                "packagetype": packagetype,
                "filename": filename,
                "url": f"https://files.pythonhosted.org/{filename}",
                "digests": {"sha256": "a" * 64},
                "upload_time_iso_8601": upload_time,
                "upload_time": upload_time.rstrip("Z").replace("T", " "),
            }
        ],
    }
    return json.dumps(meta).encode()


def _pypi_package_meta(
    package: str,
    versions: dict[str, str],  # version -> upload_time_iso_8601
) -> bytes:
    """Build a PyPI /pypi/{package}/json response used by get_previous_version."""
    releases = {
        ver: [
            {
                "packagetype": "sdist",
                "filename": f"{package}-{ver}.tar.gz",
                "upload_time_iso_8601": ts,
                "upload_time": ts.rstrip("Z").replace("T", " "),
            }
        ]
        for ver, ts in versions.items()
    }
    meta = {
        "info": {"name": package, "version": max(versions)},
        "releases": releases,
    }
    return json.dumps(meta).encode()


def _make_changelog_entry(
    package: str,
    version: str,
    serial: int,
    action: str = "new release",
    timestamp: int = 1_700_000_000,
) -> tuple:
    """Return a (package, version, timestamp, action, serial) tuple."""
    return (package, version, timestamp, action, serial)


def _mock_xmlrpc(mocker, head_serial: int, entries: list[tuple]) -> MagicMock:
    """Patch PypiCollector._xmlrpc_client to return a mock ServerProxy."""
    mock_client = MagicMock()
    mock_client.changelog_last_serial.return_value = head_serial
    mock_client.changelog_since_serial.return_value = entries
    mocker.patch.object(PypiCollector, "_xmlrpc_client", return_value=mock_client)
    return mock_client


# ---------------------------------------------------------------------------
# load_watchlist
# ---------------------------------------------------------------------------


def test_load_watchlist_builds_rank_map(mocker):
    packages = ["boto3", "requests", "numpy"]
    mocker.patch(
        "urllib.request.urlopen",
        return_value=_FakeResp(_top_packages_json(packages)),
    )
    c = _make_collector()
    c.load_watchlist(top_n=3)

    assert c._watchlist["boto3"] == 1
    assert c._watchlist["requests"] == 2
    assert c._watchlist["numpy"] == 3


def test_load_watchlist_respects_top_n(mocker):
    packages = [f"pkg{i}" for i in range(50)]
    mocker.patch(
        "urllib.request.urlopen",
        return_value=_FakeResp(_top_packages_json(packages)),
    )
    c = _make_collector()
    c.load_watchlist(top_n=10)
    assert len(c._watchlist) == 10


def test_load_watchlist_lowercases_names(mocker):
    packages = ["Boto3", "Requests"]
    mocker.patch(
        "urllib.request.urlopen",
        return_value=_FakeResp(_top_packages_json(packages)),
    )
    c = _make_collector()
    c.load_watchlist(top_n=5)
    assert "boto3" in c._watchlist
    assert "requests" in c._watchlist
    assert "Boto3" not in c._watchlist


def test_load_watchlist_raises_on_network_failure(mocker):
    mocker.patch(
        "urllib.request.urlopen",
        side_effect=Exception("connection refused"),
    )
    c = _make_collector()
    with pytest.raises(WatchlistError, match="failed to fetch"):
        c.load_watchlist(top_n=10)


def test_load_watchlist_raises_on_bad_json_shape(mocker):
    mocker.patch(
        "urllib.request.urlopen",
        return_value=_FakeResp(json.dumps({"bad": "shape"}).encode()),
    )
    c = _make_collector()
    with pytest.raises(WatchlistError, match="unexpected"):
        c.load_watchlist(top_n=10)


def test_load_watchlist_top_n_zero_sets_watchlist_none(mocker):
    """load_watchlist(0) must set _watchlist=None without making any network calls."""
    urlopen_mock = mocker.patch("urllib.request.urlopen")
    c = _make_collector()
    c.load_watchlist(top_n=0)
    assert c._watchlist is None
    urlopen_mock.assert_not_called()


# ---------------------------------------------------------------------------
# poll — first run (serial == 0)
# ---------------------------------------------------------------------------


def test_poll_first_run_seeds_30_day_lookback(mocker):
    """On first run (_last_serial == 0) poll sets _last_serial to ~30 days back."""
    head = 1_000_000
    c = _make_collector()
    c._watchlist = {}

    mock_client = _mock_xmlrpc(mocker, head_serial=head, entries=[])
    mocker.patch("urllib.request.urlopen")  # no HTTP calls expected

    list(c.poll())

    expected_seed = head - PYPI_SERIALS_PER_DAY * 30
    mock_client.changelog_since_serial.assert_called_once_with(expected_seed)
    # After the cycle serial advances to HEAD
    assert c._last_serial == head


def test_poll_first_run_does_not_trigger_gap_reset(mocker):
    """Gap reset must not fire on first run even though gap > threshold."""
    head = 2_000_000  # gap from 0 is enormous
    c = _make_collector()
    c._last_serial = 0
    c._watchlist = {}

    mock_client = _mock_xmlrpc(mocker, head_serial=head, entries=[])
    mocker.patch("urllib.request.urlopen")

    list(c.poll())

    # changelog_since_serial should have been called (not skipped by gap reset)
    mock_client.changelog_since_serial.assert_called_once()


# ---------------------------------------------------------------------------
# poll — gap reset
# ---------------------------------------------------------------------------


def test_poll_gap_reset_yields_nothing_and_updates_serial(mocker):
    """When gap > PYPI_GAP_RESET_THRESHOLD, poll yields nothing and resets serial."""
    head = 500_000
    c = _make_collector()
    c._last_serial = head - PYPI_GAP_RESET_THRESHOLD - 1  # just over threshold

    mock_client = _mock_xmlrpc(mocker, head_serial=head, entries=[])

    releases = list(c.poll())

    assert releases == []
    assert c._last_serial == head
    mock_client.changelog_since_serial.assert_not_called()


def test_poll_gap_reset_exactly_at_threshold_does_not_reset(mocker):
    """Gap equal to threshold is allowed; only strictly greater triggers reset."""
    head = 500_000
    c = _make_collector()
    c._last_serial = head - PYPI_GAP_RESET_THRESHOLD  # exactly at threshold
    c._watchlist = {}

    mock_client = _mock_xmlrpc(mocker, head_serial=head, entries=[])
    mocker.patch("urllib.request.urlopen")

    list(c.poll())

    mock_client.changelog_since_serial.assert_called_once()


# ---------------------------------------------------------------------------
# poll — normal flow
# ---------------------------------------------------------------------------


def test_poll_yields_new_release_for_watchlisted_package(mocker):
    c = _make_collector()
    c._watchlist = {"requests": 1}
    c._last_serial = 999_000

    head = 999_100
    entries = [_make_changelog_entry("requests", "2.32.0", 999_050)]
    version_meta = _pypi_version_meta("requests", "2.32.0")

    _mock_xmlrpc(mocker, head_serial=head, entries=entries)
    mocker.patch("urllib.request.urlopen", return_value=_FakeResp(version_meta))

    releases = list(c.poll())

    assert len(releases) == 1
    assert releases[0].package == "requests"
    assert releases[0].version == "2.32.0"
    assert releases[0].ecosystem == "pypi"
    assert releases[0].rank == 1
    assert c._last_serial == head


def test_poll_skips_packages_not_in_watchlist(mocker):
    c = _make_collector()
    c._watchlist = {"requests": 1}
    c._last_serial = 999_000

    entries = [_make_changelog_entry("django", "5.0.0", 999_050)]
    _mock_xmlrpc(mocker, head_serial=999_100, entries=entries)
    mocker.patch("urllib.request.urlopen")

    releases = list(c.poll())
    assert releases == []


def test_poll_new_mode_watchlist_none_passes_all_packages(mocker):
    """When _watchlist is None (--new mode), packages outside the top list are yielded."""
    c = _make_collector()
    c._watchlist = None  # --new mode
    c._last_serial = 999_000

    entries = [_make_changelog_entry("obscure-package", "0.1.0", 999_050)]
    version_meta = _pypi_version_meta("obscure-package", "0.1.0")

    _mock_xmlrpc(mocker, head_serial=999_100, entries=entries)
    mocker.patch("urllib.request.urlopen", return_value=_FakeResp(version_meta))

    releases = list(c.poll())

    # Package is NOT in any watchlist but must still be yielded in --new mode
    assert len(releases) == 1
    assert releases[0].package == "obscure-package"
    assert releases[0].rank == 0  # no rank in --new mode


def test_poll_new_mode_new_limit_caps_releases(mocker):
    """When _watchlist is None and _new_limit=2, only 2 releases are yielded."""
    c = _make_collector()
    c._watchlist = None  # --new mode
    c._new_limit = 2
    c._last_serial = 999_000

    # 5 distinct packages in the changelog
    entries = [_make_changelog_entry(f"pkg{i}", "1.0.0", 999_050 + i) for i in range(5)]

    _mock_xmlrpc(mocker, head_serial=999_100, entries=entries)
    # Each urlopen call needs a fresh FakeResp so the BytesIO isn't exhausted
    mocker.patch(
        "urllib.request.urlopen",
        side_effect=lambda *a, **kw: _FakeResp(_pypi_version_meta("pkg0", "1.0.0")),
    )

    releases = list(c.poll())

    assert len(releases) == 2


def test_poll_deduplicates_changelog_entries(mocker):
    """Same (package, version) appearing twice should yield only one Release."""
    c = _make_collector()
    c._watchlist = {"requests": 1}
    c._last_serial = 999_000

    entries = [
        _make_changelog_entry("requests", "2.32.0", 999_050),
        _make_changelog_entry("requests", "2.32.0", 999_051),  # duplicate
    ]
    version_meta = _pypi_version_meta("requests", "2.32.0")

    _mock_xmlrpc(mocker, head_serial=999_100, entries=entries)
    mocker.patch("urllib.request.urlopen", return_value=_FakeResp(version_meta))

    releases = list(c.poll())
    assert len(releases) == 1


def test_poll_skips_non_new_release_actions(mocker):
    """Only action == 'new release' should be processed."""
    c = _make_collector()
    c._watchlist = {"requests": 1}
    c._last_serial = 999_000

    entries = [
        _make_changelog_entry("requests", "2.32.0", 999_050, action="add source file"),
        _make_changelog_entry("requests", "2.32.0", 999_051, action="remove"),
    ]
    _mock_xmlrpc(mocker, head_serial=999_100, entries=entries)
    mocker.patch("urllib.request.urlopen")

    releases = list(c.poll())
    assert releases == []


def test_poll_skips_wheel_only_release(mocker):
    c = _make_collector()
    c._watchlist = {"requests": 1}
    c._last_serial = 999_000

    entries = [_make_changelog_entry("requests", "2.32.0", 999_050)]
    wheel_meta = _pypi_version_meta("requests", "2.32.0", packagetype="bdist_wheel")

    _mock_xmlrpc(mocker, head_serial=999_100, entries=entries)
    mocker.patch("urllib.request.urlopen", return_value=_FakeResp(wheel_meta))

    releases = list(c.poll())
    assert releases == []


def test_poll_advances_serial_to_head_after_completion(mocker):
    c = _make_collector()
    c._watchlist = {}
    c._last_serial = 999_000

    _mock_xmlrpc(mocker, head_serial=999_500, entries=[])
    mocker.patch("urllib.request.urlopen")

    list(c.poll())
    assert c._last_serial == 999_500


def test_poll_handles_head_serial_failure_gracefully(mocker):
    c = _make_collector()
    c._watchlist = {"requests": 1}
    c._last_serial = 999_000

    mock_client = MagicMock()
    mock_client.changelog_last_serial.side_effect = Exception("XMLRPC timeout")
    mocker.patch.object(PypiCollector, "_xmlrpc_client", return_value=mock_client)

    releases = list(c.poll())
    assert releases == []
    # serial should not change
    assert c._last_serial == 999_000


def test_poll_handles_changelog_failure_gracefully(mocker):
    c = _make_collector()
    c._watchlist = {"requests": 1}
    c._last_serial = 999_000

    mock_client = MagicMock()
    mock_client.changelog_last_serial.return_value = 999_100
    mock_client.changelog_since_serial.side_effect = Exception("XMLRPC error")
    mocker.patch.object(PypiCollector, "_xmlrpc_client", return_value=mock_client)

    releases = list(c.poll())
    assert releases == []


def test_poll_uses_canonical_name_from_metadata(mocker):
    """The Release.package should use the canonical name from JSON info, not the
    lowercased version from the changelog."""
    c = _make_collector()
    c._watchlist = {"requests": 1}
    c._last_serial = 999_000

    entries = [_make_changelog_entry("requests", "2.32.0", 999_050)]
    # canonical name has mixed case in the JSON info block
    version_meta_bytes = json.dumps(
        {
            "info": {"name": "Requests", "version": "2.32.0"},
            "urls": [
                {
                    "packagetype": "sdist",
                    "filename": "Requests-2.32.0.tar.gz",
                    "url": "https://files.pythonhosted.org/Requests-2.32.0.tar.gz",
                    "digests": {"sha256": "a" * 64},
                    "upload_time_iso_8601": "2024-05-01T12:00:00.000000Z",
                }
            ],
        }
    ).encode()

    _mock_xmlrpc(mocker, head_serial=999_100, entries=entries)
    mocker.patch("urllib.request.urlopen", return_value=_FakeResp(version_meta_bytes))

    releases = list(c.poll())
    assert len(releases) == 1
    assert releases[0].package == "Requests"


# ---------------------------------------------------------------------------
# get_previous_version
# ---------------------------------------------------------------------------


def test_get_previous_version_returns_preceding_version(mocker):
    c = _make_collector()
    pkg_meta = _pypi_package_meta(
        "requests",
        {
            "2.28.0": "2022-06-01T00:00:00.000000Z",
            "2.29.0": "2023-01-01T00:00:00.000000Z",
            "2.32.0": "2024-05-01T00:00:00.000000Z",
        },
    )
    mocker.patch("urllib.request.urlopen", return_value=_FakeResp(pkg_meta))
    prev = c.get_previous_version("requests", "2.32.0")
    assert prev == "2.29.0"


def test_get_previous_version_returns_none_for_first_version(mocker):
    c = _make_collector()
    pkg_meta = _pypi_package_meta(
        "requests",
        {"2.28.0": "2022-06-01T00:00:00.000000Z"},
    )
    mocker.patch("urllib.request.urlopen", return_value=_FakeResp(pkg_meta))
    prev = c.get_previous_version("requests", "2.28.0")
    assert prev is None


def test_get_previous_version_returns_none_for_unknown_version(mocker):
    c = _make_collector()
    pkg_meta = _pypi_package_meta(
        "requests",
        {"2.28.0": "2022-06-01T00:00:00.000000Z"},
    )
    mocker.patch("urllib.request.urlopen", return_value=_FakeResp(pkg_meta))
    prev = c.get_previous_version("requests", "99.0.0")
    assert prev is None


def test_get_previous_version_returns_none_on_network_error(mocker):
    c = _make_collector()
    mocker.patch(
        "urllib.request.urlopen",
        side_effect=Exception("network error"),
    )
    prev = c.get_previous_version("requests", "2.32.0")
    assert prev is None


# ---------------------------------------------------------------------------
# save_state / load_state
# ---------------------------------------------------------------------------


def test_save_and_load_state_roundtrip(db_conn):
    c = _make_collector()
    c._last_serial = 1_234_567
    c.save_state(db_conn)

    c2 = _make_collector()
    c2.load_state(db_conn)
    assert c2._last_serial == 1_234_567


def test_load_state_defaults_to_zero_when_no_state(db_conn):
    c = _make_collector()
    c.load_state(db_conn)
    assert c._last_serial == 0
