# Violation Report

Audit performed against every directive in AGENTS.md.
All four tool wrappers were run (`package-monitor-complex`, `package-monitor-lint`,
`package-monitor-typecheck`, `package-monitor-graph`). The full test suite (393 tests)
was confirmed green before this audit.

Only concrete AGENTS.md directive violations are listed. Subjective style preferences
and acceptable patterns are excluded.

---

## 1. LINT VIOLATIONS — unused imports (`ruff F401`) and ambiguous name (`E741`)

**Severity: High** — AGENTS.md requires `package-monitor-lint` to be clean.

### Source files

| File | Line | Issue |
|------|------|-------|
| `src/scm/collectors/pypi.py` | 23 | `import time` — unused (F401) |
| `src/scm/config.py` | 18 | `from dataclasses import ... fields` — unused (F401) |
| `src/scm/orchestrator.py` | 16 | `datetime` and `timezone` imported but unused (F401) |

### Test files

| File | Lines / details |
|------|-----------------|
| `tests/test_collectors/test_npm.py` | 7 unused imports (F401) |
| `tests/test_collectors/test_pypi.py` | `import sqlite3` (line 7), `import time` (line 9), 3 more unused imports (F401) |
| `tests/test_dashboard/test_app.py` | 1 unused import (F401) |
| `tests/test_dashboard/test_queries.py` | 2 unused imports (F401) |
| `tests/test_dashboard/test_scanner.py` | 2 unused imports (F401) |
| `tests/test_extractor.py` | 1 unused import (F401) |
| `tests/test_notifiers/test_local.py` | 2 unused imports (F401) |
| `tests/test_notifiers/test_twitter.py` | 1 unused import (F401) |
| `tests/test_orchestrator.py` | 2 unused imports + unused variable `release` line 250 (F401, F841) |
| `tests/test_plugins.py` | 2 unused imports (F401) |
| `tests/test_scanners.py` | 4 unused imports including `tarfile`, `tempfile` (F401) |
| `tests/test_scheduler.py` | 3 unused imports + **ambiguous variable name `l`** line 163 (F401, E741) |
| `tests/test_storage.py` | 2 unused imports (F401) |

**Fix required:** Remove all unused imports; rename `l` to a descriptive name;
remove or use the `release` variable at `tests/test_orchestrator.py:250`.

---

## 2. TYPE CHECKER VIOLATIONS — `pyright` errors

**Severity: High** — AGENTS.md requires `package-monitor-typecheck` to be clean.

### `src/scm/dashboard/app.py` — 18 route return-type errors

Flask route functions that return `tuple[Response, int]` (e.g. `(jsonify(...), 202)`)
are annotated with return type `Response`. Pyright rejects the `int` status-code
component.

Affected routes (all in `create_app()`):
`api_scan_start`, `api_scan_reset`, `api_scan_force`, `api_cron_status`,
`api_cron_install`, `api_cron_uninstall`, `api_verdict_log_path`,
`api_settings_save`.

**Fix required:** Change return-type annotations from `Response` to
`Response | tuple[Response, int]` (or use Flask's `ResponseReturnValue` type alias)
for every affected route function.

### `src/scm/notifiers/twitter.py:109` — 1 attribute-access error

`response.data["id"]` — pyright reports `reportAttributeAccessIssue` because
`tweepy.Response.data` is typed as `dict | list | ... | None` and pyright cannot
prove it is subscriptable here.

**Fix required:** Add a type-narrowing guard:
```python
if not isinstance(response.data, dict):
    raise RuntimeError("Unexpected tweepy response shape")
tweet_id = response.data["id"]
```

---

## 3. LOUDNESS VIOLATION — `print()` in library functions

**Severity: Medium**

AGENTS.md states: "`print()` does not exist in library code."

`src/scm/scheduler.py` — the following calls are inside *library functions*
(not CLI entrypoints):

| Line | Call |
|------|------|
| 128 | `print(f"Installed: {new_line}")` inside `install_cron()` |
| 141 | `print("package-monitor polling cron entry removed ...")` inside `uninstall_cron()` |
| 161 | `print(f"Installed: {new_line}")` inside `install_dashboard_cron()` |
| 172 | `print("package-monitor-dashboard cron entry removed ...")` inside `uninstall_dashboard_cron()` |

The `print()` calls in the `*_main()` functions (lines 224, 241, 307, 324) are
CLI entrypoints and are **not** violations.

**Fix required:** Replace lines 128, 141, 161, 172 with `log.info(...)`.

---

## 4. MISSING DOCSTRING — `strip_ansi()` in `src/scm/analyzer.py`

**Severity: Low**

AGENTS.md requires docstrings on all public functions. `strip_ansi()` at
`src/scm/analyzer.py:38-39` has type annotations but no docstring.

**Fix required:**
```python
def strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences from *text* before regex parsing."""
    return ANSI_ESCAPE.sub("", text)
```

---

## Summary

| # | File(s) | Directive | Severity |
|---|---------|-----------|----------|
| 1 | 3 source files + 13 test files | `ruff` lint must be clean (F401 unused imports, F841 unused var, E741 ambiguous name) | **High** |
| 2 | `src/scm/dashboard/app.py` (18 errors), `src/scm/notifiers/twitter.py:109` | `pyright` type checks must pass | **High** |
| 3 | `src/scm/scheduler.py:128,141,161,172` | No `print()` in library code | **Medium** |
| 4 | `src/scm/analyzer.py:38` | Docstring required on all public functions | **Low** |
