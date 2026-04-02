"""Type checking entrypoint - runs pyright for static type analysis."""

from __future__ import annotations

import argparse
import subprocess
import sys


def main(argv: list[str] | None = None) -> int:
    """Run pyright type checker on the codebase."""
    parser = argparse.ArgumentParser(
        prog="package-monitor-typecheck",
        description="Run pyright type checker on the codebase.",
    )
    parser.add_argument(
        "paths",
        nargs="*",
        default=["src"],
        help="Paths to type check (default: src)",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Enable strict type checking",
    )
    args = parser.parse_args(argv)

    cmd = ["pyright"]
    if args.strict:
        cmd.append("--strict")
    cmd.extend(args.paths)

    print(f"Running: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=False, text=True)

    if result.returncode == 0:
        print("\n✓ Type checking passed!")
    else:
        print("\n✗ Type errors found.")

    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
