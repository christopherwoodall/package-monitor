# AGENTS.md — The package-monitor Field Guide

> This file is the single source of truth for anyone — human or AI — picking up
> this codebase. It is a living document. If you change something that affects
> how this project is built, understood, or operated, update this file in the
> same commit. No exceptions.

---

## What This Project Is

`package-monitor` watches the most-downloaded packages across npm and PyPI and
runs every new release through an AI-powered security analysis. When a new
version of `lodash`, `requests`, or any other high-traffic package lands, we
download the tarball, diff it against the previous release, hand that diff to
`opencode`, and route the verdict to configured notification channels (local
file, Twitter, Slack).

The threat model is supply-chain attacks: malicious code injected into a
legitimate package between releases. The system is designed to catch that window
before the release is widely installed.

---

## Project Identity

| Key | Value |
|-----|-------|
| Distribution name | `package-monitor` |
| Python import name | `scm` |
| Source root | `src/scm/` |
| Test root | `tests/` |
| Binaries directory | `binaries/` (project root, never deleted by code) |
| Database file | `scm.db` (project root, created on first run) |
| Python requirement | `>=3.11` (currently running 3.14.0) |
| Package manager | `uv` — use it for **everything** |
| opencode version | 1.3.13 |
| Default workers | 4 (parallel release processing threads) |

---

## Getting Started

```bash
# Install in dev mode (creates .venv, registers entry points)
uv sync --extra dev

# Run the test suite — should be green before and after every change
uv run pytest -v

# Single poll cycle, top 10 packages, both ecosystems
uv run package-monitor --once --top 10

# Continuous monitoring, top 100, every 5 minutes
uv run package-monitor --top 100 --interval 300

# Watch ALL newly published releases regardless of download rank
uv run package-monitor --new --once

# Watch ALL new releases, but process at most 50 per poll cycle
uv run package-monitor --new --new-limit 50 --once

# With Twitter notifications
uv run package-monitor --once --notifiers local,twitter --top 10

# npm only
uv run package-monitor --once --ecosystem npm --top 50

# Install as a polling cron job (every 5 minutes, top 1000)
uv run package-monitor-install-cron --schedule "*/5 * * * *"

# Remove the polling cron job
uv run package-monitor-uninstall-cron

# Web dashboard — binds to 0.0.0.0:5000 by default
uv run package-monitor-dashboard --db scm.db

# Install dashboard as a persistent @reboot service
uv run package-monitor-dashboard-install-service --db /absolute/path/to/scm.db

# Remove the dashboard service entry
uv run package-monitor-dashboard-uninstall-service

# Run a single test module
uv run pytest tests/test_db.py -v

# Development tools
uv run package-monitor-lint          # ruff check + format --check
uv run package-monitor-typecheck     # pyright type analysis
uv run package-monitor-complex       # radon cyclomatic complexity
uv run package-monitor-graph         # pydeps module dependency graph
```

---

## Engineering Philosophy

This section describes how we think, not just what we type. Read it before
writing a line of code.

### Delete before you add

The instinct to reach for a new abstraction, class, or helper is usually wrong.
Before adding anything, ask: does this already exist? Can I make the existing
code handle this case? Can I remove something else to make this problem disappear?

Dead code is not neutral — it has a carrying cost. Every function someone has to
read and skip past is friction. When a feature is removed, remove everything
that only existed to support it. When a refactor makes a helper redundant, delete
the helper. An empty file is better than a file full of code nobody calls.

### Make it boring

Clever code is a liability. The right abstraction is the one a tired engineer
can read at 2am and understand in thirty seconds. We do not use Python's more
exotic features to save a few lines. We do not build frameworks when a loop
would do. We do not introduce indirection unless it eliminates something worse.

If a function fits in ten lines, it stays in ten lines. If a one-liner requires
a comment to explain it, write it in two lines instead.

### Loudness is a feature

Every meaningful step logs at an appropriate level. Every warning contains enough
context to diagnose the problem without opening the source. Every exception is
caught at the right layer — not too early (hiding the problem) and not too late
(crashing the process over a recoverable error).

`print()` does not exist in library code. `log.info` / `log.warning` /
`log.exception` are the only communication channels. If something goes wrong,
the log tells you what, where, and with what inputs.

Silent failures are bugs. A function that returns `None` when it should have
raised is a bug. A thread that exits quietly on an exception is a bug.

### Tests describe intent, not implementation

A test suite that breaks every time you rename a private variable is not a test
suite — it's a maintenance burden. Tests assert *what the system does*, not
*how it does it*. They mock at the boundary (network, filesystem, subprocess),
not in the middle of business logic.

Every test should be readable as a specification. The test name says what
scenario is being described. The body sets up that scenario, triggers the
behavior, and makes one focused assertion. If you need more than a few asserts,
you probably have more than one test.

The test suite is the fastest way to tell a new engineer what the code does.
Keep it that way.

### The file system is a source of truth

Tarballs in `binaries/` are permanent. They are not caches — they are the audit
trail. The database is queryable state layered on top. Neither is throwaway.
Do not design features that require deleting either.

### Know when to stop

Scope creep is the enemy of correctness. When a task is done, stop. Do not
"improve" adjacent code that isn't broken. Do not add configurability that
nobody has asked for. Do not generalize something that only has one use case.
Write the note in `AGENTS.md` and move on.

---

## Architecture

### Data flow

```
watchlist (npm/pypi top-N)
       │
       ▼
  [Collector.poll()]  ←── persisted state (seq / serial)
       │ yields Release
       ▼
  [orchestrator]
       │
       ├── get_previous_version()
       ├── download_tarball()        →  binaries/
       ├── extract + scan            ←  binaries/
       ├── analyze()                 →  opencode subprocess
       ├── save to DB
       └── notify()                  →  local / twitter / slack
```

### Module responsibilities

| Module | Responsibility | Must NOT |
|--------|----------------|----------|
| `models.py` | Pure dataclasses | Contain any logic |
| `db.py` | SQLite schema + CRUD | Contain business logic |
| `storage.py` | Download + hash + persist tarballs | Know about scanning or analysis |
| `extractor.py` | Safe tarball extraction to temp dirs | Know about network or DB |
| `scanners/` | Per-scanner plugins (diff, base64, binary) | Know about network or DB |
| `analyzer.py` | Subprocess opencode + parse verdict | Know about storage or DB |
| `orchestrator.py` | Wire everything together | Duplicate logic from other modules |
| `cli.py` | argparse + startup | Contain business logic |
| `scheduler.py` | crontab install/uninstall for polling job AND dashboard service | Know about npm or analysis |
| `plugins.py` | entry_points discovery for collectors, notifiers, scanners | Know about specific plugins |
| `collectors/npm.py` | npm CouchDB changes feed | Know about PyPI or scanning |
| `collectors/pypi.py` | PyPI XMLRPC changelog | Know about npm or scanning |
| `dashboard/` | Flask web UI + scan triggers + cron management API | Duplicate orchestrator logic |

If you are about to put something in a module that its "Must NOT" column
forbids, you are adding it to the wrong module.

### Why this module split

The split is not academic. It means any module can be tested in isolation
with straightforward mocks at its boundaries. `extractor.py` never makes a
network call, so its tests never need `mocker.patch("urllib.request.urlopen")`.
`storage.py` never touches the DB, so its tests never need a `db_conn` fixture.
The boundaries are the seams.

### Why entry_points for plugins

ABCs enforce the interface contract. Entry_points enable *discovery*. A
third-party package that adds a new collector only needs to add one line to its
own `pyproject.toml` — zero changes to this repo. The npm and PyPI collectors
bundled here are just the default plugins, not the entire universe.

`plugins.py` is the single loader. The CLI and orchestrator consume it. Nothing
else ever imports a collector, notifier, or scanner directly.

### Why `ThreadPoolExecutor` for release processing

Each release: download → extract → scan → opencode (up to 300s) → notify.
Sequential processing of four simultaneous releases = up to 20 minutes of
latency. With `--workers 4` the worst-case drops to ~5 minutes. The work is
I/O-bound (downloads, opencode subprocess), so threading benefits even under
the GIL.

### Why `subprocess.run(cwd=)` over `os.chdir()`

`os.chdir()` is process-global state. Two threads calling it simultaneously is
a race condition. `subprocess.run(cwd=)` is per-invocation and thread-safe.
This is non-negotiable — never use `os.chdir()` anywhere in this codebase.

### Why crontab over launchd

crontab is cross-platform Unix. launchd is macOS-only. The project is designed
to run on Linux servers. crontab is the right default.

### Why `@reboot` crontab for the dashboard service

The dashboard is a long-running Flask process. `@reboot` in crontab starts it
once per login session (macOS) or once per boot (Linux), which is the closest
cross-platform equivalent to a systemd service without requiring root or
OS-specific tooling. Two separate markers (`package-monitor` and
`package-monitor-dashboard`) ensure the polling cron and dashboard service
entries never interfere with each other during install or uninstall.
`get_cron_status(marker)` is a shared helper that reads the crontab once and
scans for the marker string — both the API and the CLI use the same code path.

### Why autocommit SQLite (`isolation_level=None`)

Explicit control over transaction boundaries is preferable to Python's implicit
transaction management, which has surprising behavior around DDL statements.
Every statement commits immediately. For multi-step atomicity, use explicit
`conn.execute("BEGIN")` / `conn.execute("COMMIT")`.

### Why `_current_month()` in `db.py`

A small helper so tests can `mocker.patch("scm.db._current_month")` without
needing to mock the entire `datetime` class. Mockable seams should be explicit
and narrow.

### Why `parents[2]` in storage.py and `parents[3]` in notifiers/local.py

`storage.py` lives at `src/scm/storage.py`:
`parents[0]` = `src/scm/` → `parents[1]` = `src/` → **`parents[2]`** = project root

`local.py` lives at `src/scm/notifiers/local.py`:
`parents[0]` = `src/scm/notifiers/` → `parents[1]` = `src/scm/` →
`parents[2]` = `src/` → **`parents[3]`** = project root

Counting from the wrong end crashes on startup. Count carefully.

---

## Hard Constraints

These are non-negotiable.

1. **No `requests`** in `src/scm/`. Use `urllib.request.urlopen` with streaming
   65536-byte chunks. `tweepy` may use `requests` internally — that's fine.

2. **No `urlretrieve`** — it's deprecated and not streaming. `urlopen` only.

3. **No string-prefix path checks.** Use `Path.is_relative_to()` for path
   validation. `"/foobar".startswith("/foo")` returns `True`.
   `Path("/foobar").is_relative_to(Path("/foo"))` returns `False`. The string
   version is a path-traversal vulnerability.

4. **Tarballs are permanent.** Once a file lands in `binaries/`, this code
   never deletes it. Re-runs reuse the cached tarball (sha256 comparison).

5. **opencode invocation** — always run `opencode --help` before writing any
   subprocess call. The only correct form for v1.3.13:
   ```python
   cmd = ["opencode", "run"]
   if model:
       cmd += ["--model", model]
   cmd.append(prompt)
   subprocess.run(
       cmd,
       capture_output=True, text=True, timeout=timeout,
       cwd=str(workspace),
   )
   ```
   `opencode run` is a subcommand, not a flag. `--model MODEL` is optional;
   omit it to use opencode's configured default.

6. **Strip ANSI escape codes from opencode output before regex parsing.**
   opencode writes colored terminal output. `analyzer.py` applies
   `ANSI_ESCAPE = re.compile(r"\x1b\[[^a-zA-Z]*[a-zA-Z]")` and strips all
   escape sequences before calling `parse_verdict()`. Any future output-parsing
   code must do the same.

7. **Autocommit SQLite.** `isolation_level=None` everywhere. No implicit
   transactions. Multi-statement atomicity requires explicit `BEGIN`/`COMMIT`.

8. **`upsert_release` raises `DuplicateRelease`** on UNIQUE constraint violation.
   Callers decide what to do. It is never silently swallowed.

9. **`WatchlistError` is never swallowed.** If the watchlist cannot load,
   raise loudly. No silent fallback to a cached or empty list.

10. **`from __future__ import annotations`** in every source file.

11. **`log = logging.getLogger(__name__)`** at module level in every file.
    Never use the root logger directly.

12. **Full type annotations** on every public function and method. No `Any`
    unless genuinely unavoidable and commented.

13. **Build backend is `setuptools.build_meta`** — not
    `setuptools.backends.legacy:build` (that module does not exist). Using the
    wrong form causes `uv sync` to fail with `ModuleNotFoundError`.

14. **Never store callable references in module-level dispatch dicts.** A dict
    like `{"npm": download_npm_tarball}` captures the object at import time.
    `mocker.patch("scm.storage.download_npm_tarball")` replaces the module
    attribute but the dict still holds the original. Store names as strings and
    look up with `getattr(sys.modules[__name__], name)` at call time so patches
    are respected.

15. **Always join worker threads before returning from a supervisor.**
    In `run_multi()` and `ScanManager._supervisor()`, the supervisor joins all
    worker threads before returning. Never rely on daemon threads to finish
    cleanup work.

16. **Validate both `is_relative_to(base)` and `path.exists()` before
    `send_file`.** `is_relative_to` is the security check; `path.exists()` is
    the usability check. Skipping the existence check raises `NotFound` with no
    useful error message.

---

## Data Sources

### npm watchlist

`https://registry.npmjs.org/download-counts/latest` — a real npm package
containing `package/counts.json` with ~3.8M packages and their monthly download
counts. Updated daily. Sort descending, take top-N.

### npm change feed

`https://replicate.npmjs.com/registry/_changes` — CouchDB replication feed.
HEAD `update_seq` is ~103M. The `GAP_RESET_THRESHOLD = 20_000` protects against
replaying millions of stale changes on first run. A fresh DB always triggers a
gap reset; this is expected.

**First-run mechanism (npm):** `load_state()` seeds `_poll_epoch = now - 30 days`
when no state exists (`_last_seq == 0`). On first poll the gap (`~103M - 0`)
always exceeds `GAP_RESET_THRESHOLD`. The code captures
`is_first_run = (self._last_seq == 0)` *before* mutating `_last_seq`, then sets
`_last_seq = head_seq`. When `is_first_run` is true it preserves `_poll_epoch`
and falls through to call `_detect_new_versions(pkg, self._poll_epoch)` for
every watchlisted package, then sets `_poll_epoch = cycle_start` and returns.
When `is_first_run` is false (genuine stall on a non-zero seq), it sets
`_poll_epoch = cycle_start` and returns early — no scan.

### PyPI watchlist

`https://hugovk.github.io/top-pypi-packages/top-pypi-packages-30-days.min.json`
— JSON with a `rows` array of `{project, download_count}` objects. Sort by
download count descending, take top-N.

### PyPI change feed

The PyPI XMLRPC API at `https://pypi.org/pypi` via `xmlrpc.client.ServerProxy`:

- `changelog_last_serial()` — current HEAD serial (mirrors npm `update_seq`)
- `changelog_since_serial(n)` — returns `(package, version, timestamp, action,
  serial)` tuples; filter to `action == "new release"`, deduplicate
  `(pkg.lower(), version)`

The RSS feed at `https://pypi.org/rss/updates.xml` shows only the 100 most
recent *global* PyPI releases. Top-1000 packages release infrequently enough
that zero appear in a typical 5-minute window. Do not use RSS.

`PYPI_GAP_RESET_THRESHOLD = 200_000` (~7 days at ~28,880 serials/day).

**First-run mechanism (PyPI):** `load_state()` leaves `_last_serial = 0` when no
state exists. The gap-reset guard is `if self._last_serial != 0 and gap > threshold`
— it does not fire on first run. First run is detected by
`if self._last_serial == 0`: seeds `_last_serial = max(0, current_serial -
PYPI_SERIALS_PER_DAY * 30)`, then falls through to the normal
`changelog_since_serial` path which returns the last 30 days of entries directly.

### PyPI metadata endpoints

- `/pypi/{package}/{version}/json` → file list is at `meta["urls"]` (flat list)
- `/pypi/{package}/json` → version history is at `meta["releases"]` (dict keyed
  by version)

These are different keys on different endpoints. Using the wrong one causes a
silent `KeyError`.

---

## Configuration System

### Three-layer priority

```
CLI flags  >  config.yaml  >  built-in defaults in Config dataclass
```

### Rules

- `Config` dataclass in `src/scm/config.py` is the single source of truth for
  all tunables. Adding a new tunable means adding a field here first.
- `load_config(path)` reads `config.yaml` (or the file at `path`) and returns a
  `Config`. If the file does not exist, returns a `Config` with all defaults.
- `apply_cli_overrides(cfg, args)` merges explicit CLI flags on top. Argparse
  defaults are set to the `_UNSET` sentinel so we can distinguish
  "user passed this flag" from "user left it at the argparse default".
- Both `cli.py` and `dashboard/app.py:main()` follow the same pattern:
  1. `parse_args()` — all defaults set to `_UNSET`
  2. `load_config(path)` — reads YAML or returns defaults
  3. `apply_cli_overrides(cfg, args)` — CLI wins over YAML
  4. Use `cfg.*` throughout
- No module other than `cli.py` and `dashboard/app.py:main()` reads
  `config.yaml` directly. Config is passed down as a value.
- `ConfigError` is raised (never swallowed) when `config.yaml` exists but is
  malformed YAML, is not a mapping, or contains unknown keys.

### config.yaml structure

```yaml
# top-level fields map 1:1 to Config fields
db: scm.db
top: 1000        # set to 0 (or use new: true) to watch all new releases
new: false       # true → sets top=0; equivalent to --new CLI flag
new_limit: 100   # max releases yielded per poll cycle in --new mode (0 = unlimited)
interval: 300
once: false
ecosystems: [npm, pypi]
notifiers: [local]
workers: 4
analyze_timeout: 300
log_level: INFO
binaries_dir: null
twitter: false
no_local: false

dashboard:
  host: 0.0.0.0
  port: 5000
  reports_dir: null

analyzer:
  model: github-copilot/claude-sonnet-4.6
  prompt: |
    # Supply Chain Diff Review
    ...  (full ~270-line prompt)
```

Unknown keys at any level cause `ConfigError` at startup.

### Threading analyzer_model and analyzer_prompt

`Config.analyzer_model` (default `"github-copilot/claude-sonnet-4.6"`) and
`Config.analyzer_prompt` (default = `""` — the prompt must be supplied via `config.yaml`)
are threaded through:
`Config` → `cli.py`/`main()` → `orchestrator.run_multi()` →
`_run_collector_thread()` → `run()` → `_process_release()` →
`analyzer.analyze()` → `run_opencode()`.

`ScanManager` accepts them on `start()` and `force_scan_package()`, threading
them through `_supervisor()` → `_run_one()` and `_run_force()` respectively.
The dashboard's `api_scan_start` and `api_scan_force` routes call
`load_config(app.config["CONFIG_PATH"])` at request time to pick up the
current values (including any edits saved via the settings page).

---

## Extending the System

### Adding a new collector

1. Create `src/scm/collectors/myecosystem.py`
2. Implement all abstract methods from `Collector` (see `collectors/__init__.py`)
3. Register in `pyproject.toml`:
   ```toml
   [project.entry-points."package_monitor.collectors"]
   myecosystem = "scm.collectors.myecosystem:MyEcosystemCollector"
   ```
4. Run `uv sync` to register the entry point
5. Use with: `uv run package-monitor --ecosystem myecosystem`

Minimal stub (copy and fill in the blanks):
```python
from __future__ import annotations

import logging
import sqlite3
from typing import Iterator

from scm import db as db_module
from scm.collectors import Collector, WatchlistError
from scm.models import Release

log = logging.getLogger(__name__)


class MyEcosystemCollector(Collector):
    ecosystem = "myecosystem"

    def __init__(self) -> None:
        self._watchlist: dict[str, int] = {}
        self._last_seq: int = 0

    def load_watchlist(self, top_n: int) -> None:
        # Must raise WatchlistError on any failure — no silent fallback
        raise WatchlistError("not implemented")

    def poll(self) -> Iterator[Release]:
        # Yield Release objects. Update state AFTER all yields.
        return iter([])

    def get_previous_version(self, package: str, new_version: str) -> str | None:
        return None

    def save_state(self, conn: sqlite3.Connection) -> None:
        db_module.set_collector_state(conn, self.ecosystem, {"seq": self._last_seq})

    def load_state(self, conn: sqlite3.Connection) -> None:
        state = db_module.get_collector_state(conn, self.ecosystem)
        self._last_seq = int(state.get("seq", 0))
```

Key things to get right in a real collector:
- Seed state on first run to a meaningful lookback window (30 days is standard),
  not zero and not now.
- Gap protection: if the feed is too far behind, reset to HEAD and return.
  Log loudly.
- Update sequence/serial pointer *after* all yields, so a mid-iteration crash
  retries from the same position.
- `get_previous_version` is called by the orchestrator after polling. If your
  registry has a version history API, implement it here.

### Adding a new notifier

1. Create `src/scm/notifiers/mychannel.py`
2. Implement the `Notifier` ABC (`notifiers/__init__.py`)
3. Register in `pyproject.toml`:
   ```toml
   [project.entry-points."package_monitor.notifiers"]
   mychannel = "scm.notifiers.mychannel:MyChannelNotifier"
   ```
4. Run `uv sync`
5. Enable with: `uv run package-monitor --notifiers local,mychannel`

`notify()` must never raise. Catch all exceptions internally and return
`Alert(success=False, detail=str(e))`. The orchestrator does not protect against
notifier exceptions — it expects the notifier to.

---

## Lessons

### Data sources

**PyPI RSS is useless for top-package monitoring.**
`https://pypi.org/rss/updates.xml` contains only the ~100 most recent global
releases. Top-1000 packages release infrequently enough that in a typical
5-minute window, zero appear. Use the XMLRPC `changelog_since_serial` API.

**npm's `update_seq` is ~103M, not small.**
`GAP_RESET_THRESHOLD = 20_000` protects against replaying millions of stale
changes on first run. A fresh DB will always trigger a gap reset. This is
correct and expected behavior.

**PyPI has two different JSON endpoints with different shapes.**
`/pypi/{package}/{version}/json` → file list at `meta["urls"]` (flat list).
`/pypi/{package}/json` → version history at `meta["releases"]` (dict by version).
Wrong key = silent `KeyError`.

**`download-counts` npm package is real and reliable.**
`https://registry.npmjs.org/download-counts/latest` returns a tarball with
`package/counts.json` containing ~3.8M packages and monthly download counts.
Updated daily. Sort descending for top-N. Verified working 2026-04-01.

### SQLite and state

**`isolation_level=None` requires careful transaction hygiene.**
With autocommit, every statement commits immediately. `upsert_release` +
`save_artifacts` are not atomic unless wrapped in explicit `BEGIN`/`COMMIT`.
This is currently acceptable. If we need atomicity, the mechanism is there.

**`upsert_release` raises `DuplicateRelease`, not silently returns.**
Callers must handle it. The orchestrator catches it and skips the duplicate
release. Never swallow it lower in the stack.

### Testing

**Dataclass equality tests must fix all fields, including timestamps.**
`datetime.now()` called twice produces distinct microsecond values. Two
`_make_release()` calls with no explicit `discovered_at` will never be equal.
Pass a shared explicit timestamp in equality tests.

**Test assertions must match exact rendered output.**
`LocalNotifier` renders markdown. Read the actual source before writing
string-match assertions.

### Concurrency

**`ScanManager` is one global instance per Flask app.**
Instantiated once in `create_app`, stored in `app.config["SCAN_MANAGER"]`.
Instantiating per-request loses all state.

**`run_multi()` dispatches via `kwargs=dict(...)` exclusively.**
All parameters are passed by name to `_run_collector_thread`. This prevents
silent ordering bugs when the function signature changes.

### Collectors

**`ScanManager._run_one` must call `collector.load_state()` explicitly.**
`_run_one` calls `poll()` directly for per-release logging. `load_state()` must
be called before `poll()` or the collector starts with zeroed-out seq/epoch.

### Dashboard

**`/report` must validate paths against `REPORTS_ROOT`, not `BINARIES_ROOT`.**
`LocalNotifier` writes reports to `project_root/reports/`. The route validates
against `app.config["REPORTS_ROOT"]`. `create_app()` accepts a
`reports_root: Path | None = None` parameter (default
`Path(__file__).resolve().parents[3] / "reports"`). A `--reports-dir` CLI flag
mirrors the existing `--binaries-dir` flag.

**`get_latest_per_package` extra filters must use `AND`, not a second `WHERE`.**
The query has a hard-coded `WHERE v.id IN (subquery)` clause. Extra ecosystem/
result conditions are built as `AND <cond1> AND <cond2>` (variable named
`extra_filter`) and interpolated after the closing `)` of the subquery. The
no-filter case interpolates an empty string — valid SQL.

**`ScanManager` scan history is capped at `_MAX_HISTORY = 20` entries.**
`_record_history()` is called at the end of every scan (supervisor or force).
`history()` returns entries newest-first (reversed insertion order). The Flask
route `/api/scan/history` exposes this list as JSON.

**Force-scan bypasses the collector poll entirely.**
`POST /api/scan/force` with `{ecosystem, package, version}` constructs a
`Release` directly and runs `orchestrator._process_release` in a background
thread. Useful for re-scanning a specific package@version without waiting for
the collector to detect it via the changes feed or XMLRPC.

**`package.html` has a "re-scan" button per version row (Actions column).**
Clicking "re-scan" POSTs to `/api/scan/force` with the page's ecosystem/package
and the row's version. On 202 the page shows "Scanning pkg@ver…", disables all
re-scan buttons, and polls `/api/scan/status` every 2s. When the status
transitions from `running` → idle, `window.location.reload()` is called so the
server-rendered history table picks up the new verdict without a separate JSON
endpoint. On 409 an inline message links to `/scan` for details.

**`POST /api/scan/reset` wipes collector_state rows.**
Deletes the named ecosystem rows from `collector_state` so the next scan
triggers first-run state seeding (30-day lookback). Does not require a scan
to be idle — safe to call at any time (the running scan has already loaded
state into memory).

**Cron API routes call `scheduler.py` functions directly.**
`GET /api/cron/status` calls `get_cron_status(marker)` twice — once for
`_MARKER` (polling job) and once for `_DASHBOARD_MARKER` (dashboard service).
`POST /api/cron/install` and `POST /api/cron/uninstall` dispatch to
`install_cron` / `uninstall_cron` (type=`"monitor"`) or
`install_dashboard_cron` / `uninstall_dashboard_cron` (type=`"dashboard"`).
The `type` field is required and validated — unknown values return 400.
`SchedulerError` from any scheduler function becomes a 500 response with
`{"error": "..."}` — never propagated as an unhandled exception.

**Polling install and dashboard install use separate markers to avoid collisions.**
`_MARKER = "package-monitor"` and `_DASHBOARD_MARKER = "package-monitor-dashboard"`.
`install_cron` filters out lines where `_MARKER in line AND _DASHBOARD_MARKER not in line`
— this correctly strips the polling line without touching the dashboard line (whose
text also contains `_MARKER` as a substring). Mirror logic in `uninstall_cron`.
If this guard is wrong, uninstalling the polling job also removes the dashboard
service.

**Settings page (`GET /settings`) reads and writes `config.yaml` directly.**
`app.config["CONFIG_PATH"]` is set in `create_app()` and is the path to the YAML
file. `GET /settings` renders `settings.html` pre-populated with the current
`Config` values. `POST /api/settings` deserializes the JSON body, validates it,
rebuilds the YAML dict, and writes it back using a `_LiteralDumper` that forces
literal block scalar style (`|`) for any multi-line string (specifically the
`analyzer.prompt` field). The ScanManager picks up the new values on the next
`start()` call — settings take effect without a restart for the next scan.

**Settings page sections and exposed fields:**

| Section | Fields |
|---|---|
| Core | `top`, `interval`, `workers`, `analyze_timeout`, `log_level` |
| Ecosystems & Notifiers | `ecosystems` (comma-separated), `notifiers` (comma-separated) |
| Analyzer | `analyzer_model`, `analyzer_prompt` (full-height textarea) |
| Dashboard | `dashboard_host`, `dashboard_port` (note: requires restart) |

Path/flag-only fields excluded from the settings UI: `db`, `binaries_dir`,
`reports_dir`, `once`, `twitter`, `no_local`.

**Sidebar layout in `base.html` and `_sidebar.html`.**
The dashboard uses a responsive two-column layout (`flex flex-col md:flex-row`).
`_sidebar.html` is a shared partial included by `base.html` — never duplicated.
Three nav items: Dashboard (`/`), Run Scan (`/scan`), Settings (`/settings`).
Active state is determined server-side via `request.endpoint` in Jinja.
On `md+` screens: vertical sidebar on the left. Below `md`: items render as a
compact horizontal strip below the top nav bar. No JavaScript required.
Active item on package detail pages (`package`) highlights "Dashboard".

---

## File Map

```
package-monitor/
├── pyproject.toml              entry points, dependencies, build config
├── AGENTS.md                   this file
├── config.yaml                 optional runtime configuration (all keys documented)
├── scm.db                      SQLite database (created on first run)
├── binaries/                   permanent tarball storage (never deleted)
├── reports/                    local notifier output (markdown files)
└── src/scm/
    ├── __init__.py             __version__ = "0.1.0"
    ├── config.py               Config dataclass, load_config, apply_cli_overrides
    ├── models.py               4 dataclasses: Release, StoredArtifact, Verdict, Alert
    ├── db.py                   schema, CRUD, collector_state, tweet counts
    ├── storage.py              download_tarball, sha256, integrity check
    ├── extractor.py            safe_extract — tar traversal validation, temp dir management
    ├── analyzer.py             run_opencode, parse_verdict, analyze; strips ANSI from output
    ├── plugins.py              load_collectors(), load_notifiers(), load_scanners()
    ├── orchestrator.py         process_release(), run(), run_multi()
    ├── cli.py                  argparse entry point
    ├── scheduler.py            install_cron, uninstall_cron, install_dashboard_cron,
    │                           uninstall_dashboard_cron, get_cron_status
    ├── collectors/
    │   ├── __init__.py         Collector ABC, WatchlistError
    │   ├── npm.py              NpmCollector
    │   └── pypi.py             PypiCollector
    ├── notifiers/
    │   ├── __init__.py         Notifier ABC
    │   ├── local.py            LocalNotifier — markdown files in reports/
    │   ├── twitter.py          TwitterNotifier — tweepy, rate-limited, 490/500 monthly budget guard
    │   └── slack.py            SlackNotifier — stub; always returns success=False (not yet implemented)
    ├── scanners/
    │   ├── __init__.py         Scanner ABC
    │   ├── diff.py             DiffScanner — unified diff of file trees
    │   ├── base64_strings.py   Base64Scanner — detects encoded payloads
    │   └── binary_strings.py   BinaryStringsScanner — suspicious strings in binaries
    ├── dashboard/
    │   ├── app.py              Flask app factory, routes, cron API, settings API, CLI entry point
    │   ├── queries.py          read-only SQL queries for the UI
    │   ├── scanner.py          ScanManager — background scan coordination
    │   └── templates/
    │       ├── base.html       two-column flex layout, includes _sidebar.html
    │       ├── _sidebar.html   shared sidebar partial (Dashboard, Run Scan, Settings nav)
    │       ├── index.html
    │       ├── package.html
    │       ├── scan.html       scan triggers, force-scan, reset state, cron management,
    │       │                   scan history, live log
    │       └── settings.html   settings form (4 sections, POST /api/settings)
    └── devtools/
        ├── __init__.py         Package marker (no business logic)
        ├── lint.py             `package-monitor-lint` entrypoint — runs ruff check + format
        ├── typecheck.py        `package-monitor-typecheck` entrypoint — runs pyright
        ├── complex.py          `package-monitor-complex` entrypoint — runs radon cc
        └── graph.py            `package-monitor-graph` entrypoint — runs pydeps --show-deps
```

---

## Development Tools (devtools)

Standalone CLI utilities for code quality analysis. These are **not** part of
package-monitor's core functionality — they are standalone commands for
developers working on the codebase.

### Design Principles

- **Self-contained**: Each module is standalone with its own `main()` function
- **Direct subprocess calls**: No abstractions — just `subprocess.run([tool, ...])`
- **Simple argparse**: No shared argument parsers or complex CLI frameworks
- **Exit code convention**: 0 = success/no issues, 1 = issues found/tool failed

### Tool Wrappers

| Command | Tool | Purpose | Default Paths |
|---------|------|---------|---------------|
| `package-monitor-lint` | ruff | Linting + format checking | `src tests` |
| `package-monitor-typecheck` | pyright | Static type analysis | `src` |
| `package-monitor-complex` | radon cc | Cyclomatic complexity | `src` |
| `package-monitor-graph` | pydeps | Module dependency graph | `src/scm` |

### Graph Output Format

`package-monitor-graph --format json` produces JSON suitable for AI agents:

```json
{
  "scm.analyzer": {
    "bacon": 1,
    "imported_by": ["scm.orchestrator"],
    "imports": ["scm", "scm.models", "scm.scanners"],
    "name": "scm.analyzer",
    "path": "/path/to/src/scm/analyzer.py"
  }
}
```

- `bacon`: Distance from entry point (0 = entry, 1 = direct import, etc.)
- `imports`: Modules this module imports
- `imported_by`: Modules that import this module
- `path`: Absolute filesystem path (null for stdlib/installed packages)

The output is a dependency graph that can be fed to AI agents for codebase
analysis without requiring them to parse Python source directly.

### Adding New Dev Tools

1. Create `src/scm/devtools/<toolname>.py`
2. Implement `def main(argv: list[str] | None = None) -> int:`
3. Add entrypoint to `pyproject.toml`:
   ```toml
   [project.scripts]
   package-monitor-<toolname> = "scm.devtools.<toolname>:main"
   ```
4. Add tool to `[project.optional-dependencies] dev` if needed
5. Run `uv sync` to register the entrypoint
6. Update this section of AGENTS.md

---

**Update this file in the same commit that introduces the change it describes.
Not after. Not in a follow-up. In the same commit.**

*Last updated: 2026-04-02 — Added `--new-limit` / `new_limit` feature: caps releases yielded per poll cycle in `--new` mode; documented in CLI examples and config.yaml structure; tests added for cap behaviour in both npm and PyPI collectors; settings page and scan UI wired up*
