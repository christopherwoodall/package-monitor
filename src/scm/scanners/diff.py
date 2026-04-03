"""Diff scanner — produces a unified-diff markdown report between two releases.

This scanner is self-contained: it builds the full diff report inline using
difflib and the helpers from extractor.py.  It does not depend on differ.py.

DiffScanner needs old_root to build the diff, which is why the Scanner
signature includes old_root.  Other scanners ignore that argument.
"""

from __future__ import annotations

import difflib
import logging
from pathlib import Path

from scm.extractor import collect_files, is_text
from scm.scanners import Scanner

log = logging.getLogger(__name__)

# Defaults — overridable via config.yaml scanner_config.diff section.
_DEFAULT_MAX_DIFF_BYTES: int = 500_000
_DEFAULT_CONTEXT_LINES: int = 3


class DiffScanner(Scanner):
    """Build a unified-diff markdown report between old and new release tarballs."""

    name = "diff"

    def __init__(self) -> None:
        self._max_diff_bytes: int = _DEFAULT_MAX_DIFF_BYTES
        self._context_lines: int = _DEFAULT_CONTEXT_LINES
        # Set after each scan() so analyzer.py can read it via getattr.
        self.last_truncated: bool = False

    def configure(self, options: dict) -> None:
        if "max_diff_bytes" in options:
            self._max_diff_bytes = int(options["max_diff_bytes"])
        if "context_lines" in options:
            self._context_lines = int(options["context_lines"])
        log.debug(
            "diff scanner configured: max_diff_bytes=%d context_lines=%d",
            self._max_diff_bytes,
            self._context_lines,
        )

    def scan(
        self,
        old_root: Path | None,
        new_root: Path,
        changed_files: list[str],
        added_files: list[str],
    ) -> str:
        """Build the diff report markdown.

        Args:
            old_root:      Root of the extracted old release tarball, or None if
                           there is no previous version to diff against.
            new_root:      Root of the extracted new release tarball.
            changed_files: Not used directly — derived from old/new file maps.
            added_files:   Not used directly — derived from old/new file maps.

        Returns:
            Markdown string with the diff report, or '' if old_root is None.
        """
        if old_root is None:
            log.debug("diff scanner: no old_root — force-scan with no previous version")
            self.last_truncated = False
            return (
                "## Diff Scanner\n\n"
                "**Note:** No previous version available for comparison. "
                "This appears to be an initial release or force-scan without historical context."
            )

        old_files = collect_files(old_root)
        new_files = collect_files(new_root)

        report_text, was_truncated = self._build_report(old_files, new_files)
        self.last_truncated = was_truncated
        log.info(
            "diff scanner: %d old files, %d new files, truncated=%s",
            len(old_files),
            len(new_files),
            was_truncated,
        )
        return report_text

    def _build_report(
        self,
        old_files: dict[str, Path],
        new_files: dict[str, Path],
    ) -> tuple[str, bool]:
        """Build the markdown diff report. Returns (report_text, was_truncated)."""
        old_set = set(old_files)
        new_set = set(new_files)

        added = sorted(new_set - old_set)
        deleted = sorted(old_set - new_set)
        common = sorted(old_set & new_set)

        changed = [
            f for f in common if old_files[f].read_bytes() != new_files[f].read_bytes()
        ]
        unchanged = [f for f in common if f not in changed]

        parts: list[str] = [
            "| Metric | Count |\n",
            "|--------|-------|\n",
            f"| Added files | {len(added)} |\n",
            f"| Deleted files | {len(deleted)} |\n",
            f"| Changed files | {len(changed)} |\n",
            f"| Unchanged files | {len(unchanged)} |\n\n",
        ]

        if added:
            parts.append("## Added Files\n")
            for f in added:
                parts.append(f"- `{f}`\n")
            parts.append("\n")

        if deleted:
            parts.append("## Deleted Files\n")
            for f in deleted:
                parts.append(f"- `{f}`\n")
            parts.append("\n")

        if changed:
            parts.append("## Changed Files\n\n")

        diff_bytes = 0
        was_truncated = False

        for filename in changed:
            if was_truncated:
                break

            old_path = old_files[filename]
            new_path = new_files[filename]

            if not is_text(old_path) or not is_text(new_path):
                section = f"### `{filename}`\n\nBinary file changed.\n\n"
            else:
                old_lines = old_path.read_text(
                    encoding="utf-8", errors="replace"
                ).splitlines(keepends=True)
                new_lines = new_path.read_text(
                    encoding="utf-8", errors="replace"
                ).splitlines(keepends=True)
                diff_iter = difflib.unified_diff(
                    old_lines,
                    new_lines,
                    fromfile=f"a/{filename}",
                    tofile=f"b/{filename}",
                    n=self._context_lines,
                )
                diff_text = "".join(diff_iter)
                if not diff_text:
                    continue
                section = f"### `{filename}`\n\n```diff\n{diff_text}```\n\n"

            section_bytes = len(section.encode("utf-8"))
            if diff_bytes + section_bytes >= self._max_diff_bytes:
                was_truncated = True
                break
            diff_bytes += section_bytes
            parts.append(section)

        if was_truncated:
            parts.append("\n> ⚠️ Diff truncated — further changes not shown.\n")

        return "".join(parts), was_truncated
