"""Run opencode on scanner findings and parse its security verdict.

Subprocess only — knows nothing about storage, DB, or network.
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from scm.models import Release, StoredArtifact, Verdict

if TYPE_CHECKING:
    from scm.scanners import Scanner

log = logging.getLogger(__name__)

__all__ = [
    "AnalyzerError",
    "run_opencode",
    "parse_verdict",
    "analyze",
]

# ---------------------------------------------------------------------------
# ANSI stripping
# ---------------------------------------------------------------------------

ANSI_ESCAPE = re.compile(r"\x1b\[[^a-zA-Z]*[a-zA-Z]")


def strip_ansi(text: str) -> str:
    return ANSI_ESCAPE.sub("", text)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class AnalyzerError(Exception):
    """Raised when opencode produces no usable output."""


# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------


def run_opencode(
    workspace: Path,
    timeout: int = 300,
    model: str | None = None,
    prompt: str | None = None,
) -> tuple[str, str | None]:
    """Invoke ``opencode run [--model MODEL] <prompt>`` inside *workspace*.

    Uses subprocess cwd= (thread-safe; no os.chdir).
    Raises AnalyzerError on empty output or TimeoutExpired.

    Returns a tuple of (combined_output, log_path) where log_path is the
    absolute path to the opencode log file created during this invocation,
    or None if it could not be identified.

    Args:
        workspace: Directory containing scanner_findings.md.
        timeout:   Maximum seconds to wait for opencode.
        model:     If set, passed as ``--model MODEL`` to opencode.
        prompt:    Prompt text to send.
    """
    effective_prompt = prompt or ""
    cmd = ["opencode", "run"]
    if model:
        cmd += ["--model", model]
    cmd.append(effective_prompt)

    log.info(
        "running opencode in %s  (timeout=%ds, model=%s)",
        workspace,
        timeout,
        model or "<default>",
    )

    # Snapshot opencode log directory before the call so we can identify the
    # new log file created during this invocation.
    log_dir = Path.home() / ".local" / "share" / "opencode" / "log"
    before: set[Path] = set(log_dir.glob("*.log")) if log_dir.is_dir() else set()

    try:
        result = subprocess.run(  # noqa: S603
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(workspace),
        )
    except subprocess.TimeoutExpired as exc:
        raise AnalyzerError(f"opencode timed out after {timeout}s") from exc
    except FileNotFoundError as exc:
        raise AnalyzerError("opencode binary not found — is it installed?") from exc

    # Identify newly created log file (set diff).
    log_path: str | None = None
    if log_dir.is_dir():
        after: set[Path] = set(log_dir.glob("*.log"))
        new_files = after - before
        if new_files:
            log_path = str(sorted(new_files)[-1])
            log.debug("opencode log file: %s", log_path)
        else:
            log.debug("no new opencode log file detected")

    if result.returncode != 0:
        log.warning(
            "opencode exited with code %d — attempting to parse output anyway",
            result.returncode,
        )

    combined = strip_ansi(result.stdout + result.stderr)
    if not combined.strip():
        raise AnalyzerError("opencode returned empty output")

    log.debug("opencode output length: %d chars", len(combined))
    return combined, log_path


def parse_verdict(output: str) -> tuple[str, str, str]:
    """Parse Verdict / Confidence / Summary lines from opencode output.

    Returns (result, confidence, summary).  Defaults to ('error', 'low', '').
    """
    result = "error"
    confidence = "low"
    summary = ""

    v_match = re.search(
        r"^Verdict:\s*(malicious|benign|unknown)", output, re.MULTILINE | re.IGNORECASE
    )
    c_match = re.search(
        r"^Confidence:\s*(high|medium|low)", output, re.MULTILINE | re.IGNORECASE
    )
    s_match = re.search(r"^Summary:\s*(.+)", output, re.MULTILINE | re.IGNORECASE)

    if v_match:
        result = v_match.group(1).lower()
    if c_match:
        confidence = c_match.group(1).lower()
    if s_match:
        summary = s_match.group(1).strip()[:120]

    return result, confidence, summary


def analyze(
    release: Release,
    old_artifact: StoredArtifact,
    new_artifact: StoredArtifact,
    old_root: Path | None,
    new_root: Path,
    changed_files: list[str],
    added_files: list[str],
    timeout: int = 300,
    model: str | None = None,
    prompt: str | None = None,
    scanners: list[Scanner] | None = None,
) -> Verdict:
    """Run all scanners, write scanner_findings.md, run opencode, return a Verdict.

    Cleans up the workspace in all cases.

    Args:
        release:       The Release being analysed.
        old_artifact:  The previous release tarball metadata.
        new_artifact:  The new release tarball metadata.
        old_root:      Root of the extracted old release tarball (or None).
        new_root:      Root of the extracted new release tarball.
        changed_files: Relative paths of files changed vs. previous release.
        added_files:   Relative paths of files new in this release.
        timeout:       Maximum seconds to wait for opencode.
        model:         If set, passed as ``--model MODEL`` to opencode.
        prompt:        Prompt text to send.
        scanners:      Scanner instances to run before opencode.
    """
    workspace = Path(tempfile.mkdtemp())
    try:
        cf = changed_files or []
        af = added_files or []
        sections: list[str] = []

        for scanner in scanners or []:
            try:
                result = scanner.scan(old_root, new_root, cf, af)
                if result:
                    sections.append(f"## {scanner.name}\n\n{result}")
            except Exception as exc:  # noqa: BLE001
                log.warning("scanner %r raised unexpectedly: %s", scanner.name, exc)

        if sections:
            findings_text = "\n\n".join(sections)
            (workspace / "scanner_findings.md").write_text(
                findings_text, encoding="utf-8"
            )
            log.debug("wrote scanner_findings.md (%d section(s))", len(sections))

        raw_output, opencode_log_path = run_opencode(
            workspace, timeout, model=model, prompt=prompt
        )
        result_str, confidence, summary = parse_verdict(raw_output)

        log.info(
            "verdict: %s / %s  —  %s",
            result_str.upper(),
            confidence,
            summary[:80] or "(no summary)",
        )
        return Verdict(
            release=release,
            old_artifact=old_artifact,
            new_artifact=new_artifact,
            result=result_str,
            confidence=confidence,
            summary=summary,
            analysis=raw_output,
            analyzed_at=datetime.now(timezone.utc),
            opencode_log_path=opencode_log_path,
        )
    finally:
        shutil.rmtree(workspace, ignore_errors=True)
        log.debug("cleaned workspace %s", workspace)
