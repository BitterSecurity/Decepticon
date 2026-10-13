"""Behavior tests for the Bash installer."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parents[5]
INSTALL_SCRIPT = REPO_ROOT / "scripts" / "install.sh"


def test_stable_resolver_reads_release_payload_from_curl(tmp_path: Path) -> None:
    script_without_main = INSTALL_SCRIPT.read_text(encoding="utf-8").rsplit('\nmain "$@"', 1)[0]
    releases = [
        {
            "tag_name": "v1.2.3",
            "published_at": "2020-01-01T00:00:00Z",
            "draft": False,
            "prerelease": False,
        }
    ]
    harness = tmp_path / "resolve-stable.sh"
    harness.write_text(
        f"""{script_without_main}
curl() {{
    printf '%s' "$RELEASES_JSON"
}}
resolve_stable_soaked
""",
        encoding="utf-8",
    )
    env = os.environ | {
        "DECEPTICON_STABLE_SOAK_DAYS": "7",
        "RELEASES_JSON": json.dumps(releases),
    }

    completed = subprocess.run(
        ["bash", str(harness)],
        check=True,
        capture_output=True,
        env=env,
        text=True,
    )

    assert completed.stdout.strip() == "1.2.3"


def run_installer_function(
    tmp_path: Path, commands: str, overrides: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    script_without_main = INSTALL_SCRIPT.read_text(encoding="utf-8").rsplit('\nmain "$@"', 1)[0]
    harness = tmp_path / "installer-harness.sh"
    harness.write_text(f"{script_without_main}\n{commands}\n", encoding="utf-8")
    env = os.environ | {"CHANNEL": "stable", "VERSION": "", "BRANCH": "main"} | overrides
    return subprocess.run(
        ["bash", str(harness)], capture_output=True, env=env, text=True, check=False
    )


@pytest.mark.parametrize("channel", ["stable", "latest"])
def test_resolver_follows_repository_redirects(tmp_path: Path, channel: str) -> None:
    completed = run_installer_function(
        tmp_path,
        r"""
curl() {
    local follow=false arg url=""
    for arg in "$@"; do
        case "$arg" in
            --location|-*L*) follow=true ;;
            https://*) url="$arg" ;;
        esac
    done
    if [[ "$follow" == false ]]; then
        printf '%s' '{"message":"Moved Permanently","url":"https://api.github.com/repositories/123/releases"}'
    elif [[ "$url" == *releases/latest ]]; then
        printf '%s' '{"tag_name":"v1.2.4"}'
    else
        printf '%s' '[{"tag_name":"v1.2.3","published_at":"2020-01-01T00:00:00Z","draft":false,"prerelease":false}]'
    fi
}
resolve_version
printf '%s\n' "$DECEPTICON_VERSION" "$RAW_BASE"
""",
        {"CHANNEL": channel},
    )

    assert completed.returncode == 0, completed.stderr
    version = "1.2.3" if channel == "stable" else "1.2.4"
    assert completed.stdout.splitlines()[-2:] == [
        version,
        f"https://raw.githubusercontent.com/BitterSecurity/Decepticon/v{version}",
    ]


@pytest.mark.parametrize("version", ["1.2.3", "v1.2.3"])
def test_explicit_version_pins_configs_without_api_lookup(tmp_path: Path, version: str) -> None:
    completed = run_installer_function(
        tmp_path,
        """
curl() { echo "Unexpected API lookup" >&2; exit 99; }
resolve_version
printf '%s\\n' "$DECEPTICON_VERSION" "$RAW_BASE"
""",
        {"VERSION": version},
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.splitlines() == [
        "1.2.3",
        "https://raw.githubusercontent.com/BitterSecurity/Decepticon/v1.2.3",
    ]


def test_unresolved_version_hint_sets_environment_for_bash(tmp_path: Path) -> None:
    completed = run_installer_function(
        tmp_path,
        """
# Use a supported architecture without consulting the host.
uname() { if [[ "$1" == -m ]]; then echo x86_64; else echo Linux; fi; }
DECEPTICON_VERSION=stable
create_launcher "$TEST_BIN"
""",
        {"TEST_BIN": str(tmp_path / "bin")},
    )

    assert completed.returncode == 1
    assert "curl -fsSL https://decepticon.red/install | VERSION=<x.y.z> bash" in completed.stderr
