"""Tests for scm.notifiers.local.LocalNotifier."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from scm.models import Alert, Release, StoredArtifact, Verdict
from scm.notifiers.local import LocalNotifier


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_release(**kwargs) -> Release:
    defaults = dict(
        ecosystem="npm",
        package="express",
        version="5.0.0",
        previous_version="4.19.0",
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
        size_bytes=1024,
    )


def _make_verdict(release: Release, result: str = "benign") -> Verdict:
    return Verdict(
        release=release,
        old_artifact=_make_artifact("4.19.0"),
        new_artifact=_make_artifact("5.0.0"),
        result=result,
        confidence="high",
        summary="clean release",
        analysis="no suspicious changes found",
        analyzed_at=datetime.now(timezone.utc),
    )


def _make_conn() -> sqlite3.Connection:
    """In-memory DB — LocalNotifier doesn't write to it directly."""
    return sqlite3.connect(":memory:")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_local_notifier_creates_report_file(tmp_path, mocker):
    mocker.patch("scm.notifiers.local.REPORTS_ROOT", tmp_path / "reports")
    release = _make_release()
    verdict = _make_verdict(release)
    conn = _make_conn()

    notifier = LocalNotifier()
    alert = notifier.notify(verdict, conn)

    assert alert.success is True
    report_path = Path(alert.detail)
    assert report_path.exists()


def test_local_notifier_report_contains_verdict(tmp_path, mocker):
    mocker.patch("scm.notifiers.local.REPORTS_ROOT", tmp_path / "reports")
    release = _make_release()
    verdict = _make_verdict(release, result="malicious")
    conn = _make_conn()

    notifier = LocalNotifier()
    alert = notifier.notify(verdict, conn)

    content = Path(alert.detail).read_text()
    assert "MALICIOUS" in content
    assert "express" in content
    assert "5.0.0" in content


def test_local_notifier_report_path_structure(tmp_path, mocker):
    reports_root = tmp_path / "reports"
    mocker.patch("scm.notifiers.local.REPORTS_ROOT", reports_root)
    release = _make_release(ecosystem="npm", package="lodash", version="4.17.22")
    verdict = _make_verdict(release)
    conn = _make_conn()

    notifier = LocalNotifier()
    alert = notifier.notify(verdict, conn)

    expected = reports_root / "npm" / "lodash" / "4.17.22.md"
    assert Path(alert.detail) == expected
    assert expected.exists()


def test_local_notifier_returns_failed_alert_on_error(tmp_path, mocker):
    # Point REPORTS_ROOT to a file (not a dir) to force mkdir failure
    blocker = tmp_path / "reports"
    blocker.write_text("I am a file, not a directory")
    mocker.patch("scm.notifiers.local.REPORTS_ROOT", blocker)

    release = _make_release()
    verdict = _make_verdict(release)
    conn = _make_conn()

    notifier = LocalNotifier()
    alert = notifier.notify(verdict, conn)
    assert alert.success is False
    assert alert.notifier == "local"


def test_local_notifier_name_is_local():
    assert LocalNotifier.name == "local"


def test_local_notifier_alert_detail_is_path_string(tmp_path, mocker):
    mocker.patch("scm.notifiers.local.REPORTS_ROOT", tmp_path / "reports")
    release = _make_release()
    verdict = _make_verdict(release)
    conn = _make_conn()
    notifier = LocalNotifier()
    alert = notifier.notify(verdict, conn)
    # detail must be a string (path), not a Path object
    assert isinstance(alert.detail, str)


def test_local_notifier_includes_sha256_in_report(tmp_path, mocker):
    mocker.patch("scm.notifiers.local.REPORTS_ROOT", tmp_path / "reports")
    release = _make_release()
    old_art = _make_artifact("4.19.0")
    new_art = _make_artifact("5.0.0")
    old_art.sha256 = "old_sha256_hash" + "a" * 49
    new_art.sha256 = "new_sha256_hash" + "b" * 49
    verdict = Verdict(
        release=release,
        old_artifact=old_art,
        new_artifact=new_art,
        result="benign",
        confidence="high",
        summary="ok",
        analysis="",
        analyzed_at=datetime.now(timezone.utc),
    )
    conn = _make_conn()
    notifier = LocalNotifier()
    alert = notifier.notify(verdict, conn)
    content = Path(alert.detail).read_text()
    assert "old_sha256_hash" in content
    assert "new_sha256_hash" in content
