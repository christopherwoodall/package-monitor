"""Scanner plugin interface for package-monitor.

Scanners run against the extracted new release tarball before opencode sees it.
Each scanner returns a markdown string of findings (or '' if nothing found).
Their output is concatenated into scanner_findings.md in the opencode workspace.

Third-party scanners self-register under:
    package_monitor.scanners  →  Scanner subclasses
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from pathlib import Path

log = logging.getLogger(__name__)


class Scanner(ABC):
    """Base class for all release scanners.

    Subclasses must set ``name`` as a class attribute and implement ``scan``.
    Scanners are stateless and thread-safe — one instance is shared across all
    concurrent release-processing threads.
    """

    name: str  # class-level attribute; must be set by every subclass

    @abstractmethod
    def scan(
        self,
        old_root: Path | None,
        new_root: Path,
        changed_files: list[str],
        added_files: list[str],
    ) -> str:
        """Scan the extracted new release and return markdown findings.

        Args:
            old_root:      Root directory of the extracted old release tarball,
                           or None if there is no previous version.  Most scanners
                           ignore this; DiffScanner uses it to build the diff.
            new_root:      Root directory of the extracted new release tarball.
            changed_files: Relative paths of files that changed vs. the previous release.
            added_files:   Relative paths of files that are new in this release.

        Returns:
            A markdown string with findings, or '' if nothing suspicious was found.
            Must NEVER raise — catch all exceptions internally and return ''.
        """

    def configure(self, options: dict) -> None:
        """Apply scanner-specific configuration from the scanners YAML section.

        Called once at startup (after instantiation) if a matching scanner
        config block exists in config.yaml.  The default implementation is a
        no-op; subclasses override to apply their options.

        Args:
            options: Dict of key→value from the scanner's YAML config block.
        """
