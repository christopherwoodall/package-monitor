"""Complexity analysis entrypoint - runs radon for cyclomatic complexity."""

from __future__ import annotations

import argparse
import subprocess
import sys


def main(argv: list[str] | None = None) -> int:
    """Run radon cyclomatic complexity analysis on the codebase."""
    parser = argparse.ArgumentParser(
        prog="package-monitor-complex",
        description="Analyze cyclomatic complexity using radon.",
    )
    parser.add_argument(
        "paths",
        nargs="*",
        default=["src"],
        help="Paths to analyze (default: src)",
    )
    parser.add_argument(
        "--min-rank",
        default="C",
        choices=["A", "B", "C", "D", "E", "F"],
        help="Minimum complexity rank to show (default: C)",
    )
    parser.add_argument(
        "--average",
        "-a",
        action="store_true",
        help="Show average complexity at the end",
    )
    parser.add_argument(
        "--show-all",
        action="store_true",
        help="Show all functions (not just those meeting min-rank)",
    )
    args = parser.parse_args(argv)

    cmd = ["radon", "cc"]

    # Add rank filter if not showing all
    if not args.show_all:
        rank_flag = f"-n{args.min_rank.lower()}"
        cmd.append(rank_flag)

    if args.average:
        cmd.append("-a")

    cmd.extend(args.paths)

    print(f"Running: {' '.join(cmd)}")
    print("-" * 60)
    print("Complexity rankings: A (best) → F (worst)")
    print("-" * 60)

    result = subprocess.run(cmd, capture_output=False, text=True)

    if result.returncode == 0:
        print("\n✓ Complexity analysis complete.")
    else:
        print("\n✗ Analysis failed.")

    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
