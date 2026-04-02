"""Notifier ABC."""

from __future__ import annotations

import sqlite3
from abc import ABC, abstractmethod

from scm.models import Alert, Verdict


class Notifier(ABC):
    """Base class for all notification channels.

    Subclass, set ``name = "mychannel"`` as a class attribute,
    implement ``notify``, and register via entry_points.
    """

    name: str  # set in every subclass

    @abstractmethod
    def notify(self, verdict: Verdict, conn: sqlite3.Connection) -> Alert:
        """Send a notification and return an Alert.

        Must NEVER raise — catch all exceptions internally and return
        ``Alert(success=False, detail=str(e))``.
        """
