"""Dependency graph entrypoint - runs pydeps for module analysis."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    """Generate module dependency graph using pydeps."""
    parser = argparse.ArgumentParser(
        prog="package-monitor-graph",
        description="Generate module dependency graph for AI consumption.",
    )
    parser.add_argument(
        "package",
        nargs="?",
        default="src/scm",
        help="Package path to analyze (default: src/scm)",
    )
    parser.add_argument(
        "--max-bacon",
        type=int,
        default=2,
        help="Max hops from package (0=infinite, default: 2)",
    )
    parser.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="Output format (default: text)",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        help="Output file (default: stdout)",
    )
    parser.add_argument(
        "--no-externals",
        action="store_true",
        help="Exclude external dependencies",
    )
    args = parser.parse_args(argv)

    cmd = [
        "pydeps",
        "--show-deps",
        "--no-output",
        "--max-bacon",
        str(args.max_bacon),
    ]

    if args.no_externals:
        cmd.append("--only")
        cmd.append("scm")

    cmd.append(args.package)

    print(f"Running: {' '.join(cmd)}", file=sys.stderr)

    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        print(f"Error: {result.stderr}", file=sys.stderr)
        return result.returncode

    output = result.stdout

    # If JSON format requested, try to parse and pretty-print
    if args.format == "json":
        try:
            data = json.loads(output)
            output = json.dumps(data, indent=2)
        except json.JSONDecodeError:
            # If pydeps doesn't return valid JSON, wrap the text output
            output = json.dumps(
                {"package": args.package, "dependencies_raw": output},
                indent=2,
            )

    # Write to file or stdout
    if args.output:
        args.output.write_text(output)
        print(f"Graph written to {args.output}", file=sys.stderr)
    else:
        print(output)

    return 0


if __name__ == "__main__":
    sys.exit(main())
