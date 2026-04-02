"""Slack notifier stub — documents the extension point for Slack/webhook channels.

To activate:
1. Implement the notify() method below.
2. Register in pyproject.toml:
   [project.entry-points."package_monitor.notifiers"]
   slack = "scm.notifiers.slack:SlackNotifier"
3. Run: uv sync
4. Set env var: SLACK_WEBHOOK_URL=https://hooks.slack.com/services/...
5. Use: package-monitor --notifiers local,slack

Other webhook-based channels (Discord, Teams, etc.) follow the same pattern.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from datetime import datetime, timezone

from scm.models import Alert, Verdict
from scm.notifiers import Notifier

log = logging.getLogger(__name__)


class SlackNotifier(Notifier):
    name = "slack"

    def notify(self, verdict: Verdict, conn: sqlite3.Connection) -> Alert:
        """Post a Slack message via incoming webhook.  Not yet implemented."""
        now = datetime.now(timezone.utc)
        webhook_url = os.environ.get("SLACK_WEBHOOK_URL", "")
        if not webhook_url:
            return Alert(
                verdict=verdict,
                notifier=self.name,
                sent_at=now,
                success=False,
                detail="SLACK_WEBHOOK_URL not set",
            )
        # TODO: implement Slack webhook POST using urllib.request (no requests lib)
        return Alert(
            verdict=verdict,
            notifier=self.name,
            sent_at=now,
            success=False,
            detail="SlackNotifier not yet implemented",
        )
