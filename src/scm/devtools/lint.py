"""Linting entrypoint - runs ruff check and ruff format --check."""

from __future__ import annotations

import argparse
import subprocess
import sys


def main(argv: list[str] | None = None) -> int:
    """Run ruff linter and format checker on the codebase."""
    parser = argparse.ArgumentParser(
        prog="package-monitor-lint",
        description="Run ruff linter and format checker on the codebase.",
    )
    parser.add_argument(
        "paths",
        nargs="*",
        default=["src", "tests"],
        help="Paths to lint (default: src tests)",
    )
    parser.add_argument(
        "--fix",
        action="store_true",
        help="Auto-fix issues where possible",
    )
    args = parser.parse_args(argv)

    exit_code = 0

    # Run ruff check
    check_cmd = ["ruff", "check"]
    if args.fix:
        check_cmd.append("--fix")
    check_cmd.extend(args.paths)

    print(f"Running: {' '.join(check_cmd)}")
    result = subprocess.run(check_cmd, capture_output=False, text=True)
    if result.returncode != 0:
        exit_code = 1

    # Run ruff format --check
    format_cmd = ["ruff", "format", "--check"]
    format_cmd.extend(args.paths)

    print(f"\nRunning: {' '.join(format_cmd)}")
    result = subprocess.run(format_cmd, capture_output=False, text=True)
    if result.returncode != 0:
        exit_code = 1

    if exit_code == 0:
        print("\n✓ All linting checks passed!")
    else:
        print("\n✗ Linting issues found. Run with --fix to auto-fix where possible.")

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
