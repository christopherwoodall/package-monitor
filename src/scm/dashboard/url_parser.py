"""Parse package registry URLs into (ecosystem, package, version | None).

Supported registries and URL patterns:

npm
---
  https://www.npmjs.com/package/{pkg}
  https://www.npmjs.com/package/{pkg}/v/{version}
  https://npmjs.com/package/{pkg}
  https://npmjs.org/package/{pkg}
  https://registry.npmjs.org/{pkg}
  https://registry.npmjs.org/{pkg}/{version}
  Scoped packages (@scope/name) work in all of the above.

PyPI
----
  https://pypi.org/project/{pkg}/
  https://pypi.org/project/{pkg}/{version}/
  https://pypi.python.org/pypi/{pkg}
  https://pypi.python.org/pypi/{pkg}/{version}
  https://pypi.python.org/project/{pkg}
  https://pypi.python.org/project/{pkg}/{version}
  https://test.pypi.org/project/{pkg}/
  https://test.pypi.org/project/{pkg}/{version}/

Version resolution (when URL carries no version)
-------------------------------------------------
  npm  : GET https://registry.npmjs.org/{pkg} → dist-tags.latest
  PyPI : GET https://pypi.org/pypi/{pkg}/json → info.version
"""

from __future__ import annotations

import json
import logging
import re
import urllib.parse
import urllib.request

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Public exception
# ---------------------------------------------------------------------------


class UnsupportedURLError(ValueError):
    """Raised when the URL cannot be mapped to a known registry."""


class PackageNotFoundError(LookupError):
    """Raised when the registry returns 404 or the package does not exist."""


# ---------------------------------------------------------------------------
# Parsed result
# ---------------------------------------------------------------------------


class ParsedPackageURL:
    """Result of :func:`parse_package_url`.

    Attributes:
        ecosystem: ``"npm"`` or ``"pypi"``.
        package:   Canonical package name as returned by the registry.
        version:   Resolved version string.
        resolved_from: ``"url"`` if the version came from the URL itself,
                       ``"latest"`` if it was resolved via the registry API.
    """

    __slots__ = ("ecosystem", "package", "version", "resolved_from")

    def __init__(
        self,
        ecosystem: str,
        package: str,
        version: str,
        resolved_from: str,
    ) -> None:
        self.ecosystem = ecosystem
        self.package = package
        self.version = version
        self.resolved_from = resolved_from

    def __repr__(self) -> str:
        return (
            f"ParsedPackageURL(ecosystem={self.ecosystem!r}, "
            f"package={self.package!r}, version={self.version!r}, "
            f"resolved_from={self.resolved_from!r})"
        )


# ---------------------------------------------------------------------------
# Internal: npm URL patterns
# ---------------------------------------------------------------------------

# Matches  /package/@scope/name  or  /package/name  with optional /v/{ver}
_NPM_PACKAGE_PATH = re.compile(
    r"^/package/"
    r"(?P<pkg>(?:@[^/]+/[^/]+|[^/]+))"  # bare or scoped name
    r"(?:/v/(?P<ver>[^/]+))?/?$"
)

# Matches  /package/@scope/name/versions/{ver}  (legacy npmjs URL)
_NPM_PACKAGE_VERSIONS_PATH = re.compile(
    r"^/package/"
    r"(?P<pkg>(?:@[^/]+/[^/]+|[^/]+))"
    r"/versions/(?P<ver>[^/]+)/?$"
)

# Matches  /{pkg}  or  /{pkg}/{version}  on registry.npmjs.org
# Scoped: /@scope/name  or  /@scope/name/ver
_NPM_REGISTRY_PATH = re.compile(
    r"^/"
    r"(?P<pkg>(?:@[^/]+/[^/]+|[^/]+))"
    r"(?:/(?P<ver>[^/]+))?/?$"
)

_NPM_HOSTS = {"www.npmjs.com", "npmjs.com", "npmjs.org"}
_NPM_REGISTRY_HOSTS = {"registry.npmjs.org", "registry.npmjs.com"}


def _parse_npm(parsed: urllib.parse.ParseResult) -> tuple[str, str | None]:
    """Return (package, version|None) from a parsed npm URL, or raise UnsupportedURLError."""
    host = parsed.netloc.lower().split(":")[0]
    path = parsed.path

    if host in _NPM_HOSTS:
        m = _NPM_PACKAGE_VERSIONS_PATH.match(path)
        if m:
            return _decode(m.group("pkg")), _decode(m.group("ver"))
        m = _NPM_PACKAGE_PATH.match(path)
        if m:
            ver = m.group("ver")
            return _decode(m.group("pkg")), _decode(ver) if ver else None
        raise UnsupportedURLError(f"Unrecognised npmjs.com path: {path!r}")

    if host in _NPM_REGISTRY_HOSTS:
        m = _NPM_REGISTRY_PATH.match(path)
        if m:
            pkg = m.group("pkg")
            ver = m.group("ver")
            # Skip metadata segments like "latest", "dist-tags", etc. that
            # aren't version strings — they look like bare paths on the registry.
            if ver in ("latest", "dist-tags", "access", "time"):
                ver = None
            return _decode(pkg), _decode(ver) if ver else None
        raise UnsupportedURLError(f"Unrecognised registry.npmjs.org path: {path!r}")

    raise UnsupportedURLError(f"Unknown npm host: {host!r}")


# ---------------------------------------------------------------------------
# Internal: PyPI URL patterns
# ---------------------------------------------------------------------------

_PYPI_PROJECT_PATH = re.compile(r"^/project/(?P<pkg>[^/]+)(?:/(?P<ver>[^/]+))?/?$")

_PYPI_LEGACY_PATH = re.compile(r"^/pypi/(?P<pkg>[^/]+)(?:/(?P<ver>[^/]+))?/?$")

_PYPI_HOSTS = {"pypi.org", "test.pypi.org", "pypi.python.org"}


def _parse_pypi(parsed: urllib.parse.ParseResult) -> tuple[str, str | None]:
    """Return (package, version|None) from a parsed PyPI URL, or raise UnsupportedURLError."""
    host = parsed.netloc.lower().split(":")[0]
    path = parsed.path

    if host not in _PYPI_HOSTS:
        raise UnsupportedURLError(f"Unknown PyPI host: {host!r}")

    # /project/{pkg}[/{ver}]
    m = _PYPI_PROJECT_PATH.match(path)
    if m:
        ver = m.group("ver")
        return _decode(m.group("pkg")), _decode(ver) if ver else None

    # /pypi/{pkg}[/{ver}]
    m = _PYPI_LEGACY_PATH.match(path)
    if m:
        ver = m.group("ver")
        # Skip the /json segment used by the JSON API
        if ver and ver.lower() == "json":
            ver = None
        return _decode(m.group("pkg")), _decode(ver) if ver else None

    raise UnsupportedURLError(f"Unrecognised PyPI path: {path!r}")


# ---------------------------------------------------------------------------
# Internal: version resolution
# ---------------------------------------------------------------------------

_NPM_REGISTRY = "https://registry.npmjs.org"
_PYPI_JSON_API = "https://pypi.org/pypi"


def _resolve_npm_latest(package: str) -> str:
    """Fetch the latest version tag for an npm package."""
    encoded = urllib.parse.quote(package, safe="@/")
    url = f"{_NPM_REGISTRY}/{encoded}"
    log.debug("resolving npm latest for %s via %s", package, url)
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:  # noqa: S310
            data = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise PackageNotFoundError(f"npm package not found: {package!r}") from exc
        raise PackageNotFoundError(
            f"registry error for npm package {package!r}: {exc}"
        ) from exc
    except Exception as exc:
        raise PackageNotFoundError(
            f"could not reach npm registry for {package!r}: {exc}"
        ) from exc

    version: str | None = data.get("dist-tags", {}).get("latest")
    if not version:
        raise PackageNotFoundError(f"npm package {package!r} has no dist-tags.latest")
    return version


def _resolve_pypi_latest(package: str) -> str:
    """Fetch the latest version for a PyPI package."""
    encoded = urllib.parse.quote(package.lower(), safe="")
    url = f"{_PYPI_JSON_API}/{encoded}/json"
    log.debug("resolving PyPI latest for %s via %s", package, url)
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:  # noqa: S310
            data = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise PackageNotFoundError(f"PyPI package not found: {package!r}") from exc
        raise PackageNotFoundError(
            f"registry error for PyPI package {package!r}: {exc}"
        ) from exc
    except Exception as exc:
        raise PackageNotFoundError(
            f"could not reach PyPI for {package!r}: {exc}"
        ) from exc

    version: str | None = data.get("info", {}).get("version")
    if not version:
        raise PackageNotFoundError(f"PyPI package {package!r} has no info.version")
    return version


# ---------------------------------------------------------------------------
# Internal: helpers
# ---------------------------------------------------------------------------


def _decode(s: str) -> str:
    """Percent-decode a URL path segment."""
    return urllib.parse.unquote(s)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def parse_package_url(url: str) -> ParsedPackageURL:
    """Parse *url* into a :class:`ParsedPackageURL`.

    If the URL contains no version, the latest version is resolved from the
    registry (one live HTTP request).

    Args:
        url: Any supported package registry URL.

    Returns:
        A :class:`ParsedPackageURL` with ecosystem, package, version, and
        resolved_from set.

    Raises:
        UnsupportedURLError: The URL cannot be mapped to a known registry.
        PackageNotFoundError: The registry returned 404 or has no version info.
        ValueError: The URL is not parseable.
    """
    url = url.strip()
    # Normalise: add scheme if missing so urllib can parse the host
    if url.startswith("//"):
        url = "https:" + url
    elif not url.startswith(("http://", "https://")):
        url = "https://" + url

    parsed = urllib.parse.urlparse(url)
    host = parsed.netloc.lower().split(":")[0]

    # ── npm ──────────────────────────────────────────────────────────────────
    if host in _NPM_HOSTS or host in _NPM_REGISTRY_HOSTS:
        package, version = _parse_npm(parsed)
        if version is None:
            version = _resolve_npm_latest(package)
            resolved_from = "latest"
        else:
            resolved_from = "url"
        log.info(
            "parsed npm URL → %s@%s (resolved_from=%s)", package, version, resolved_from
        )
        return ParsedPackageURL("npm", package, version, resolved_from)

    # ── PyPI ─────────────────────────────────────────────────────────────────
    if host in _PYPI_HOSTS:
        package, version = _parse_pypi(parsed)
        if version is None:
            version = _resolve_pypi_latest(package)
            resolved_from = "latest"
        else:
            resolved_from = "url"
        log.info(
            "parsed PyPI URL → %s@%s (resolved_from=%s)",
            package,
            version,
            resolved_from,
        )
        return ParsedPackageURL("pypi", package, version, resolved_from)

    raise UnsupportedURLError(
        f"Unsupported registry host: {host!r}. "
        "Supported registries: npmjs.com, registry.npmjs.org, pypi.org, "
        "pypi.python.org, test.pypi.org."
    )
