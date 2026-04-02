"""Tests for scm.storage — sha256_file, _check_integrity, download_npm_tarball,
download_pypi_tarball, download_tarball."""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import scm.storage as storage_module
from scm.models import StoredArtifact
from scm.storage import (
    DownloadError,
    NoSdistError,
    _check_integrity,
    download_npm_tarball,
    download_pypi_tarball,
    download_tarball,
    sha256_file,
)


# ---------------------------------------------------------------------------
# sha256_file
# ---------------------------------------------------------------------------


def test_sha256_file_known_hash(tmp_path):
    f = tmp_path / "data.bin"
    f.write_bytes(b"hello world")
    expected = hashlib.sha256(b"hello world").hexdigest()
    assert sha256_file(f) == expected


def test_sha256_file_empty(tmp_path):
    f = tmp_path / "empty.bin"
    f.write_bytes(b"")
    expected = hashlib.sha256(b"").hexdigest()
    assert sha256_file(f) == expected


def test_sha256_file_large(tmp_path):
    # 200 KiB — exercises the chunked read path
    data = b"x" * 200_000
    f = tmp_path / "big.bin"
    f.write_bytes(data)
    expected = hashlib.sha256(data).hexdigest()
    assert sha256_file(f) == expected


# ---------------------------------------------------------------------------
# _check_integrity
# ---------------------------------------------------------------------------


def test_check_integrity_sha512_valid(tmp_path):
    import base64

    data = b"test content"
    digest = hashlib.sha512(data).digest()
    integrity = "sha512-" + base64.b64encode(digest).decode()
    f = tmp_path / "f.bin"
    f.write_bytes(data)
    assert _check_integrity(f, integrity) is True


def test_check_integrity_sha512_invalid(tmp_path):
    import base64

    f = tmp_path / "f.bin"
    f.write_bytes(b"real content")
    bogus = "sha512-" + base64.b64encode(b"wrong" * 10).decode()
    assert _check_integrity(f, bogus) is False


def test_check_integrity_malformed_string(tmp_path):
    f = tmp_path / "f.bin"
    f.write_bytes(b"data")
    # No dash separator — should return False, not raise
    assert _check_integrity(f, "notvalid") is False


def test_check_integrity_unknown_algo(tmp_path):
    f = tmp_path / "f.bin"
    f.write_bytes(b"data")
    import base64

    bogus = "md99-" + base64.b64encode(b"whatever").decode()
    assert _check_integrity(f, bogus) is False


# ---------------------------------------------------------------------------
# download_npm_tarball — network mocked
# ---------------------------------------------------------------------------


def _fake_meta_response(tarball_url: str, integrity: str = "") -> bytes:
    meta = {
        "name": "lodash",
        "version": "4.17.21",
        "dist": {
            "tarball": tarball_url,
            "integrity": integrity,
        },
    }
    return json.dumps(meta).encode()


def _make_fake_tarball(tmp_path: Path) -> Path:
    """Create a minimal gzip file (not a real tarball, but enough for size/sha256)."""
    import gzip

    p = tmp_path / "lodash-4.17.21.tgz"
    p.write_bytes(gzip.compress(b"fake tarball content"))
    return p


class _FakeHTTPResponse:
    """Minimal mock for urllib response context manager."""

    def __init__(self, data: bytes):
        self._data = io.BytesIO(data)

    def read(self, n: int = -1) -> bytes:
        return self._data.read(n)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass


def test_download_npm_tarball_success(tmp_path, mocker):
    tarball_data = b"fake tarball data " * 100
    tarball_filename = "lodash-4.17.21.tgz"
    tarball_url = f"https://registry.npmjs.org/lodash/-/{tarball_filename}"

    meta_resp = _FakeHTTPResponse(_fake_meta_response(tarball_url))
    tgz_resp = _FakeHTTPResponse(tarball_data)

    call_count = 0

    def fake_urlopen(req_or_url, timeout=None):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return meta_resp
        return tgz_resp

    mocker.patch("scm.storage.BINARIES_ROOT", tmp_path / "binaries")
    mocker.patch("urllib.request.urlopen", side_effect=fake_urlopen)

    artifact = download_npm_tarball("lodash", "4.17.21")
    assert isinstance(artifact, StoredArtifact)
    assert artifact.package == "lodash"
    assert artifact.version == "4.17.21"
    assert artifact.filename == tarball_filename
    assert artifact.size_bytes == len(tarball_data)
    assert artifact.path.exists()


def test_download_npm_tarball_cache_hit(tmp_path, mocker):
    import base64

    tarball_data = b"cached tarball"
    tarball_filename = "lodash-4.17.21.tgz"
    tarball_url = f"https://registry.npmjs.org/lodash/-/{tarball_filename}"

    # Pre-create the destination file
    dest_dir = tmp_path / "binaries" / "npm" / "lodash" / "4.17.21"
    dest_dir.mkdir(parents=True)
    dest_path = dest_dir / tarball_filename
    dest_path.write_bytes(tarball_data)

    # Build valid integrity string
    digest = hashlib.sha512(tarball_data).digest()
    integrity = "sha512-" + base64.b64encode(digest).decode()

    meta_resp = _FakeHTTPResponse(_fake_meta_response(tarball_url, integrity))

    urlopen_calls = []

    def fake_urlopen(req_or_url, timeout=None):
        urlopen_calls.append(req_or_url)
        return meta_resp

    mocker.patch("scm.storage.BINARIES_ROOT", tmp_path / "binaries")
    mocker.patch("urllib.request.urlopen", side_effect=fake_urlopen)

    artifact = download_npm_tarball("lodash", "4.17.21")
    # Only one urlopen call (for metadata); no download call
    assert len(urlopen_calls) == 1
    assert artifact.size_bytes == len(tarball_data)


def test_download_npm_tarball_metadata_failure(tmp_path, mocker):
    mocker.patch("scm.storage.BINARIES_ROOT", tmp_path / "binaries")
    mocker.patch(
        "urllib.request.urlopen",
        side_effect=Exception("connection refused"),
    )
    with pytest.raises(DownloadError, match="failed to fetch metadata"):
        download_npm_tarball("lodash", "4.17.21")


def test_download_npm_tarball_download_failure_cleans_up(tmp_path, mocker):
    tarball_url = "https://registry.npmjs.org/lodash/-/lodash-4.17.21.tgz"
    meta_resp = _FakeHTTPResponse(_fake_meta_response(tarball_url))

    call_count = 0

    def fake_urlopen(req_or_url, timeout=None):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return meta_resp
        raise Exception("download failed mid-stream")

    mocker.patch("scm.storage.BINARIES_ROOT", tmp_path / "binaries")
    mocker.patch("urllib.request.urlopen", side_effect=fake_urlopen)

    with pytest.raises(DownloadError, match="download failed"):
        download_npm_tarball("lodash", "4.17.21")

    # Partial file must be cleaned up
    partial = (
        tmp_path / "binaries" / "npm" / "lodash" / "4.17.21" / "lodash-4.17.21.tgz"
    )
    assert not partial.exists()


def test_download_npm_tarball_scoped_package_sanitised(tmp_path, mocker):
    """@scope/pkg should produce safe_pkg = 'scope__pkg' on the filesystem."""
    tarball_data = b"scoped tarball"
    tarball_url = "https://registry.npmjs.org/@babel/core/-/core-7.0.0.tgz"
    meta_resp = _FakeHTTPResponse(_fake_meta_response(tarball_url))
    tgz_resp = _FakeHTTPResponse(tarball_data)

    call_count = 0

    def fake_urlopen(req_or_url, timeout=None):
        nonlocal call_count
        call_count += 1
        return meta_resp if call_count == 1 else tgz_resp

    mocker.patch("scm.storage.BINARIES_ROOT", tmp_path / "binaries")
    mocker.patch("urllib.request.urlopen", side_effect=fake_urlopen)

    artifact = download_npm_tarball("@babel/core", "7.0.0")
    # Path must not contain '@' or '/'
    assert "@" not in str(artifact.path)
    assert "babel__core" in str(artifact.path)


# ---------------------------------------------------------------------------
# download_pypi_tarball — network mocked
# ---------------------------------------------------------------------------


def _fake_pypi_meta(
    package: str,
    version: str,
    filename: str,
    tarball_url: str,
    sha256: str = "",
    packagetype: str = "sdist",
) -> bytes:
    meta = {
        "info": {"name": package, "version": version},
        "urls": [
            {
                "packagetype": packagetype,
                "filename": filename,
                "url": tarball_url,
                "digests": {"sha256": sha256},
                "upload_time_iso_8601": "2024-05-01T12:00:00.000000Z",
            }
        ],
    }
    return json.dumps(meta).encode()


def test_download_pypi_tarball_success(tmp_path, mocker):
    tarball_data = b"fake sdist content " * 100
    filename = "requests-2.32.0.tar.gz"
    tarball_url = f"https://files.pythonhosted.org/{filename}"
    expected_sha256 = hashlib.sha256(tarball_data).hexdigest()

    meta_resp = _FakeHTTPResponse(
        _fake_pypi_meta(
            "requests", "2.32.0", filename, tarball_url, sha256=expected_sha256
        )
    )
    tgz_resp = _FakeHTTPResponse(tarball_data)

    call_count = 0

    def fake_urlopen(req_or_url, timeout=None):
        nonlocal call_count
        call_count += 1
        return meta_resp if call_count == 1 else tgz_resp

    mocker.patch("scm.storage.BINARIES_ROOT", tmp_path / "binaries")
    mocker.patch("urllib.request.urlopen", side_effect=fake_urlopen)

    artifact = download_pypi_tarball("requests", "2.32.0")
    assert isinstance(artifact, StoredArtifact)
    assert artifact.ecosystem == "pypi"
    assert artifact.package == "requests"
    assert artifact.version == "2.32.0"
    assert artifact.filename == filename
    assert artifact.sha256 == expected_sha256
    assert artifact.size_bytes == len(tarball_data)
    assert artifact.path.exists()


def test_download_pypi_tarball_cache_hit(tmp_path, mocker):
    tarball_data = b"cached pypi sdist"
    filename = "requests-2.32.0.tar.gz"
    tarball_url = f"https://files.pythonhosted.org/{filename}"
    expected_sha256 = hashlib.sha256(tarball_data).hexdigest()

    # Pre-create the cached file
    dest_dir = tmp_path / "binaries" / "pypi" / "requests" / "2.32.0"
    dest_dir.mkdir(parents=True)
    dest_path = dest_dir / filename
    dest_path.write_bytes(tarball_data)

    meta_resp = _FakeHTTPResponse(
        _fake_pypi_meta(
            "requests", "2.32.0", filename, tarball_url, sha256=expected_sha256
        )
    )

    urlopen_calls = []

    def fake_urlopen(req_or_url, timeout=None):
        urlopen_calls.append(req_or_url)
        return meta_resp

    mocker.patch("scm.storage.BINARIES_ROOT", tmp_path / "binaries")
    mocker.patch("urllib.request.urlopen", side_effect=fake_urlopen)

    artifact = download_pypi_tarball("requests", "2.32.0")
    # Only metadata call — no download
    assert len(urlopen_calls) == 1
    assert artifact.sha256 == expected_sha256


def test_download_pypi_tarball_no_sdist_raises(tmp_path, mocker):
    filename = "requests-2.32.0-py3-none-any.whl"
    tarball_url = f"https://files.pythonhosted.org/{filename}"

    meta_resp = _FakeHTTPResponse(
        _fake_pypi_meta(
            "requests", "2.32.0", filename, tarball_url, packagetype="bdist_wheel"
        )
    )

    mocker.patch("scm.storage.BINARIES_ROOT", tmp_path / "binaries")
    mocker.patch("urllib.request.urlopen", return_value=meta_resp)

    with pytest.raises(NoSdistError, match="wheel-only"):
        download_pypi_tarball("requests", "2.32.0")


def test_download_pypi_tarball_metadata_failure(tmp_path, mocker):
    mocker.patch("scm.storage.BINARIES_ROOT", tmp_path / "binaries")
    mocker.patch(
        "urllib.request.urlopen",
        side_effect=Exception("connection refused"),
    )
    with pytest.raises(DownloadError, match="failed to fetch PyPI metadata"):
        download_pypi_tarball("requests", "2.32.0")


def test_download_pypi_tarball_download_failure_cleans_up(tmp_path, mocker):
    filename = "requests-2.32.0.tar.gz"
    tarball_url = f"https://files.pythonhosted.org/{filename}"
    meta_resp = _FakeHTTPResponse(
        _fake_pypi_meta("requests", "2.32.0", filename, tarball_url)
    )

    call_count = 0

    def fake_urlopen(req_or_url, timeout=None):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return meta_resp
        raise Exception("network dropped")

    mocker.patch("scm.storage.BINARIES_ROOT", tmp_path / "binaries")
    mocker.patch("urllib.request.urlopen", side_effect=fake_urlopen)

    with pytest.raises(DownloadError, match="PyPI download failed"):
        download_pypi_tarball("requests", "2.32.0")

    partial = tmp_path / "binaries" / "pypi" / "requests" / "2.32.0" / filename
    assert not partial.exists()


def test_download_pypi_tarball_sha256_mismatch_deletes_file(tmp_path, mocker):
    tarball_data = b"actual content"
    filename = "requests-2.32.0.tar.gz"
    tarball_url = f"https://files.pythonhosted.org/{filename}"
    wrong_sha256 = "a" * 64  # deliberate mismatch

    meta_resp = _FakeHTTPResponse(
        _fake_pypi_meta(
            "requests", "2.32.0", filename, tarball_url, sha256=wrong_sha256
        )
    )
    tgz_resp = _FakeHTTPResponse(tarball_data)

    call_count = 0

    def fake_urlopen(req_or_url, timeout=None):
        nonlocal call_count
        call_count += 1
        return meta_resp if call_count == 1 else tgz_resp

    mocker.patch("scm.storage.BINARIES_ROOT", tmp_path / "binaries")
    mocker.patch("urllib.request.urlopen", side_effect=fake_urlopen)

    with pytest.raises(DownloadError, match="sha256 mismatch"):
        download_pypi_tarball("requests", "2.32.0")

    dest = tmp_path / "binaries" / "pypi" / "requests" / "2.32.0" / filename
    assert not dest.exists()


# ---------------------------------------------------------------------------
# download_tarball dispatcher
# ---------------------------------------------------------------------------


def test_download_tarball_dispatches_npm(mocker):
    mock_npm = mocker.patch(
        "scm.storage.download_npm_tarball", return_value=MagicMock()
    )
    download_tarball("npm", "lodash", "4.17.21")
    mock_npm.assert_called_once_with("lodash", "4.17.21")


def test_download_tarball_dispatches_pypi(mocker):
    mock_pypi = mocker.patch(
        "scm.storage.download_pypi_tarball", return_value=MagicMock()
    )
    download_tarball("pypi", "requests", "2.32.0")
    mock_pypi.assert_called_once_with("requests", "2.32.0")


def test_download_tarball_unknown_ecosystem_raises():
    with pytest.raises(ValueError, match="no downloader registered"):
        download_tarball("crates", "serde", "1.0.0")
