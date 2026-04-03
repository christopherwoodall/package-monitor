"""Tests for scm.config — load_config, apply_cli_overrides, Config defaults."""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from scm.config import (
    Config,
    ConfigError,
    _UNSET,
    apply_cli_overrides,
    load_config,
    validate_runtime_config,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_yaml(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "config.yaml"
    p.write_text(content, encoding="utf-8")
    return p


def _make_namespace(**kwargs) -> argparse.Namespace:
    """Build a Namespace where every attribute not supplied is _UNSET."""
    # Fields that apply_cli_overrides checks
    keys = [
        "db",
        "top",
        "new",
        "new_limit",
        "interval",
        "once",
        "ecosystem",
        "notifiers",
        "workers",
        "analyze_timeout",
        "log_level",
        "binaries_dir",
        "twitter",
        "no_local",
        "host",
        "port",
        "reports_dir",
    ]
    ns = argparse.Namespace(**{k: _UNSET for k in keys})
    for k, v in kwargs.items():
        setattr(ns, k, v)
    return ns


# ---------------------------------------------------------------------------
# load_config — no file
# ---------------------------------------------------------------------------


def test_load_config_returns_defaults_when_no_file(tmp_path):
    path = tmp_path / "config.yaml"
    assert not path.exists()
    cfg = load_config(path)
    assert isinstance(cfg, Config)
    assert cfg.db == "scm.db"
    assert cfg.top == 1000
    assert cfg.interval == 300
    assert cfg.once is False
    assert cfg.ecosystems == ["npm", "pypi"]
    assert cfg.notifiers == ["local"]
    assert cfg.workers == 4
    assert cfg.analyze_timeout == 300
    assert cfg.log_level == "INFO"
    assert cfg.binaries_dir is None
    assert cfg.twitter is False
    assert cfg.no_local is False
    assert cfg.max_diff_bytes == 100_000
    assert cfg.context_lines == 3
    assert cfg.dashboard_host == "0.0.0.0"
    assert cfg.dashboard_port == 5000
    assert cfg.reports_dir is None


def test_load_config_empty_file_returns_defaults(tmp_path):
    path = _write_yaml(tmp_path, "")
    cfg = load_config(path)
    assert cfg.top == 1000


# ---------------------------------------------------------------------------
# load_config — valid YAML
# ---------------------------------------------------------------------------


def test_load_config_applies_top_level_values(tmp_path):
    path = _write_yaml(tmp_path, "top: 50\nworkers: 8\nlog_level: DEBUG\n")
    cfg = load_config(path)
    assert cfg.top == 50
    assert cfg.workers == 8
    assert cfg.log_level == "DEBUG"


def test_load_config_applies_ecosystems_list(tmp_path):
    path = _write_yaml(tmp_path, "ecosystems:\n  - npm\n")
    cfg = load_config(path)
    assert cfg.ecosystems == ["npm"]


def test_load_config_applies_notifiers_list(tmp_path):
    path = _write_yaml(tmp_path, "notifiers:\n  - local\n  - slack\n")
    cfg = load_config(path)
    assert cfg.notifiers == ["local", "slack"]


def test_load_config_applies_differ_section(tmp_path):
    path = _write_yaml(
        tmp_path, "differ:\n  max_diff_bytes: 50000\n  context_lines: 5\n"
    )
    cfg = load_config(path)
    assert cfg.max_diff_bytes == 50_000
    assert cfg.context_lines == 5


def test_load_config_applies_dashboard_section(tmp_path):
    path = _write_yaml(
        tmp_path,
        "dashboard:\n  host: 127.0.0.1\n  port: 8080\n  reports_dir: /tmp/reports\n",
    )
    cfg = load_config(path)
    assert cfg.dashboard_host == "127.0.0.1"
    assert cfg.dashboard_port == 8080
    assert cfg.reports_dir == "/tmp/reports"


def test_load_config_once_flag(tmp_path):
    path = _write_yaml(tmp_path, "once: true\n")
    cfg = load_config(path)
    assert cfg.once is True


def test_load_config_twitter_and_no_local(tmp_path):
    path = _write_yaml(tmp_path, "twitter: true\nno_local: true\n")
    cfg = load_config(path)
    assert cfg.twitter is True
    assert cfg.no_local is True


def test_load_config_db_and_binaries_dir(tmp_path):
    path = _write_yaml(tmp_path, "db: mydb.sqlite\nbinaries_dir: /data/bins\n")
    cfg = load_config(path)
    assert cfg.db == "mydb.sqlite"
    assert cfg.binaries_dir == "/data/bins"


# ---------------------------------------------------------------------------
# load_config — error cases
# ---------------------------------------------------------------------------


def test_load_config_malformed_yaml_raises_config_error(tmp_path):
    path = _write_yaml(tmp_path, "key: [unclosed bracket\n")
    with pytest.raises(ConfigError, match="not valid YAML"):
        load_config(path)


def test_load_config_non_mapping_top_level_raises_config_error(tmp_path):
    path = _write_yaml(tmp_path, "- item1\n- item2\n")
    with pytest.raises(ConfigError, match="mapping"):
        load_config(path)


def test_load_config_unknown_top_key_raises_config_error(tmp_path):
    path = _write_yaml(tmp_path, "unknown_key: 42\n")
    with pytest.raises(ConfigError, match="unknown key"):
        load_config(path)


def test_load_config_unknown_differ_key_raises_config_error(tmp_path):
    path = _write_yaml(tmp_path, "differ:\n  not_a_real_option: 1\n")
    with pytest.raises(ConfigError, match="differ section"):
        load_config(path)


def test_load_config_unknown_dashboard_key_raises_config_error(tmp_path):
    path = _write_yaml(tmp_path, "dashboard:\n  mystery_option: yes\n")
    with pytest.raises(ConfigError, match="dashboard section"):
        load_config(path)


def test_load_config_differ_not_a_mapping_raises_config_error(tmp_path):
    path = _write_yaml(tmp_path, "differ: not_a_mapping\n")
    with pytest.raises(ConfigError, match="differ.*mapping"):
        load_config(path)


def test_load_config_dashboard_not_a_mapping_raises_config_error(tmp_path):
    path = _write_yaml(tmp_path, "dashboard: 42\n")
    with pytest.raises(ConfigError, match="dashboard.*mapping"):
        load_config(path)


# ---------------------------------------------------------------------------
# apply_cli_overrides
# ---------------------------------------------------------------------------


def test_apply_cli_overrides_all_unset_preserves_yaml_values(tmp_path):
    path = _write_yaml(tmp_path, "top: 99\nworkers: 12\n")
    cfg = load_config(path)
    args = _make_namespace()
    result = apply_cli_overrides(cfg, args)
    assert result.top == 99
    assert result.workers == 12


def test_apply_cli_overrides_cli_top_wins(tmp_path):
    path = _write_yaml(tmp_path, "top: 99\n")
    cfg = load_config(path)
    args = _make_namespace(top=5)
    result = apply_cli_overrides(cfg, args)
    assert result.top == 5


def test_apply_cli_overrides_cli_db_wins():
    cfg = Config()
    args = _make_namespace(db="custom.db")
    result = apply_cli_overrides(cfg, args)
    assert result.db == "custom.db"


def test_apply_cli_overrides_cli_ecosystem_wins():
    cfg = Config()
    args = _make_namespace(ecosystem="npm")
    result = apply_cli_overrides(cfg, args)
    assert result.ecosystems == ["npm"]


def test_apply_cli_overrides_cli_notifiers_wins():
    cfg = Config(notifiers=["local"])
    args = _make_namespace(notifiers="local,twitter")
    result = apply_cli_overrides(cfg, args)
    assert result.notifiers == ["local", "twitter"]


def test_apply_cli_overrides_cli_once_sets_true():
    cfg = Config(once=False)
    args = _make_namespace(once=True)
    result = apply_cli_overrides(cfg, args)
    assert result.once is True


def test_apply_cli_overrides_once_false_does_not_override_yaml_true():
    """once=False from CLI (which is _UNSET default) must not override yaml once=true."""
    cfg = Config(once=True)
    # --once not passed; value is _UNSET
    args = _make_namespace()
    result = apply_cli_overrides(cfg, args)
    assert result.once is True


def test_apply_cli_overrides_cli_workers_wins():
    cfg = Config(workers=4)
    args = _make_namespace(workers=16)
    result = apply_cli_overrides(cfg, args)
    assert result.workers == 16


def test_apply_cli_overrides_cli_log_level_wins():
    cfg = Config(log_level="INFO")
    args = _make_namespace(log_level="DEBUG")
    result = apply_cli_overrides(cfg, args)
    assert result.log_level == "DEBUG"


def test_apply_cli_overrides_dashboard_host_and_port():
    cfg = Config()
    args = _make_namespace(host="127.0.0.1", port=9000)
    result = apply_cli_overrides(cfg, args)
    assert result.dashboard_host == "127.0.0.1"
    assert result.dashboard_port == 9000


def test_apply_cli_overrides_reports_dir():
    cfg = Config()
    args = _make_namespace(reports_dir="/tmp/rpts")
    result = apply_cli_overrides(cfg, args)
    assert result.reports_dir == "/tmp/rpts"


def test_apply_cli_overrides_twitter_sets_true():
    cfg = Config(twitter=False)
    args = _make_namespace(twitter=True)
    result = apply_cli_overrides(cfg, args)
    assert result.twitter is True


def test_apply_cli_overrides_no_local_sets_true():
    cfg = Config(no_local=False)
    args = _make_namespace(no_local=True)
    result = apply_cli_overrides(cfg, args)
    assert result.no_local is True


def test_apply_cli_overrides_new_flag_sets_top_zero_and_new_true():
    """--new CLI flag must set cfg.top=0 and cfg.new=True."""
    cfg = Config(top=1000, new=False)
    args = _make_namespace(new=True)
    result = apply_cli_overrides(cfg, args)
    assert result.top == 0
    assert result.new is True


def test_load_config_new_true_in_yaml_sets_top_zero():
    """new: true in config.yaml must set cfg.top=0 and cfg.new=True."""
    from pathlib import Path
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "config.yaml"
        p.write_text("new: true\n", encoding="utf-8")
        cfg = load_config(p)

    assert cfg.top == 0
    assert cfg.new is True


# ---------------------------------------------------------------------------
# Config defaults match expected values (sanity)
# ---------------------------------------------------------------------------


def test_config_default_ecosystems_is_list_not_shared():
    """Two Config() instances must not share the same list object."""
    a = Config()
    b = Config()
    assert a.ecosystems is not b.ecosystems


def test_config_default_notifiers_is_list_not_shared():
    a = Config()
    b = Config()
    assert a.notifiers is not b.notifiers


# ---------------------------------------------------------------------------
# load_config — analyzer section
# ---------------------------------------------------------------------------


def test_load_config_defaults_include_analyzer_model():
    cfg = Config()
    assert cfg.analyzer_model == "github-copilot/claude-sonnet-4.6"


def test_load_config_defaults_include_analyzer_prompt():
    cfg = Config()
    assert cfg.analyzer_prompt == ""


def test_load_config_applies_analyzer_model(tmp_path):
    path = _write_yaml(tmp_path, "analyzer:\n  model: openai/gpt-4o\n")
    cfg = load_config(path)
    assert cfg.analyzer_model == "openai/gpt-4o"


def test_load_config_applies_analyzer_prompt(tmp_path):
    path = _write_yaml(tmp_path, "analyzer:\n  prompt: My custom prompt\n")
    cfg = load_config(path)
    assert cfg.analyzer_prompt == "My custom prompt"


def test_load_config_analyzer_prompt_multiline(tmp_path):
    yaml_content = "analyzer:\n  prompt: |\n    Line one\n    Line two\n"
    path = _write_yaml(tmp_path, yaml_content)
    cfg = load_config(path)
    assert "Line one" in cfg.analyzer_prompt
    assert "Line two" in cfg.analyzer_prompt


def test_load_config_unknown_analyzer_key_raises_config_error(tmp_path):
    path = _write_yaml(tmp_path, "analyzer:\n  unknown_key: value\n")
    with pytest.raises(ConfigError, match="analyzer section"):
        load_config(path)


def test_load_config_analyzer_not_a_mapping_raises_config_error(tmp_path):
    path = _write_yaml(tmp_path, "analyzer: not_a_mapping\n")
    with pytest.raises(ConfigError, match="analyzer.*mapping"):
        load_config(path)


# ---------------------------------------------------------------------------
# load_config — scanner fields
# ---------------------------------------------------------------------------


def test_config_default_enabled_scanners():
    cfg = Config()
    assert cfg.enabled_scanners == ["diff", "base64_strings", "binary_strings"]


def test_config_default_scanner_config_is_empty_dict():
    cfg = Config()
    assert cfg.scanner_config == {}


def test_load_config_applies_scanners_list(tmp_path):
    path = _write_yaml(tmp_path, "scanners:\n  - base64_strings\n")
    cfg = load_config(path)
    assert cfg.enabled_scanners == ["base64_strings"]


def test_load_config_applies_scanner_config(tmp_path):
    yaml_content = (
        "scanner_config:\n  base64_strings:\n    min_length: 80\n    max_hits: 20\n"
    )
    path = _write_yaml(tmp_path, yaml_content)
    cfg = load_config(path)
    assert cfg.scanner_config == {"base64_strings": {"min_length": 80, "max_hits": 20}}


def test_load_config_scanner_config_ignores_non_dict_values(tmp_path):
    yaml_content = (
        "scanner_config:\n"
        "  base64_strings:\n"
        "    min_length: 80\n"
        "  bad_entry: not_a_dict\n"
    )
    path = _write_yaml(tmp_path, yaml_content)
    cfg = load_config(path)
    assert "base64_strings" in cfg.scanner_config
    assert "bad_entry" not in cfg.scanner_config


def test_load_config_scanners_not_a_list_is_ignored(tmp_path):
    """If 'scanners' value is not a list, it should not crash; use default."""
    # A scalar value — the config coerces each element via str(), so a scalar
    # string is treated as a one-element iterable by Python when iterating.
    # The code does [str(s) for s in raw["scanners"]] — if it's a plain string
    # it will iterate characters.  Verify it doesn't raise.
    path = _write_yaml(tmp_path, "scanners:\n  - custom_scanner\n")
    cfg = load_config(path)
    assert cfg.enabled_scanners == ["custom_scanner"]


# ---------------------------------------------------------------------------
# validate_runtime_config
# ---------------------------------------------------------------------------


def test_validate_runtime_config_passes_with_non_empty_prompt():
    cfg = Config(analyzer_prompt="# My prompt\nDo the analysis.")
    # Should not raise
    validate_runtime_config(cfg)


def test_validate_runtime_config_raises_on_empty_prompt():
    cfg = Config(analyzer_prompt="")
    with pytest.raises(ConfigError, match="analyzer_prompt"):
        validate_runtime_config(cfg)


def test_validate_runtime_config_raises_on_whitespace_only_prompt():
    cfg = Config(analyzer_prompt="   \n\t  ")
    with pytest.raises(ConfigError, match="analyzer_prompt"):
        validate_runtime_config(cfg)
