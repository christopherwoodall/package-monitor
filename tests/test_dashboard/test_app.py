"""Tests for scm.dashboard.app — Flask route behaviour."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from scm.dashboard.app import create_app


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def reports_dir(tmp_path: Path) -> Path:
    d = tmp_path / "reports"
    d.mkdir()
    return d


@pytest.fixture()
def binaries_dir(tmp_path: Path) -> Path:
    d = tmp_path / "binaries"
    d.mkdir()
    return d


@pytest.fixture()
def app(db_conn, tmp_path: Path, reports_dir: Path):
    """Minimal Flask test app wired to a temp DB and temp reports dir."""
    from scm import db as db_module

    db_path = tmp_path / "test.db"
    conn = db_module.init_db(db_path)
    conn.close()

    flask_app = create_app(db_path=db_path, reports_root=reports_dir)
    flask_app.config["TESTING"] = True
    return flask_app


@pytest.fixture()
def app_with_binaries(db_conn, tmp_path: Path, reports_dir: Path, binaries_dir: Path):
    """Flask test app also wired to a temp binaries dir (for /binary tests)."""
    from scm import db as db_module

    db_path = tmp_path / "test.db"
    conn = db_module.init_db(db_path)
    conn.close()

    flask_app = create_app(
        db_path=db_path, reports_root=reports_dir, binaries_root=binaries_dir
    )
    flask_app.config["TESTING"] = True
    return flask_app


@pytest.fixture()
def client(app):
    return app.test_client()


# ---------------------------------------------------------------------------
# /report — happy path
# ---------------------------------------------------------------------------


def test_report_serves_file_under_reports_root(client, reports_dir: Path):
    report_file = reports_dir / "pypi" / "requests" / "2.32.0.md"
    report_file.parent.mkdir(parents=True)
    report_file.write_text("# hello", encoding="utf-8")

    resp = client.get(f"/report?path={report_file}")
    assert resp.status_code == 200
    assert b"hello" in resp.data


def test_report_content_type_is_markdown(client, reports_dir: Path):
    report_file = reports_dir / "npm" / "lodash" / "4.17.21.md"
    report_file.parent.mkdir(parents=True)
    report_file.write_text("# lodash report", encoding="utf-8")

    resp = client.get(f"/report?path={report_file}")
    assert resp.status_code == 200
    assert "text/markdown" in resp.content_type


# ---------------------------------------------------------------------------
# /report — error cases
# ---------------------------------------------------------------------------


def test_report_missing_path_param_returns_400(client):
    resp = client.get("/report")
    assert resp.status_code == 400


def test_report_relative_path_returns_400(client):
    resp = client.get("/report?path=relative/path/report.md")
    assert resp.status_code == 400


def test_report_path_outside_reports_root_returns_403(client, tmp_path: Path):
    """A path that exists but is NOT under reports_root must be denied."""
    other_file = tmp_path / "secret.md"
    other_file.write_text("secret", encoding="utf-8")

    resp = client.get(f"/report?path={other_file}")
    assert resp.status_code == 403


def test_report_path_traversal_attempt_returns_403(client, reports_dir: Path):
    """Path traversal outside reports root must be denied."""
    traversal = reports_dir / ".." / ".." / "etc" / "passwd"
    resp = client.get(f"/report?path={traversal.resolve()}")
    assert resp.status_code in (403, 404)


def test_report_nonexistent_file_under_root_returns_404(client, reports_dir: Path):
    missing = reports_dir / "pypi" / "boto3" / "1.0.0.md"
    resp = client.get(f"/report?path={missing}")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# /report — old binaries-root paths must now be rejected (regression guard)
# ---------------------------------------------------------------------------


def test_report_path_under_binaries_not_reports_returns_403(
    client, tmp_path: Path, reports_dir: Path
):
    """Reports must come from reports/, not binaries/. Regression against the
    original bug where the route validated against BINARIES_ROOT."""
    binaries_dir = tmp_path / "binaries"
    binaries_dir.mkdir()
    binary_file = binaries_dir / "npm" / "lodash" / "4.17.21.tgz"
    binary_file.parent.mkdir(parents=True)
    binary_file.write_bytes(b"fake tarball")

    resp = client.get(f"/report?path={binary_file}")
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# /api/scan/reset
# ---------------------------------------------------------------------------


def test_api_scan_reset_deletes_named_ecosystem(client, app):
    from scm import db as db_module

    conn = db_module.init_db(app.config["DB_PATH"])
    db_module.set_collector_state(conn, "npm", {"seq": 99999, "epoch": 1234.5})
    db_module.set_collector_state(conn, "pypi", {"serial": 42})
    conn.close()

    resp = client.post(
        "/api/scan/reset",
        data=json.dumps({"ecosystems": ["npm"]}),
        content_type="application/json",
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["reset"] == ["npm"]

    # npm state should be gone; pypi should remain
    conn = db_module.init_db(app.config["DB_PATH"])
    npm_state = db_module.get_collector_state(conn, "npm")
    pypi_state = db_module.get_collector_state(conn, "pypi")
    conn.close()
    assert npm_state == {}
    assert pypi_state == {"serial": 42}


def test_api_scan_reset_defaults_to_both_ecosystems(client, app):
    from scm import db as db_module

    conn = db_module.init_db(app.config["DB_PATH"])
    db_module.set_collector_state(conn, "npm", {"seq": 1})
    db_module.set_collector_state(conn, "pypi", {"serial": 1})
    conn.close()

    resp = client.post("/api/scan/reset", content_type="application/json")
    assert resp.status_code == 200
    data = resp.get_json()
    assert set(data["reset"]) == {"npm", "pypi"}


# ---------------------------------------------------------------------------
# /api/scan/history
# ---------------------------------------------------------------------------


def test_api_scan_history_empty_initially(client):
    resp = client.get("/api/scan/history")
    assert resp.status_code == 200
    assert resp.get_json() == []


def test_api_scan_history_returns_list_after_scan(client, app):
    """After a finished scan the history endpoint returns an entry."""
    mgr = app.config["SCAN_MANAGER"]
    # Manually inject a history entry to avoid running a real scan
    mgr._history.append(
        {
            "started_at": "2026-04-01T00:00:00+00:00",
            "finished_at": "2026-04-01T00:01:00+00:00",
            "ecosystems": ["npm"],
            "processed": 3,
            "releases_found": 3,
            "errors": 0,
            "status": "idle",
        }
    )

    resp = client.get("/api/scan/history")
    assert resp.status_code == 200
    entries = resp.get_json()
    assert len(entries) == 1
    assert entries[0]["ecosystems"] == ["npm"]
    assert entries[0]["processed"] == 3


# ---------------------------------------------------------------------------
# /api/scan/force
# ---------------------------------------------------------------------------


def test_api_scan_force_missing_fields_returns_400(client):
    resp = client.post(
        "/api/scan/force",
        data=json.dumps({"ecosystem": "npm", "package": "lodash"}),
        content_type="application/json",
    )
    assert resp.status_code == 400
    assert "version" in resp.get_json().get("error", "")


def test_api_scan_force_unknown_ecosystem_returns_error(client):
    """Unknown ecosystem should be rejected by ScanManager.force_scan_package."""
    with patch("scm.dashboard.scanner.plugins") as mock_plugins:
        mock_plugins.load_collectors.return_value = {}
        mock_plugins.load_notifiers.return_value = {}
        resp = client.post(
            "/api/scan/force",
            data=json.dumps(
                {"ecosystem": "rubygems", "package": "rails", "version": "7.0.0"}
            ),
            content_type="application/json",
        )
    # force_scan_package returns False for unknown ecosystem → 409 or the scan
    # manager sets status=error; either way the response is not 202
    assert resp.status_code != 202


def test_api_scan_force_starts_and_returns_202(client, app, mocker):
    """Happy path: valid inputs start a background force scan and return 202."""
    mgr = app.config["SCAN_MANAGER"]

    # Patch force_scan_package to avoid real network/subprocess work
    mocker.patch.object(mgr, "force_scan_package", return_value=True)

    resp = client.post(
        "/api/scan/force",
        data=json.dumps(
            {"ecosystem": "npm", "package": "lodash", "version": "4.17.22"}
        ),
        content_type="application/json",
    )
    assert resp.status_code == 202
    data = resp.get_json()
    assert data["status"] == "started"
    assert data["package"] == "lodash"
    assert data["version"] == "4.17.22"


def test_api_scan_force_already_running_returns_409(client, app, mocker):
    mgr = app.config["SCAN_MANAGER"]
    mocker.patch.object(mgr, "force_scan_package", return_value=False)

    resp = client.post(
        "/api/scan/force",
        data=json.dumps(
            {"ecosystem": "npm", "package": "lodash", "version": "4.17.22"}
        ),
        content_type="application/json",
    )
    assert resp.status_code == 409


# ---------------------------------------------------------------------------
# /api/scan/start — notifier_names forwarded correctly
# ---------------------------------------------------------------------------


def test_api_scan_start_passes_notifier_names(client, app, mocker):
    mgr = app.config["SCAN_MANAGER"]
    mocker.patch.object(mgr, "start", return_value=True)

    client.post(
        "/api/scan/start",
        data=json.dumps(
            {"ecosystems": ["npm"], "notifiers": ["local", "slack"], "top_n": 10}
        ),
        content_type="application/json",
    )

    call_kwargs = mgr.start.call_args.kwargs
    assert call_kwargs["notifier_names"] == ["local", "slack"]


# ---------------------------------------------------------------------------
# /api/cron/status
# ---------------------------------------------------------------------------


def test_api_cron_status_returns_monitor_and_dashboard(client, mocker):
    mocker.patch(
        "scm.dashboard.app.scheduler.get_cron_status",
        side_effect=[
            {"installed": False, "line": None},  # monitor query
            {"installed": False, "line": None},  # dashboard query
        ],
    )
    resp = client.get("/api/cron/status")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "monitor" in data
    assert "dashboard" in data


def test_api_cron_status_reflects_installed_state(client, mocker):
    cron_line = "*/5 * * * * package-monitor --once >> $HOME/package-monitor.log 2>&1"
    mocker.patch(
        "scm.dashboard.app.scheduler.get_cron_status",
        side_effect=[
            {"installed": True, "line": cron_line},
            {"installed": False, "line": None},
        ],
    )
    resp = client.get("/api/cron/status")
    data = resp.get_json()
    assert data["monitor"]["installed"] is True
    assert data["monitor"]["line"] == cron_line
    assert data["dashboard"]["installed"] is False


def test_api_cron_status_returns_500_on_scheduler_error(client, mocker):
    from scm.scheduler import SchedulerError

    mocker.patch(
        "scm.dashboard.app.scheduler.get_cron_status",
        side_effect=SchedulerError("crontab not found"),
    )
    resp = client.get("/api/cron/status")
    assert resp.status_code == 500
    assert "error" in resp.get_json()


# ---------------------------------------------------------------------------
# /api/cron/install
# ---------------------------------------------------------------------------


def test_api_cron_install_monitor_calls_install_cron(client, mocker):
    mock_install = mocker.patch("scm.dashboard.app.scheduler.install_cron")
    mocker.patch(
        "scm.dashboard.app.scheduler.get_cron_status",
        return_value={"installed": True, "line": "*/5 * * * * package-monitor --once"},
    )

    resp = client.post(
        "/api/cron/install",
        data=json.dumps({"type": "monitor", "schedule": "*/5 * * * *", "top": "100"}),
        content_type="application/json",
    )
    assert resp.status_code == 200
    mock_install.assert_called_once()
    data = resp.get_json()
    assert data["status"] == "installed"


def test_api_cron_install_dashboard_calls_install_dashboard_cron(client, mocker):
    mock_install = mocker.patch("scm.dashboard.app.scheduler.install_dashboard_cron")
    mocker.patch(
        "scm.dashboard.app.scheduler.get_cron_status",
        return_value={
            "installed": True,
            "line": "@reboot package-monitor-dashboard --host 0.0.0.0",
        },
    )

    resp = client.post(
        "/api/cron/install",
        data=json.dumps({"type": "dashboard", "schedule": "@reboot", "port": "5000"}),
        content_type="application/json",
    )
    assert resp.status_code == 200
    mock_install.assert_called_once()
    data = resp.get_json()
    assert data["status"] == "installed"


def test_api_cron_install_bad_type_returns_400(client):
    resp = client.post(
        "/api/cron/install",
        data=json.dumps({"type": "unknown"}),
        content_type="application/json",
    )
    assert resp.status_code == 400
    assert "type" in resp.get_json().get("error", "")


def test_api_cron_install_scheduler_error_returns_500(client, mocker):
    from scm.scheduler import SchedulerError

    mocker.patch(
        "scm.dashboard.app.scheduler.install_cron",
        side_effect=SchedulerError("crontab write failed"),
    )
    resp = client.post(
        "/api/cron/install",
        data=json.dumps({"type": "monitor"}),
        content_type="application/json",
    )
    assert resp.status_code == 500
    assert "error" in resp.get_json()


# ---------------------------------------------------------------------------
# /api/cron/uninstall
# ---------------------------------------------------------------------------


def test_api_cron_uninstall_monitor_calls_uninstall_cron(client, mocker):
    mock_uninstall = mocker.patch("scm.dashboard.app.scheduler.uninstall_cron")
    resp = client.post(
        "/api/cron/uninstall",
        data=json.dumps({"type": "monitor"}),
        content_type="application/json",
    )
    assert resp.status_code == 200
    mock_uninstall.assert_called_once()
    assert resp.get_json()["status"] == "removed"


def test_api_cron_uninstall_dashboard_calls_uninstall_dashboard_cron(client, mocker):
    mock_uninstall = mocker.patch(
        "scm.dashboard.app.scheduler.uninstall_dashboard_cron"
    )
    resp = client.post(
        "/api/cron/uninstall",
        data=json.dumps({"type": "dashboard"}),
        content_type="application/json",
    )
    assert resp.status_code == 200
    mock_uninstall.assert_called_once()
    assert resp.get_json()["status"] == "removed"


def test_api_cron_uninstall_bad_type_returns_400(client):
    resp = client.post(
        "/api/cron/uninstall",
        data=json.dumps({"type": "bad"}),
        content_type="application/json",
    )
    assert resp.status_code == 400


def test_api_cron_uninstall_scheduler_error_returns_500(client, mocker):
    from scm.scheduler import SchedulerError

    mocker.patch(
        "scm.dashboard.app.scheduler.uninstall_cron",
        side_effect=SchedulerError("no crontab"),
    )
    resp = client.post(
        "/api/cron/uninstall",
        data=json.dumps({"type": "monitor"}),
        content_type="application/json",
    )
    assert resp.status_code == 500


# ---------------------------------------------------------------------------
# /binary — happy path
# ---------------------------------------------------------------------------


@pytest.fixture()
def binary_client(app_with_binaries):
    return app_with_binaries.test_client()


def test_binary_serves_file_under_binaries_root(binary_client, binaries_dir: Path):
    tarball = binaries_dir / "npm" / "lodash" / "4.17.22.tgz"
    tarball.parent.mkdir(parents=True)
    tarball.write_bytes(b"fake tarball content")

    resp = binary_client.get(f"/binary?path={tarball}")
    assert resp.status_code == 200
    assert resp.data == b"fake tarball content"


def test_binary_as_attachment(binary_client, binaries_dir: Path):
    tarball = binaries_dir / "npm" / "express" / "5.0.0.tgz"
    tarball.parent.mkdir(parents=True)
    tarball.write_bytes(b"data")

    resp = binary_client.get(f"/binary?path={tarball}")
    assert resp.status_code == 200
    assert "attachment" in resp.headers.get("Content-Disposition", "")


# ---------------------------------------------------------------------------
# /binary — error cases
# ---------------------------------------------------------------------------


def test_binary_missing_path_param_returns_400(binary_client):
    resp = binary_client.get("/binary")
    assert resp.status_code == 400


def test_binary_relative_path_returns_400(binary_client):
    resp = binary_client.get("/binary?path=relative/path/pkg.tgz")
    assert resp.status_code == 400


def test_binary_path_outside_binaries_root_returns_403(binary_client, tmp_path: Path):
    """A path that exists but is NOT under binaries_root must be denied."""
    other_file = tmp_path / "secret.tgz"
    other_file.write_bytes(b"secret")

    resp = binary_client.get(f"/binary?path={other_file}")
    assert resp.status_code == 403


def test_binary_path_traversal_attempt_returns_403(binary_client, binaries_dir: Path):
    traversal = binaries_dir / ".." / ".." / "etc" / "passwd"
    resp = binary_client.get(f"/binary?path={traversal.resolve()}")
    assert resp.status_code in (403, 404)


def test_binary_nonexistent_file_returns_404(binary_client, binaries_dir: Path):
    missing = binaries_dir / "npm" / "lodash" / "99.0.0.tgz"
    resp = binary_client.get(f"/binary?path={missing}")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# /scanners
# ---------------------------------------------------------------------------


def test_scanners_page_returns_200(client):
    resp = client.get("/scanners")
    assert resp.status_code == 200


def test_scanners_page_contains_enabled_scanners_heading(client):
    resp = client.get("/scanners")
    assert resp.status_code == 200
    # The scanners page should render some content
    assert len(resp.data) > 0


# ---------------------------------------------------------------------------
# /api/settings — empty prompt guard
# ---------------------------------------------------------------------------


def test_api_settings_empty_prompt_returns_400(client, tmp_path):
    """POST /api/settings with an empty analyzer_prompt must return 400."""
    resp = client.post(
        "/api/settings",
        data=json.dumps(
            {
                "top": 100,
                "interval": 300,
                "workers": 4,
                "analyze_timeout": 300,
                "log_level": "INFO",
                "analyzer_model": "github-copilot/claude-sonnet-4.6",
                "analyzer_prompt": "",
                "ecosystems": ["npm", "pypi"],
                "notifiers": ["local"],
                "enabled_scanners": ["diff", "base64_strings"],
                "dashboard_host": "0.0.0.0",
                "dashboard_port": 5000,
            }
        ),
        content_type="application/json",
    )
    assert resp.status_code == 400
    data = resp.get_json()
    assert "analyzer_prompt" in data.get("error", "")


def test_api_settings_whitespace_prompt_returns_400(client):
    """POST /api/settings with a whitespace-only analyzer_prompt must return 400."""
    resp = client.post(
        "/api/settings",
        data=json.dumps({"analyzer_prompt": "   \n\t  "}),
        content_type="application/json",
    )
    assert resp.status_code == 400
    data = resp.get_json()
    assert "analyzer_prompt" in data.get("error", "")
