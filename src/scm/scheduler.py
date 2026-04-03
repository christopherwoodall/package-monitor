"""Crontab install / uninstall for package-monitor.

Cross-platform (Unix only — crontab command required).

CLI entrypoints
---------------
package-monitor-install-cron             install polling job
package-monitor-uninstall-cron           remove polling job
package-monitor-dashboard-install-service   install @reboot dashboard job
package-monitor-dashboard-uninstall-service remove dashboard @reboot job
"""

from __future__ import annotations

import argparse
import logging
import shutil
import subprocess
import sys

log = logging.getLogger(__name__)

_MARKER = "package-monitor"  # identifies the polling cron line
_DASHBOARD_MARKER = "package-monitor-dashboard"  # identifies the dashboard line
_DEFAULT_SCHEDULE = "*/5 * * * *"
_DEFAULT_DASHBOARD_SCHEDULE = "@reboot"


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class SchedulerError(Exception):
    """Raised when crontab cannot be read or written."""


# ---------------------------------------------------------------------------
# crontab helpers
# ---------------------------------------------------------------------------


def _crontab_binary() -> str:
    binary = shutil.which("crontab")
    if binary is None:
        raise SchedulerError("crontab binary not found — is cron installed?")
    return binary


def _read_crontab() -> str:
    crontab = _crontab_binary()
    result = subprocess.run(
        [crontab, "-l"], capture_output=True, text=True
    )  # noqa: S603
    if result.returncode not in (0, 1):  # 1 = no crontab yet (normal)
        raise SchedulerError(f"crontab -l failed: {result.stderr.strip()}")
    return result.stdout


def _write_crontab(content: str) -> None:
    crontab = _crontab_binary()
    result = subprocess.run(  # noqa: S603
        [crontab, "-"],
        input=content,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise SchedulerError(f"crontab write failed: {result.stderr.strip()}")


def _build_cron_line(
    schedule: str,
    extra_args: str,
) -> str:
    """Build the crontab line for package-monitor (polling job)."""
    pm_bin = shutil.which("package-monitor") or "package-monitor"
    return f"{schedule} {pm_bin} --once{extra_args} >> $HOME/package-monitor.log 2>&1"


def _build_dashboard_cron_line(schedule: str, extra_args: str) -> str:
    """Build the crontab line for the dashboard service."""
    pm_bin = shutil.which("package-monitor-dashboard") or "package-monitor-dashboard"
    return (
        f"{schedule} {pm_bin} --host 0.0.0.0{extra_args}"
        f" >> $HOME/package-monitor-dashboard.log 2>&1"
    )


# ---------------------------------------------------------------------------
# Shared status query
# ---------------------------------------------------------------------------


def get_cron_status(marker: str) -> dict[str, object]:
    """Return whether a crontab line matching *marker* is installed.

    Returns a dict ``{"installed": bool, "line": str | None}``.
    Raises :class:`SchedulerError` if crontab cannot be read.
    """
    content = _read_crontab()
    for line in content.splitlines():
        if marker in line:
            return {"installed": True, "line": line}
    return {"installed": False, "line": None}


# ---------------------------------------------------------------------------
# Polling job install / uninstall
# ---------------------------------------------------------------------------


def install_cron(schedule: str, extra_args: str) -> None:
    """Add (or replace) the package-monitor polling cron entry."""
    current = _read_crontab()
    # Remove any existing package-monitor polling line (idempotent reinstall).
    # Be careful not to strip the dashboard line — only strip lines containing
    # _MARKER that are NOT the dashboard line.
    filtered = "\n".join(
        line
        for line in current.splitlines()
        if not (_MARKER in line and _DASHBOARD_MARKER not in line)
    )
    new_line = _build_cron_line(schedule, extra_args)
    new_crontab = (
        filtered.rstrip("\n") + ("\n" if filtered.strip() else "") + new_line + "\n"
    )
    _write_crontab(new_crontab)
    log.info("cron installed: %s", new_line)
    print(f"Installed: {new_line}")


def uninstall_cron() -> None:
    """Remove the package-monitor polling cron entry if present."""
    current = _read_crontab()
    filtered_lines = [
        line
        for line in current.splitlines()
        if not (_MARKER in line and _DASHBOARD_MARKER not in line)
    ]
    _write_crontab("\n".join(filtered_lines) + ("\n" if filtered_lines else ""))
    log.info("polling cron entry removed")
    print("package-monitor polling cron entry removed (if it existed).")


# ---------------------------------------------------------------------------
# Dashboard service install / uninstall
# ---------------------------------------------------------------------------


def install_dashboard_cron(schedule: str, extra_args: str) -> None:
    """Add (or replace) the package-monitor-dashboard @reboot cron entry."""
    current = _read_crontab()
    filtered = "\n".join(
        line for line in current.splitlines() if _DASHBOARD_MARKER not in line
    )
    new_line = _build_dashboard_cron_line(schedule, extra_args)
    new_crontab = (
        filtered.rstrip("\n") + ("\n" if filtered.strip() else "") + new_line + "\n"
    )
    _write_crontab(new_crontab)
    log.info("dashboard service cron installed: %s", new_line)
    print(f"Installed: {new_line}")


def uninstall_dashboard_cron() -> None:
    """Remove the package-monitor-dashboard cron entry if present."""
    current = _read_crontab()
    filtered_lines = [
        line for line in current.splitlines() if _DASHBOARD_MARKER not in line
    ]
    _write_crontab("\n".join(filtered_lines) + ("\n" if filtered_lines else ""))
    log.info("dashboard service cron entry removed")
    print("package-monitor-dashboard cron entry removed (if it existed).")


# ---------------------------------------------------------------------------
# CLI entrypoints — polling job
# ---------------------------------------------------------------------------


def install_cron_main(argv: list[str] | None = None) -> None:
    """Entrypoint: package-monitor-install-cron"""
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )
    parser = argparse.ArgumentParser(
        prog="package-monitor-install-cron",
        description="Install package-monitor as a cron job.",
    )
    parser.add_argument(
        "--schedule",
        default=_DEFAULT_SCHEDULE,
        help="Cron schedule (default: %(default)s)",
    )
    parser.add_argument("--db", default="", help="--db PATH to embed in cron command")
    parser.add_argument("--top", default="", help="--top N to embed in cron command")
    parser.add_argument(
        "--twitter",
        action="store_true",
        help="Enable Twitter notifications in cron job",
    )
    parser.add_argument("--notifiers", default="", help="--notifiers value to embed")
    parser.add_argument(
        "--workers", default="", help="--workers N to embed in cron command"
    )
    args = parser.parse_args(argv)

    extra_parts: list[str] = []
    if args.db:
        extra_parts += ["--db", args.db]
    if args.top:
        extra_parts += ["--top", args.top]
    if args.twitter:
        extra_parts.append("--twitter")
    if args.notifiers:
        extra_parts += ["--notifiers", args.notifiers]
    if args.workers:
        extra_parts += ["--workers", args.workers]

    extra_args = (" " + " ".join(extra_parts)) if extra_parts else ""

    try:
        install_cron(args.schedule, extra_args)
    except SchedulerError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


def uninstall_cron_main(argv: list[str] | None = None) -> None:
    """Entrypoint: package-monitor-uninstall-cron"""
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )
    parser = argparse.ArgumentParser(
        prog="package-monitor-uninstall-cron",
        description="Remove the package-monitor polling cron job.",
    )
    parser.parse_args(argv)
    try:
        uninstall_cron()
    except SchedulerError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


# ---------------------------------------------------------------------------
# CLI entrypoints — dashboard service
# ---------------------------------------------------------------------------


def dashboard_install_cron_main(argv: list[str] | None = None) -> None:
    """Entrypoint: package-monitor-dashboard-install-service"""
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )
    parser = argparse.ArgumentParser(
        prog="package-monitor-dashboard-install-service",
        description=(
            "Install the package-monitor dashboard as an @reboot cron job so it "
            "starts automatically on login / machine restart."
        ),
    )
    parser.add_argument(
        "--schedule",
        default=_DEFAULT_DASHBOARD_SCHEDULE,
        help="Cron schedule (default: %(default)s).  Use @reboot to start on boot.",
    )
    parser.add_argument(
        "--db",
        default="",
        help="--db PATH to embed in the dashboard command",
    )
    parser.add_argument(
        "--port",
        default="",
        help="--port N to embed in the dashboard command",
    )
    parser.add_argument(
        "--host",
        default="",
        help=(
            "--host ADDR to embed in the dashboard command "
            "(default omitted — dashboard uses 0.0.0.0)"
        ),
    )
    parser.add_argument(
        "--log-level",
        default="",
        help="--log-level LEVEL to embed in the dashboard command",
    )
    args = parser.parse_args(argv)

    extra_parts: list[str] = []
    if args.db:
        extra_parts += ["--db", args.db]
    if args.port:
        extra_parts += ["--port", args.port]
    if args.host:
        extra_parts += ["--host", args.host]
    if args.log_level:
        extra_parts += ["--log-level", args.log_level]

    extra_args = (" " + " ".join(extra_parts)) if extra_parts else ""

    try:
        install_dashboard_cron(args.schedule, extra_args)
    except SchedulerError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


def dashboard_uninstall_cron_main(argv: list[str] | None = None) -> None:
    """Entrypoint: package-monitor-dashboard-uninstall-service"""
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )
    parser = argparse.ArgumentParser(
        prog="package-monitor-dashboard-uninstall-service",
        description="Remove the package-monitor-dashboard @reboot cron entry.",
    )
    parser.parse_args(argv)
    try:
        uninstall_dashboard_cron()
    except SchedulerError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
