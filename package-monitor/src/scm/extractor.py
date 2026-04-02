"""Safe tarball extraction and file-collection helpers.

Never touches the network, database, or diff logic.
"""

from __future__ import annotations

import logging
import tarfile
from pathlib import Path

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class ExtractionError(Exception):
    """Raised when a tar archive contains unsafe paths or symlinks."""


# ---------------------------------------------------------------------------
# Safe extraction
# ---------------------------------------------------------------------------


def safe_extract(archive: Path, dest: Path) -> Path:
    """Extract a .tar.gz / .tgz / .tar.bz2 to *dest* with traversal protection.

    For every member:
    - Resolves final path and asserts it stays inside dest  (uses is_relative_to,
      NOT string prefix — see AGENTS.md Lesson 5).
    - For symlinks: resolves the link target and applies the same check.

    Returns the single top-level directory if the archive contains exactly one,
    otherwise returns dest itself.
    """
    dest_resolved = dest.resolve()
    try:
        tf = tarfile.open(archive)
    except tarfile.TarError as exc:
        raise ExtractionError(f"cannot open archive {archive}: {exc}") from exc

    with tf:
        for member in tf.getmembers():
            member_path = (dest / member.name).resolve()
            if not member_path.is_relative_to(dest_resolved):
                raise ExtractionError(
                    f"path traversal detected: {member.name!r} escapes {dest}"
                )
            if member.issym() or member.islnk():
                # Resolve symlink target relative to the directory it would live in
                member_dir = (dest / member.name).parent
                link_target = (member_dir / member.linkname).resolve()
                if not link_target.is_relative_to(dest_resolved):
                    raise ExtractionError(
                        f"symlink traversal detected: {member.name!r} → {member.linkname!r}"
                    )
        tf.extractall(dest)  # noqa: S202 — members already validated above

    children = [p for p in dest.iterdir()]
    if len(children) == 1 and children[0].is_dir():
        return children[0]
    return dest


# ---------------------------------------------------------------------------
# File helpers
# ---------------------------------------------------------------------------


def collect_files(root: Path) -> dict[str, Path]:
    """Return a sorted mapping of relative-path-string → absolute-Path for every file."""
    return dict(
        sorted(
            {
                str(p.relative_to(root)): p for p in root.rglob("*") if p.is_file()
            }.items()
        )
    )


def is_text(path: Path) -> bool:
    """Return True if the first 8 KiB of the file decodes as UTF-8."""
    try:
        path.read_bytes()[:8192].decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False
