"""Text verdict and machine-readable evidence."""

import argparse
import json
from pathlib import Path

from .core import CASES
from .runner import run


def main(argv=None):
    parser = argparse.ArgumentParser(description="Rehearse local AWS write recovery")
    sub = parser.add_subparsers(dest="command", required=True)
    cmd = sub.add_parser("run")
    cmd.add_argument("--case", required=True, choices=CASES)
    cmd.add_argument("--publisher", required=True, choices=("broken", "fixed"))
    cmd.add_argument("--port", type=int, default=9297)
    cmd.add_argument("--report", type=Path)
    args = parser.parse_args(argv)
    result = run(args.case, args.publisher, args.port)
    if args.report:
        args.report.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(f"{result['verdict']}: {args.publisher} / {args.case}")
    for name, ok in result["checks"].items():
        print(f"  {'ok' if ok else 'FAIL'} {name}")
    if result.get("error"):
        print("  " + result["error"])
    if result.get("cleanup_errors"):
        print("  " + "; ".join(result["cleanup_errors"]))
    return 0 if result["verdict"] == "PASS" else 1
