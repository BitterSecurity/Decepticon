from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

COMPOSE = Path(__file__).resolve().parents[1] / "docker-compose.yml"


@pytest.mark.parametrize(
    "version,expected", [("", "latest"), ("stable", "stable"), ("1.4.1", "1.4.1")]
)
def test_compose_image_versions(tmp_path: Path, version: str, expected: str) -> None:
    # Given no version marker, or an explicit channel/version pin.
    (tmp_path / ".env").write_text("", encoding="utf-8")
    env = os.environ | {"DECEPTICON_VERSION": version}

    # When Compose resolves the actual image references for every profile.
    completed = subprocess.run(
        [
            "docker",
            "compose",
            "--project-directory",
            str(tmp_path),
            "--env-file",
            str(tmp_path / ".env"),
            "-f",
            str(COMPOSE),
            "--profile",
            "*",
            "config",
            "--images",
        ],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    # Then every Decepticon image uses the selected tag.
    assert completed.returncode == 0, completed.stderr
    images = [
        image
        for image in completed.stdout.splitlines()
        if image.startswith("ghcr.io/bittersecurity/decepticon-")
    ]
    assert len(images) == 8
    assert all(image.endswith(f":{expected}") for image in images), images
