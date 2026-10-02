"""Generate reproducible, observed verification evidence; no fake summary values."""

import importlib.metadata
import json
import platform
import sqlite3
import subprocess
import sys
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "reports"


def run(label: str, args: list[str]) -> dict:
    process = subprocess.run(
        args,
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    (REPORTS / f"{label}.txt").write_text(process.stdout, encoding="utf-8")
    print(process.stdout, end="")
    return {"name": label, "command": args, "returncode": process.returncode}


def main() -> None:
    REPORTS.mkdir(exist_ok=True)
    checks = [
        run("lint", [sys.executable, "-m", "ruff", "check", "src", "tests", "scripts"]),
        run(
            "format", [sys.executable, "-m", "ruff", "format", "--check", "src", "tests", "scripts"]
        ),
        run(
            "pytest",
            [
                sys.executable,
                "-W",
                "error",
                "-m",
                "pytest",
                "-q",
                "-W",
                "error",
                "--junitxml=reports/junit.xml",
                "--cov=private_db_mcp",
                "--cov-report=term-missing",
                "--cov-report=json:reports/coverage.json",
            ],
        ),
    ]
    counts = {}
    junit = REPORTS / "junit.xml"
    if junit.exists():
        suites = ET.parse(junit).getroot().findall("testsuite")
        counts = {
            key: sum(int(suite.attrib.get(key, 0)) for suite in suites)
            for key in ("tests", "failures", "errors", "skipped")
        }
    passed = (
        all(check["returncode"] == 0 for check in checks)
        and counts.get("tests", 0) > 0
        and counts.get("failures") == 0
        and counts.get("errors") == 0
    )
    evidence = {
        "created_at_utc": datetime.now(UTC).isoformat(),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "sqlite": sqlite3.sqlite_version,
        "packages": {
            name: importlib.metadata.version(name)
            for name in ("private-db-mcp", "mcp", "sqlglot", "pytest", "pytest-cov", "ruff")
        },
        "checks": checks,
        "junit": counts,
        "warnings_policy": "All Python warnings are errors in pytest and stdio test subprocesses.",
        "warning_count": 0 if passed else None,
        "all_checks_passed": passed,
    }
    (REPORTS / "verification.json").write_text(
        json.dumps(evidence, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(evidence, indent=2))
    raise SystemExit(0 if passed else 1)


if __name__ == "__main__":
    main()
