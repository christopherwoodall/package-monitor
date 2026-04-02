"""Base64 strings scanner.

Scans changed and added files for suspiciously long base64-encoded strings.
Long base64 blobs in source code are a common obfuscation technique for
embedding payloads.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from scm.extractor import is_text
from scm.scanners import Scanner

log = logging.getLogger(__name__)

# Defaults — overridable via config.yaml scanners.base64_strings section.
_DEFAULT_MIN_LENGTH: int = 60  # minimum base64 run length to flag
_DEFAULT_MAX_HITS: int = 50  # cap on total findings reported
_DEFAULT_SNIPPET_LENGTH: int = 120  # characters of the match shown in the table


class Base64StringsScanner(Scanner):
    """Flag suspiciously long base64 strings in changed/added source files."""

    name = "base64_strings"

    def __init__(self) -> None:
        self._min_length = _DEFAULT_MIN_LENGTH
        self._max_hits = _DEFAULT_MAX_HITS
        self._snippet_length = _DEFAULT_SNIPPET_LENGTH
        self._pattern = re.compile(rf"[A-Za-z0-9+/]{{{self._min_length},}}={{0,2}}")

    def configure(self, options: dict) -> None:
        if "min_length" in options:
            self._min_length = int(options["min_length"])
        if "max_hits" in options:
            self._max_hits = int(options["max_hits"])
        if "snippet_length" in options:
            self._snippet_length = int(options["snippet_length"])
        # Rebuild pattern after options are applied.
        self._pattern = re.compile(rf"[A-Za-z0-9+/]{{{self._min_length},}}={{0,2}}")
        log.debug(
            "base64_strings scanner configured: min_length=%d max_hits=%d snippet_length=%d",
            self._min_length,
            self._max_hits,
            self._snippet_length,
        )

    def scan(
        self,
        old_root: Path | None,
        new_root: Path,
        changed_files: list[str],
        added_files: list[str],
    ) -> str:
        targets = set(changed_files) | set(added_files)
        rows: list[tuple[str, int, str]] = []  # (rel_path, line_no, snippet)

        for rel in sorted(targets):
            if len(rows) >= self._max_hits:
                break
            path = new_root / rel
            if not path.exists() or not is_text(path):
                continue
            try:
                for lineno, line in enumerate(
                    path.read_text(encoding="utf-8", errors="replace").splitlines(),
                    start=1,
                ):
                    if len(rows) >= self._max_hits:
                        break
                    for m in self._pattern.finditer(line):
                        snippet = m.group()[: self._snippet_length]
                        rows.append((rel, lineno, snippet))
                        if len(rows) >= self._max_hits:
                            break
            except Exception as exc:  # noqa: BLE001
                log.warning("base64_strings: error reading %s: %s", rel, exc)

        if not rows:
            return ""

        lines = [
            "## Base64 Strings\n",
            "\n",
            f"Found {len(rows)} long base64 string(s) in changed/added files"
            f" (min_length={self._min_length}).\n",
            "\n",
            "| File | Line | Snippet |\n",
            "|---|---|---|\n",
        ]
        for rel, lineno, snippet in rows:
            # Escape pipe characters inside cells so the markdown table renders.
            safe_snippet = snippet.replace("|", "&#124;")
            lines.append(f"| `{rel}` | {lineno} | `{safe_snippet}` |\n")

        if len(rows) == self._max_hits:
            lines.append(
                f"\n> Results capped at {self._max_hits}. There may be more.\n"
            )

        return "".join(lines)
