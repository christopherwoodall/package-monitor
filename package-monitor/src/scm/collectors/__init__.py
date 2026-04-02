"""Collector ABC and shared exceptions."""

from __future__ import annotations

import sqlite3
from abc import ABC, abstractmethod
from typing import Iterator

from scm.models import Release


class WatchlistError(Exception):
    """Raised when a collector cannot load its watchlist.  Never swallowed."""


class Collector(ABC):
    """Base class for all ecosystem collectors.

    Subclass, set ``ecosystem = "myecosystem"`` as a class attribute,
    implement all abstract methods, and register via entry_points.
    """

    ecosystem: str  # set in every subclass

    @abstractmethod
    def load_watchlist(self, top_n: int) -> None:
        """Fetch and cache the top-N packages to watch.

        When top_n == 0 the watchlist filter is disabled — every newly
        published package is a candidate.  Implementations must set an
        internal sentinel (e.g. ``_watchlist = None``) and skip the
        watchlist download entirely in this mode.

        Raises WatchlistError on any failure — no silent fallback.
        """

    @abstractmethod
    def poll(self) -> Iterator[Release]:
        """Yield new Release objects discovered since last poll.

        Must update internal sequence / epoch *after* all yields so that
        a crash mid-iteration is safely retried.
        """

    def get_previous_version(self, package: str, new_version: str) -> str | None:
        """Return the version immediately preceding new_version, or None.

        Default returns None.  Collectors should override this to resolve
        previous versions from their registry.
        """
        return None

    @abstractmethod
    def save_state(self, conn: sqlite3.Connection) -> None:
        """Persist internal state via db.set_collector_state."""

    @abstractmethod
    def load_state(self, conn: sqlite3.Connection) -> None:
        """Restore internal state via db.get_collector_state."""
