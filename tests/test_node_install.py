"""Exercise the dev dependency installer with real npm lifecycle scripts."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

MAKEFILE = Path(__file__).resolve().parents[1] / "Makefile"


def _install(project: Path) -> subprocess.CompletedProcess[str]:
    node = shutil.which("node")
    assert node is not None, "Node.js is required for the dev installer tests"
    return subprocess.run(
        ["make", "-f", str(MAKEFILE), "node-install", f"NODE_BIN_DIR={Path(node).parent}"],
        cwd=project,
        env={**os.environ, "npm_config_offline": "true", "npm_config_audit": "false"},
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


@pytest.fixture
def npm_project(tmp_path: Path) -> Path:
    addon = tmp_path / "addon"
    addon.mkdir()
    (addon / "package.json").write_text(
        json.dumps(
            {"name": "abi-fixture", "version": "1.0.0", "scripts": {"install": "node build.cjs"}}
        )
    )
    (addon / "build.cjs").write_text(
        "const fs = require('node:fs');\n"
        "if (fs.existsSync('fail-build')) process.exit(1);\n"
        "fs.writeFileSync('built-abi', process.versions.modules);\n"
    )
    subprocess.run(
        ["npm", "pack", "--ignore-scripts", "--offline"],
        cwd=addon,
        capture_output=True,
        timeout=30,
        check=True,
    )
    (tmp_path / "package.json").write_text(
        json.dumps(
            {
                "name": "installer-test",
                "private": True,
                "dependencies": {"abi-fixture": "file:./addon/abi-fixture-1.0.0.tgz"},
            }
        )
    )
    install = _install(tmp_path)
    assert install.returncode == 0, install.stdout + install.stderr
    return tmp_path


def test_abi_change_rebuilds_existing_dependency(npm_project: Path) -> None:
    stamp = npm_project / "node_modules/.node-abi"
    stamp.write_text("old-abi\n")
    built = npm_project / "node_modules/abi-fixture/built-abi"
    built.write_text("old-abi")
    install = _install(npm_project)
    assert install.returncode == 0, install.stdout + install.stderr
    assert built.read_text() == stamp.read_text().strip() != "old-abi"


def test_failed_rebuild_preserves_old_abi_stamp(npm_project: Path) -> None:
    stamp = npm_project / "node_modules/.node-abi"
    stamp.write_text("old-abi\n")
    (npm_project / "node_modules/abi-fixture/fail-build").touch()
    install = _install(npm_project)
    assert install.returncode != 0, install.stdout + install.stderr
    assert stamp.read_text() == "old-abi\n"


def test_matching_abi_does_not_rebuild(npm_project: Path) -> None:
    (npm_project / "node_modules/abi-fixture/fail-build").touch()
    install = _install(npm_project)
    assert install.returncode == 0, install.stdout + install.stderr
