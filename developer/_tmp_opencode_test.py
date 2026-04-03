"""Temporary script to verify opencode can see files in a temp workspace.

Run with: uv run python _tmp_opencode_test.py
Delete after use.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

OPENCODE_CONFIG = Path(__file__).resolve().parent / "opencode-yolo.json"


def run_test(label: str, workspace: Path, prompt: str) -> None:
    print(f"\n{'=' * 60}")
    print(f"TEST: {label}")
    print(f"workspace: {workspace}")
    print(f"contents: {[str(p.relative_to(workspace)) for p in workspace.rglob('*')]}")
    print(f"prompt: {prompt!r}")
    print("running opencode...")

    env = os.environ.copy()
    env["OPENCODE_CONFIG"] = str(OPENCODE_CONFIG)

    result = subprocess.run(
        ["opencode", "run", prompt],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(workspace),
        env=env,
    )
    print(f"returncode: {result.returncode}")
    print(
        f"stdout:\n{result.stdout[-2000:] if len(result.stdout) > 2000 else result.stdout}"
    )
    if result.stderr:
        print(f"stderr:\n{result.stderr[-500:]}")


# ── Test 1: basic file in workspace root ─────────────────────────────────────
ws1 = Path(tempfile.mkdtemp())
try:
    (ws1 / "hello.txt").write_text("Hello from workspace root!\n")
    run_test(
        "basic file in workspace root",
        ws1,
        "List all files in the current directory using the Bash tool (ls -la), then read hello.txt and confirm its contents. Reply with CONFIRMED if you can read it.",
    )
finally:
    shutil.rmtree(ws1, ignore_errors=True)
    print(f"\nCleaned up workspace: {ws1}")


# ── Test 2: file inside new/ subdirectory ────────────────────────────────────
ws2 = Path(tempfile.mkdtemp())
try:
    (ws2 / "new").mkdir()
    (ws2 / "new" / "index.js").write_text("console.log('supply chain test');\n")
    (ws2 / "new" / "package.json").write_text('{"name":"test","version":"1.0.1"}\n')
    run_test(
        "files inside new/ subdirectory",
        ws2,
        "Run 'ls -la' and 'ls -la new/' in the current directory. Then read new/index.js and confirm you can see its contents. Reply with CONFIRMED if new/ exists and has files.",
    )
finally:
    shutil.rmtree(ws2, ignore_errors=True)
    print(f"\nCleaned up workspace: {ws2}")


# ── Test 3: timing — check files still exist after subprocess returns ────────
ws3 = Path(tempfile.mkdtemp())
try:
    (ws3 / "new").mkdir()
    (ws3 / "new" / "canary.txt").write_text(
        "CANARY: if you see this, new/ was present\n"
    )

    env = os.environ.copy()
    env["OPENCODE_CONFIG"] = str(OPENCODE_CONFIG)

    print(f"\n{'=' * 60}")
    print(
        "TEST: timing — does new/ still exist immediately after subprocess.run returns?"
    )
    print(f"workspace: {ws3}")

    result = subprocess.run(
        [
            "opencode",
            "run",
            "Run ls -la and ls -la new/ then immediately output the exact text TIMING_OK",
        ],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(ws3),
        env=env,
    )

    # Check if workspace still exists immediately after subprocess returns
    still_exists = (ws3 / "new").exists()
    print(f"new/ still exists after subprocess.run returns: {still_exists}")
    print(f"stdout tail:\n{result.stdout[-1000:]}")
finally:
    shutil.rmtree(ws3, ignore_errors=True)
    print(f"\nCleaned up workspace: {ws3}")

print("\n\nAll tests complete.")
