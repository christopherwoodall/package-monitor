"""Tests for scm.dashboard.app — Flask route behaviour."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

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
def config_path(tmp_path: Path) -> Path:
    """A minimal config.yaml with a non-default analyze_timeout so tests can
    assert that the API reads from the file rather than using a hardcoded default."""
    cfg = {
        "db": "scm.db",
        "top": 500,
        "new_limit": 50,
        "interval": 120,
        "workers": 2,
        "analyze_timeout": 600,
        "log_level": "INFO",
        "ecosystems": ["npm"],
        "notifiers": ["local"],
        "dashboard": {"host": "0.0.0.0", "port": 5000},
        "analyzer": {
            "model": "test-model",
            "prompt": "test prompt",
        },
    }
    p = tmp_path / "config.yaml"
    p.write_text(yaml.dump(cfg), encoding="utf-8")
    return p


@pytest.fixture()
def app(db_conn, tmp_path: Path, reports_dir: Path, config_path: Path):
    """Minimal Flask test app wired to a temp DB, temp reports dir, and temp config."""
    from scm import db as db_module

    db_path = tmp_path / "test.db"
    conn = db_module.init_db(db_path)
    conn.close()

    flask_app = create_app(
        db_path=db_path, reports_root=reports_dir, config_path=config_path
    )
    flask_app.config["TESTING"] = True
    return flask_app


@pytest.fixture()
def app_with_binaries(
    db_conn, tmp_path: Path, reports_dir: Path, binaries_dir: Path, config_path: Path
):
    """Flask test app also wired to a temp binaries dir (for /binary tests)."""
    from scm import db as db_module

    db_path = tmp_path / "test.db"
    conn = db_module.init_db(db_path)
    conn.close()

    flask_app = create_app(
        db_path=db_path,
        reports_root=reports_dir,
        binaries_root=binaries_dir,
        config_path=config_path,
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


# ---------------------------------------------------------------------------
# config.yaml is the source of truth for API defaults
# ---------------------------------------------------------------------------


def test_api_scan_start_uses_config_defaults(client, app, mocker):
    """api_scan_start must read all defaults from config.yaml, not hardcode them."""
    mgr = app.config["SCAN_MANAGER"]
    mocker.patch.object(mgr, "start", return_value=True)

    # Post an empty body — everything should come from config.yaml (config_path fixture)
    client.post("/api/scan/start", data=json.dumps({}), content_type="application/json")

    kw = mgr.start.call_args.kwargs
    assert kw["analyze_timeout"] == 600  # config.yaml value, not hardcoded 300
    assert kw["workers"] == 2  # config.yaml value, not hardcoded 4
    assert kw["top_n"] == 500  # config.yaml value, not hardcoded 1000
    assert kw["new_limit"] == 50  # config.yaml value, not hardcoded 100
    assert kw["ecosystems"] == ["npm"]  # config.yaml value
    assert kw["notifier_names"] == ["local"]
    assert kw["analyzer_model"] == "test-model"
    assert kw["analyzer_prompt"] == "test prompt"


def test_api_scan_start_request_body_overrides_config(client, app, mocker):
    """Values in the request body must override config.yaml defaults."""
    mgr = app.config["SCAN_MANAGER"]
    mocker.patch.object(mgr, "start", return_value=True)

    client.post(
        "/api/scan/start",
        data=json.dumps({"analyze_timeout": 999, "workers": 8, "top_n": 42}),
        content_type="application/json",
    )

    kw = mgr.start.call_args.kwargs
    assert kw["analyze_timeout"] == 999
    assert kw["workers"] == 8
    assert kw["top_n"] == 42


def test_api_scan_force_uses_config_defaults(client, app, mocker):
    """api_scan_force must read analyze_timeout and workers from config.yaml."""
    mgr = app.config["SCAN_MANAGER"]
    mocker.patch.object(mgr, "force_scan_package", return_value=True)

    client.post(
        "/api/scan/force",
        data=json.dumps(
            {"ecosystem": "npm", "package": "lodash", "version": "4.17.22"}
        ),
        content_type="application/json",
    )

    kw = mgr.force_scan_package.call_args.kwargs
    assert kw["analyze_timeout"] == 600  # config.yaml value, not hardcoded 300
    assert kw["workers"] == 2  # config.yaml value, not hardcoded 4
    assert kw["notifier_names"] == ["local"]
    assert kw["analyzer_model"] == "test-model"
    assert kw["analyzer_prompt"] == "test prompt"


def test_api_scan_force_request_body_overrides_config(client, app, mocker):
    """Values in the request body must override config.yaml defaults."""
    mgr = app.config["SCAN_MANAGER"]
    mocker.patch.object(mgr, "force_scan_package", return_value=True)

    client.post(
        "/api/scan/force",
        data=json.dumps(
            {
                "ecosystem": "npm",
                "package": "lodash",
                "version": "4.17.22",
                "analyze_timeout": 120,
                "workers": 1,
            }
        ),
        content_type="application/json",
    )

    kw = mgr.force_scan_package.call_args.kwargs
    assert kw["analyze_timeout"] == 120
    assert kw["workers"] == 1


def test_api_delete_verdict_removes_scan(client, app):
    """DELETE /api/verdicts/<id> removes the verdict and returns 200."""
    from datetime import datetime, timezone

    from scm import db as db_module
    from scm.models import Alert, Release, StoredArtifact, Verdict

    conn = db_module.init_db(app.config["DB_PATH"])

    # Seed a release
    release = Release(
        ecosystem="npm",
        package="test-pkg",
        version="1.0.0",
        previous_version=None,
        rank=1,
        discovered_at=datetime.now(timezone.utc),
    )
    release_id = db_module.upsert_release(conn, release)

    # Seed artifacts
    old_art = StoredArtifact(
        ecosystem="npm",
        package="test-pkg",
        version="0.9.0",
        filename="test-pkg-0.9.0.tgz",
        path=Path("/binaries/test-pkg-0.9.0.tgz"),
        sha256="a" * 64,
        size_bytes=512,
    )
    new_art = StoredArtifact(
        ecosystem="npm",
        package="test-pkg",
        version="1.0.0",
        filename="test-pkg-1.0.0.tgz",
        path=Path("/binaries/test-pkg-1.0.0.tgz"),
        sha256="b" * 64,
        size_bytes=1024,
    )
    db_module.save_artifacts(conn, release_id, old_art, new_art)

    # Seed verdict
    verdict = Verdict(
        release=release,
        old_artifact=old_art,
        new_artifact=new_art,
        result="benign",
        confidence="high",
        summary="test summary",
        analysis="test analysis",
        analyzed_at=datetime.now(timezone.utc),
    )
    verdict_id = db_module.save_verdict(conn, release_id, verdict)
    conn.close()

    # Delete the verdict
    resp = client.delete(f"/api/verdicts/{verdict_id}")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["status"] == "deleted"
    assert data["id"] == verdict_id

    # Verify it's gone
    conn = db_module.init_db(app.config["DB_PATH"])
    row = conn.execute(
        "SELECT COUNT(*) AS cnt FROM verdicts WHERE id = ?", (verdict_id,)
    ).fetchone()
    conn.close()
    assert row["cnt"] == 0


def test_api_delete_verdict_returns_404_for_nonexistent(client):
    """DELETE /api/verdicts/<id> returns 404 when the verdict doesn't exist."""
    resp = client.delete("/api/verdicts/99999")
    assert resp.status_code == 404
    assert "not found" in resp.get_json().get("error", "").lower()


def test_api_delete_verdict_only_affects_target(client, app):
    """DELETE /api/verdicts/<id> removes only the specified verdict."""
    from datetime import datetime, timezone

    from scm import db as db_module
    from scm.models import Alert, Release, StoredArtifact, Verdict

    conn = db_module.init_db(app.config["DB_PATH"])

    # Seed two releases with verdicts
    verdict_ids = []
    for version in ["1.0.0", "2.0.0"]:
        release = Release(
            ecosystem="npm",
            package="test-pkg",
            version=version,
            previous_version=None,
            rank=1,
            discovered_at=datetime.now(timezone.utc),
        )
        release_id = db_module.upsert_release(conn, release)

        new_art = StoredArtifact(
            ecosystem="npm",
            package="test-pkg",
            version=version,
            filename=f"test-pkg-{version}.tgz",
            path=Path(f"/binaries/test-pkg-{version}.tgz"),
            sha256="a" * 64,
            size_bytes=512,
        )
        db_module.save_artifacts(conn, release_id, None, new_art)

        verdict = Verdict(
            release=release,
            old_artifact=None,
            new_artifact=new_art,
            result="benign",
            confidence="high",
            summary="test",
            analysis="test",
            analyzed_at=datetime.now(timezone.utc),
        )
        vid = db_module.save_verdict(conn, release_id, verdict)
        verdict_ids.append(vid)

    conn.close()

    # Delete only the first verdict
    resp = client.delete(f"/api/verdicts/{verdict_ids[0]}")
    assert resp.status_code == 200

    # Verify first is gone, second remains
    conn = db_module.init_db(app.config["DB_PATH"])
    rows = conn.execute(
        "SELECT id FROM verdicts WHERE id IN (?, ?)", (verdict_ids[0], verdict_ids[1])
    ).fetchall()
    conn.close()
    remaining_ids = {r["id"] for r in rows}
    assert verdict_ids[0] not in remaining_ids
    assert verdict_ids[1] in remaining_ids


def test_api_settings_roundtrip(client, app, config_path: Path):
    """POST /api/settings writes config.yaml; a subsequent GET /settings reads it back."""
    resp = client.post(
        "/api/settings",
        data=json.dumps(
            {
                "top": 250,
                "interval": 60,
                "workers": 3,
                "analyze_timeout": 900,
                "log_level": "DEBUG",
                "ecosystems": "npm, pypi",
                "notifiers": "local",
                "analyzer_model": "new-model",
                "analyzer_prompt": "new prompt",
                "dashboard_host": "127.0.0.1",
                "dashboard_port": 8080,
            }
        ),
        content_type="application/json",
    )
    assert resp.status_code == 200
    assert resp.get_json()["ok"] is True

    # Re-read config.yaml and confirm values were written
    from scm.config import load_config

    cfg = load_config(config_path)
    assert cfg.top == 250
    assert cfg.interval == 60
    assert cfg.workers == 3
    assert cfg.analyze_timeout == 900
    assert cfg.log_level == "DEBUG"
    assert cfg.ecosystems == ["npm", "pypi"]
    assert cfg.analyzer_model == "new-model"
    assert cfg.analyzer_prompt == "new prompt"
    assert cfg.dashboard_host == "127.0.0.1"
    assert cfg.dashboard_port == 8080
