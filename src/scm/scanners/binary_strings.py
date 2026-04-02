"""Binary strings scanner.

Runs the system ``strings`` utility against binary files in the release
and flags printable-string output that could indicate embedded payloads.
Each file's raw ``strings`` output is capped and wrapped in a fenced code
block so opencode can see it without needing to open a binary itself.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

from scm.extractor import is_text
from scm.scanners import Scanner

log = logging.getLogger(__name__)

# Defaults — overridable via config.yaml scanner_config.binary_strings section.
_DEFAULT_MIN_LENGTH: int = 8  # passed as -n to strings
_DEFAULT_MAX_LINES_PER_FILE: int = 100  # cap per file
_DEFAULT_MAX_TOTAL_LINES: int = 500  # overall cap across all files


class BinaryStringsScanner(Scanner):
    """Run ``strings`` on changed/added binary files and surface the output."""

    name = "binary_strings"

    def __init__(self) -> None:
        self._min_length = _DEFAULT_MIN_LENGTH
        self._max_lines_per_file = _DEFAULT_MAX_LINES_PER_FILE
        self._max_total_lines = _DEFAULT_MAX_TOTAL_LINES

    def configure(self, options: dict) -> None:
        if "min_length" in options:
            self._min_length = int(options["min_length"])
        if "max_lines_per_file" in options:
            self._max_lines_per_file = int(options["max_lines_per_file"])
        if "max_total_lines" in options:
            self._max_total_lines = int(options["max_total_lines"])
        log.debug(
            "binary_strings scanner configured: min_length=%d"
            " max_lines_per_file=%d max_total_lines=%d",
            self._min_length,
            self._max_lines_per_file,
            self._max_total_lines,
        )

    def scan(
        self,
        old_root: Path | None,
        new_root: Path,
        changed_files: list[str],
        added_files: list[str],
    ) -> str:
        if shutil.which("strings") is None:
            log.debug("binary_strings: 'strings' binary not found — skipping")
            return ""

        targets = sorted(set(changed_files) | set(added_files))
        sections: list[str] = []
        total_lines = 0

        for rel in targets:
            if total_lines >= self._max_total_lines:
                break
            path = new_root / rel
            if not path.exists() or is_text(path):
                continue  # only process binary files

            try:
                result = subprocess.run(  # noqa: S603
                    ["strings", "-n", str(self._min_length), str(path)],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
            except Exception as exc:  # noqa: BLE001
                log.warning("binary_strings: strings failed on %s: %s", rel, exc)
                continue

            raw_lines = result.stdout.splitlines()
            if not raw_lines:
                continue

            # Cap per-file and overall
            remaining = self._max_total_lines - total_lines
            cap = min(self._max_lines_per_file, remaining)
            displayed = raw_lines[:cap]
            was_capped = len(raw_lines) > cap

            total_lines += len(displayed)

            body = "\n".join(displayed)
            caption = f"### `{rel}`\n\n```\n{body}\n```\n"
            if was_capped:
                caption += (
                    f"\n> Output capped at {cap} lines ({len(raw_lines)} total).\n"
                )
            sections.append(caption)

        if not sections:
            return ""

        header = (
            "## Binary Strings\n\n"
            f"Printable strings extracted from {len(sections)} binary file(s)"
            f" using `strings -n {self._min_length}`.\n\n"
        )
        if total_lines >= self._max_total_lines:
            header += f"> Total output capped at {self._max_total_lines} lines.\n\n"

        return header + "\n".join(sections)
