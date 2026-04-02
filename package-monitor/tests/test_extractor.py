"""Tests for scm.extractor — safe_extract, collect_files, is_text."""

from __future__ import annotations

import io
import os
import tarfile
from pathlib import Path

import pytest

from scm.extractor import (
    ExtractionError,
    collect_files,
    is_text,
    safe_extract,
)
from scm.models import StoredArtifact


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_tgz(tmp_path: Path, name: str, files: dict[str, bytes]) -> Path:
    """Create a .tgz archive at tmp_path/name.tgz containing given files dict."""
    tgz_path = tmp_path / f"{name}.tgz"
    with tarfile.open(tgz_path, "w:gz") as tf:
        for arcname, data in files.items():
            info = tarfile.TarInfo(name=arcname)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return tgz_path


# ---------------------------------------------------------------------------
# safe_extract
# ---------------------------------------------------------------------------


def test_safe_extract_normal_archive(tmp_path):
    tgz = _make_tgz(
        tmp_path,
        "pkg",
        {"package/index.js": b"console.log('hi')", "package/package.json": b"{}"},
    )
    dest = tmp_path / "extracted"
    dest.mkdir()
    root = safe_extract(tgz, dest)
    assert (root / "index.js").exists() or (dest / "package" / "index.js").exists()


def test_safe_extract_returns_single_subdir(tmp_path):
    tgz = _make_tgz(
        tmp_path,
        "pkg",
        {"package/index.js": b"x"},
    )
    dest = tmp_path / "out"
    dest.mkdir()
    root = safe_extract(tgz, dest)
    # Single top-level directory "package" — should return that dir
    assert root == dest / "package"


def test_safe_extract_traversal_path_raises(tmp_path):
    tgz_path = tmp_path / "evil.tgz"
    with tarfile.open(tgz_path, "w:gz") as tf:
        info = tarfile.TarInfo(name="../../etc/passwd")
        info.size = 6
        tf.addfile(info, io.BytesIO(b"secret"))
    dest = tmp_path / "out"
    dest.mkdir()
    with pytest.raises(ExtractionError, match="traversal"):
        safe_extract(tgz_path, dest)


def test_safe_extract_bad_archive_raises(tmp_path):
    bad = tmp_path / "bad.tgz"
    bad.write_bytes(b"not a tar file at all")
    dest = tmp_path / "out"
    dest.mkdir()
    with pytest.raises(ExtractionError, match="cannot open archive"):
        safe_extract(bad, dest)


def test_safe_extract_symlink_traversal_raises(tmp_path):
    """A symlink whose target escapes dest must raise ExtractionError."""
    tgz_path = tmp_path / "sym.tgz"
    with tarfile.open(tgz_path, "w:gz") as tf:
        # Create a symlink member pointing outside dest
        info = tarfile.TarInfo(name="package/evil_link")
        info.type = tarfile.SYMTYPE
        info.linkname = "../../../../etc/passwd"
        tf.addfile(info)
    dest = tmp_path / "out"
    dest.mkdir()
    with pytest.raises(ExtractionError, match="symlink traversal"):
        safe_extract(tgz_path, dest)


# ---------------------------------------------------------------------------
# collect_files
# ---------------------------------------------------------------------------


def test_collect_files_returns_relative_paths(tmp_path):
    (tmp_path / "a.js").write_text("a")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.js").write_text("b")
    files = collect_files(tmp_path)
    assert "a.js" in files
    assert str(Path("sub") / "b.js") in files


def test_collect_files_sorted(tmp_path):
    for name in ["z.js", "a.js", "m.js"]:
        (tmp_path / name).write_text(name)
    files = collect_files(tmp_path)
    keys = list(files.keys())
    assert keys == sorted(keys)


def test_collect_files_excludes_dirs(tmp_path):
    sub = tmp_path / "subdir"
    sub.mkdir()
    (sub / "f.js").write_text("x")
    files = collect_files(tmp_path)
    # "subdir" itself must not appear as a key
    assert all("subdir" not in k or "/" in k or os.sep in k for k in files)


# ---------------------------------------------------------------------------
# is_text
# ---------------------------------------------------------------------------


def test_is_text_ascii(tmp_path):
    f = tmp_path / "t.js"
    f.write_text("console.log('hello');", encoding="utf-8")
    assert is_text(f) is True


def test_is_text_utf8(tmp_path):
    f = tmp_path / "t.txt"
    f.write_text("héllo wörld", encoding="utf-8")
    assert is_text(f) is True


def test_is_text_binary(tmp_path):
    f = tmp_path / "img.png"
    # PNG header bytes — not valid UTF-8
    f.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00\xff\xfe" * 100)
    assert is_text(f) is False


def test_is_text_empty(tmp_path):
    f = tmp_path / "empty.js"
    f.write_bytes(b"")
    assert is_text(f) is True
