"""Tests for scm.collectors.npm.NpmCollector."""

from __future__ import annotations

import io
import json
import sqlite3
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from scm.collectors import WatchlistError
from scm.collectors.npm import NpmCollector
from scm.models import Release


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_collector() -> NpmCollector:
    return NpmCollector()


def _make_tgz_with_counts(counts: dict[str, int]) -> bytes:
    """Build an in-memory .tgz containing package/counts.json."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        data = json.dumps(counts).encode()
        info = tarfile.TarInfo(name="package/counts.json")
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


class _FakeResp:
    def __init__(self, data: bytes):
        self._buf = io.BytesIO(data)

    def read(self, n: int = -1) -> bytes:
        return self._buf.read(n)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass


# ---------------------------------------------------------------------------
# load_watchlist
# ---------------------------------------------------------------------------


def test_load_watchlist_builds_rank_map(mocker, tmp_path):
    counts = {"lodash": 3_000_000, "express": 2_000_000, "react": 1_000_000}
    tgz_data = _make_tgz_with_counts(counts)

    meta_payload = json.dumps(
        {"dist": {"tarball": "http://example.com/dc.tgz"}}
    ).encode()

    call_count = 0

    def fake_urlopen(req_or_url, timeout=None):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return _FakeResp(meta_payload)
        return _FakeResp(tgz_data)

    mocker.patch("urllib.request.urlopen", side_effect=fake_urlopen)

    c = _make_collector()
    c.load_watchlist(top_n=3)

    assert "lodash" in c._watchlist
    assert c._watchlist["lodash"] == 1  # rank 1 = highest downloads
    assert c._watchlist["express"] == 2
    assert c._watchlist["react"] == 3


def test_load_watchlist_respects_top_n(mocker):
    counts = {f"pkg{i}": 1000 - i for i in range(100)}
    tgz_data = _make_tgz_with_counts(counts)
    meta_payload = json.dumps({"dist": {"tarball": "http://x.com/dc.tgz"}}).encode()

    call_count = 0

    def fake_urlopen(req_or_url, timeout=None):
        nonlocal call_count
        call_count += 1
        return _FakeResp(meta_payload) if call_count == 1 else _FakeResp(tgz_data)

    mocker.patch("urllib.request.urlopen", side_effect=fake_urlopen)

    c = _make_collector()
    c.load_watchlist(top_n=10)
    assert len(c._watchlist) == 10


def test_load_watchlist_raises_watchlist_error_on_network_failure(mocker):
    mocker.patch(
        "urllib.request.urlopen",
        side_effect=Exception("connection refused"),
    )
    c = _make_collector()
    with pytest.raises(WatchlistError, match="failed to fetch"):
        c.load_watchlist(top_n=100)


def test_load_watchlist_raises_watchlist_error_on_missing_counts_json(mocker):
    # Tarball without package/counts.json
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        data = b"nothing"
        info = tarfile.TarInfo(name="package/README.md")
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))
    tgz_data = buf.getvalue()

    meta_payload = json.dumps({"dist": {"tarball": "http://x.com/dc.tgz"}}).encode()
    call_count = 0

    def fake_urlopen(req_or_url, timeout=None):
        nonlocal call_count
        call_count += 1
        return _FakeResp(meta_payload) if call_count == 1 else _FakeResp(tgz_data)

    mocker.patch("urllib.request.urlopen", side_effect=fake_urlopen)
    c = _make_collector()
    with pytest.raises(WatchlistError):
        c.load_watchlist(top_n=10)


def test_load_watchlist_top_n_zero_sets_watchlist_none(mocker):
    """load_watchlist(0) must set _watchlist=None without making any network calls."""
    urlopen_mock = mocker.patch("urllib.request.urlopen")
    c = _make_collector()
    c.load_watchlist(top_n=0)
    assert c._watchlist is None
    urlopen_mock.assert_not_called()


# ---------------------------------------------------------------------------
# poll — gap reset
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# poll — gap reset
# ---------------------------------------------------------------------------


def test_poll_gap_reset_first_run_preserves_epoch(mocker):
    """On first run (_last_seq==0) gap reset advances seq to HEAD and falls through
    to the epoch scan. With an empty watchlist no releases are yielded.
    The epoch is updated to cycle_start at the end (normal end-of-poll behaviour)."""
    import time

    c = _make_collector()
    c._last_seq = 0
    c._poll_epoch = 1_700_000_000.0  # simulates 30-day seed from load_state
    c._watchlist = {}  # empty watchlist — epoch scan loop is a no-op

    head_resp = _FakeResp(json.dumps({"update_seq": 100_000}).encode())
    mocker.patch("urllib.request.urlopen", return_value=head_resp)

    before = time.time()
    releases = list(c.poll())
    after = time.time()

    assert releases == []
    assert c._last_seq == 100_000
    # Epoch is updated to cycle_start after the (empty) epoch scan completes —
    # the original 30-day seed is no longer needed once the scan has run.
    assert before <= c._poll_epoch <= after


def test_poll_gap_reset_first_run_falls_through_to_epoch_scan(mocker):
    """On first run, after gap reset the collector scans watchlisted packages via epoch.

    Verifies the core fix: gap reset no longer returns early on first run;
    instead it calls _detect_new_versions for every watchlisted package and
    yields releases found within the epoch window.
    """
    c = _make_collector()
    c._last_seq = 0
    c._poll_epoch = 1_700_000_000.0  # epoch ~30 days ago
    c._watchlist = {"lodash": 1}

    head_resp = _FakeResp(json.dumps({"update_seq": 100_000}).encode())
    # packument for lodash: has a version published AFTER epoch 1_700_000_000
    packument_data = _packument_with_versions(
        {"4.17.22": "2023-11-16T00:00:00.000Z"}  # 2023-11-16 > 2023-11-15T00:26:40Z
    )

    call_count = 0

    def fake_urlopen(req_or_url, timeout=None):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return head_resp
        return _FakeResp(packument_data)

    mocker.patch("urllib.request.urlopen", side_effect=fake_urlopen)

    releases = list(c.poll())

    # Must yield lodash@4.17.22 from the epoch scan
    assert len(releases) == 1
    assert releases[0].package == "lodash"
    assert releases[0].version == "4.17.22"
    assert releases[0].ecosystem == "npm"
    # seq advanced to HEAD
    assert c._last_seq == 100_000
    # epoch updated to cycle_start (not the old seed)
    assert c._poll_epoch > 1_700_000_000.0


def test_poll_gap_reset_genuine_stall_updates_epoch(mocker):
    """When last_seq > 0 but gap is huge (genuine stall), epoch IS reset to now."""
    import time

    c = _make_collector()
    c._last_seq = 50_000  # non-zero — genuine stall, not first run
    c._poll_epoch = 1_700_000_000.0

    head_resp = _FakeResp(json.dumps({"update_seq": 150_000}).encode())
    mocker.patch("urllib.request.urlopen", return_value=head_resp)

    before = time.time()
    releases = list(c.poll())
    after = time.time()

    assert releases == []
    assert c._last_seq == 150_000
    # epoch should now be ~now (reset to cycle_start)
    assert before <= c._poll_epoch <= after


# ---------------------------------------------------------------------------
# poll — normal flow
# ---------------------------------------------------------------------------


def _packument_with_versions(versions: dict[str, str]) -> bytes:
    """versions = {ver: iso_timestamp}"""
    time_map = {
        **versions,
        "created": "2020-01-01T00:00:00.000Z",
        "modified": "2024-01-01T00:00:00.000Z",
    }
    return json.dumps({"time": time_map}).encode()


def test_poll_yields_new_releases_for_watchlisted_packages(mocker):
    c = _make_collector()
    c._last_seq = 1_000
    c._poll_epoch = 0.0
    c._watchlist = {"express": 1}

    # head_seq just slightly ahead — no gap reset
    head_seq = 1_001

    changes_data = {
        "results": [{"id": "express", "seq": 1_001, "changes": []}],
        "last_seq": 1_001,
    }
    # packument: express has a version published after epoch 0
    packument_data = _packument_with_versions({"5.0.0": "2024-06-01T12:00:00.000Z"})

    call_count = 0

    def fake_urlopen(req_or_url, timeout=None):
        nonlocal call_count
        call_count += 1
        url = getattr(req_or_url, "full_url", None) or str(req_or_url)
        if "registry/_changes" not in url and call_count == 1:
            # head seq
            return _FakeResp(json.dumps({"update_seq": head_seq}).encode())
        if "_changes" in url:
            return _FakeResp(json.dumps(changes_data).encode())
        # packument
        return _FakeResp(packument_data)

    mocker.patch("urllib.request.urlopen", side_effect=fake_urlopen)

    releases = list(c.poll())
    assert len(releases) == 1
    assert releases[0].package == "express"
    assert releases[0].version == "5.0.0"
    assert releases[0].ecosystem == "npm"


def test_poll_ignores_design_documents(mocker):
    c = _make_collector()
    c._last_seq = 1_000
    c._poll_epoch = 0.0
    c._watchlist = {"_design/app": 1}  # should never match

    head_seq = 1_001
    changes_data = {
        "results": [{"id": "_design/app", "seq": 1_001, "changes": []}],
        "last_seq": 1_001,
    }

    call_count = 0

    def fake_urlopen(req_or_url, timeout=None):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return _FakeResp(json.dumps({"update_seq": head_seq}).encode())
        return _FakeResp(json.dumps(changes_data).encode())

    mocker.patch("urllib.request.urlopen", side_effect=fake_urlopen)
    releases = list(c.poll())
    assert releases == []


def test_poll_ignores_packages_not_in_watchlist(mocker):
    c = _make_collector()
    c._last_seq = 1_000
    c._poll_epoch = 0.0
    c._watchlist = {"lodash": 1}

    head_seq = 1_001
    changes_data = {
        "results": [{"id": "not-lodash-xyz", "seq": 1_001, "changes": []}],
        "last_seq": 1_001,
    }

    call_count = 0

    def fake_urlopen(req_or_url, timeout=None):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return _FakeResp(json.dumps({"update_seq": head_seq}).encode())
        return _FakeResp(json.dumps(changes_data).encode())

    mocker.patch("urllib.request.urlopen", side_effect=fake_urlopen)
    releases = list(c.poll())
    assert releases == []


def test_poll_new_mode_watchlist_none_passes_all_packages(mocker):
    """When _watchlist is None (--new mode), all packages in the changes feed are yielded."""
    c = _make_collector()
    c._last_seq = 1_000
    c._poll_epoch = 0.0
    c._watchlist = None  # --new mode

    head_seq = 1_001
    changes_data = {
        "results": [
            {"id": "some-obscure-pkg", "seq": 1_001, "changes": []},
        ],
        "last_seq": 1_001,
    }
    packument_data = _packument_with_versions({"1.0.0": "2024-06-01T12:00:00.000Z"})

    call_count = 0

    def fake_urlopen(req_or_url, timeout=None):
        nonlocal call_count
        call_count += 1
        url = getattr(req_or_url, "full_url", None) or str(req_or_url)
        if call_count == 1:
            return _FakeResp(json.dumps({"update_seq": head_seq}).encode())
        if "_changes" in url:
            return _FakeResp(json.dumps(changes_data).encode())
        return _FakeResp(packument_data)

    mocker.patch("urllib.request.urlopen", side_effect=fake_urlopen)

    releases = list(c.poll())

    # Package is NOT in any watchlist but must still be yielded in --new mode
    assert len(releases) == 1
    assert releases[0].package == "some-obscure-pkg"
    assert releases[0].rank == 0  # no rank in --new mode


def test_poll_new_mode_new_limit_caps_releases(mocker):
    """When _watchlist is None and _new_limit=2, only 2 releases are yielded."""
    c = _make_collector()
    c._last_seq = 1_000
    c._poll_epoch = 0.0
    c._watchlist = None  # --new mode
    c._new_limit = 2

    head_seq = 1_005
    # 5 distinct packages in the changes feed
    changes_data = {
        "results": [
            {"id": f"pkg{i}", "seq": 1_001 + i, "changes": []} for i in range(5)
        ],
        "last_seq": 1_005,
    }
    # Each package has one version published after epoch 0
    packument_data = _packument_with_versions({"1.0.0": "2024-06-01T12:00:00.000Z"})

    call_count = 0

    def fake_urlopen(req_or_url, timeout=None):
        nonlocal call_count
        call_count += 1
        url = getattr(req_or_url, "full_url", None) or str(req_or_url)
        if call_count == 1:
            return _FakeResp(json.dumps({"update_seq": head_seq}).encode())
        if "_changes" in url:
            return _FakeResp(json.dumps(changes_data).encode())
        return _FakeResp(packument_data)

    mocker.patch("urllib.request.urlopen", side_effect=fake_urlopen)

    releases = list(c.poll())

    assert len(releases) == 2


# ---------------------------------------------------------------------------
# get_previous_version
# ---------------------------------------------------------------------------


def test_get_previous_version_returns_preceding_version(mocker):
    c = _make_collector()
    packument_data = _packument_with_versions(
        {
            "1.0.0": "2023-01-01T00:00:00.000Z",
            "1.1.0": "2023-06-01T00:00:00.000Z",
            "2.0.0": "2024-01-01T00:00:00.000Z",
        }
    )
    mocker.patch("urllib.request.urlopen", return_value=_FakeResp(packument_data))
    prev = c.get_previous_version("express", "2.0.0")
    assert prev == "1.1.0"


def test_get_previous_version_returns_none_for_first_version(mocker):
    c = _make_collector()
    packument_data = _packument_with_versions(
        {
            "1.0.0": "2023-01-01T00:00:00.000Z",
        }
    )
    mocker.patch("urllib.request.urlopen", return_value=_FakeResp(packument_data))
    prev = c.get_previous_version("express", "1.0.0")
    assert prev is None


def test_get_previous_version_returns_none_on_network_error(mocker):
    c = _make_collector()
    mocker.patch(
        "urllib.request.urlopen",
        side_effect=Exception("network error"),
    )
    prev = c.get_previous_version("express", "2.0.0")
    assert prev is None


def test_get_previous_version_returns_none_for_unknown_version(mocker):
    c = _make_collector()
    packument_data = _packument_with_versions(
        {
            "1.0.0": "2023-01-01T00:00:00.000Z",
        }
    )
    mocker.patch("urllib.request.urlopen", return_value=_FakeResp(packument_data))
    prev = c.get_previous_version("express", "99.0.0")
    assert prev is None


# ---------------------------------------------------------------------------
# save_state / load_state
# ---------------------------------------------------------------------------


def test_save_and_load_state_roundtrip(db_conn):
    c = _make_collector()
    c._last_seq = 42_000
    c._poll_epoch = 9999.5
    c.save_state(db_conn)

    c2 = _make_collector()
    c2.load_state(db_conn)
    assert c2._last_seq == 42_000
    assert c2._poll_epoch == 9999.5


def test_load_state_defaults_when_no_state(db_conn):
    c = _make_collector()
    c.load_state(db_conn)
    assert c._last_seq == 0
    # _poll_epoch defaults to ~30 days ago, not 0.0 — see test below


def test_load_state_defaults_epoch_to_30_day_lookback_on_first_run(db_conn):
    """When no state has been persisted, _poll_epoch should be ~30 days ago."""
    import time
    from scm.collectors.npm import INITIAL_LOOKBACK_SECONDS

    before = time.time()
    c = _make_collector()
    c.load_state(db_conn)
    after = time.time()

    expected_min = before - INITIAL_LOOKBACK_SECONDS
    expected_max = after - INITIAL_LOOKBACK_SECONDS

    assert expected_min <= c._poll_epoch <= expected_max
