# Violation Report

This document contains all directive violations found in the codebase, organized by category.

## PLUGIN ISOLATION

No violations found. The codebase correctly uses plugins.py as the single point of plugin loading, and orchestrator.py/cli.py import only from plugins.py, not directly from collectors/notifiers.

---

## COMPLEXITY LIMITS

### Functions Exceeding 30 Lines

1. **src/scm/config.py:112-145** - `load_config()` is ~33 lines
   - Directive: "Make it boring - If a function fits in ten lines, it stays in ten lines"
   - Fix: Split into smaller helper functions

2. **src/scm/config.py:148-188** - `_validate_keys()` is ~40 lines with high cyclomatic complexity
   - Directive: "Any function with cyclomatic complexity above 4"
   - Fix: Extract each section validation into separate helpers

3. **src/scm/config.py:191-258** - `_build_config()` is ~67 lines with complexity > 10
   - Directive: "Any function with cyclomatic complexity above 4"
   - Fix: Extract top-level, differ, dashboard, analyzer section builders

4. **src/scm/config.py:270-314** - `apply_cli_overrides()` is ~44 lines with complexity ~15
   - Directive: "Any function with cyclomatic complexity above 4"
   - Fix: Group related overrides into helper functions

5. **src/scm/collectors/npm.py:52-119** - `load_watchlist()` is ~67 lines
   - Directive: "Any function exceeding 30 lines"
   - Fix: Extract download, extract, parse steps into helpers

6. **src/scm/collectors/npm.py:181-315** - `poll()` is ~134 lines with complexity > 15
   - Directive: "Any function exceeding 30 lines; cyclomatic complexity above 4"
   - Fix: Extract gap reset logic, changes pagination, and epoch scanning into separate functions

7. **src/scm/collectors/pypi.py:59-89** - `load_watchlist()` is ~30 lines (borderline)
   - Directive: "Any function exceeding 30 lines"
   - Fix: Extract download and parse into helpers

8. **src/scm/collectors/pypi.py:110-221** - `poll()` is ~111 lines with complexity > 12
   - Directive: "Any function exceeding 30 lines; cyclomatic complexity above 4"
   - Fix: Extract gap reset, first-run seeding, and candidate filtering into helpers

9. **src/scm/storage.py:71-151** - `download_npm_tarball()` is ~80 lines
   - Directive: "Any function exceeding 30 lines"
   - Fix: Extract metadata fetch, cache check, and download into helpers

10. **src/scm/storage.py:163-267** - `download_pypi_tarball()` is ~104 lines
    - Directive: "Any function exceeding 30 lines"
    - Fix: Extract metadata fetch, sdist selection, cache check, and download into helpers

11. **src/scm/analyzer.py:56-130** - `run_opencode()` is ~74 lines
    - Directive: "Any function exceeding 30 lines"
    - Fix: Extract log file detection into helper

12. **src/scm/analyzer.py:160-234** - `analyze()` is ~74 lines
    - Directive: "Any function exceeding 30 lines"
    - Fix: Extract scanner running and findings writing into helpers

13. **src/scm/orchestrator.py:36-165** - `_process_release()` is ~129 lines
    - Directive: "Any function exceeding 30 lines"
    - Fix: Extract download, extract, analyze, and notify phases into helpers

14. **src/scm/orchestrator.py:167-220** - `run()` is ~53 lines
    - Directive: "Any function exceeding 30 lines"
    - Fix: Extract poll cycle and worker setup into helpers

15. **src/scm/orchestrator.py:265-341** - `run_multi()` is ~76 lines
    - Directive: "Any function exceeding 30 lines"
    - Fix: Thread creation and joining logic is acceptable complexity

16. **src/scm/dashboard/scanner.py:58-200** - `start()` is ~142 lines
    - Directive: "Any function exceeding 30 lines"
    - Fix: Extract collector building, notifier building, and scanner setup into helpers

17. **src/scm/dashboard/scanner.py:202-322** - `force_scan_package()` is ~120 lines
    - Directive: "Any function exceeding 30 lines"
    - Fix: Extract notifier setup and scanner building into helpers

18. **src/scm/dashboard/scanner.py:378-434** - `_supervisor()` is ~56 lines
    - Directive: "Any function exceeding 30 lines"
    - Fix: Acceptable for thread coordination logic

19. **src/scm/dashboard/scanner.py:436-509** - `_run_one()` is ~73 lines
    - Directive: "Any function exceeding 30 lines"
    - Fix: Extract release processing loop into helper

20. **src/scm/dashboard/app.py:39-771** - `create_app()` is ~732 lines
    - Directive: "Any function exceeding 30 lines"
    - Fix: Extract route handlers into separate module-level functions

21. **src/scm/scheduler.py:111-142** - `install_cron()` is ~31 lines
    - Directive: "Any function exceeding 30 lines"
    - Fix: Acceptable; mostly string operations

22. **src/scm/scheduler.py:149-172** - `install_dashboard_cron()` is ~23 lines (OK)

23. **src/scm/scanners/diff.py:85-168** - `_build_report()` is ~83 lines
    - Directive: "Any function exceeding 30 lines"
    - Fix: Extract summary table building, file list building, and diff generation into helpers

24. **src/scm/scanners/base64_strings.py:52-105** - `scan()` is ~53 lines
    - Directive: "Any function exceeding 30 lines"
    - Fix: Extract file scanning and result formatting into helpers

25. **src/scm/scanners/binary_strings.py:52-116** - `scan()` is ~64 lines
    - Directive: "Any function exceeding 30 lines"
    - Fix: Extract binary processing and section building into helpers

---

## DEFENSIVE CODING

### Bare Except Clauses

1. **src/scm/plugins.py:37** - `except Exception as exc:`
   - Context: Plugin loading
   - Fix: Log with `log.exception()` instead of `log.warning()` for full stack trace

2. **src/scm/plugins.py:50** - `except Exception as exc:`
   - Context: Plugin loading
   - Fix: Log with `log.exception()` instead of `log.warning()`

3. **src/scm/plugins.py:63** - `except Exception as exc:`
   - Context: Plugin loading
   - Fix: Log with `log.exception()` instead of `log.warning()`

4. **src/scm/collectors/npm.py:74** - `except Exception as exc:`
   - Context: Watchlist metadata fetch
   - Fix: Wrap in WatchlistError and raise (already done correctly)

5. **src/scm/collectors/npm.py:86** - `except Exception as exc:`
   - Context: Watchlist tarball download
   - Fix: Wrap in WatchlistError and raise (already done correctly)

6. **src/scm/collectors/npm.py:162** - `except Exception as exc:`
   - Context: Packument fetch in get_previous_version
   - Fix: Already logs and returns None (acceptable for optional feature)

7. **src/scm/collectors/npm.py:238** - `except Exception as exc:`
   - Context: New version detection in poll()
   - Fix: Already logs and continues (acceptable for per-package failure)

8. **src/scm/collectors/npm.py:264** - `except Exception as exc:`
   - Context: Changes fetch in poll()
   - Fix: Already logs and breaks (acceptable)

9. **src/scm/collectors/npm.py:294** - `except Exception as exc:`
   - Context: New version detection loop
   - Fix: Already logs and continues (acceptable)

10. **src/scm/collectors/pypi.py:75** - `except Exception as exc:`
    - Context: Watchlist fetch
    - Fix: Wrap in WatchlistError (already done)

11. **src/scm/collectors/pypi.py:117** - `except Exception as exc:`
    - Context: Head serial fetch
    - Fix: Already logs and returns (acceptable)

12. **src/scm/collectors/pypi.py:146** - `except Exception as exc:`
    - Context: Changelog fetch
    - Fix: Already logs and returns (acceptable)

13. **src/scm/collectors/pypi.py:189** - `except Exception as exc:`
    - Context: Version metadata fetch
    - Fix: Already logs and continues (acceptable)

14. **src/scm/collectors/pypi.py:235** - `except Exception as exc:`
    - Context: Package metadata fetch in get_previous_version
    - Fix: Already logs and returns None (acceptable)

15. **src/scm/storage.py:86** - `except Exception as exc:`
    - Context: Metadata fetch
    - Fix: Raise DownloadError (already done)

16. **src/scm/storage.py:133** - `except Exception as exc:`
    - Context: Download failure
    - Fix: Cleanup and raise DownloadError (already done)

17. **src/scm/storage.py:178** - `except Exception as exc:`
    - Context: PyPI metadata fetch
    - Fix: Raise DownloadError (already done)

18. **src/scm/storage.py:239** - `except Exception as exc:`
    - Context: PyPI download failure
    - Fix: Cleanup and raise DownloadError (already done)

19. **src/scm/storage.py:247-252** - SHA256 mismatch check
    - Fix: Already raises DownloadError (correct)

20. **src/scm/analyzer.py:201** - `except Exception as exc:`
    - Context: Scanner failure
    - Fix: Logs warning (acceptable - scanners must not crash analysis)

21. **src/scm/dashboard/scanner.py:145** - `except Exception as exc:`
    - Context: Scanner initialization
    - Fix: Logs warning (acceptable)

22. **src/scm/dashboard/scanner.py:159** - `except Exception as exc:`
    - Context: Watchlist loading
    - Fix: Logs exception and sets error status (acceptable)

23. **src/scm/dashboard/scanner.py:173** - `except Exception as exc:`
    - Context: Notifier initialization
    - Fix: Logs warning (acceptable)

24. **src/scm/dashboard/scanner.py:266** - `except Exception as exc:`
    - Context: Notifier initialization in force scan
    - Fix: Logs warning (acceptable)

25. **src/scm/dashboard/scanner.py:290** - `except Exception as exc:`
    - Context: Scanner initialization in force scan
    - Fix: Logs warning (acceptable)

### Masking Missing Values with `or {} / or [] / or ""`

1. **src/scm/db.py:246** - `return json.loads(row["state_json"]) if row else {}`
   - Context: get_collector_state return value
   - Fix: Acceptable - empty dict is valid "no state" representation

2. **src/scm/storage.py:94** - `integrity: str = dist.get("integrity", "")`
   - Context: npm integrity string
   - Fix: Acceptable - empty string means "no integrity check"

3. **src/scm/collectors/pypi.py:204** - `expected_sha256: str = sdist_entry.get("digests", {}).get("sha256", "")`
   - Context: PyPI SHA256 digest
   - Fix: Acceptable - empty string handled downstream

4. **src/scm/collectors/pypi.py:240** - `ts_str = files[0].get("upload_time_iso_8601") or files[0].get("upload_time", "")`
   - Context: Version timestamp extraction
   - Fix: Acceptable - fallback logic for optional field

5. **src/scm/config.py:226** - `differ = raw.get("differ") or {}`
   - Context: Config section parsing
   - Fix: Acceptable - missing section treated as empty

6. **src/scm/config.py:233** - `dashboard = raw.get("dashboard") or {}`
   - Context: Config section parsing
   - Fix: Acceptable - missing section treated as empty

7. **src/scm/analyzer.py:192** - `cf = changed_files or []`
   - Context: Optional parameter handling
   - Fix: **VIOLATION** - function signature already has default, guard is unnecessary
   - Change: Remove this line, use changed_files directly

8. **src/scm/analyzer.py:193** - `af = added_files or []`
   - Context: Optional parameter handling
   - Fix: **VIOLATION** - function signature already has default, guard is unnecessary
   - Change: Remove this line, use added_files directly

9. **src/scm/analyzer.py:214** - `raw_output, opencode_log_path = run_opencode(...)`
   - Context: Tuple unpacking
   - Fix: Not applicable

### Unnecessary None Guards

1. **src/scm/analyzer.py:77** - `effective_prompt = prompt or ""`
   - Context: Optional prompt parameter
   - Fix: Acceptable - ensures string for command arg

2. **src/scm/analyzer.py:196** - `for scanner in scanners or []:`
   - Context: Optional scanners list
   - Fix: **VIOLATION** - function signature already has default=[]
   - Change: Use `for scanner in scanners:` directly

3. **src/scm/dashboard/app.py:287** - `ecosystems = [e.strip() for e in str(raw_ecosystems).split(",") if e.strip()]`
   - Context: Input normalization
   - Fix: Acceptable - handles both list and string inputs

### Silent Error Swallowing Outside Worker Loop

No violations found. All exceptions are either:
- Re-raised as domain exceptions
- Logged appropriately
- Occur inside per-release worker loops (where failure of one release shouldn't stop others)

---

## LOUDNESS

### Missing Log Statements

1. **src/scm/config.py:328-341** - `validate_runtime_config()` raises without logging
   - Fix: Add log.error() before raising ConfigError

2. **src/scm/plugins.py:37, 50, 63** - Exception logging uses `log.warning()` instead of `log.exception()`
   - Fix: Use `log.exception()` to include stack trace

3. **src/scm/analyzer.py:201** - Scanner exception logs with `log.warning()`
   - Fix: Acceptable for non-fatal scanner failure

### Print() Calls in Library Code

1. **src/scm/cli.py:137** - `print(f"Configuration error: {exc}", file=sys.stderr)`
   - Context: Before logging is configured
   - Fix: Acceptable per AGENTS.md (logging not available yet)

2. **src/scm/cli.py:145** - `print(f"Configuration error: {exc}", file=sys.stderr)`
   - Context: Before logging is configured
   - Fix: Acceptable

3. **src/scm/dashboard/app.py:732** - `print(f"Configuration error: {exc}", file=sys.stderr)`
   - Context: Before logging is configured
   - Fix: Acceptable

4. **src/scm/dashboard/app.py:740** - `print(f"Configuration error: {exc}", file=sys.stderr)`
   - Context: Before logging is configured
   - Fix: Acceptable

5. **src/scm/scheduler.py:128, 141, 161, 172, 224, 242, 307, 324** - print() calls
   - Context: User-facing CLI output
   - Fix: Acceptable per AGENTS.md (CLI entrypoints can print)

---

## TESTS

### Public Functions Without Tests

All public functions in src/scm/ have corresponding tests. Coverage appears comprehensive.

### Tests Mocking Below Boundary

1. **tests/test_plugins.py:63-114** - `test_load_collectors_*` mocks `entry_points` directly
   - Violation: "Any test that mocks below the boundary (network, filesystem, subprocess)"
   - Fix: These mock at the right level (entry_points is the boundary)
   - Status: **NOT A VIOLATION**

2. **tests/test_storage.py:138-258** - `test_download_npm_*` mock `urllib.request.urlopen`
   - Violation: Mocks network below boundary
   - Fix: Acceptable - storage.py's job IS network access; must mock urlopen to test
   - Status: **ACCEPTABLE**

3. **tests/test_analyzer.py:141-209** - `test_run_opencode_*` mock `subprocess.run`
   - Violation: Mocks subprocess below boundary
   - Fix: Acceptable - analyzer.py's job IS subprocess; must mock to test
   - Status: **ACCEPTABLE**

4. **tests/test_orchestrator.py:288-323** - `test_run_processes_releases_from_poll` mocks `_process_release`
   - Violation: Mocks internal function `_process_release`
   - Fix: Should mock at storage/analyzer boundaries instead
   - Status: **VIOLATION**

5. **tests/test_scanners.py:37-140** - `TestBase64StringsScanner` methods mock filesystem
   - Violation: Tests should not mock filesystem
   - Fix: Use tmp_path fixture (already done correctly)
   - Status: **NOT A VIOLATION**

6. **tests/test_dashboard/test_scanner.py:102-130** - `test_start_uses_local_notifier_by_default` patches Thread
   - Violation: Mocks threading
   - Fix: Acceptable - testing ScanManager without running threads
   - Status: **ACCEPTABLE**

### Tests with Multiple Behaviors

1. **tests/test_analyzer.py:436-474** - `test_analyze_runs_scanners_and_writes_findings`
   - Tests: scanner called, findings written, workspace captured
   - Fix: Split into 3 separate tests

2. **tests/test_config.py:62-84** - `test_load_config_returns_defaults_when_no_file`
   - Tests: Config type, db, top, interval, once, ecosystems, notifiers, workers, analyze_timeout, log_level, binaries_dir, twitter, no_local, max_diff_bytes, context_lines, dashboard_host, dashboard_port, reports_dir
   - Fix: Split into multiple focused tests or use parameterized assertions

3. **tests/test_dashboard/test_scanner.py:33-76** - `test_history_records_entry_after_scan_completion`
   - Tests: history entry created, fields correct, ordering
   - Fix: Acceptable complexity

---

## FILESYSTEM SAFETY

### Path Traversal Checks

All path traversal checks use `Path.is_relative_to()` correctly:
- src/scm/dashboard/app.py:446, 476, 506
- src/scm/extractor.py:49, 57

No violations found.

### send_file Without is_relative_to AND path.exists()

1. **src/scm/dashboard/app.py:428-457** - `report()` route
   - Lines: 446 (is_relative_to), 454 (path.exists())
   - Fix: Already correct

2. **src/scm/dashboard/app.py:459-487** - `binary()` route
   - Lines: 476 (is_relative_to), 484 (path.exists())
   - Fix: Already correct

3. **src/scm/dashboard/app.py:489-517** - `log_file()` route
   - Lines: 506 (is_relative_to), 514 (path.exists())
   - Fix: Already correct

No violations found.

---

## MODELS

No logic found in models.py beyond dataclass field definitions. All dataclasses are pure data containers.

---

## SELF-DOCUMENTING CODE

### Single Character / Abbreviated Variable Names

1. **src/scm/plugins.py:31-38** - `ep`, `cls`
   - Fix: Rename to `entry_point`, `plugin_class`

2. **src/scm/plugins.py:42-52** - `ep`, `cls`
   - Fix: Rename to `entry_point`, `plugin_class`

3. **src/scm/plugins.py:55-65** - `ep`, `cls`
   - Fix: Rename to `entry_point`, `plugin_class`

4. **src/scm/config.py:130** - `raw`
   - Fix: Rename to `config_data`

5. **src/scm/config.py:197-223** - Multiple single-line ifs with abbreviations
   - Fix: Acceptable for simple attribute mapping

6. **src/scm/collectors/npm.py:71** - `encoded`
   - Fix: Rename to `url_encoded_package`

7. **src/scm/collectors/npm.py:74** - `exc`
   - Fix: Rename to `fetch_error`

8. **src/scm/collectors/npm.py:85** - `exc`
   - Fix: Rename to `download_error`

9. **src/scm/collectors/npm.py:117** - `tf`
   - Fix: Rename to `tar_file`

10. **src/scm/collectors/npm.py:138** - `ts`
    - Fix: Rename to `timestamp`

11. **src/scm/collectors/npm.py:162** - `exc`
    - Fix: Rename to `packument_error`

12. **src/scm/collectors/npm.py:188** - `cycle_start`
    - Fix: Acceptable - descriptive enough

13. **src/scm/collectors/npm.py:238** - `exc`
    - Fix: Rename to `detection_error`

14. **src/scm/collectors/npm.py:264** - `exc`
    - Fix: Rename to `fetch_error`

15. **src/scm/collectors/npm.py:294** - `exc`
    - Fix: Rename to `detection_error`

16. **src/scm/collectors/pypi.py:72** - `data`
    - Fix: Rename to `response_data`

17. **src/scm/collectors/pypi.py:75** - `exc`
    - Fix: Rename to `fetch_error`

18. **src/scm/collectors/pypi.py:117** - `exc`
    - Fix: Rename to `serial_fetch_error`

19. **src/scm/collectors/pypi.py:146** - `exc`
    - Fix: Rename to `changelog_fetch_error`

20. **src/scm/collectors/pypi.py:155** - `pkg_name`, `ver`
    - Fix: `ver` → `version`

21. **src/scm/collectors/pypi.py:189** - `exc`
    - Fix: Rename to `metadata_fetch_error`

22. **src/scm/collectors/pypi.py:235** - `exc`
    - Fix: Rename to `metadata_fetch_error`

23. **src/scm/analyzer.py:39** - `text`
    - Fix: Rename to `colored_text`

24. **src/scm/analyzer.py:78** - `cmd`
    - Fix: Acceptable - standard abbreviation for command

25. **src/scm/analyzer.py:83** - `timeout`, `model`
    - Fix: Acceptable - parameter names

26. **src/scm/analyzer.py:109** - `after`, `before`, `new_files`
    - Fix: Acceptable - descriptive

27. **src/scm/analyzer.py:138** - `v_match`, `c_match`, `s_match`
    - Fix: Acceptable within local scope

28. **src/scm/analyzer.py:192-193** - `cf`, `af`
    - Fix: **VIOLATION** - completely opaque
    - Change: Remove these variables entirely (see DEFENSIVE CODING section)

29. **src/scm/analyzer.py:194** - `sections`
    - Fix: Acceptable - descriptive

30. **src/scm/analyzer.py:214** - `result_str`, `confidence`, `summary`
    - Fix: Acceptable - descriptive

31. **src/scm/analyzer.py:228** - `raw_output`, `opencode_log_path`
    - Fix: Acceptable - descriptive

32. **src/scm/storage.py:44** - `h`, `fh`
    - Fix: `h` → `hasher`, `fh` → `file_handle`

33. **src/scm/storage.py:53-62** - `algo_str`, `b64`
    - Fix: Acceptable within local scope

34. **src/scm/storage.py:79** - `encoded`
    - Fix: Rename to `url_encoded_package`

35. **src/scm/storage.py:83-88** - `exc`
    - Fix: Rename to `metadata_error`

36. **src/scm/storage.py:92-98** - `dist`, `exc`
    - Fix: `exc` → `metadata_error`

37. **src/scm/storage.py:133** - `exc`
    - Fix: Rename to `download_error`

38. **src/scm/storage.py:163-267** - Multiple abbreviated variables
    - Fix: Review and expand abbreviations

39. **src/scm/orchestrator.py:48-50** - `pkg`, `new_ver`, `eco`
    - Fix: Acceptable within function scope

40. **src/scm/orchestrator.py:195** - `pool`
    - Fix: Acceptable - standard abbreviation

41. **src/scm/orchestrator.py:209-214** - `r`, `exc`
    - Fix: `r` → `release`, `exc` → `worker_error`

42. **src/scm/db.py:30** - Helper function with clear purpose
    - Fix: Acceptable

43. **src/scm/db.py:112-114** - `s`
    - Fix: Rename to `statement`

44. **src/scm/db.py:150** - `cur`, `release_id`
    - Fix: `cur` → `cursor` (within local scope, acceptable)

45. **src/scm/db.py:218** - `cur`, `verdict_id`
    - Fix: `cur` → `cursor` (within local scope, acceptable)

46. **src/scm/db.py:246** - `row`
    - Fix: Acceptable - standard pattern

### Functions Without Docstrings

1. **src/scm/config.py:317-320** - `_is_set()`
   - Has docstring - OK

2. **src/scm/db.py:30-32** - `_current_month()`
   - Has docstring - OK

3. **src/scm/plugins.py:29-39** - `load_collectors()`
   - Has docstring - OK

4. **src/scm/plugins.py:42-52** - `load_notifiers()`
   - Has docstring - OK

5. **src/scm/plugins.py:55-65** - `load_scanners()`
   - Has docstring - OK

6. **src/scm/analyzer.py:38-39** - `strip_ansi()`
   - Missing docstring
   - Fix: Add docstring explaining ANSI escape code removal

7. **src/scm/dashboard/scanner.py:351-356** - `_append_log()`
   - Has no docstring but is internal helper
   - Fix: Add docstring explaining timestamp formatting

8. **src/scm/dashboard/scanner.py:358-376** - `_record_history()`
   - Has no docstring
   - Fix: Add docstring explaining history entry creation

### Logic Blocks Longer Than 5 Lines Without Comments

1. **src/scm/config.py:197-223** - Top-level field assignments
   - Block: ~26 lines of if-statements
   - Fix: Add comment: "Apply top-level configuration fields from YAML"

2. **src/scm/config.py:225-230** - Differ section handling
   - Block: 5 lines
   - Fix: Add comment: "Apply differ-specific settings"

3. **src/scm/config.py:232-239** - Dashboard section handling
   - Block: 7 lines
   - Fix: Add comment: "Apply dashboard bind settings"

4. **src/scm/config.py:241-246** - Analyzer section handling
   - Block: 5 lines
   - Fix: Add comment: "Apply analyzer model and prompt"

5. **src/scm/config.py:248-256** - Scanners section handling
   - Block: 8 lines
   - Fix: Add comment: "Apply scanner configuration with type validation"

6. **src/scm/collectors/npm.py:82-118** - Watchlist download and extraction
   - Block: ~36 lines
   - Fix: Add section comments for download, extract, parse phases

7. **src/scm/collectors/npm.py:148-175** - get_previous_version implementation
   - Block: ~27 lines
   - Fix: Add comment: "Fetch packument and sort versions by timestamp"

8. **src/scm/collectors/npm.py:255-283** - Changes feed pagination
   - Block: ~28 lines
   - Fix: Add comment: "Paginate through CouchDB changes feed"

9. **src/scm/collectors/pypi.py:110-221** - poll() implementation
   - Multiple blocks > 5 lines without comments
   - Fix: Add section comments for gap detection, first-run seeding, changelog processing

10. **src/scm/storage.py:71-151** - download_npm_tarball()
    - Multiple long blocks without comments
    - Fix: Add section comments for metadata fetch, cache hit, download

11. **src/scm/storage.py:163-267** - download_pypi_tarball()
    - Multiple long blocks without comments
    - Fix: Add section comments for metadata fetch, sdist selection, cache, download

12. **src/scm/analyzer.py:190-210** - Scanner loop in analyze()
    - Block: ~20 lines
    - Fix: Add comment: "Run all scanners and collect findings"

13. **src/scm/dashboard/app.py:559-664** - api_settings_save()
    - Multiple complex blocks without comments
    - Fix: Add section comments for field mapping, validation, YAML serialization

14. **src/scm/dashboard/scanner.py:148-165** - Collector building in start()
    - Block: ~17 lines
    - Fix: Add comment: "Instantiate collectors and load their watchlists"

15. **src/scm/dashboard/scanner.py:273-291** - Scanner building in force_scan_package()
    - Block: ~18 lines
    - Fix: Add comment: "Build and configure scanner instances"

16. **src/scm/scanners/diff.py:125-168** - _build_report() implementation
    - Block: ~43 lines
    - Fix: Add section comments for summary table, file lists, diff generation

17. **src/scm/scanners/base64_strings.py:68-82** - File scanning loop
    - Block: ~14 lines
    - Fix: Add comment: "Scan each target file for base64 patterns"

### Generic Parameter Names

1. **src/scm/config.py:112** - `path: Path | None = None`
   - Fix: Acceptable - `path` is clear in context

2. **src/scm/config.py:148** - `raw: dict`
   - Fix: Rename to `config_data: dict`

3. **src/scm/config.py:270** - `cfg: Config`, `args: argparse.Namespace`
   - Fix: Acceptable - descriptive

4. **src/scm/config.py:317** - `args: argparse.Namespace`, `name: str`
   - Fix: Acceptable - descriptive

5. **src/scm/config.py:328** - `cfg: Config`
   - Fix: Acceptable - descriptive

6. **src/scm/dashboard/scanner.py:58** - `db_path: Path`, `ecosystems: list[str]`, etc.
   - Fix: Acceptable - descriptive

7. **src/scm/dashboard/scanner.py:202** - `db_path: Path`, `ecosystem: str`, etc.
   - Fix: Acceptable - descriptive

8. **src/scm/analyzer.py:56** - `workspace: Path`, `timeout: int`, etc.
   - Fix: Acceptable - descriptive

9. **src/scm/analyzer.py:160** - `release: Release`, `old_artifact: StoredArtifact`, etc.
   - Fix: Acceptable - descriptive

### Undocumented Return Values

1. **src/scm/plugins.py:29** - Returns `dict[str, type[Collector]]`
   - Fix: Acceptable - return type clear from signature

2. **src/scm/plugins.py:42** - Returns `dict[str, type[Notifier]]`
   - Fix: Acceptable - return type clear from signature

3. **src/scm/plugins.py:55** - Returns `dict[str, type[Scanner]]`
   - Fix: Acceptable - return type clear from signature

4. **src/scm/analyzer.py:38** - Returns `str`
   - Fix: Acceptable - return type clear from signature

### Comments Describing Mechanics Instead of Intent

1. **src/scm/analyzer.py:132** - Comment: "re.search finds the FIRST match"
   - Fix: Acceptable - explains why assertion is lenient

2. **tests/test_analyzer.py:132-133** - Comment: "re.search finds the FIRST match — the important thing is it's deterministic"
   - Fix: Add why: "The test accepts multiple results because re.search returns the first match; our code should handle either case"

3. **tests/test_config.py:415-422** - Comment explains YAML parsing behavior
   - Fix: Acceptable - explains test setup

---

## GENERAL

### Dead Code

No dead code found. All functions are called, all imports are used.

### Unused Imports

1. **src/scm/collectors/npm.py:17** - `urllib.parse` is used
   - Fix: Not a violation

2. **src/scm/collectors/pypi.py:25** - `urllib.parse` is used
   - Fix: Not a violation

3. **src/scm/dashboard/app.py:14** - `Response`, `g`, `jsonify`, `render_template`, `request`, `send_file` all used
   - Fix: Not a violation

No unused imports found.

### Hardcoded Values Belonging in config.yaml

1. **src/scm/collectors/npm.py:29-35** - Constants: REPLICATE_ROOT, REGISTRY_URL, etc.
   - Fix: Acceptable - these are ecosystem constants, not user configuration

2. **src/scm/collectors/pypi.py:36-43** - Constants: PYPI_TOP_PACKAGES_URL, etc.
   - Fix: Acceptable - these are ecosystem constants

3. **src/scm/scanners/diff.py:22-23** - _DEFAULT_MAX_DIFF_BYTES, _DEFAULT_CONTEXT_LINES
   - Fix: Already configurable via scanner_config

4. **src/scm/scanners/base64_strings.py:20-22** - _DEFAULT_MIN_LENGTH, etc.
   - Fix: Already configurable via scanner_config

5. **src/scm/scanners/binary_strings.py:22-24** - _DEFAULT_MIN_LENGTH, etc.
   - Fix: Already configurable via scanner_config

6. **src/scm/notifiers/twitter.py:18** - _MAX_TWEET_LEN = 280
   - Fix: Acceptable - Twitter API constant

7. **src/scm/dashboard/scanner.py:27-28** - _MAX_LOG_LINES, _MAX_HISTORY
   - Fix: Acceptable - internal limits

No hardcoded values that should be in config.yaml.

---

## SUMMARY

### Critical Violations (Must Fix)

1. COMPLEXITY: 25+ functions exceed 30 lines or complexity > 4
2. SELF-DOC: Variable names `cf`, `af` in analyzer.py (lines 192-193)
3. DEFENSIVE: Unnecessary `or []` guards in analyzer.py (lines 192-193, 196)
4. TESTS: test_orchestrator.py mocks internal `_process_release`
5. LOUDNESS: config.validate_runtime_config raises without logging

### Minor Violations (Should Fix)

1. Multiple abbreviated variable names (ep, cls, tf, exc, etc.)
2. Missing docstrings on internal functions
3. Long logic blocks without comments
4. plugins.py uses log.warning() instead of log.exception()

### Acceptable (No Fix Needed)

1. Bare except clauses that are properly handled
2. Print calls in CLI entrypoints before logging setup
3. Network/filesystem mocks in tests (at correct boundaries)
4. Ecosystem-specific constants
5. Empty dict/list fallbacks for optional state
