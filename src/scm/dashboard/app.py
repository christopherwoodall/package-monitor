"""Flask application factory + routes for the package-monitor dashboard.

Start with:
    uv run package-monitor-dashboard [--db scm.db] [--port 5000]
"""

from __future__ import annotations

import argparse
import logging
import sqlite3
from pathlib import Path

import yaml
from flask import Flask, Response, g, jsonify, render_template, request, send_file

from scm import db as db_module
from scm import plugins
from scm import scheduler
from scm.config import (
    Config,
    ConfigError,
    _UNSET,
    apply_cli_overrides,
    load_config,
    validate_runtime_config,
)
from scm.dashboard import queries
from scm.dashboard.scanner import ScanManager
from scm.dashboard.url_parser import PackageNotFoundError, UnsupportedURLError

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def create_app(
    db_path: Path,
    binaries_root: Path | None = None,
    reports_root: Path | None = None,
    config_path: Path | None = None,
) -> Flask:
    """Create and return a configured Flask application.

    Args:
        db_path:       Absolute path to the SQLite database file.
        binaries_root: Optional override for binaries directory.
        reports_root:  Optional override for reports directory (used by /report).
                       Defaults to project_root/reports — must match LocalNotifier.
        config_path:   Path to config.yaml, stored on app so /settings can read/write it.
    """
    app = Flask(
        __name__,
        template_folder="templates",
    )

    # app.py is at src/scm/dashboard/app.py → parents[3] = project root
    _project_root = Path(__file__).resolve().parents[3]
    _default_reports_root = _project_root / "reports"
    _default_binaries_root = _project_root / "binaries"

    app.config["DB_PATH"] = db_path
    app.config["BINARIES_ROOT"] = binaries_root or _default_binaries_root
    app.config["REPORTS_ROOT"] = reports_root or _default_reports_root
    app.config["LOGS_ROOT"] = Path.home() / ".local" / "share" / "opencode" / "log"
    app.config["SCAN_MANAGER"] = ScanManager()
    app.config["CONFIG_PATH"] = config_path or Path("config.yaml")

    # ------------------------------------------------------------------
    # Per-request DB connection
    # ------------------------------------------------------------------

    def _get_conn() -> sqlite3.Connection:
        if "db_conn" not in g:
            g.db_conn = db_module.init_db(app.config["DB_PATH"])
        return g.db_conn

    @app.teardown_appcontext
    def _close_conn(exc: BaseException | None) -> None:
        conn = g.pop("db_conn", None)
        if conn is not None:
            conn.close()

    # ------------------------------------------------------------------
    # Page routes
    # ------------------------------------------------------------------

    @app.route("/")
    def index() -> str:
        conn = _get_conn()
        stats = queries.get_stats(conn)
        breakdown = queries.get_ecosystem_breakdown(conn)
        collector_states = queries.get_collector_states(conn)
        return render_template(
            "index.html",
            stats=stats,
            breakdown=breakdown,
            collector_states=collector_states,
        )

    @app.route("/package/<ecosystem>/<path:name>")
    def package_detail(ecosystem: str, name: str) -> str:
        conn = _get_conn()
        history = queries.get_package_history(conn, ecosystem, name)
        return render_template(
            "package.html",
            ecosystem=ecosystem,
            package=name,
            history=history,
        )

    @app.route("/scan")
    def scan_page() -> str:
        mgr: ScanManager = app.config["SCAN_MANAGER"]
        return render_template("scan.html", scan_status=mgr.status())

    @app.route("/settings")
    def settings_page() -> str:
        cfg_path: Path = app.config["CONFIG_PATH"]
        try:
            cfg = load_config(cfg_path)
        except ConfigError as exc:
            log.warning("could not load config for settings page: %s", exc)
            cfg = Config()
        return render_template("settings.html", cfg=cfg)

    @app.route("/scanners")
    def scanners_page() -> str:
        cfg_path: Path = app.config["CONFIG_PATH"]
        try:
            cfg = load_config(cfg_path)
        except ConfigError as exc:
            log.warning("could not load config for scanners page: %s", exc)
            cfg = Config()
        available_scanners = list(plugins.load_scanners().keys())
        return render_template(
            "scanners.html", cfg=cfg, available_scanners=available_scanners
        )

    # ------------------------------------------------------------------
    # API routes
    # ------------------------------------------------------------------

    @app.route("/api/verdicts")
    def api_verdicts() -> Response:
        conn = _get_conn()
        try:
            offset = int(request.args.get("offset", 0))
            limit = int(request.args.get("limit", 50))
        except ValueError:
            offset, limit = 0, 50

        ecosystem = request.args.get("ecosystem") or None
        result = request.args.get("result") or None

        rows = queries.get_verdicts_paginated(
            conn, offset=offset, limit=limit, ecosystem=ecosystem, result=result
        )
        return jsonify(rows)

    @app.route("/api/packages")
    def api_packages() -> Response:
        """Return one row per (ecosystem, package) — the most recent verdict only.

        Query params:
            offset   int   default 0
            limit    int   default 50
            ecosystem str  filter by ecosystem
            result    str  filter by result
        """
        conn = _get_conn()
        try:
            offset = int(request.args.get("offset", 0))
            limit = int(request.args.get("limit", 50))
        except ValueError:
            offset, limit = 0, 50

        ecosystem = request.args.get("ecosystem") or None
        result = request.args.get("result") or None

        rows = queries.get_latest_per_package(
            conn, offset=offset, limit=limit, ecosystem=ecosystem, result=result
        )
        return jsonify(rows)

    @app.route("/api/scan/start", methods=["POST"])
    def api_scan_start() -> Response:
        mgr: ScanManager = app.config["SCAN_MANAGER"]
        data = request.get_json(silent=True) or {}

        try:
            _cfg = load_config(app.config["CONFIG_PATH"])
        except ConfigError:
            _cfg = Config()

        raw_ecosystems = data.get("ecosystems", _cfg.ecosystems)
        if isinstance(raw_ecosystems, list):
            ecosystems = [e.strip() for e in raw_ecosystems if e.strip()]
        else:
            ecosystems = [
                e.strip() for e in str(raw_ecosystems).split(",") if e.strip()
            ]

        raw_notifiers = data.get("notifiers", _cfg.notifiers)
        if isinstance(raw_notifiers, list):
            notifier_names = [n.strip() for n in raw_notifiers if n.strip()]
        else:
            notifier_names = [
                n.strip() for n in str(raw_notifiers).split(",") if n.strip()
            ]

        top_n = int(data.get("top_n", _cfg.top))
        # new_only=true (or top_n explicitly 0) means watch all new releases
        if data.get("new_only") or top_n == 0:
            top_n = 0
        new_limit = int(data.get("new_limit", _cfg.new_limit))
        workers = int(data.get("workers", _cfg.workers))
        analyze_timeout = int(data.get("analyze_timeout", _cfg.analyze_timeout))

        started = mgr.start(
            db_path=app.config["DB_PATH"],
            ecosystems=ecosystems,
            top_n=top_n,
            new_limit=new_limit,
            workers=workers,
            analyze_timeout=analyze_timeout,
            notifier_names=notifier_names,
            analyzer_model=_cfg.analyzer_model or None,
            analyzer_prompt=_cfg.analyzer_prompt or None,
            enabled_scanners=_cfg.enabled_scanners,
            scanner_config=_cfg.scanner_config,
        )

        if started:
            return jsonify({"status": "started"}), 202
        return jsonify({"status": "already_running"}), 409

    @app.route("/api/scan/status")
    def api_scan_status() -> Response:
        mgr: ScanManager = app.config["SCAN_MANAGER"]
        return jsonify(mgr.status())

    @app.route("/api/scan/history")
    def api_scan_history() -> Response:
        mgr: ScanManager = app.config["SCAN_MANAGER"]
        return jsonify(mgr.history())

    @app.route("/api/scan/reset", methods=["POST"])
    def api_scan_reset() -> Response:
        """Delete collector_state rows so the next scan does a fresh 30-day lookback.

        Body (JSON): {"ecosystems": ["npm", "pypi"]}  — defaults to both.
        """
        conn = _get_conn()
        data = request.get_json(silent=True) or {}
        raw = data.get("ecosystems", ["npm", "pypi"])
        if isinstance(raw, str):
            ecosystems = [e.strip() for e in raw.split(",") if e.strip()]
        else:
            ecosystems = [e.strip() for e in raw if e.strip()]

        reset = []
        for eco in ecosystems:
            conn.execute("DELETE FROM collector_state WHERE ecosystem = ?", (eco,))
            reset.append(eco)
            log.info("collector state reset for ecosystem: %s", eco)

        return jsonify({"reset": reset}), 200

    @app.route("/api/scan/force", methods=["POST"])
    def api_scan_force() -> Response:
        """Force-scan a single package@version, bypassing the collector poll.

        Body (JSON):
            {
                "ecosystem": "npm",
                "package":   "lodash",
                "version":   "4.17.22",
                "notifiers": ["local"],   // optional, defaults to config.yaml
                "analyze_timeout": 300,   // optional, defaults to config.yaml
                "workers": 4              // optional, defaults to config.yaml
            }
        """
        mgr: ScanManager = app.config["SCAN_MANAGER"]
        data = request.get_json(silent=True) or {}

        ecosystem = (data.get("ecosystem") or "").strip()
        package = (data.get("package") or "").strip()
        version = (data.get("version") or "").strip()

        if not ecosystem or not package or not version:
            return jsonify(
                {"error": "ecosystem, package and version are required"}
            ), 400

        try:
            _cfg = load_config(app.config["CONFIG_PATH"])
        except ConfigError:
            _cfg = Config()

        raw_notifiers = data.get("notifiers", _cfg.notifiers)
        if isinstance(raw_notifiers, list):
            notifier_names = [n.strip() for n in raw_notifiers if n.strip()]
        else:
            notifier_names = [
                n.strip() for n in str(raw_notifiers).split(",") if n.strip()
            ]

        analyze_timeout = int(data.get("analyze_timeout", _cfg.analyze_timeout))
        workers = int(data.get("workers", _cfg.workers))

        started = mgr.force_scan_package(
            db_path=app.config["DB_PATH"],
            ecosystem=ecosystem,
            package=package,
            version=version,
            workers=workers,
            analyze_timeout=analyze_timeout,
            notifier_names=notifier_names,
            analyzer_model=_cfg.analyzer_model or None,
            analyzer_prompt=_cfg.analyzer_prompt or None,
            enabled_scanners=_cfg.enabled_scanners,
            scanner_config=_cfg.scanner_config,
        )

        if started:
            return jsonify(
                {"status": "started", "package": package, "version": version}
            ), 202
        return jsonify({"status": "already_running"}), 409

    @app.route("/api/scan/force-url", methods=["POST"])
    def api_scan_force_url() -> Response:
        """Force-scan a package identified by its registry URL.

        Parses the URL to extract ecosystem/package, resolves the latest
        version from the registry if the URL contains no version, then
        delegates to the same force-scan pipeline as ``/api/scan/force``.

        Body (JSON):
            {
                "url":            "https://www.npmjs.com/package/lodash",
                "notifiers":      ["local"],   // optional
                "analyze_timeout": 300,         // optional
                "workers":         4            // optional
            }

        Responses:
            202  {"status": "started", "ecosystem": ..., "package": ...,
                  "version": ..., "resolved_from": "url" | "latest"}
            400  {"error": "..."} — missing/unsupported URL
            404  {"error": "..."} — package not found in registry
            409  {"status": "already_running"}
        """
        mgr: ScanManager = app.config["SCAN_MANAGER"]
        data = request.get_json(silent=True) or {}

        url = (data.get("url") or "").strip()
        if not url:
            return jsonify({"error": "url is required"}), 400

        try:
            _cfg = load_config(app.config["CONFIG_PATH"])
        except ConfigError:
            _cfg = Config()

        raw_notifiers = data.get("notifiers", _cfg.notifiers)
        if isinstance(raw_notifiers, list):
            notifier_names = [n.strip() for n in raw_notifiers if n.strip()]
        else:
            notifier_names = [
                n.strip() for n in str(raw_notifiers).split(",") if n.strip()
            ]

        analyze_timeout = int(data.get("analyze_timeout", _cfg.analyze_timeout))
        workers = int(data.get("workers", _cfg.workers))

        try:
            parsed = mgr.force_scan_url(
                db_path=app.config["DB_PATH"],
                url=url,
                workers=workers,
                analyze_timeout=analyze_timeout,
                notifier_names=notifier_names,
                analyzer_model=_cfg.analyzer_model or None,
                analyzer_prompt=_cfg.analyzer_prompt or None,
                enabled_scanners=_cfg.enabled_scanners,
                scanner_config=_cfg.scanner_config,
            )
        except UnsupportedURLError as exc:
            return jsonify({"error": str(exc)}), 400
        except PackageNotFoundError as exc:
            return jsonify({"error": str(exc)}), 404
        except RuntimeError:
            return jsonify({"status": "already_running"}), 409

        return jsonify(
            {
                "status": "started",
                "ecosystem": parsed.ecosystem,
                "package": parsed.package,
                "version": parsed.version,
                "resolved_from": parsed.resolved_from,
            }
        ), 202

    # ------------------------------------------------------------------
    # Cron / service API routes
    # ------------------------------------------------------------------

    @app.route("/api/cron/status")
    def api_cron_status() -> Response:
        """Return installation status for both the polling cron and the dashboard service cron."""
        try:
            poll_status = scheduler.get_cron_status(scheduler._MARKER)
            dash_status = scheduler.get_cron_status(scheduler._DASHBOARD_MARKER)
        except scheduler.SchedulerError as exc:
            return jsonify({"error": str(exc)}), 500
        return jsonify({"monitor": poll_status, "dashboard": dash_status})

    @app.route("/api/cron/install", methods=["POST"])
    def api_cron_install() -> Response:
        """Install a cron entry for the polling job or the dashboard service.

        Body (JSON):
            {
                "type":     "monitor" | "dashboard",   // required
                "schedule": "*/5 * * * *",             // optional, default per type
                // monitor options:
                "db": "",  "top": "",  "notifiers": "",  "workers": "",
                // dashboard options:
                "port": "",  "host": "",  "log_level": ""
            }
        """
        data = request.get_json(silent=True) or {}
        job_type = (data.get("type") or "").strip()
        if job_type not in ("monitor", "dashboard"):
            return jsonify({"error": "type must be 'monitor' or 'dashboard'"}), 400

        try:
            if job_type == "monitor":
                schedule = (data.get("schedule") or scheduler._DEFAULT_SCHEDULE).strip()
                extra_parts: list[str] = []
                if data.get("db"):
                    extra_parts += ["--db", str(data["db"])]
                if data.get("new"):
                    extra_parts += ["--new"]
                    if data.get("new_limit") is not None:
                        extra_parts += ["--new-limit", str(data["new_limit"])]
                elif data.get("top"):
                    extra_parts += ["--top", str(data["top"])]
                if data.get("notifiers"):
                    extra_parts += ["--notifiers", str(data["notifiers"])]
                if data.get("workers"):
                    extra_parts += ["--workers", str(data["workers"])]
                extra_args = (" " + " ".join(extra_parts)) if extra_parts else ""
                scheduler.install_cron(schedule, extra_args)
                status = scheduler.get_cron_status(scheduler._MARKER)
            else:
                schedule = (
                    data.get("schedule") or scheduler._DEFAULT_DASHBOARD_SCHEDULE
                ).strip()
                extra_parts = []
                if data.get("db"):
                    extra_parts += ["--db", str(data["db"])]
                if data.get("port"):
                    extra_parts += ["--port", str(data["port"])]
                if data.get("host"):
                    extra_parts += ["--host", str(data["host"])]
                if data.get("log_level"):
                    extra_parts += ["--log-level", str(data["log_level"])]
                extra_args = (" " + " ".join(extra_parts)) if extra_parts else ""
                scheduler.install_dashboard_cron(schedule, extra_args)
                status = scheduler.get_cron_status(scheduler._DASHBOARD_MARKER)
        except scheduler.SchedulerError as exc:
            log.warning("cron install failed: %s", exc)
            return jsonify({"error": str(exc)}), 500

        return jsonify({"status": "installed", "line": status.get("line")}), 200

    @app.route("/api/cron/uninstall", methods=["POST"])
    def api_cron_uninstall() -> Response:
        """Remove a cron entry.

        Body (JSON): {"type": "monitor" | "dashboard"}
        """
        data = request.get_json(silent=True) or {}
        job_type = (data.get("type") or "").strip()
        if job_type not in ("monitor", "dashboard"):
            return jsonify({"error": "type must be 'monitor' or 'dashboard'"}), 400

        try:
            if job_type == "monitor":
                scheduler.uninstall_cron()
            else:
                scheduler.uninstall_dashboard_cron()
        except scheduler.SchedulerError as exc:
            log.warning("cron uninstall failed: %s", exc)
            return jsonify({"error": str(exc)}), 500

        return jsonify({"status": "removed"}), 200

    @app.route("/report")
    def report() -> Response:
        """Serve a local report file by path query param.

        Example: /report?path=/absolute/path/to/report.md
        Security: path must be under REPORTS_ROOT.
        """
        report_path_str = request.args.get("path", "")
        if not report_path_str:
            return Response("Missing path parameter", status=400)

        report_path = Path(report_path_str)

        reports_root: Path = app.config["REPORTS_ROOT"]

        if not report_path.is_absolute():
            return Response("Path must be absolute", status=400)

        if not report_path.is_relative_to(reports_root):
            log.warning(
                "report path %s is not under reports root %s — denied",
                report_path,
                reports_root,
            )
            return Response("Access denied", status=403)

        if not report_path.exists():
            return Response("Report not found", status=404)

        return send_file(report_path, mimetype="text/markdown")

    @app.route("/binary")
    def binary() -> Response:
        """Serve a tarball from binaries/ as a file download.

        Example: /binary?path=/absolute/path/to/lodash-4.17.22.tgz
        Security: path must be under BINARIES_ROOT.
        """
        binary_path_str = request.args.get("path", "")
        if not binary_path_str:
            return Response("Missing path parameter", status=400)

        binary_path = Path(binary_path_str)
        binaries_root: Path = app.config["BINARIES_ROOT"]

        if not binary_path.is_absolute():
            return Response("Path must be absolute", status=400)

        if not binary_path.is_relative_to(binaries_root):
            log.warning(
                "binary path %s is not under binaries root %s — denied",
                binary_path,
                binaries_root,
            )
            return Response("Access denied", status=403)

        if not binary_path.exists():
            return Response("Tarball not found", status=404)

        return send_file(binary_path, as_attachment=True)

    @app.route("/log")
    def log_file() -> Response:
        """Serve an opencode log file by path query param.

        Example: /log?path=/home/user/.local/share/opencode/log/2026-04-02T053609.log
        Security: path must be under LOGS_ROOT.
        """
        log_path_str = request.args.get("path", "")
        if not log_path_str:
            return Response("Missing path parameter", status=400)

        log_path = Path(log_path_str)
        logs_root: Path = app.config["LOGS_ROOT"]

        if not log_path.is_absolute():
            return Response("Path must be absolute", status=400)

        if not log_path.is_relative_to(logs_root):
            log.warning(
                "log path %s is not under logs root %s — denied",
                log_path,
                logs_root,
            )
            return Response("Access denied", status=403)

        if not log_path.exists():
            return Response("Log file not found", status=404)

        return send_file(log_path, mimetype="text/plain")

    @app.route("/api/verdict/<int:verdict_id>/log_path")
    def api_verdict_log_path(verdict_id: int) -> Response:
        """Return the opencode_log_path for a verdict row, or 404 if not set.

        Response JSON: {"log_path": "/abs/path/to/file.log"}
        """
        conn = _get_conn()
        row = conn.execute(
            "SELECT opencode_log_path FROM verdicts WHERE id = ?", (verdict_id,)
        ).fetchone()
        if row is None:
            return jsonify({"error": "verdict not found"}), 404
        if row["opencode_log_path"] is None:
            return jsonify({"error": "no log path recorded for this verdict"}), 404
        return jsonify({"log_path": row["opencode_log_path"]})

    # ------------------------------------------------------------------
    # Cron / service API routes
    # ------------------------------------------------------------------
    # Settings API
    # ------------------------------------------------------------------

    @app.route("/api/settings", methods=["POST"])
    def api_settings_save() -> Response:
        """Save settings to config.yaml.

        Body (JSON): subset of Config fields to update.
        Reads the current config.yaml, applies the submitted values, and
        writes back.  Unknown keys are silently ignored (validated by
        load_config on the next startup).
        """
        cfg_path: Path = app.config["CONFIG_PATH"]
        data = request.get_json(silent=True) or {}

        # Load current state (or defaults if file missing/empty)
        try:
            cfg = load_config(cfg_path)
        except ConfigError as exc:
            return jsonify({"error": f"Failed to read config: {exc}"}), 500

        # Apply submitted values — only known safe fields
        _FIELD_MAP: dict[str, str] = {
            "top": "top",
            "new_limit": "new_limit",
            "interval": "interval",
            "workers": "workers",
            "analyze_timeout": "analyze_timeout",
            "log_level": "log_level",
            "analyzer_model": "analyzer_model",
            "analyzer_prompt": "analyzer_prompt",
            "dashboard_host": "dashboard_host",
            "dashboard_port": "dashboard_port",
        }
        _LIST_FIELDS = {"ecosystems", "notifiers", "enabled_scanners"}

        for key, attr in _FIELD_MAP.items():
            if key in data:
                val = data[key]
                if attr in (
                    "top",
                    "new_limit",
                    "interval",
                    "workers",
                    "analyze_timeout",
                    "dashboard_port",
                ):
                    try:
                        setattr(cfg, attr, int(val))
                    except (TypeError, ValueError):
                        pass
                else:
                    setattr(cfg, attr, str(val))

        for key in _LIST_FIELDS:
            if key in data:
                val = data[key]
                if isinstance(val, list):
                    setattr(cfg, key, [str(v).strip() for v in val if str(v).strip()])
                elif isinstance(val, str):
                    setattr(
                        cfg,
                        key,
                        [v.strip() for v in val.split(",") if v.strip()],
                    )

        # Guard: analyzer_prompt must not be empty
        prompt_val = data.get("analyzer_prompt", cfg.analyzer_prompt)
        if not str(prompt_val).strip():
            return jsonify({"error": "analyzer_prompt must not be empty"}), 400

        if "scanner_config" in data and isinstance(data["scanner_config"], dict):
            cfg.scanner_config = {
                str(k): dict(v)
                for k, v in data["scanner_config"].items()
                if isinstance(v, dict)
            }

        # Serialise back to YAML — use a literal block scalar for the prompt
        def _literal_str_representer(dumper: yaml.Dumper, data: str) -> yaml.ScalarNode:
            if "\n" in data:
                return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")
            return dumper.represent_scalar("tag:yaml.org,2002:str", data)

        class _LiteralDumper(yaml.Dumper):
            pass

        _LiteralDumper.add_representer(str, _literal_str_representer)

        raw: dict = {
            "db": cfg.db,
            "top": cfg.top,
            "interval": cfg.interval,
            "once": cfg.once,
            "ecosystems": cfg.ecosystems,
            "notifiers": cfg.notifiers,
            "workers": cfg.workers,
            "analyze_timeout": cfg.analyze_timeout,
            "log_level": cfg.log_level,
            "dashboard": {
                "host": cfg.dashboard_host,
                "port": cfg.dashboard_port,
            },
            "analyzer": {
                "model": cfg.analyzer_model,
                "prompt": cfg.analyzer_prompt,
            },
            "scanners": cfg.enabled_scanners,
        }
        if cfg.scanner_config:
            raw["scanner_config"] = cfg.scanner_config
        if cfg.binaries_dir:
            raw["binaries_dir"] = cfg.binaries_dir
        if cfg.reports_dir:
            raw["dashboard"]["reports_dir"] = cfg.reports_dir

        try:
            yaml_text = yaml.dump(
                raw,
                Dumper=_LiteralDumper,
                default_flow_style=False,
                allow_unicode=True,
                sort_keys=False,
            )
            cfg_path.write_text(yaml_text, encoding="utf-8")
        except Exception as exc:
            log.exception("failed to write config.yaml: %s", exc)
            return jsonify({"error": f"Failed to write config: {exc}"}), 500

        log.info("config.yaml updated via settings API")
        return jsonify({"ok": True})

    @app.route("/api/verdicts/<int:verdict_id>", methods=["DELETE"])
    def api_delete_verdict(verdict_id: int) -> Response:
        """Delete a verdict by ID.

        Cascades to delete associated artifacts and alerts rows.
        """
        conn = _get_conn()
        deleted = queries.delete_verdict(conn, verdict_id)
        if deleted:
            log.info("verdict %d deleted", verdict_id)
            return jsonify({"status": "deleted", "id": verdict_id}), 200
        return jsonify({"error": "verdict not found"}), 404

    return app


# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> None:
    """Entrypoint for ``package-monitor-dashboard``."""
    import sys

    parser = argparse.ArgumentParser(
        prog="package-monitor-dashboard",
        description="Web dashboard for package-monitor.",
    )
    parser.add_argument(
        "--config",
        default=None,
        help="Path to config.yaml (default: config.yaml in current directory)",
    )
    parser.add_argument(
        "--db",
        default=_UNSET,
        help="SQLite database path",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=_UNSET,
        help="HTTP port",
    )
    parser.add_argument(
        "--host",
        default=_UNSET,
        help="Bind address",
    )
    parser.add_argument("--debug", action="store_true", help="Enable Flask debug mode")
    parser.add_argument(
        "--binaries-dir",
        default=_UNSET,
        dest="binaries_dir",
        help="Override binaries storage directory",
    )
    parser.add_argument(
        "--reports-dir",
        default=_UNSET,
        dest="reports_dir",
        help="Override reports directory for /report serving (default: project_root/reports)",
    )
    parser.add_argument(
        "--log-level",
        default=_UNSET,
        dest="log_level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging verbosity",
    )

    args = parser.parse_args(argv)

    config_path = Path(args.config) if args.config else None
    try:
        cfg = load_config(config_path)
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)  # noqa: T201
        sys.exit(1)

    cfg = apply_cli_overrides(cfg, args)

    try:
        validate_runtime_config(cfg)
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)  # noqa: T201
        sys.exit(1)

    logging.basicConfig(
        level=getattr(logging, cfg.log_level),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stdout,
    )

    db_path = Path(cfg.db).resolve()
    binaries_root = Path(cfg.binaries_dir).resolve() if cfg.binaries_dir else None
    reports_root = Path(cfg.reports_dir).resolve() if cfg.reports_dir else None

    # Ensure schema exists before serving any requests
    conn = db_module.init_db(db_path)
    conn.close()
    log.info(
        "dashboard starting  db=%s  host=%s  port=%d",
        db_path,
        cfg.dashboard_host,
        cfg.dashboard_port,
    )

    app = create_app(
        db_path=db_path,
        binaries_root=binaries_root,
        reports_root=reports_root,
        config_path=Path(args.config).resolve()
        if args.config
        else Path("config.yaml").resolve(),
    )
    app.run(host=cfg.dashboard_host, port=cfg.dashboard_port, debug=args.debug)
