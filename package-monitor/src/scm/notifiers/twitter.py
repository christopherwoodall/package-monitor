"""Twitter/X notifier — posts a security alert tweet via tweepy v2."""

from __future__ import annotations

import logging
import os
import sqlite3
from datetime import datetime, timezone

import tweepy

from scm import db as db_module
from scm.models import Alert, Verdict
from scm.notifiers import Notifier

log = logging.getLogger(__name__)

_MAX_TWEET_LEN = 280


class NotifierConfigError(Exception):
    """Raised at init if required Twitter env vars are missing."""


class TwitterNotifier(Notifier):
    name = "twitter"

    def __init__(self) -> None:
        required = {
            "TWITTER_API_KEY": os.environ.get("TWITTER_API_KEY"),
            "TWITTER_API_SECRET": os.environ.get("TWITTER_API_SECRET"),
            "TWITTER_ACCESS_TOKEN": os.environ.get("TWITTER_ACCESS_TOKEN"),
            "TWITTER_ACCESS_TOKEN_SECRET": os.environ.get(
                "TWITTER_ACCESS_TOKEN_SECRET"
            ),
        }
        missing = [k for k, v in required.items() if not v]
        if missing:
            raise NotifierConfigError(f"missing Twitter env vars: {', '.join(missing)}")

        self._client = tweepy.Client(
            consumer_key=required["TWITTER_API_KEY"],
            consumer_secret=required["TWITTER_API_SECRET"],
            access_token=required["TWITTER_ACCESS_TOKEN"],
            access_token_secret=required["TWITTER_ACCESS_TOKEN_SECRET"],
        )
        log.debug("TwitterNotifier initialised")

    # -------------------------------------------------------------------------
    # Tweet construction
    # -------------------------------------------------------------------------

    @staticmethod
    def _build_tweet(
        ecosystem: str,
        rank: int,
        package: str,
        version: str,
        result: str,
        confidence: str,
        summary: str,
    ) -> str:
        """Build a ≤280-char tweet, slicing summary to fit.  Uses len() not bytes."""
        line1 = "🚨 Supply Chain Alert"
        line2 = f"{ecosystem.upper()} #{rank}: {package}@{version}"
        line3 = f"Verdict: {result.upper()} ({confidence} confidence)"
        line5 = f"npmjs.com/package/{package}/v/{version}"

        # Base without summary line (4 newlines joining 4 lines)
        base = "\n".join([line1, line2, line3, line5])
        available = _MAX_TWEET_LEN - len(base) - 1  # -1 for the extra \n before summary

        if available > 0 and summary:
            summary_part = summary[:available]
            return "\n".join([line1, line2, line3, summary_part, line5])
        return base

    # -------------------------------------------------------------------------
    # Notifier interface
    # -------------------------------------------------------------------------

    def notify(self, verdict: Verdict, conn: sqlite3.Connection) -> Alert:
        release = verdict.release
        now = datetime.now(timezone.utc)

        # Monthly budget guard
        if db_module.get_tweet_count(conn) >= 490:
            log.warning("monthly tweet budget exhausted (490/500) — skipping tweet")
            return Alert(
                verdict=verdict,
                notifier=self.name,
                sent_at=now,
                success=False,
                detail="monthly tweet budget exhausted (490/500)",
            )

        tweet_text = self._build_tweet(
            ecosystem=release.ecosystem,
            rank=release.rank,
            package=release.package,
            version=release.version,
            result=verdict.result,
            confidence=verdict.confidence,
            summary=verdict.summary,
        )

        try:
            response = self._client.create_tweet(text=tweet_text)
            tweet_id = response.data["id"]
            db_module.increment_tweet_count(conn)
            log.info("tweet posted  id=%s", tweet_id)
            return Alert(
                verdict=verdict,
                notifier=self.name,
                sent_at=now,
                success=True,
                detail=f"tweet_id={tweet_id}",
            )
        except tweepy.TweepyException as exc:
            log.exception("TwitterNotifier failed: %s", exc)
            return Alert(
                verdict=verdict,
                notifier=self.name,
                sent_at=now,
                success=False,
                detail=str(exc),
            )
        except Exception as exc:
            log.exception("TwitterNotifier unexpected error: %s", exc)
            return Alert(
                verdict=verdict,
                notifier=self.name,
                sent_at=now,
                success=False,
                detail=str(exc),
            )
