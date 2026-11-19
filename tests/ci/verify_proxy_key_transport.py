"""Run isolated task-source transport checks; never use another editable checkout."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PACKAGES = ("decepticon", "decepticon-core", "decepticon-sdk")
TRANSPORT_TEST = "packages/decepticon/tests/unit/middleware/test_proxy_key_transport.py"


def check_types(full: bool) -> int:
    files = (
        []
        if full
        else [
            "packages/decepticon/decepticon/middleware/proxy_key_override.py",
            TRANSPORT_TEST,
            "tests/ci/verify_proxy_key_transport.py",
        ]
    )
    result = subprocess.run(
        [str(ROOT / ".venv/bin/basedpyright"), "--outputjson", *files],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
        timeout=180,
    )
    report = json.loads(result.stdout)
    print(json.dumps({"isolatedTaskSourceTypes": True, **report["summary"]}))
    for diagnostic in report["generalDiagnostics"]:
        if diagnostic["severity"] == "error":
            print(json.dumps(diagnostic))
    if not report["summary"]["filesAnalyzed"] or result.returncode not in (0, 1):
        return 2
    return 1 if report["summary"]["errorCount"] else 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--historical", action="store_true")
    parser.add_argument("--doctor", action="store_true")
    parser.add_argument("--types", action="store_true")
    parser.add_argument("--types-full", action="store_true")
    args = parser.parse_args()
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join(str(ROOT / "packages" / name) for name in PACKAGES),
        "DECEPTICON_SKIP_BOOT": "1",
        "DECEPTICON_PLUGINS": "__test__",
        "DECEPTICON_VERIFY_PROXY_REKEY_HISTORICAL": "1" if args.historical else "0",
    }
    interpreter = str(ROOT / ".venv/bin/python")
    guard = subprocess.run(
        [
            interpreter,
            "-c",
            "import pathlib,sys,decepticon,decepticon_core,decepticon_sdk; "
            f"root=pathlib.Path({str(ROOT)!r}); "
            "assert pathlib.Path(sys.prefix).resolve().is_relative_to(root), "
            "'Use an owned environment installed with uv sync --locked --dev --extra mcp'; "
            "assert all(pathlib.Path(m.__file__).resolve().is_relative_to(root) "
            "for m in (decepticon,decepticon_core,decepticon_sdk)); "
            "print('Owned environment; all three packages import from the current task worktree')",
        ],
        env=env,
        cwd=ROOT,
        check=False,
    )
    if guard.returncode:
        return 2
    if args.doctor:
        return 0
    if args.types or args.types_full:
        return check_types(args.types_full)
    tests = [TRANSPORT_TEST]
    if not args.historical:
        tests.extend(
            [
                "packages/decepticon/tests/unit/middleware/test_proxy_key_override.py",
                "packages/decepticon/tests/unit/middleware/test_model_override.py",
                "packages/decepticon/tests/unit/llm",
            ]
        )
    with tempfile.TemporaryDirectory(prefix="proxy-key-transport-") as scratch:
        report = Path(scratch) / "pytest.xml"
        outcome = subprocess.run(
            [interpreter, "-m", "pytest", "-q", f"--junitxml={report}", *tests],
            env=env,
            cwd=ROOT,
            check=False,
            timeout=180,
        )
        suites = ET.parse(report).getroot().findall("testsuite")
        counts = {
            key: sum(int(suite.get(key, "0")) for suite in suites)
            for key in ("tests", "failures", "errors", "skipped")
        }
    print(json.dumps({"historical": args.historical, **counts}, sort_keys=True))
    # Setup/collection problems, skips or incomplete runs cannot establish the
    # historical regression. Expect all six real assertions to reject old code.
    if args.historical:
        expected = {"tests": 6, "failures": 6, "errors": 0, "skipped": 0}
        return 1 if outcome.returncode == 1 and counts == expected else 2
    if counts["skipped"]:
        return 2
    return outcome.returncode if counts["tests"] and not counts["errors"] else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, subprocess.SubprocessError, ET.ParseError) as error:
        print(f"Transport verification infrastructure failed: {type(error).__name__}")
        raise SystemExit(2) from None
