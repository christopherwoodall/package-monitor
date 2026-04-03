"""Tests for scm.dashboard.url_parser."""

from __future__ import annotations

import json
import urllib.error
from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest

from scm.dashboard.url_parser import (
    PackageNotFoundError,
    ParsedPackageURL,
    UnsupportedURLError,
    parse_package_url,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_urlopen(body: dict | str, status: int = 200):
    """Return a context-manager mock that yields a response-like object."""
    if isinstance(body, dict):
        raw = json.dumps(body).encode()
    else:
        raw = body.encode()

    cm = MagicMock()
    cm.__enter__ = MagicMock(return_value=MagicMock(read=MagicMock(return_value=raw)))
    cm.__exit__ = MagicMock(return_value=False)
    return cm


def _npm_packument(latest: str = "4.17.22") -> dict:
    return {"dist-tags": {"latest": latest}, "name": "lodash"}


def _pypi_info(version: str = "2.28.2") -> dict:
    return {"info": {"name": "requests", "version": version}}


# ---------------------------------------------------------------------------
# npm — URL parsing (no network)
# ---------------------------------------------------------------------------


class TestNpmUrlParsing:
    """Verify URL → (package, version|None) extraction without making HTTP calls."""

    @pytest.mark.parametrize(
        "url,expected_pkg,expected_ver",
        [
            # Standard npmjs.com
            ("https://www.npmjs.com/package/lodash", "lodash", None),
            ("https://npmjs.com/package/lodash", "lodash", None),
            ("https://npmjs.org/package/lodash", "lodash", None),
            # With explicit version
            ("https://www.npmjs.com/package/lodash/v/4.17.22", "lodash", "4.17.22"),
            # versions path (legacy)
            (
                "https://www.npmjs.com/package/lodash/versions/4.17.21",
                "lodash",
                "4.17.21",
            ),
            # Scoped packages
            ("https://www.npmjs.com/package/@scope/name", "@scope/name", None),
            (
                "https://www.npmjs.com/package/@scope/name/v/1.2.3",
                "@scope/name",
                "1.2.3",
            ),
            # registry.npmjs.org
            ("https://registry.npmjs.org/lodash", "lodash", None),
            ("https://registry.npmjs.org/lodash/4.17.22", "lodash", "4.17.22"),
            ("https://registry.npmjs.org/@scope/name", "@scope/name", None),
            ("https://registry.npmjs.org/@scope/name/1.0.0", "@scope/name", "1.0.0"),
            # Trailing slash
            ("https://www.npmjs.com/package/express/", "express", None),
            # No scheme
            ("www.npmjs.com/package/lodash", "lodash", None),
            # Fragment / query ignored
            ("https://www.npmjs.com/package/lodash?tab=versions", "lodash", None),
        ],
    )
    def test_parse_npm_url(self, url, expected_pkg, expected_ver, mocker):
        if expected_ver is None:
            # Prevent actual HTTP call — version resolution not needed for parsing test
            mocker.patch(
                "scm.dashboard.url_parser._resolve_npm_latest",
                return_value="9.9.9",
            )
        result = parse_package_url(url)
        assert result.ecosystem == "npm"
        assert result.package == expected_pkg
        if expected_ver is not None:
            assert result.version == expected_ver
            assert result.resolved_from == "url"

    def test_npm_url_with_version_no_network_call(self, mocker):
        mock_resolve = mocker.patch("scm.dashboard.url_parser._resolve_npm_latest")
        parse_package_url("https://www.npmjs.com/package/lodash/v/4.17.22")
        mock_resolve.assert_not_called()

    def test_npm_url_without_version_calls_resolve(self, mocker):
        mock_resolve = mocker.patch(
            "scm.dashboard.url_parser._resolve_npm_latest", return_value="4.17.22"
        )
        result = parse_package_url("https://www.npmjs.com/package/lodash")
        mock_resolve.assert_called_once_with("lodash")
        assert result.version == "4.17.22"
        assert result.resolved_from == "latest"

    def test_npm_scoped_version_from_url(self, mocker):
        mocker.patch(
            "scm.dashboard.url_parser._resolve_npm_latest", return_value="1.0.0"
        )
        result = parse_package_url("https://www.npmjs.com/package/@babel/core/v/7.23.0")
        assert result.package == "@babel/core"
        assert result.version == "7.23.0"
        assert result.resolved_from == "url"

    def test_npm_scoped_no_version_resolves_latest(self, mocker):
        mocker.patch(
            "scm.dashboard.url_parser._resolve_npm_latest", return_value="7.23.0"
        )
        result = parse_package_url("https://www.npmjs.com/package/@babel/core")
        assert result.package == "@babel/core"
        assert result.version == "7.23.0"
        assert result.resolved_from == "latest"

    def test_npm_percent_encoded_scoped(self, mocker):
        mocker.patch(
            "scm.dashboard.url_parser._resolve_npm_latest", return_value="1.0.0"
        )
        result = parse_package_url("https://www.npmjs.com/package/%40babel%2Fcore")
        assert result.package == "@babel/core"

    def test_registry_npmjs_latest_segment_not_treated_as_version(self, mocker):
        mocker.patch(
            "scm.dashboard.url_parser._resolve_npm_latest", return_value="4.17.22"
        )
        result = parse_package_url("https://registry.npmjs.org/lodash/latest")
        # "latest" is skipped as a version segment → resolved from registry
        assert result.resolved_from == "latest"


# ---------------------------------------------------------------------------
# PyPI — URL parsing (no network)
# ---------------------------------------------------------------------------


class TestPypiUrlParsing:
    @pytest.mark.parametrize(
        "url,expected_pkg,expected_ver",
        [
            # pypi.org /project/
            ("https://pypi.org/project/requests/", "requests", None),
            ("https://pypi.org/project/requests/2.28.2/", "requests", "2.28.2"),
            # test.pypi.org
            ("https://test.pypi.org/project/requests/", "requests", None),
            ("https://test.pypi.org/project/requests/2.28.2/", "requests", "2.28.2"),
            # Legacy pypi.python.org /pypi/
            ("https://pypi.python.org/pypi/requests", "requests", None),
            ("https://pypi.python.org/pypi/requests/2.28.2", "requests", "2.28.2"),
            # Legacy pypi.python.org /project/
            ("https://pypi.python.org/project/requests", "requests", None),
            ("https://pypi.python.org/project/requests/2.28.2", "requests", "2.28.2"),
            # Fragment ignored
            ("https://pypi.org/project/requests/#history", "requests", None),
            # No scheme
            ("pypi.org/project/requests/", "requests", None),
        ],
    )
    def test_parse_pypi_url(self, url, expected_pkg, expected_ver, mocker):
        if expected_ver is None:
            mocker.patch(
                "scm.dashboard.url_parser._resolve_pypi_latest",
                return_value="9.9.9",
            )
        result = parse_package_url(url)
        assert result.ecosystem == "pypi"
        assert result.package == expected_pkg
        if expected_ver is not None:
            assert result.version == expected_ver
            assert result.resolved_from == "url"

    def test_pypi_url_with_version_no_network_call(self, mocker):
        mock_resolve = mocker.patch("scm.dashboard.url_parser._resolve_pypi_latest")
        parse_package_url("https://pypi.org/project/requests/2.28.2/")
        mock_resolve.assert_not_called()

    def test_pypi_url_without_version_calls_resolve(self, mocker):
        mock_resolve = mocker.patch(
            "scm.dashboard.url_parser._resolve_pypi_latest", return_value="2.28.2"
        )
        result = parse_package_url("https://pypi.org/project/requests/")
        mock_resolve.assert_called_once_with("requests")
        assert result.version == "2.28.2"
        assert result.resolved_from == "latest"

    def test_pypi_json_api_segment_not_treated_as_version(self, mocker):
        # /pypi/requests/json — the "json" segment is a metadata path, not a version
        mocker.patch(
            "scm.dashboard.url_parser._resolve_pypi_latest", return_value="2.28.2"
        )
        result = parse_package_url("https://pypi.python.org/pypi/requests/json")
        assert result.resolved_from == "latest"


# ---------------------------------------------------------------------------
# Unsupported / malformed URLs
# ---------------------------------------------------------------------------


class TestUnsupportedURLs:
    @pytest.mark.parametrize(
        "url",
        [
            "https://github.com/user/repo",
            "https://crates.io/crates/tokio",
            "https://rubygems.org/gems/rails",
            "https://pkg.go.dev/github.com/user/repo",
            "not-a-url-at-all",
        ],
    )
    def test_unsupported_raises(self, url):
        with pytest.raises(UnsupportedURLError):
            parse_package_url(url)


# ---------------------------------------------------------------------------
# Version resolution — npm
# ---------------------------------------------------------------------------


class TestNpmVersionResolution:
    def test_resolve_npm_latest_success(self, mocker):
        mocker.patch(
            "urllib.request.urlopen",
            return_value=_mock_urlopen(_npm_packument("4.17.22")),
        )
        from scm.dashboard.url_parser import _resolve_npm_latest

        assert _resolve_npm_latest("lodash") == "4.17.22"

    def test_resolve_npm_404_raises_package_not_found(self, mocker):
        mocker.patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.HTTPError(
                url="", code=404, msg="Not Found", hdrs=None, fp=None
            ),
        )
        from scm.dashboard.url_parser import _resolve_npm_latest

        with pytest.raises(PackageNotFoundError, match="not found"):
            _resolve_npm_latest("no-such-package")

    def test_resolve_npm_no_latest_tag_raises(self, mocker):
        mocker.patch(
            "urllib.request.urlopen",
            return_value=_mock_urlopen({"dist-tags": {}}),
        )
        from scm.dashboard.url_parser import _resolve_npm_latest

        with pytest.raises(PackageNotFoundError, match="no dist-tags.latest"):
            _resolve_npm_latest("lodash")

    def test_resolve_npm_network_error_raises(self, mocker):
        mocker.patch(
            "urllib.request.urlopen",
            side_effect=OSError("connection refused"),
        )
        from scm.dashboard.url_parser import _resolve_npm_latest

        with pytest.raises(PackageNotFoundError, match="could not reach"):
            _resolve_npm_latest("lodash")


# ---------------------------------------------------------------------------
# Version resolution — PyPI
# ---------------------------------------------------------------------------


class TestPypiVersionResolution:
    def test_resolve_pypi_latest_success(self, mocker):
        mocker.patch(
            "urllib.request.urlopen",
            return_value=_mock_urlopen(_pypi_info("2.28.2")),
        )
        from scm.dashboard.url_parser import _resolve_pypi_latest

        assert _resolve_pypi_latest("requests") == "2.28.2"

    def test_resolve_pypi_404_raises_package_not_found(self, mocker):
        mocker.patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.HTTPError(
                url="", code=404, msg="Not Found", hdrs=None, fp=None
            ),
        )
        from scm.dashboard.url_parser import _resolve_pypi_latest

        with pytest.raises(PackageNotFoundError, match="not found"):
            _resolve_pypi_latest("no-such-package")

    def test_resolve_pypi_no_version_raises(self, mocker):
        mocker.patch(
            "urllib.request.urlopen",
            return_value=_mock_urlopen({"info": {}}),
        )
        from scm.dashboard.url_parser import _resolve_pypi_latest

        with pytest.raises(PackageNotFoundError, match="no info.version"):
            _resolve_pypi_latest("requests")

    def test_resolve_pypi_network_error_raises(self, mocker):
        mocker.patch(
            "urllib.request.urlopen",
            side_effect=OSError("connection refused"),
        )
        from scm.dashboard.url_parser import _resolve_pypi_latest

        with pytest.raises(PackageNotFoundError, match="could not reach"):
            _resolve_pypi_latest("requests")


# ---------------------------------------------------------------------------
# ParsedPackageURL dataclass
# ---------------------------------------------------------------------------


class TestParsedPackageURL:
    def test_repr(self):
        p = ParsedPackageURL("npm", "lodash", "4.17.22", "url")
        assert "npm" in repr(p)
        assert "lodash" in repr(p)
        assert "4.17.22" in repr(p)

    def test_slots(self):
        p = ParsedPackageURL("npm", "lodash", "4.17.22", "url")
        assert p.ecosystem == "npm"
        assert p.package == "lodash"
        assert p.version == "4.17.22"
        assert p.resolved_from == "url"


# ---------------------------------------------------------------------------
# API endpoint: POST /api/scan/force-url
# ---------------------------------------------------------------------------


@pytest.fixture()
def app_client(tmp_path, mocker):
    """Minimal Flask test client with ScanManager mocked."""
    from scm.dashboard.app import create_app

    db_path = tmp_path / "test.db"
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        "analyzer:\n  prompt: 'test prompt'\n  model: 'test-model'\n",
        encoding="utf-8",
    )
    flask_app = create_app(
        db_path=db_path,
        config_path=cfg_path,
    )
    flask_app.config["TESTING"] = True
    with flask_app.test_client() as client:
        yield client, flask_app


class TestForceScanUrlEndpoint:
    def test_missing_url_returns_400(self, app_client):
        client, _ = app_client
        resp = client.post(
            "/api/scan/force-url",
            json={},
            content_type="application/json",
        )
        assert resp.status_code == 400
        assert "url" in resp.get_json()["error"]

    def test_unsupported_url_returns_400(self, app_client, mocker):
        # Patch where parse_package_url is called — inside scanner.py
        mocker.patch(
            "scm.dashboard.scanner.parse_package_url",
            side_effect=UnsupportedURLError("unsupported registry"),
        )
        client, _ = app_client
        resp = client.post(
            "/api/scan/force-url",
            json={"url": "https://github.com/user/repo"},
            content_type="application/json",
        )
        assert resp.status_code == 400
        assert "unsupported registry" in resp.get_json()["error"]

    def test_package_not_found_returns_404(self, app_client, mocker):
        # Patch where parse_package_url is called — inside scanner.py
        mocker.patch(
            "scm.dashboard.scanner.parse_package_url",
            side_effect=PackageNotFoundError("npm package not found: 'ghost'"),
        )
        client, _ = app_client
        resp = client.post(
            "/api/scan/force-url",
            json={"url": "https://www.npmjs.com/package/ghost"},
            content_type="application/json",
        )
        assert resp.status_code == 404
        assert "not found" in resp.get_json()["error"]

    def test_valid_url_returns_202(self, app_client, mocker):
        parsed = ParsedPackageURL("npm", "lodash", "4.17.22", "latest")
        mocker.patch(
            "scm.dashboard.scanner.parse_package_url",
            return_value=parsed,
        )
        mocker.patch(
            "scm.dashboard.scanner.ScanManager.force_scan_package",
            return_value=True,
        )
        client, _ = app_client
        resp = client.post(
            "/api/scan/force-url",
            json={"url": "https://www.npmjs.com/package/lodash"},
            content_type="application/json",
        )
        assert resp.status_code == 202
        data = resp.get_json()
        assert data["status"] == "started"
        assert data["ecosystem"] == "npm"
        assert data["package"] == "lodash"
        assert data["version"] == "4.17.22"
        assert data["resolved_from"] == "latest"

    def test_already_running_returns_409(self, app_client, mocker):
        parsed = ParsedPackageURL("npm", "lodash", "4.17.22", "latest")
        mocker.patch(
            "scm.dashboard.scanner.parse_package_url",
            return_value=parsed,
        )
        mocker.patch(
            "scm.dashboard.scanner.ScanManager.force_scan_package",
            return_value=False,  # already running
        )
        client, _ = app_client
        resp = client.post(
            "/api/scan/force-url",
            json={"url": "https://www.npmjs.com/package/lodash"},
            content_type="application/json",
        )
        assert resp.status_code == 409

    def test_pypi_url_returns_202(self, app_client, mocker):
        parsed = ParsedPackageURL("pypi", "requests", "2.28.2", "url")
        mocker.patch(
            "scm.dashboard.scanner.parse_package_url",
            return_value=parsed,
        )
        mocker.patch(
            "scm.dashboard.scanner.ScanManager.force_scan_package",
            return_value=True,
        )
        client, _ = app_client
        resp = client.post(
            "/api/scan/force-url",
            json={"url": "https://pypi.org/project/requests/2.28.2/"},
            content_type="application/json",
        )
        assert resp.status_code == 202
        data = resp.get_json()
        assert data["ecosystem"] == "pypi"
        assert data["package"] == "requests"
        assert data["version"] == "2.28.2"
        assert data["resolved_from"] == "url"
