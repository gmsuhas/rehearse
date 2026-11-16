"""Integration acceptance: a real, inspectable red and green, not just exit codes."""

import json
import subprocess
import sys
from pathlib import Path

from rehearse.core import CASES


def call(publisher, case):
    report = Path("reports") / f"{publisher}-{case}.json"
    result = subprocess.run(
        [sys.executable, "-m", "rehearse", "run", "--publisher", publisher,
         "--case", case, "--report", str(report)],
        check=False, capture_output=True, text=True, timeout=90,
    )
    print(result.stdout, end="")
    print(result.stderr, end="", file=sys.stderr)
    data = json.loads(report.read_text(encoding="utf-8"))
    return result, data


def main():
    Path("reports").mkdir(exist_ok=True)
    for case in CASES:
        result, data = call("fixed", case)
        assert result.returncode == 0 and data["verdict"] == "PASS", data
        assert all(data["checks"].values()), data
        assert data["boto3_version"] == "1.44.3", data
    for case in ("duplicate", "lost-s3", "lost-index"):
        result, data = call("broken", case)
        assert result.returncode != 0 and data["verdict"] == "FAIL", data
        assert data["checks"]["one_object"] is False, data
        assert data["observation"]["object_count"] == 2, data
        assert data["checks"]["fault_applied"] is True, data
    print("Verified: fixed PASS in all five cases; broken FAIL with two objects in three cases")


if __name__ == "__main__":
    main()
