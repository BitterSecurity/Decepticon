"""Render real Compose interpolation to catch premature startup health failure."""

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

COMPOSE = Path(__file__).resolve().parents[1] / "docker-compose.yml"


@pytest.mark.parametrize("budget", [None, "", "600", "1200"])
def test_health_startup_grace_respects_operator_budget(tmp_path: Path, budget: str | None) -> None:
    if shutil.which("docker") is None:
        pytest.skip("Docker CLI is unavailable")
    env = os.environ.copy()
    env.pop("DECEPTICON_STARTUP_TIMEOUT_SECONDS", None)
    if budget is not None:
        env["DECEPTICON_STARTUP_TIMEOUT_SECONDS"] = budget
    dotenv = tmp_path / ".env"
    dotenv.write_text("")
    result = subprocess.run(
        [
            "docker",
            "compose",
            "--project-directory",
            str(tmp_path),
            "--env-file",
            str(dotenv),
            "-f",
            str(COMPOSE),
            "config",
            "--format",
            "json",
        ],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    services = json.loads(result.stdout)["services"]
    for name in ("litellm", "langgraph"):
        health = services[name]["healthcheck"]
        duration = health["start_period"]
        actual = sum(
            int(value) * {"h": 3600, "m": 60, "s": 1}[unit]
            for value, unit in re.findall(r"(\d+)([hms])", duration)
        )
        assert actual == int(budget or "600"), (name, duration)
        assert health["retries"] == 12
        assert health["test"][0] == "CMD"
