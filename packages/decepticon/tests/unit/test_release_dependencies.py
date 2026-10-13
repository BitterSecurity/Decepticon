import os
import shutil
import subprocess
import tomllib
from pathlib import Path
from typing import Final

import pytest
import yaml
from packaging.requirements import Requirement

ROOT: Final = Path(__file__).resolve().parents[4]


@pytest.mark.parametrize("version", ["1.4.1", "1.5.0rc1"])
@pytest.mark.parametrize(
    "dependency",
    [
        ("decepticon", "", "decepticon-core"),
        ("decepticon-sdk", "", "decepticon-core"),
        ("decepticon-sdk", "fixtures", "decepticon"),
    ],
)
def test_release_stamp_requires_matching_siblings(
    tmp_path: Path, version: str, dependency: tuple[str, str, str]
) -> None:
    # Given the real workspace manifests and the release workflow's shell step.
    package, extra, sibling = dependency
    manifests = [
        Path("pyproject.toml"),
        *(path.relative_to(ROOT) for path in (ROOT / "packages").glob("*/pyproject.toml")),
    ]
    for manifest in manifests:
        destination = tmp_path / manifest
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / manifest, destination)
    workflow = yaml.safe_load((ROOT / ".github/workflows/release.yml").read_text())
    stamp = next(
        step["run"]
        for step in workflow["jobs"]["publish-pypi"]["steps"]
        if step.get("name") == "Stamp version from the tag across the workspace"
    )

    # When GitHub runs the stamp step for this release tag.
    subprocess.run(
        ["bash", "--noprofile", "--norc", "-e", "-o", "pipefail", "-c", stamp],
        cwd=tmp_path,
        env={**os.environ, "GITHUB_REF_NAME": f"v{version}"},
        check=True,
        capture_output=True,
        text=True,
    )

    # Then all versions agree and each sibling requirement excludes older releases.
    for manifest in manifests:
        assert tomllib.loads((tmp_path / manifest).read_text())["project"]["version"] == version
    project = tomllib.loads((tmp_path / "packages" / package / "pyproject.toml").read_text())[
        "project"
    ]
    dependencies = project["optional-dependencies"][extra] if extra else project["dependencies"]
    requirement = next(Requirement(dep) for dep in dependencies if Requirement(dep).name == sibling)
    assert str(requirement.specifier) == f"=={version}"
    assert "1.4.0" not in requirement.specifier
