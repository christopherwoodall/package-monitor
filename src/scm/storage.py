"""Download tarballs, compute SHA-256, persist to binaries/.

Never deletes files.  Re-runs reuse cached tarballs.
No requests library — urllib.request.urlopen only.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import urllib.parse
import urllib.request
from pathlib import Path

from scm.models import StoredArtifact

log = logging.getLogger(__name__)

# Resolved at import time so CLI can override via storage.BINARIES_ROOT = Path(...)
# storage.py is at src/scm/storage.py  →  parents[2] = project root
BINARIES_ROOT: Path = Path(__file__).resolve().parents[2] / "binaries"
NPM_REGISTRY = "https://registry.npmjs.org"
PYPI_REGISTRY = "https://pypi.org/pypi"


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class DownloadError(Exception):
    """Raised on any failure to fetch or verify a tarball."""


# ---------------------------------------------------------------------------
# Hashing helpers
# ---------------------------------------------------------------------------


def sha256_file(path: Path) -> str:
    """Stream-hash a file; never loads it fully into memory."""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _check_integrity(path: Path, integrity: str) -> bool:
    """Verify a file against an npm integrity string (sha512-<b64> or sha1-<b64>)."""
    try:
        algo_str, b64 = integrity.split("-", 1)
        expected = base64.b64decode(b64)
        h = hashlib.new(algo_str)
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                h.update(chunk)
        return h.digest() == expected
    except Exception as exc:  # noqa: BLE001
        log.warning("integrity check failed for %s: %s", path, exc)
        return False


# ---------------------------------------------------------------------------
# npm tarball download
# ---------------------------------------------------------------------------


def download_npm_tarball(package: str, version: str) -> StoredArtifact:
    """Fetch (or return cached) a tarball for an npm package version.

    - Encodes scoped packages correctly: @scope/pkg → %40scope%2Fpkg
    - Streams to disk in 64 KiB chunks; never uses urlretrieve
    - Cleans up partial file on any exception
    - Returns a fully-populated StoredArtifact
    """
    encoded = urllib.parse.quote(package, safe="")
    meta_url = f"{NPM_REGISTRY}/{encoded}/{version}"
    log.info("fetching npm metadata  %s@%s", package, version)

    try:
        with urllib.request.urlopen(meta_url, timeout=30) as resp:  # noqa: S310
            meta = json.loads(resp.read())
    except Exception as exc:
        raise DownloadError(
            f"failed to fetch metadata for {package}@{version}: {exc}"
        ) from exc

    try:
        dist = meta["dist"]
        tarball_url: str = dist["tarball"]
        integrity: str = dist.get("integrity", "")
    except (KeyError, TypeError) as exc:
        raise DownloadError(
            f"unexpected metadata shape for {package}@{version}: {exc}"
        ) from exc

    filename = tarball_url.rsplit("/", 1)[-1]
    # Sanitise package name for filesystem: @scope/pkg → scope__pkg
    safe_pkg = package.lstrip("@").replace("/", "__")
    dest_dir = BINARIES_ROOT / "npm" / safe_pkg / version
    dest_path = dest_dir / filename

    # ── cache hit ──────────────────────────────────────────────────────────
    if dest_path.exists():
        if integrity and _check_integrity(dest_path, integrity):
            log.info("cache hit  %s@%s  (%s)", package, version, dest_path.name)
            return StoredArtifact(
                ecosystem="npm",
                package=package,
                version=version,
                filename=filename,
                path=dest_path,
                sha256=sha256_file(dest_path),
                size_bytes=dest_path.stat().st_size,
            )
        log.warning(
            "cache file exists but integrity mismatch — re-downloading %s@%s",
            package,
            version,
        )

    # ── download ───────────────────────────────────────────────────────────
    dest_dir.mkdir(parents=True, exist_ok=True)
    log.info("downloading  %s@%s  →  %s", package, version, dest_path)
    try:
        with urllib.request.urlopen(tarball_url, timeout=30) as resp:  # noqa: S310
            with dest_path.open("wb") as fh:
                for chunk in iter(lambda: resp.read(65536), b""):
                    fh.write(chunk)
    except Exception as exc:
        # Clean up partial file — binaries are only permanent once complete
        if dest_path.exists():
            dest_path.unlink(missing_ok=True)
            log.debug("removed partial file %s", dest_path)
        raise DownloadError(f"download failed for {package}@{version}: {exc}") from exc

    digest = sha256_file(dest_path)
    size = dest_path.stat().st_size
    log.info("saved  %s  sha256=%s…  size=%d B", dest_path.name, digest[:12], size)
    return StoredArtifact(
        ecosystem="npm",
        package=package,
        version=version,
        filename=filename,
        path=dest_path,
        sha256=digest,
        size_bytes=size,
    )


# ---------------------------------------------------------------------------
# PyPI tarball download
# ---------------------------------------------------------------------------


class NoSdistError(Exception):
    """Raised when a PyPI version has no sdist (source distribution) file."""


def download_pypi_tarball(package: str, version: str) -> StoredArtifact:
    """Fetch (or return cached) an sdist tarball for a PyPI package version.

    - Hits https://pypi.org/pypi/{package}/{version}/json for metadata
    - Finds the first sdist file entry; raises NoSdistError if none found
    - Streams to disk in 64 KiB chunks; verifies sha256 from PyPI metadata
    - Stores under binaries/pypi/<safe_pkg>/<version>/<filename>
    - Returns a fully-populated StoredArtifact
    """
    meta_url = f"{PYPI_REGISTRY}/{urllib.parse.quote(package, safe='')}/{version}/json"
    log.info("fetching PyPI metadata  %s@%s", package, version)

    try:
        with urllib.request.urlopen(meta_url, timeout=30) as resp:  # noqa: S310
            meta = json.loads(resp.read())
    except Exception as exc:
        raise DownloadError(
            f"failed to fetch PyPI metadata for {package}@{version}: {exc}"
        ) from exc

    # Find the sdist file entry in the release file list
    try:
        files = meta["urls"]
    except (KeyError, TypeError) as exc:
        raise DownloadError(
            f"unexpected PyPI metadata shape for {package}@{version}: {exc}"
        ) from exc

    sdist_entry: dict | None = None
    for f in files:
        if f.get("packagetype") == "sdist":
            sdist_entry = f
            break

    if sdist_entry is None:
        raise NoSdistError(
            f"{package}@{version} has no sdist — wheel-only release, skipping"
        )

    tarball_url: str = sdist_entry["url"]
    filename: str = sdist_entry["filename"]
    expected_sha256: str = sdist_entry.get("digests", {}).get("sha256", "")

    # Sanitise package name for filesystem: hyphens/dots are fine, but be safe
    safe_pkg = package.replace("/", "__")
    dest_dir = BINARIES_ROOT / "pypi" / safe_pkg / version
    dest_path = dest_dir / filename

    # ── cache hit ──────────────────────────────────────────────────────────
    if dest_path.exists():
        actual_sha256 = sha256_file(dest_path)
        if not expected_sha256 or actual_sha256 == expected_sha256:
            log.info("cache hit  %s@%s  (%s)", package, version, dest_path.name)
            return StoredArtifact(
                ecosystem="pypi",
                package=package,
                version=version,
                filename=filename,
                path=dest_path,
                sha256=actual_sha256,
                size_bytes=dest_path.stat().st_size,
            )
        log.warning(
            "cache file exists but sha256 mismatch — re-downloading %s@%s",
            package,
            version,
        )

    # ── download ───────────────────────────────────────────────────────────
    dest_dir.mkdir(parents=True, exist_ok=True)
    log.info("downloading  %s@%s  →  %s", package, version, dest_path)
    try:
        with urllib.request.urlopen(tarball_url, timeout=60) as resp:  # noqa: S310
            with dest_path.open("wb") as fh:
                for chunk in iter(lambda: resp.read(65536), b""):
                    fh.write(chunk)
    except Exception as exc:
        if dest_path.exists():
            dest_path.unlink(missing_ok=True)
            log.debug("removed partial file %s", dest_path)
        raise DownloadError(
            f"PyPI download failed for {package}@{version}: {exc}"
        ) from exc

    actual_sha256 = sha256_file(dest_path)
    if expected_sha256 and actual_sha256 != expected_sha256:
        dest_path.unlink(missing_ok=True)
        raise DownloadError(
            f"sha256 mismatch for {package}@{version}: "
            f"expected {expected_sha256} got {actual_sha256}"
        )

    size = dest_path.stat().st_size
    log.info(
        "saved  %s  sha256=%s…  size=%d B", dest_path.name, actual_sha256[:12], size
    )
    return StoredArtifact(
        ecosystem="pypi",
        package=package,
        version=version,
        filename=filename,
        path=dest_path,
        sha256=actual_sha256,
        size_bytes=size,
    )


# ---------------------------------------------------------------------------
# Ecosystem dispatcher
# ---------------------------------------------------------------------------

_ECOSYSTEM_DOWNLOADERS: dict[str, str] = {
    "npm": "download_npm_tarball",
    "pypi": "download_pypi_tarball",
}


def download_tarball(ecosystem: str, package: str, version: str) -> StoredArtifact:
    """Dispatch to the correct downloader based on ecosystem.

    Looks up the function by name at call time so patches applied in tests
    are respected (avoids stale references in a pre-built dict).

    Raises ValueError for unknown ecosystems.
    """
    import sys

    fn_name = _ECOSYSTEM_DOWNLOADERS.get(ecosystem)
    if fn_name is None:
        raise ValueError(f"no downloader registered for ecosystem {ecosystem!r}")
    # Look up in this module so mocker.patch("scm.storage.download_X") works
    module = sys.modules[__name__]
    fn = getattr(module, fn_name)
    return fn(package, version)
