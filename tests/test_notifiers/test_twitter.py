"""Tests for scm.notifiers.twitter.TwitterNotifier."""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from scm.models import Release, StoredArtifact, Verdict

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TWITTER_ENV = {
    "TWITTER_API_KEY": "key",
    "TWITTER_API_SECRET": "secret",
    "TWITTER_ACCESS_TOKEN": "token",
    "TWITTER_ACCESS_TOKEN_SECRET": "token_secret",
}


def _make_release(**kwargs) -> Release:
    defaults = dict(
        ecosystem="npm",
        package="lodash",
        version="4.17.22",
        previous_version="4.17.21",
        rank=2,
        discovered_at=datetime.now(timezone.utc),
    )
    return Release(**{**defaults, **kwargs})


def _make_artifact(version: str) -> StoredArtifact:
    return StoredArtifact(
        ecosystem="npm",
        package="lodash",
        version=version,
        filename=f"lodash-{version}.tgz",
        path=Path(f"/tmp/lodash-{version}.tgz"),
        sha256="a" * 64,
        size_bytes=1024,
    )


def _make_verdict(release: Release, result: str = "malicious") -> Verdict:
    return Verdict(
        release=release,
        old_artifact=_make_artifact("4.17.21"),
        new_artifact=_make_artifact("4.17.22"),
        result=result,
        confidence="high",
        summary="Evil postinstall exfiltrates secrets",
        analysis="detailed analysis text",
        analyzed_at=datetime.now(timezone.utc),
    )


def _make_db_conn() -> sqlite3.Connection:
    from scm.db import init_db
    import tempfile

    tmp = tempfile.mkdtemp()
    return init_db(Path(tmp) / "t.db")


# ---------------------------------------------------------------------------
# TwitterNotifier construction
# ---------------------------------------------------------------------------


def test_twitter_notifier_raises_on_missing_env():
    from scm.notifiers.twitter import NotifierConfigError, TwitterNotifier

    with patch.dict(os.environ, {}, clear=True):
        # Remove all Twitter env vars
        for k in _TWITTER_ENV:
            os.environ.pop(k, None)
        with pytest.raises(NotifierConfigError, match="missing Twitter env vars"):
            TwitterNotifier()


def test_twitter_notifier_init_succeeds_with_all_env(mocker):
    from scm.notifiers.twitter import TwitterNotifier

    mocker.patch("tweepy.Client.__init__", return_value=None)
    with patch.dict(os.environ, _TWITTER_ENV):
        notifier = TwitterNotifier()
    assert notifier.name == "twitter"


# ---------------------------------------------------------------------------
# _build_tweet
# ---------------------------------------------------------------------------


def test_build_tweet_length_within_280():
    from scm.notifiers.twitter import TwitterNotifier

    tweet = TwitterNotifier._build_tweet(
        ecosystem="npm",
        rank=2,
        package="lodash",
        version="4.17.22",
        result="malicious",
        confidence="high",
        summary="Evil postinstall exfiltrates secrets via base64-encoded remote payload curl pipe sh",
    )
    assert len(tweet) <= 280


def test_build_tweet_contains_package_info():
    from scm.notifiers.twitter import TwitterNotifier

    tweet = TwitterNotifier._build_tweet(
        ecosystem="npm",
        rank=5,
        package="express",
        version="5.0.1",
        result="malicious",
        confidence="high",
        summary="Bad stuff",
    )
    assert "express" in tweet
    assert "5.0.1" in tweet
    assert "MALICIOUS" in tweet


def test_build_tweet_truncates_long_summary():
    from scm.notifiers.twitter import TwitterNotifier

    very_long_summary = "A" * 300
    tweet = TwitterNotifier._build_tweet(
        ecosystem="npm",
        rank=1,
        package="react",
        version="19.0.0",
        result="unknown",
        confidence="medium",
        summary=very_long_summary,
    )
    assert len(tweet) <= 280


def test_build_tweet_no_summary_still_fits():
    from scm.notifiers.twitter import TwitterNotifier

    tweet = TwitterNotifier._build_tweet(
        ecosystem="npm",
        rank=1,
        package="react",
        version="19.0.0",
        result="benign",
        confidence="high",
        summary="",
    )
    assert len(tweet) <= 280


# ---------------------------------------------------------------------------
# notify — success path
# ---------------------------------------------------------------------------


def test_notify_posts_tweet_and_returns_success_alert(mocker):
    from scm.notifiers.twitter import TwitterNotifier

    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.data = {"id": "1234567890"}
    mock_client.create_tweet.return_value = mock_response
    mocker.patch("tweepy.Client.__init__", return_value=None)
    mocker.patch("tweepy.Client.create_tweet", return_value=mock_response)

    with patch.dict(os.environ, _TWITTER_ENV):
        notifier = TwitterNotifier()
        notifier._client = mock_client

    conn = _make_db_conn()
    release = _make_release()
    verdict = _make_verdict(release)

    alert = notifier.notify(verdict, conn)
    assert alert.success is True
    assert "tweet_id=1234567890" in alert.detail
    mock_client.create_tweet.assert_called_once()


def test_notify_increments_tweet_count(mocker):
    from scm.notifiers.twitter import TwitterNotifier
    from scm.db import get_tweet_count

    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.data = {"id": "999"}
    mock_client.create_tweet.return_value = mock_response
    mocker.patch("tweepy.Client.__init__", return_value=None)

    with patch.dict(os.environ, _TWITTER_ENV):
        notifier = TwitterNotifier()
        notifier._client = mock_client

    conn = _make_db_conn()
    release = _make_release()
    verdict = _make_verdict(release)

    assert get_tweet_count(conn) == 0
    notifier.notify(verdict, conn)
    assert get_tweet_count(conn) == 1


# ---------------------------------------------------------------------------
# notify — budget guard
# ---------------------------------------------------------------------------


def test_notify_skips_when_budget_exhausted(mocker):
    from scm.notifiers.twitter import TwitterNotifier
    from scm.db import get_tweet_count

    mock_client = MagicMock()
    mocker.patch("tweepy.Client.__init__", return_value=None)

    with patch.dict(os.environ, _TWITTER_ENV):
        notifier = TwitterNotifier()
        notifier._client = mock_client

    conn = _make_db_conn()

    # Simulate 490 tweets already sent this month
    mocker.patch("scm.db.get_tweet_count", return_value=490)

    release = _make_release()
    verdict = _make_verdict(release)

    alert = notifier.notify(verdict, conn)
    assert alert.success is False
    assert "budget" in alert.detail
    mock_client.create_tweet.assert_not_called()


# ---------------------------------------------------------------------------
# notify — tweepy error path
# ---------------------------------------------------------------------------


def test_notify_returns_failed_alert_on_tweepy_error(mocker):
    import tweepy
    from scm.notifiers.twitter import TwitterNotifier

    mock_client = MagicMock()
    mock_client.create_tweet.side_effect = tweepy.TweepyException("rate limited")
    mocker.patch("tweepy.Client.__init__", return_value=None)

    with patch.dict(os.environ, _TWITTER_ENV):
        notifier = TwitterNotifier()
        notifier._client = mock_client

    conn = _make_db_conn()
    release = _make_release()
    verdict = _make_verdict(release)

    alert = notifier.notify(verdict, conn)
    assert alert.success is False
    assert "rate limited" in alert.detail


def test_notify_returns_failed_alert_on_unexpected_error(mocker):
    from scm.notifiers.twitter import TwitterNotifier

    mock_client = MagicMock()
    mock_client.create_tweet.side_effect = RuntimeError("unexpected")
    mocker.patch("tweepy.Client.__init__", return_value=None)

    with patch.dict(os.environ, _TWITTER_ENV):
        notifier = TwitterNotifier()
        notifier._client = mock_client

    conn = _make_db_conn()
    release = _make_release()
    verdict = _make_verdict(release)

    alert = notifier.notify(verdict, conn)
    assert alert.success is False
