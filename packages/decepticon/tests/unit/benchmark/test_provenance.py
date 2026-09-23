from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

from benchmark.config import BenchmarkConfig
from benchmark.provenance import (
    ProvenanceCaptureError,
    capture_container_images,
    capture_declared_model_assignments,
    capture_run_provenance,
)
from benchmark.reporter import Reporter
from benchmark.runner import _build_run_provenance
from benchmark.schemas import BenchmarkReport, Challenge, FilterConfig, RunProvenance


def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def test_capture_run_provenance_records_source_artifacts_and_sanitized_config(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "Benchmark Test")
    _git(repo, "config", "user.email", "benchmark@example.invalid")

    prompts = repo / "prompts"
    skills = repo / "skills"
    prompts.mkdir()
    skills.mkdir()
    (prompts / "agent.md").write_text("system prompt\n", encoding="utf-8")
    (skills / "SKILL.md").write_text("skill body\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "fixture")
    expected_commit = _git(repo, "rev-parse", "HEAD")

    provenance = capture_run_provenance(
        repo_root=repo,
        config={
            "provider": "xbow",
            "timeout": 1800,
            "api_key": "must-not-leak",
            "litellm_url": (
                "https://alice:secret@litellm.example/v1?token=must-not-leak&region=eu"
            ),
            "headers": {
                "Authorization": "Bearer must-not-leak",
                "Cookie": "session=must-not-leak",
                "X-Signature": "must-not-leak",
            },
        },
        model_profile="max",
        model_assignments={"decepticon": ["anthropic/claude-opus-4-8"]},
        container_images={"langgraph": "sha256:" + "c" * 64},
        artifact_paths={"agent-prompts": prompts, "skill-corpus": skills},
    )

    assert provenance.source_commit == expected_commit
    assert provenance.source_dirty is False
    assert provenance.model_profile == "max"
    assert provenance.model_assignments == {"decepticon": ["anthropic/claude-opus-4-8"]}
    assert provenance.artifact_hashes == {
        "agent-prompts": "22fea0014f60c17ea57f78d690000052103c49f3fcd1ecdf75789d8db70e0c61",
        "skill-corpus": "6d5868b0afae30ab26935de97c3e1dece584b7f14a9ca8b01eb35e9225cadff8",
    }
    assert provenance.config == {
        "provider": "xbow",
        "timeout": 1800,
        "api_key": "<redacted>",
        "litellm_url": (
            "https://<redacted>@litellm.example/v1?token=%3Credacted%3E&region=%3Credacted%3E"
        ),
        "headers": {
            "Authorization": "<redacted>",
            "Cookie": "<redacted>",
            "X-Signature": "<redacted>",
        },
    }
    assert provenance.run_id
    assert provenance.python_version
    assert provenance.platform


def test_capture_run_provenance_marks_uncommitted_source(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "Benchmark Test")
    _git(repo, "config", "user.email", "benchmark@example.invalid")
    tracked = repo / "tracked.txt"
    tracked.write_text("before\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "fixture")
    tracked.write_text("after\n", encoding="utf-8")

    provenance = capture_run_provenance(
        repo_root=repo,
        config={"provider": "xbow"},
        model_profile="test",
        model_assignments={"decepticon": ["openai/gpt-5-nano"]},
        container_images={"langgraph": "sha256:" + "d" * 64},
        artifact_paths={"source": tracked},
    )

    assert provenance.source_dirty is True


def test_capture_declared_model_assignments_uses_explicit_priority_and_role_override(
    monkeypatch,
) -> None:
    monkeypatch.setenv("DECEPTICON_AUTH_PRIORITY", "anthropic_api,openai_api")
    runtime = {
        "auth_priority": "anthropic_api,openai_api",
        "profile": "max",
        "assignments": {
            "decepticon": ["plugin/custom-primary", "openai/gpt-5.5"],
        },
    }
    monkeypatch.setattr(
        "benchmark.provenance.subprocess.run",
        lambda argv, **_kwargs: subprocess.CompletedProcess(
            argv,
            0,
            stdout="DECEPTICON_PROVENANCE=" + json.dumps(runtime) + "\n",
            stderr="",
        ),
    )

    profile, assignments = capture_declared_model_assignments()

    assert profile == "max"
    assert assignments == {
        "decepticon": ["plugin/custom-primary", "openai/gpt-5.5"],
    }


def test_capture_declared_models_rejects_container_priority_mismatch(monkeypatch) -> None:
    monkeypatch.setenv("DECEPTICON_AUTH_PRIORITY", "anthropic_api,openai_api")
    runtime = {
        "auth_priority": "openai_api",
        "profile": "max",
        "assignments": {"decepticon": ["openai/gpt-5.5"]},
    }
    monkeypatch.setattr(
        "benchmark.provenance.subprocess.run",
        lambda argv, **_kwargs: subprocess.CompletedProcess(
            argv,
            0,
            stdout="DECEPTICON_PROVENANCE=" + json.dumps(runtime),
            stderr="",
        ),
    )

    with pytest.raises(ProvenanceCaptureError, match="effective models"):
        capture_declared_model_assignments()


def test_artifact_hash_ignores_generated_python_cache(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    prompts = repo / "prompts"
    cache = prompts / "__pycache__"
    cache.mkdir(parents=True)
    (prompts / "agent.md").write_text("prompt\n", encoding="utf-8")
    (cache / "agent.cpython-313.pyc").write_bytes(b"machine-specific")
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "Benchmark Test")
    _git(repo, "config", "user.email", "benchmark@example.invalid")
    _git(repo, "add", "prompts/agent.md")
    _git(repo, "commit", "-qm", "fixture")

    arguments = {
        "repo_root": repo,
        "config": {"provider": "xbow"},
        "model_profile": "test",
        "model_assignments": {"decepticon": ["openai/gpt-5-nano"]},
        "container_images": {"langgraph": "sha256:" + "d" * 64},
        "artifact_paths": {"prompts": prompts},
    }
    with_cache = capture_run_provenance(**arguments)
    (cache / "agent.cpython-313.pyc").unlink()
    without_cache = capture_run_provenance(**arguments)

    assert with_cache.artifact_hashes == without_cache.artifact_hashes


def test_capture_container_images_prefers_registry_digest_and_keeps_local_image_id(
    monkeypatch,
) -> None:
    def run_docker(argv, **_kwargs):
        command = tuple(argv)
        if command[:2] == ("docker", "ps"):
            return subprocess.CompletedProcess(
                argv,
                0,
                stdout="ghcr.io/purpleailab/decepticon-langgraph:stable\n",
                stderr="",
            )
        image = command[-1]
        if image == "ghcr.io/purpleailab/decepticon-langgraph:stable":
            stdout = (
                '["ghcr.io/purpleailab/decepticon-langgraph@sha256:'
                + "a" * 64
                + '"]|sha256:'
                + "b" * 64
            )
        else:
            stdout = "[]|sha256:" + "c" * 64
        return subprocess.CompletedProcess(argv, 0, stdout=stdout, stderr="")

    monkeypatch.setattr("benchmark.provenance.subprocess.run", run_docker)

    images = capture_container_images(["local/challenge:latest"])

    assert images == {
        "ghcr.io/purpleailab/decepticon-langgraph:stable": (
            "ghcr.io/purpleailab/decepticon-langgraph@sha256:" + "a" * 64
        ),
        "local/challenge:latest": "sha256:" + "c" * 64,
    }


def test_capture_container_images_excludes_unrelated_running_projects(monkeypatch) -> None:
    def run_docker(argv, **_kwargs):
        command = tuple(argv)
        if command[:2] == ("docker", "ps"):
            output = (
                "decepticon:stable\n"
                if "--filter" in command
                else "decepticon:stable\nunrelated:latest\n"
            )
            return subprocess.CompletedProcess(argv, 0, stdout=output, stderr="")
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout='["decepticon@sha256:' + "a" * 64 + '"]|sha256:' + "b" * 64,
            stderr="",
        )

    monkeypatch.setattr("benchmark.provenance.subprocess.run", run_docker)

    images = capture_container_images([])

    assert set(images) == {"decepticon:stable"}


def test_reporter_exposes_provenance_in_batch_index_and_markdown(tmp_path: Path) -> None:
    now = datetime(2026, 8, 30, tzinfo=timezone.utc)
    report = BenchmarkReport(
        provider_name="xbow",
        provenance=RunProvenance(
            run_id="run-001",
            captured_at=now,
            context_mode="hinted",
            source_commit="1" * 40,
            source_dirty=False,
            benchmark_revisions={"benchmark/xbow-validation-benchmarks": "2" * 40},
            model_profile="max",
            model_assignments={"decepticon": ["anthropic/claude-opus-4-8"]},
            artifact_hashes={"agent-prompts": "3" * 64},
            container_images={"langgraph": "sha256:" + "4" * 64},
            config={"provider": "xbow"},
            python_version="3.13.7",
            platform="linux-x86_64",
        ),
        total=0,
        passed=0,
        failed=0,
        pass_rate=0.0,
        by_level={},
        by_tag={},
        results=[],
        started_at=now,
        completed_at=now,
        duration_seconds=0.0,
    )
    reporter = Reporter(tmp_path)

    batch_dir = reporter.write_evidence(report)
    markdown_path = reporter.write_markdown(report)

    index = json.loads((batch_dir / "index.json").read_text(encoding="utf-8"))
    markdown = markdown_path.read_text(encoding="utf-8")
    assert index["provenance"] == report.provenance.model_dump(mode="json")
    assert "## Reproducibility" in markdown
    assert "| Source Commit | `1111111111111111111111111111111111111111` |" in markdown
    assert "| Context Mode | hinted |" in markdown
    assert "| Model Profile | max |" in markdown


def test_build_run_provenance_wires_runtime_inputs_into_reproducibility_record(
    tmp_path: Path,
    monkeypatch,
) -> None:
    prompts = tmp_path / "packages/decepticon/decepticon/agents/prompts"
    skills = tmp_path / "packages/decepticon/decepticon/skills"
    config_dir = tmp_path / "config"
    prompts.mkdir(parents=True)
    skills.mkdir(parents=True)
    config_dir.mkdir()
    (prompts / "decepticon.md").write_text("prompt\n", encoding="utf-8")
    (skills / "SKILL.md").write_text("skill\n", encoding="utf-8")
    challenge_dir = tmp_path / "benchmark/fixture"
    challenge_dir.mkdir(parents=True)
    (challenge_dir / "benchmark.json").write_text('{"id": "fixture"}\n', encoding="utf-8")
    (config_dir / "litellm.yaml").write_text("model_list: []\n", encoding="utf-8")
    (tmp_path / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.name", "Benchmark Test")
    _git(tmp_path, "config", "user.email", "benchmark@example.invalid")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "fixture")
    monkeypatch.setattr(
        "benchmark.runner.capture_declared_model_assignments",
        lambda: (
            "max",
            {
                "decepticon": [
                    "auth/gpt-5.3-codex",
                    "anthropic/claude-opus-4-8",
                    "openai/gpt-5.5",
                ]
            },
        ),
    )
    monkeypatch.setattr(
        "benchmark.runner.capture_container_images",
        lambda _refs: {"langgraph:stable": "sha256:" + "a" * 64},
    )

    provenance = _build_run_provenance(
        config=BenchmarkConfig(provider="xbow", timeout=42),
        filters=FilterConfig(levels=[3], tags=["ssti"]),
        parallel=2,
        challenges=[
            Challenge(
                id="XBEN-001-24",
                name="fixture",
                description="fixture",
                level=3,
                tags=["ssti"],
                docker_image="challenge:latest",
                compose_dir=challenge_dir,
            )
        ],
        repo_root=tmp_path,
    )

    assert provenance.config["parallel"] == 2
    assert provenance.model_assignments["decepticon"][0] == "auth/gpt-5.3-codex"
    assert provenance.container_images == {"langgraph:stable": "sha256:" + "a" * 64}
    assert "provider-definition" in provenance.artifact_hashes
