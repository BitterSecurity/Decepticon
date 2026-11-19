"""Execute the real CI aggregation shell with GitHub dependency outcomes."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from typing import ClassVar

ROOT = Path(__file__).resolve().parents[2]
HISTORICAL_COMMIT = "be0892d55187c2ebb9ee24109020558241eaab14"
HISTORICAL = False
JOBS = (
    "changes",
    "compose-validate",
    "python",
    "cli",
    "launcher",
    "docker-build",
    "docker-build-arm64",
    "security",
    "telemetry",
    "actionlint",
)


def aggregate_script(historical: bool) -> str:
    """Read the single literal shell block in the actual ci-ok job.

    This deliberately accepts only this job's existing shape, rather than
    pretending to parse arbitrary YAML. A changed/ambiguous shape is an
    infrastructure error, never a successful historical reproduction.
    """
    for binary in ("bash", "jq"):
        if shutil.which(binary) is None:
            raise RuntimeError(f"Required local executable missing: {binary}")
    content = (
        subprocess.check_output(
            ["git", "show", f"{HISTORICAL_COMMIT}:.github/workflows/ci.yml"],
            cwd=ROOT,
            text=True,
        )
        if historical
        else (ROOT / ".github/workflows/ci.yml").read_text()
    )
    lines = content.splitlines()
    starts = [i for i, line in enumerate(lines) if line == "  ci-ok:"]
    if len(starts) != 1:
        raise RuntimeError("Expected exactly one ci-ok job")
    start = starts[0]
    end = next(
        (i for i in range(start + 1, len(lines)) if re.match(r"^  [\w-]+:", lines[i])),
        len(lines),
    )
    job = lines[start:end]
    blocks = [i for i, line in enumerate(job) if line == "        run: |"]
    if len(blocks) != 1:
        raise RuntimeError("Expected one literal aggregation shell block")
    body: list[str] = []
    for line in job[blocks[0] + 1 :]:
        if line and not line.startswith("          "):
            break
        body.append(line[10:] if line else "")
    script = "\n".join(body)
    if not script.strip():
        raise RuntimeError("Aggregation shell block is empty")
    subprocess.run(["bash", "-n", "-c", script], check=True, capture_output=True)
    return script


class CiAggregateTests(unittest.TestCase):
    script: ClassVar[str]

    @classmethod
    def setUpClass(cls) -> None:
        cls.script = aggregate_script(HISTORICAL)

    def assert_outcome(self, payload: str, expected: bool) -> None:
        result = subprocess.run(
            ["bash", "-e", "-c", self.script],
            cwd=ROOT,
            env={**os.environ, "RESULTS": payload},
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode in (126, 127):
            raise RuntimeError("Aggregation shell could not execute a required command")
        self.assertEqual(result.returncode == 0, expected, result.stdout + result.stderr)

    def test_success_and_intentional_scope_skips(self) -> None:
        for status in ("success", "skipped"):
            with self.subTest(status=status):
                self.assert_outcome(
                    json.dumps({job: {"result": status, "outputs": {}} for job in JOBS}),
                    True,
                )
        mixed = {job: {"result": "success"} for job in JOBS}
        for job in ("python", "docker-build", "docker-build-arm64", "telemetry"):
            mixed[job] = {"result": "skipped"}
        self.assert_outcome(json.dumps(mixed), True)

    def test_unfinished_and_unknown_outcomes_are_refused(self) -> None:
        for status in (
            "failure",
            "cancelled",
            "abandoned",
            "timed_out",
            "neutral",
            "stale",
            "action_required",
            "queued",
            "in_progress",
            "unknown",
            "",
        ):
            with self.subTest(status=status):
                needs = {job: {"result": "success"} for job in JOBS}
                needs["launcher"] = {"result": status}
                self.assert_outcome(json.dumps(needs), False)

    def test_missing_or_invalid_results_are_refused(self) -> None:
        for payload in (
            "{}",
            "[]",
            "null",
            '"success"',
            "not json",
            "",
            '{"launcher":{}}',
            '{"launcher":{"result":null}}',
        ):
            with self.subTest(payload=payload):
                self.assert_outcome(payload, False)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--historical", action="store_true")
    HISTORICAL = parser.parse_args().historical
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(CiAggregateTests)
    outcome = unittest.TextTestRunner(verbosity=2).run(suite)
    # Setup, executable, parse and timeout errors cannot prove a regression.
    sys.exit(2 if outcome.errors else 0 if outcome.wasSuccessful() else 1)
