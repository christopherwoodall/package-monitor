"""Tests for scm.scheduler — install_cron, uninstall_cron, CLI entrypoints."""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, call, patch

import pytest

from scm.scheduler import (
    SchedulerError,
    _build_cron_line,
    _build_dashboard_cron_line,
    _read_crontab,
    _write_crontab,
    dashboard_install_cron_main,
    dashboard_uninstall_cron_main,
    get_cron_status,
    install_cron,
    install_cron_main,
    install_dashboard_cron,
    uninstall_cron,
    uninstall_cron_main,
    uninstall_dashboard_cron,
)

# ---------------------------------------------------------------------------
# _build_cron_line
# ---------------------------------------------------------------------------


def test_build_cron_line_contains_schedule():
    line = _build_cron_line("*/5 * * * *", "")
    assert "*/5 * * * *" in line


def test_build_cron_line_contains_once_flag():
    line = _build_cron_line("0 * * * *", "")
    assert "--once" in line


def test_build_cron_line_contains_extra_args():
    line = _build_cron_line("*/5 * * * *", " --top 100")
    assert "--top 100" in line


def test_build_cron_line_contains_log_redirect():
    line = _build_cron_line("*/5 * * * *", "")
    assert ">>" in line
    assert "package-monitor.log" in line


# ---------------------------------------------------------------------------
# _read_crontab
# ---------------------------------------------------------------------------


def test_read_crontab_returns_stdout_on_success(mocker):
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = "# existing cron\n0 * * * * some_job\n"
    mock_result.stderr = ""
    mocker.patch("subprocess.run", return_value=mock_result)
    mocker.patch("shutil.which", return_value="/usr/bin/crontab")

    content = _read_crontab()
    assert "existing cron" in content


def test_read_crontab_returns_empty_on_returncode_1(mocker):
    """Exit code 1 means 'no crontab yet' — should return empty string, not raise."""
    mock_result = MagicMock()
    mock_result.returncode = 1
    mock_result.stdout = ""
    mock_result.stderr = "no crontab for user"
    mocker.patch("subprocess.run", return_value=mock_result)
    mocker.patch("shutil.which", return_value="/usr/bin/crontab")

    content = _read_crontab()
    assert content == ""


def test_read_crontab_raises_on_bad_returncode(mocker):
    mock_result = MagicMock()
    mock_result.returncode = 2
    mock_result.stdout = ""
    mock_result.stderr = "permission denied"
    mocker.patch("subprocess.run", return_value=mock_result)
    mocker.patch("shutil.which", return_value="/usr/bin/crontab")

    with pytest.raises(SchedulerError, match="crontab -l failed"):
        _read_crontab()


def test_read_crontab_raises_when_binary_not_found(mocker):
    mocker.patch("shutil.which", return_value=None)
    with pytest.raises(SchedulerError, match="crontab binary not found"):
        _read_crontab()


# ---------------------------------------------------------------------------
# _write_crontab
# ---------------------------------------------------------------------------


def test_write_crontab_calls_subprocess(mocker):
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stderr = ""
    mock_run = mocker.patch("subprocess.run", return_value=mock_result)
    mocker.patch("shutil.which", return_value="/usr/bin/crontab")

    _write_crontab("0 * * * * myjob\n")
    mock_run.assert_called_once()


def test_write_crontab_raises_on_failure(mocker):
    mock_result = MagicMock()
    mock_result.returncode = 1
    mock_result.stderr = "write failed"
    mocker.patch("subprocess.run", return_value=mock_result)
    mocker.patch("shutil.which", return_value="/usr/bin/crontab")

    with pytest.raises(SchedulerError, match="crontab write failed"):
        _write_crontab("bad content\n")


# ---------------------------------------------------------------------------
# install_cron
# ---------------------------------------------------------------------------


def test_install_cron_adds_line(mocker):
    written = []

    def fake_read():
        return ""

    def fake_write(content):
        written.append(content)

    mocker.patch("scm.scheduler._read_crontab", side_effect=fake_read)
    mocker.patch("scm.scheduler._write_crontab", side_effect=fake_write)

    install_cron("*/5 * * * *", "")
    assert len(written) == 1
    assert "package-monitor" in written[0]


def test_install_cron_replaces_existing_line(mocker):
    existing = "*/5 * * * * package-monitor --once >> $HOME/package-monitor.log 2>&1\n"
    written = []

    mocker.patch("scm.scheduler._read_crontab", return_value=existing)
    mocker.patch("scm.scheduler._write_crontab", side_effect=written.append)

    install_cron("0 * * * *", " --top 50")
    assert len(written) == 1
    # Only one package-monitor line in the result
    pm_lines = [
        l
        for l in written[0].splitlines()
        if "package-monitor" in l and not l.startswith("#")
    ]
    assert len(pm_lines) == 1
    assert "0 * * * *" in pm_lines[0]


def test_install_cron_preserves_other_entries(mocker):
    existing = "0 0 * * * other_job\n"
    written = []

    mocker.patch("scm.scheduler._read_crontab", return_value=existing)
    mocker.patch("scm.scheduler._write_crontab", side_effect=written.append)

    install_cron("*/5 * * * *", "")
    assert "other_job" in written[0]


# ---------------------------------------------------------------------------
# uninstall_cron
# ---------------------------------------------------------------------------


def test_uninstall_cron_removes_pm_line(mocker):
    existing = (
        "0 0 * * * other_job\n"
        "*/5 * * * * package-monitor --once >> $HOME/package-monitor.log 2>&1\n"
    )
    written = []

    mocker.patch("scm.scheduler._read_crontab", return_value=existing)
    mocker.patch("scm.scheduler._write_crontab", side_effect=written.append)

    uninstall_cron()
    assert len(written) == 1
    assert "package-monitor" not in written[0]
    assert "other_job" in written[0]


def test_uninstall_cron_noop_when_no_entry(mocker):
    existing = "0 0 * * * other_job\n"
    written = []

    mocker.patch("scm.scheduler._read_crontab", return_value=existing)
    mocker.patch("scm.scheduler._write_crontab", side_effect=written.append)

    uninstall_cron()  # must not raise
    assert "other_job" in written[0]


# ---------------------------------------------------------------------------
# CLI entrypoints
# ---------------------------------------------------------------------------


def test_install_cron_main_calls_install_cron(mocker):
    mock_install = mocker.patch("scm.scheduler.install_cron")
    install_cron_main(["--schedule", "0 * * * *", "--top", "100"])
    mock_install.assert_called_once()
    args = mock_install.call_args[0]
    assert args[0] == "0 * * * *"
    assert "--top" in args[1]


def test_install_cron_main_exits_on_scheduler_error(mocker):
    mocker.patch("scm.scheduler.install_cron", side_effect=SchedulerError("no crontab"))
    with pytest.raises(SystemExit) as exc_info:
        install_cron_main([])
    assert exc_info.value.code == 1


def test_uninstall_cron_main_calls_uninstall_cron(mocker):
    mock_uninstall = mocker.patch("scm.scheduler.uninstall_cron")
    uninstall_cron_main([])
    mock_uninstall.assert_called_once()


def test_uninstall_cron_main_exits_on_scheduler_error(mocker):
    mocker.patch("scm.scheduler.uninstall_cron", side_effect=SchedulerError("fail"))
    with pytest.raises(SystemExit) as exc_info:
        uninstall_cron_main([])
    assert exc_info.value.code == 1


# ---------------------------------------------------------------------------
# _build_dashboard_cron_line
# ---------------------------------------------------------------------------


def test_build_dashboard_cron_line_contains_schedule():
    line = _build_dashboard_cron_line("@reboot", "")
    assert "@reboot" in line


def test_build_dashboard_cron_line_binds_to_0000():
    line = _build_dashboard_cron_line("@reboot", "")
    assert "--host 0.0.0.0" in line


def test_build_dashboard_cron_line_contains_extra_args():
    line = _build_dashboard_cron_line("@reboot", " --port 8080")
    assert "--port 8080" in line


def test_build_dashboard_cron_line_logs_to_dashboard_log():
    line = _build_dashboard_cron_line("@reboot", "")
    assert "package-monitor-dashboard.log" in line


# ---------------------------------------------------------------------------
# get_cron_status
# ---------------------------------------------------------------------------


def test_get_cron_status_not_installed(mocker):
    mocker.patch("scm.scheduler._read_crontab", return_value="0 0 * * * other_job\n")
    status = get_cron_status("package-monitor")
    assert status["installed"] is False
    assert status["line"] is None


def test_get_cron_status_installed(mocker):
    cron_line = "*/5 * * * * package-monitor --once >> $HOME/package-monitor.log 2>&1"
    mocker.patch("scm.scheduler._read_crontab", return_value=f"{cron_line}\n")
    status = get_cron_status("package-monitor")
    assert status["installed"] is True
    assert status["line"] == cron_line


def test_get_cron_status_dashboard_installed(mocker):
    cron_line = "@reboot package-monitor-dashboard --host 0.0.0.0"
    mocker.patch(
        "scm.scheduler._read_crontab", return_value=f"0 0 * * * other\n{cron_line}\n"
    )
    status = get_cron_status("package-monitor-dashboard")
    assert status["installed"] is True
    assert status["line"] == cron_line


def test_get_cron_status_raises_on_crontab_error(mocker):
    mocker.patch("scm.scheduler._read_crontab", side_effect=SchedulerError("not found"))
    with pytest.raises(SchedulerError):
        get_cron_status("package-monitor")


# ---------------------------------------------------------------------------
# install_dashboard_cron
# ---------------------------------------------------------------------------


def test_install_dashboard_cron_adds_line(mocker):
    written = []
    mocker.patch("scm.scheduler._read_crontab", return_value="")
    mocker.patch("scm.scheduler._write_crontab", side_effect=written.append)

    install_dashboard_cron("@reboot", "")
    assert len(written) == 1
    assert "package-monitor-dashboard" in written[0]
    assert "@reboot" in written[0]


def test_install_dashboard_cron_replaces_existing(mocker):
    existing = (
        "@reboot package-monitor-dashboard --host 0.0.0.0"
        " >> $HOME/package-monitor-dashboard.log 2>&1\n"
    )
    written = []
    mocker.patch("scm.scheduler._read_crontab", return_value=existing)
    mocker.patch("scm.scheduler._write_crontab", side_effect=written.append)

    install_dashboard_cron("@reboot", " --port 8080")
    # Only one dashboard line in the result
    dash_lines = [
        ln for ln in written[0].splitlines() if "package-monitor-dashboard" in ln
    ]
    assert len(dash_lines) == 1
    assert "--port 8080" in dash_lines[0]


def test_install_dashboard_cron_preserves_polling_line(mocker):
    existing = "*/5 * * * * package-monitor --once >> $HOME/package-monitor.log 2>&1\n"
    written = []
    mocker.patch("scm.scheduler._read_crontab", return_value=existing)
    mocker.patch("scm.scheduler._write_crontab", side_effect=written.append)

    install_dashboard_cron("@reboot", "")
    # The polling line must still be present
    assert "package-monitor --once" in written[0]


# ---------------------------------------------------------------------------
# uninstall_dashboard_cron
# ---------------------------------------------------------------------------


def test_uninstall_dashboard_cron_removes_dashboard_line(mocker):
    existing = (
        "0 0 * * * other_job\n"
        "@reboot package-monitor-dashboard --host 0.0.0.0"
        " >> $HOME/package-monitor-dashboard.log 2>&1\n"
    )
    written = []
    mocker.patch("scm.scheduler._read_crontab", return_value=existing)
    mocker.patch("scm.scheduler._write_crontab", side_effect=written.append)

    uninstall_dashboard_cron()
    assert "package-monitor-dashboard" not in written[0]
    assert "other_job" in written[0]


def test_uninstall_dashboard_cron_preserves_polling_line(mocker):
    existing = (
        "*/5 * * * * package-monitor --once >> $HOME/package-monitor.log 2>&1\n"
        "@reboot package-monitor-dashboard --host 0.0.0.0"
        " >> $HOME/package-monitor-dashboard.log 2>&1\n"
    )
    written = []
    mocker.patch("scm.scheduler._read_crontab", return_value=existing)
    mocker.patch("scm.scheduler._write_crontab", side_effect=written.append)

    uninstall_dashboard_cron()
    assert "package-monitor --once" in written[0]
    assert "package-monitor-dashboard" not in written[0]


def test_uninstall_dashboard_cron_noop_when_no_entry(mocker):
    existing = "0 0 * * * other_job\n"
    written = []
    mocker.patch("scm.scheduler._read_crontab", return_value=existing)
    mocker.patch("scm.scheduler._write_crontab", side_effect=written.append)

    uninstall_dashboard_cron()  # must not raise
    assert "other_job" in written[0]


# ---------------------------------------------------------------------------
# install_cron does NOT touch dashboard line (regression)
# ---------------------------------------------------------------------------


def test_install_cron_does_not_remove_dashboard_line(mocker):
    """Polling install must not strip the dashboard @reboot line."""
    existing = (
        "@reboot package-monitor-dashboard --host 0.0.0.0"
        " >> $HOME/package-monitor-dashboard.log 2>&1\n"
    )
    written = []
    mocker.patch("scm.scheduler._read_crontab", return_value=existing)
    mocker.patch("scm.scheduler._write_crontab", side_effect=written.append)

    install_cron("*/5 * * * *", "")
    assert "package-monitor-dashboard" in written[0]


def test_uninstall_cron_does_not_remove_dashboard_line(mocker):
    """Polling uninstall must not strip the dashboard @reboot line."""
    existing = (
        "*/5 * * * * package-monitor --once >> $HOME/package-monitor.log 2>&1\n"
        "@reboot package-monitor-dashboard --host 0.0.0.0"
        " >> $HOME/package-monitor-dashboard.log 2>&1\n"
    )
    written = []
    mocker.patch("scm.scheduler._read_crontab", return_value=existing)
    mocker.patch("scm.scheduler._write_crontab", side_effect=written.append)

    uninstall_cron()
    assert "package-monitor-dashboard" in written[0]
    assert "package-monitor --once" not in written[0]


# ---------------------------------------------------------------------------
# Dashboard CLI entrypoints
# ---------------------------------------------------------------------------


def test_dashboard_install_cron_main_calls_install_dashboard_cron(mocker):
    mock_install = mocker.patch("scm.scheduler.install_dashboard_cron")
    dashboard_install_cron_main(["--schedule", "@reboot", "--port", "8080"])
    mock_install.assert_called_once()
    args = mock_install.call_args[0]
    assert args[0] == "@reboot"
    assert "--port" in args[1]


def test_dashboard_install_cron_main_exits_on_error(mocker):
    mocker.patch(
        "scm.scheduler.install_dashboard_cron",
        side_effect=SchedulerError("no crontab"),
    )
    with pytest.raises(SystemExit) as exc_info:
        dashboard_install_cron_main([])
    assert exc_info.value.code == 1


def test_dashboard_uninstall_cron_main_calls_uninstall_dashboard_cron(mocker):
    mock_uninstall = mocker.patch("scm.scheduler.uninstall_dashboard_cron")
    dashboard_uninstall_cron_main([])
    mock_uninstall.assert_called_once()


def test_dashboard_uninstall_cron_main_exits_on_error(mocker):
    mocker.patch(
        "scm.scheduler.uninstall_dashboard_cron",
        side_effect=SchedulerError("fail"),
    )
    with pytest.raises(SystemExit) as exc_info:
        dashboard_uninstall_cron_main([])
    assert exc_info.value.code == 1
