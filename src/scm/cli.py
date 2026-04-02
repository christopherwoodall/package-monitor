"""CLI entrypoint for package-monitor.

Discovers collectors and notifiers via entry_points (importlib.metadata).
Supports multiple ecosystems monitored in parallel (default: npm,pypi).

Config priority (highest wins): CLI flags > config.yaml > built-in defaults.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from scm import plugins, storage as storage_module
from scm import db as db_module
from scm import orchestrator
from scm.config import (
    Config,
    ConfigError,
    _UNSET,
    apply_cli_overrides,
    load_config,
    validate_runtime_config,
)

log = logging.getLogger(__name__)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="package-monitor",
        description="Supply chain security monitor for npm and PyPI (and more).",
    )
    p.add_argument(
        "--config",
        default=None,
        help="Path to config.yaml (default: config.yaml in current directory)",
    )
    p.add_argument("--db", default=_UNSET, help="SQLite database path")
    p.add_argument(
        "--top",
        type=int,
        default=_UNSET,
        help="Top-N packages to watch per ecosystem (0 = all new releases)",
    )
    p.add_argument(
        "--new",
        action="store_true",
        default=_UNSET,
        help="Watch all newly published releases regardless of download rank (sets --top 0)",
    )
    p.add_argument(
        "--new-limit",
        type=int,
        default=_UNSET,
        dest="new_limit",
        help=(
            "Max releases to process per poll cycle in --new mode "
            "(0 = unlimited, default 100)"
        ),
    )
    p.add_argument(
        "--interval",
        type=int,
        default=_UNSET,
        help="Poll interval in seconds",
    )
    p.add_argument(
        "--once",
        action="store_true",
        default=_UNSET,
        help="Run one poll cycle then exit",
    )
    p.add_argument(
        "--ecosystem",
        default=_UNSET,
        help="Comma-separated collector ecosystem names to monitor in parallel",
    )
    p.add_argument(
        "--notifiers",
        default=_UNSET,
        help="Comma-separated notifier names",
    )
    p.add_argument(
        "--twitter",
        action="store_true",
        default=_UNSET,
        help="Shortcut for --notifiers local,twitter",
    )
    p.add_argument(
        "--no-local",
        action="store_true",
        default=_UNSET,
        help="Disable the local filesystem notifier",
    )
    p.add_argument(
        "--workers",
        type=int,
        default=_UNSET,
        help="Parallel release processing workers per ecosystem",
    )
    p.add_argument(
        "--analyze-timeout",
        type=int,
        default=_UNSET,
        dest="analyze_timeout",
        help="opencode timeout per release in seconds",
    )
    p.add_argument(
        "--binaries-dir",
        default=_UNSET,
        dest="binaries_dir",
        help="Override binaries storage directory",
    )
    p.add_argument(
        "--log-level",
        default=_UNSET,
        dest="log_level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging verbosity",
    )
    return p


def _resolve_notifiers(cfg: Config) -> list[str]:
    """Apply twitter / no_local shortcut flags to the notifiers list."""
    names = list(cfg.notifiers)
    if cfg.twitter and "twitter" not in names:
        names.append("twitter")
    if cfg.no_local and "local" in names:
        names.remove("local")
    return names


def main(argv: list[str] | None = None) -> None:
    parser = _build_parser()
    args = parser.parse_args(argv)

    # ── load config (YAML → defaults) then apply CLI overrides ───────────────
    config_path = Path(args.config) if args.config else None
    try:
        cfg = load_config(config_path)
    except ConfigError as exc:
        # Can't use log yet — logging not configured
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

    # Optional binaries-dir override
    if cfg.binaries_dir:
        storage_module.BINARIES_ROOT = Path(cfg.binaries_dir).resolve()

    # ── load plugins ─────────────────────────────────────────────────────────
    available_collectors = plugins.load_collectors()
    available_notifiers = plugins.load_notifiers()
    available_scanners = plugins.load_scanners()

    # ── resolve ecosystem list ────────────────────────────────────────────────
    if not cfg.ecosystems:
        log.error(
            "no ecosystems specified — set ecosystems in config.yaml or use --ecosystem"
        )
        sys.exit(1)

    for eco in cfg.ecosystems:
        if eco not in available_collectors:
            log.error(
                "unknown ecosystem %r — available: %s",
                eco,
                ", ".join(available_collectors) or "(none)",
            )
            sys.exit(1)

    # ── resolve notifier names ────────────────────────────────────────────────
    notifier_names = _resolve_notifiers(cfg)

    notifiers = []
    for name in notifier_names:
        if name not in available_notifiers:
            log.warning("notifier %r not found — skipping", name)
            continue
        try:
            notifiers.append(available_notifiers[name]())
        except Exception as exc:
            log.error("failed to initialise notifier %r: %s", name, exc)
            sys.exit(1)

    # ── init DB ───────────────────────────────────────────────────────────────
    db_path = Path(cfg.db).resolve()
    conn = db_module.init_db(db_path)
    conn.close()

    # ── build and configure scanner instances ─────────────────────────────────
    scanners = []
    for name in cfg.enabled_scanners:
        if name not in available_scanners:
            log.warning("scanner %r not registered — skipping", name)
            continue
        try:
            scanner = available_scanners[name]()
            opts = cfg.scanner_config.get(name, {})
            if opts:
                scanner.configure(opts)
            scanners.append(scanner)
        except Exception as exc:
            log.warning("failed to initialise scanner %r: %s", name, exc)

    if scanners:
        log.info("scanners enabled: %s", ", ".join(s.name for s in scanners))
    else:
        log.info("no scanners enabled")

    # ── create collectors and load watchlists ─────────────────────────────────
    collectors = []
    for eco in cfg.ecosystems:
        collector = available_collectors[eco]()
        try:
            collector.load_watchlist(cfg.top, new_limit=cfg.new_limit)
        except Exception as exc:
            log.error("failed to load watchlist for %r: %s", eco, exc)
            sys.exit(1)
        collectors.append(collector)

    # ── startup banner ─────────────────────────────────────────────────────────
    notifier_list = ", ".join(n.name for n in notifiers) or "(none)"
    mode = "once" if cfg.once else f"continuous, {cfg.interval}s interval"
    log.info(
        "SCM starting  ecosystems=%s (%s)  db=%s  binaries=%s"
        "  notifiers=%s  workers=%d  mode=%s",
        ", ".join(cfg.ecosystems),
        "all packages" if cfg.top == 0 else f"top {cfg.top} each",
        db_path,
        storage_module.BINARIES_ROOT,
        notifier_list,
        cfg.workers,
        mode,
    )

    # ── run ───────────────────────────────────────────────────────────────────
    orchestrator.run_multi(
        collectors=collectors,
        notifiers=notifiers,
        db_path=db_path,
        interval=cfg.interval,
        once=cfg.once,
        top_n=cfg.top,
        analyze_timeout=cfg.analyze_timeout,
        workers=cfg.workers,
        analyzer_model=cfg.analyzer_model,
        analyzer_prompt=cfg.analyzer_prompt,
        scanners=scanners or None,
    )
