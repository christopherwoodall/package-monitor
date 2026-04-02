"""Configuration system for package-monitor.

Priority (highest wins):
    CLI flags  >  config.yaml  >  built-in defaults in Config dataclass

Rules:
- Config is loaded ONCE in cli.py (or dashboard main()) via load_config().
- apply_cli_overrides() merges explicit CLI flags on top.
- No other module reads config.yaml directly.
- ConfigError is raised (never swallowed) when config.yaml exists but is
  malformed or contains unknown keys.
"""

from __future__ import annotations

import argparse
import logging
from dataclasses import dataclass, field, fields
from pathlib import Path

import yaml

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class ConfigError(Exception):
    """Raised when config.yaml exists but is malformed or has unknown keys."""


# ---------------------------------------------------------------------------
# Config dataclass — single source of truth for all tunables
# ---------------------------------------------------------------------------

# Top-level YAML keys that map directly to Config fields.
_TOP_LEVEL_KEYS: frozenset[str] = frozenset(
    {
        "db",
        "top",
        "new",
        "new_limit",
        "interval",
        "once",
        "ecosystems",
        "notifiers",
        "workers",
        "analyze_timeout",
        "log_level",
        "binaries_dir",
        "twitter",
        "no_local",
        "scanners",
        "scanner_config",
    }
)

# Nested YAML section keys and their field mappings.
_DIFFER_KEYS: frozenset[str] = frozenset({"max_diff_bytes", "context_lines"})
_DASHBOARD_KEYS: frozenset[str] = frozenset({"host", "port", "reports_dir"})
_ANALYZER_KEYS: frozenset[str] = frozenset({"model", "prompt"})


@dataclass
class Config:
    """All runtime tunables in one place.

    Field names mirror CLI flag names (with hyphens replaced by underscores).
    """

    # ── core ─────────────────────────────────────────────────────────────────
    db: str = "scm.db"
    top: int = 1000
    new: bool = False
    new_limit: int = 100  # max releases to yield per poll in --new mode (0 = unlimited)
    interval: int = 300
    once: bool = False
    ecosystems: list[str] = field(default_factory=lambda: ["npm", "pypi"])
    notifiers: list[str] = field(default_factory=lambda: ["local"])
    workers: int = 4
    analyze_timeout: int = 300
    log_level: str = "INFO"
    binaries_dir: str | None = None
    twitter: bool = False
    no_local: bool = False

    # ── differ ────────────────────────────────────────────────────────────────
    max_diff_bytes: int = 100_000
    context_lines: int = 3

    # ── dashboard ─────────────────────────────────────────────────────────────
    dashboard_host: str = "0.0.0.0"
    dashboard_port: int = 5000
    reports_dir: str | None = None

    # ── analyzer ──────────────────────────────────────────────────────────────
    analyzer_model: str = "github-copilot/claude-sonnet-4.6"
    analyzer_prompt: str = ""

    # ── scanners ──────────────────────────────────────────────────────────────
    enabled_scanners: list[str] = field(
        default_factory=lambda: ["diff", "base64_strings", "binary_strings"]
    )
    scanner_config: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


def load_config(path: Path | None = None) -> Config:
    """Load config.yaml from *path* and return a Config.

    If *path* is None, defaults to ``config.yaml`` in the current working
    directory.  If the file does not exist, returns a Config with all defaults.

    Raises ConfigError if the file exists but:
    - is not valid YAML
    - contains unknown top-level keys
    - contains unknown keys inside ``differ:`` or ``dashboard:`` sections
    """
    config_path = path if path is not None else Path("config.yaml")

    if not config_path.exists():
        log.debug("no config file at %s — using built-in defaults", config_path)
        return Config()

    log.info("loading config from %s", config_path)
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"config.yaml is not valid YAML: {exc}") from exc

    if raw is None:
        # Empty file is equivalent to no file.
        return Config()

    if not isinstance(raw, dict):
        raise ConfigError(
            f"config.yaml must be a YAML mapping at the top level, got {type(raw).__name__}"
        )

    _validate_keys(raw)
    return _build_config(raw)


def _validate_keys(raw: dict) -> None:
    """Raise ConfigError for any unrecognised key."""
    known_top = _TOP_LEVEL_KEYS | {"differ", "dashboard", "analyzer"}
    unknown_top = set(raw) - known_top
    if unknown_top:
        raise ConfigError(
            f"config.yaml contains unknown key(s): {', '.join(sorted(unknown_top))}"
        )

    differ_section = raw.get("differ")
    if differ_section is not None:
        if not isinstance(differ_section, dict):
            raise ConfigError("config.yaml: 'differ' must be a mapping")
        unknown_differ = set(differ_section) - _DIFFER_KEYS
        if unknown_differ:
            raise ConfigError(
                f"config.yaml differ section contains unknown key(s): "
                f"{', '.join(sorted(unknown_differ))}"
            )

    dashboard_section = raw.get("dashboard")
    if dashboard_section is not None:
        if not isinstance(dashboard_section, dict):
            raise ConfigError("config.yaml: 'dashboard' must be a mapping")
        unknown_dash = set(dashboard_section) - _DASHBOARD_KEYS
        if unknown_dash:
            raise ConfigError(
                f"config.yaml dashboard section contains unknown key(s): "
                f"{', '.join(sorted(unknown_dash))}"
            )

    analyzer_section = raw.get("analyzer")
    if analyzer_section is not None:
        if not isinstance(analyzer_section, dict):
            raise ConfigError("config.yaml: 'analyzer' must be a mapping")
        unknown_analyzer = set(analyzer_section) - _ANALYZER_KEYS
        if unknown_analyzer:
            raise ConfigError(
                f"config.yaml analyzer section contains unknown key(s): "
                f"{', '.join(sorted(unknown_analyzer))}"
            )


def _build_config(raw: dict) -> Config:
    """Apply raw YAML dict on top of defaults and return a Config."""
    cfg = Config()

    # ── top-level fields ──────────────────────────────────────────────────────
    if "db" in raw:
        cfg.db = str(raw["db"])
    if "top" in raw:
        cfg.top = int(raw["top"])
    if "new" in raw:
        if bool(raw["new"]):
            cfg.top = 0
            cfg.new = True
    if "new_limit" in raw:
        cfg.new_limit = int(raw["new_limit"])
    if "interval" in raw:
        cfg.interval = int(raw["interval"])
    if "once" in raw:
        cfg.once = bool(raw["once"])
    if "ecosystems" in raw:
        cfg.ecosystems = [str(e) for e in raw["ecosystems"]]
    if "notifiers" in raw:
        cfg.notifiers = [str(n) for n in raw["notifiers"]]
    if "workers" in raw:
        cfg.workers = int(raw["workers"])
    if "analyze_timeout" in raw:
        cfg.analyze_timeout = int(raw["analyze_timeout"])
    if "log_level" in raw:
        cfg.log_level = str(raw["log_level"]).upper()
    if "binaries_dir" in raw and raw["binaries_dir"] is not None:
        cfg.binaries_dir = str(raw["binaries_dir"])
    if "twitter" in raw:
        cfg.twitter = bool(raw["twitter"])
    if "no_local" in raw:
        cfg.no_local = bool(raw["no_local"])

    # ── differ section ────────────────────────────────────────────────────────
    differ = raw.get("differ") or {}
    if "max_diff_bytes" in differ:
        cfg.max_diff_bytes = int(differ["max_diff_bytes"])
    if "context_lines" in differ:
        cfg.context_lines = int(differ["context_lines"])

    # ── dashboard section ─────────────────────────────────────────────────────
    dashboard = raw.get("dashboard") or {}
    if "host" in dashboard:
        cfg.dashboard_host = str(dashboard["host"])
    if "port" in dashboard:
        cfg.dashboard_port = int(dashboard["port"])
    if "reports_dir" in dashboard and dashboard["reports_dir"] is not None:
        cfg.reports_dir = str(dashboard["reports_dir"])

    # ── analyzer section ──────────────────────────────────────────────────────
    analyzer = raw.get("analyzer") or {}
    if "model" in analyzer:
        cfg.analyzer_model = str(analyzer["model"])
    if "prompt" in analyzer:
        cfg.analyzer_prompt = str(analyzer["prompt"])

    # ── scanners section ──────────────────────────────────────────────────────
    if "scanners" in raw:
        cfg.enabled_scanners = [str(s) for s in raw["scanners"]]
    if "scanner_config" in raw and isinstance(raw["scanner_config"], dict):
        cfg.scanner_config = {
            str(k): dict(v)
            for k, v in raw["scanner_config"].items()
            if isinstance(v, dict)
        }

    return cfg


# ---------------------------------------------------------------------------
# CLI override merger
# ---------------------------------------------------------------------------

# Sentinel — argparse defaults are set to this so we can tell "user passed
# this flag explicitly" from "user left it at the argparse default".
_UNSET = object()


def apply_cli_overrides(cfg: Config, args: argparse.Namespace) -> Config:
    """Return a new Config with every explicitly-passed CLI flag merged on top.

    Checks each attribute against the _UNSET sentinel.  If the argparse
    default was set to _UNSET and the attribute is still _UNSET, that flag
    was not passed — the YAML value (or built-in default) is preserved.
    """
    # ── core ─────────────────────────────────────────────────────────────────
    if _is_set(args, "db"):
        cfg.db = args.db
    if _is_set(args, "top"):
        cfg.top = args.top
    if _is_set(args, "new") and args.new:
        cfg.top = 0
        cfg.new = True
    if _is_set(args, "new_limit"):
        cfg.new_limit = args.new_limit
    if _is_set(args, "interval"):
        cfg.interval = args.interval
    if _is_set(args, "once") and args.once:
        cfg.once = True
    if _is_set(args, "ecosystem"):
        cfg.ecosystems = [e.strip() for e in args.ecosystem.split(",") if e.strip()]
    if _is_set(args, "notifiers"):
        cfg.notifiers = [n.strip() for n in args.notifiers.split(",") if n.strip()]
    if _is_set(args, "workers"):
        cfg.workers = args.workers
    if _is_set(args, "analyze_timeout"):
        cfg.analyze_timeout = args.analyze_timeout
    if _is_set(args, "log_level"):
        cfg.log_level = args.log_level
    if _is_set(args, "binaries_dir") and args.binaries_dir is not None:
        cfg.binaries_dir = args.binaries_dir
    if _is_set(args, "twitter") and args.twitter:
        cfg.twitter = True
    if _is_set(args, "no_local") and args.no_local:
        cfg.no_local = True

    # ── dashboard ─────────────────────────────────────────────────────────────
    if _is_set(args, "host"):
        cfg.dashboard_host = args.host
    if _is_set(args, "port"):
        cfg.dashboard_port = args.port
    if _is_set(args, "reports_dir") and args.reports_dir is not None:
        cfg.reports_dir = args.reports_dir

    return cfg


def _is_set(args: argparse.Namespace, name: str) -> bool:
    """Return True if the argparse attribute exists and is not the _UNSET sentinel."""
    val = getattr(args, name, _UNSET)
    return val is not _UNSET


# ---------------------------------------------------------------------------
# Runtime validation
# ---------------------------------------------------------------------------


def validate_runtime_config(cfg: Config) -> None:
    """Raise ConfigError if the config is not suitable for running a scan.

    Called by cli.py and dashboard/app.py:main() after apply_cli_overrides.
    Currently checks that analyzer_prompt is non-empty (a blank prompt would
    send opencode nothing to act on).

    Raises:
        ConfigError: if analyzer_prompt is empty or whitespace-only.
    """
    if not cfg.analyzer_prompt or not cfg.analyzer_prompt.strip():
        raise ConfigError(
            "analyzer_prompt is empty — set analyzer.prompt in config.yaml"
        )
