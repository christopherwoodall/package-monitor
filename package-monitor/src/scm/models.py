"""Pure dataclasses — zero logic, zero imports beyond stdlib typing."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass
class Release:
    ecosystem: str  # "npm", "pypi", …
    package: str
    version: str
    previous_version: str | None
    rank: int
    discovered_at: datetime  # UTC


@dataclass
class StoredArtifact:
    """A tarball saved permanently to disk."""

    ecosystem: str
    package: str
    version: str
    filename: str
    path: Path  # absolute path inside binaries/
    sha256: str
    size_bytes: int


@dataclass
class Verdict:
    release: Release
    old_artifact: StoredArtifact
    new_artifact: StoredArtifact
    result: str  # "malicious" | "benign" | "unknown" | "error"
    confidence: str  # "high" | "medium" | "low" | "unknown"
    summary: str  # one-sentence summary suitable for a tweet
    analysis: str  # full raw text (ANSI-stripped)
    analyzed_at: datetime  # UTC
    opencode_log_path: str | None = None  # path to opencode log file for this run


@dataclass
class Alert:
    verdict: Verdict
    notifier: str
    sent_at: datetime
    success: bool
    detail: str  # error message, file path, or tweet ID
