"""Discover collectors, notifiers, and scanners via importlib.metadata entry_points.

Third-party packages self-register under:
  package_monitor.collectors   →  Collector subclasses
  package_monitor.notifiers    →  Notifier subclasses
  package_monitor.scanners     →  Scanner subclasses

This module is the single point of plugin loading.
Neither cli.py nor orchestrator.py imports plugins directly.
"""

from __future__ import annotations

import logging
from importlib.metadata import entry_points

from scm.collectors import Collector
from scm.collectors import WatchlistError  # noqa: F401 — re-exported for callers
from scm.notifiers import Notifier
from scm.scanners import Scanner

log = logging.getLogger(__name__)

_COLLECTORS_GROUP = "package_monitor.collectors"
_NOTIFIERS_GROUP = "package_monitor.notifiers"
_SCANNERS_GROUP = "package_monitor.scanners"


def load_collectors() -> dict[str, type[Collector]]:
    """Return {name: CollectorClass} for every registered collector."""
    result: dict[str, type[Collector]] = {}
    for ep in entry_points(group=_COLLECTORS_GROUP):
        try:
            cls = ep.load()
            result[ep.name] = cls
            log.debug("loaded collector plugin  %s → %s", ep.name, cls)
        except Exception as exc:  # noqa: BLE001
            log.warning("failed to load collector plugin %r: %s", ep.name, exc)
    return result


def load_notifiers() -> dict[str, type[Notifier]]:
    """Return {name: NotifierClass} for every registered notifier."""
    result: dict[str, type[Notifier]] = {}
    for ep in entry_points(group=_NOTIFIERS_GROUP):
        try:
            cls = ep.load()
            result[ep.name] = cls
            log.debug("loaded notifier plugin  %s → %s", ep.name, cls)
        except Exception as exc:  # noqa: BLE001
            log.warning("failed to load notifier plugin %r: %s", ep.name, exc)
    return result


def load_scanners() -> dict[str, type[Scanner]]:
    """Return {name: ScannerClass} for every registered scanner."""
    result: dict[str, type[Scanner]] = {}
    for ep in entry_points(group=_SCANNERS_GROUP):
        try:
            cls = ep.load()
            result[ep.name] = cls
            log.debug("loaded scanner plugin  %s → %s", ep.name, cls)
        except Exception as exc:  # noqa: BLE001
            log.warning("failed to load scanner plugin %r: %s", ep.name, exc)
    return result
