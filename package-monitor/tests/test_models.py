"""Tests for models dataclasses."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from scm.models import Alert, Release, StoredArtifact, Verdict


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


def _make_artifact(**kwargs) -> StoredArtifact:
    defaults = dict(
        ecosystem="npm",
        package="express",
        version="5.0.0",
        filename="express-5.0.0.tgz",
        path=Path("/tmp/express-5.0.0.tgz"),
        sha256="abc123",
        size_bytes=1024,
    )
    return StoredArtifact(**{**defaults, **kwargs})


def test_release_instantiates():
    r = _make_release()
    assert r.ecosystem == "npm"
    assert r.package == "express"
    assert r.previous_version == "4.19.0"


def test_release_previous_version_none():
    r = _make_release(previous_version=None)
    assert r.previous_version is None


def test_stored_artifact_instantiates():
    a = _make_artifact()
    assert a.sha256 == "abc123"
    assert isinstance(a.path, Path)


def test_verdict_instantiates():
    r = _make_release()
    old_a = _make_artifact(version="4.19.0")
    new_a = _make_artifact(version="5.0.0")
    v = Verdict(
        release=r,
        old_artifact=old_a,
        new_artifact=new_a,
        result="benign",
        confidence="high",
        summary="all good",
        analysis="full text",
        analyzed_at=datetime.now(timezone.utc),
    )
    assert v.result == "benign"
    assert v.confidence == "high"


def test_alert_instantiates():
    r = _make_release()
    old_a = _make_artifact(version="4.19.0")
    new_a = _make_artifact(version="5.0.0")
    v = Verdict(
        release=r,
        old_artifact=old_a,
        new_artifact=new_a,
        result="unknown",
        confidence="low",
        summary="",
        analysis="",
        analyzed_at=datetime.now(timezone.utc),
    )
    a = Alert(
        verdict=v,
        notifier="local",
        sent_at=datetime.now(timezone.utc),
        success=True,
        detail="/tmp/report.md",
    )
    assert a.success is True
    assert "report" in a.detail


def test_dataclass_equality():
    ts = datetime.now(timezone.utc)
    r1 = _make_release(discovered_at=ts)
    r2 = _make_release(discovered_at=ts)
    assert r1 == r2


def test_dataclass_inequality():
    r1 = _make_release(version="1.0.0")
    r2 = _make_release(version="2.0.0")
    assert r1 != r2
