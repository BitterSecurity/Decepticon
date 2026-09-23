"""Tests for benchmark.schemas Pydantic models."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from benchmark.schemas import (
    BenchmarkReport,
    Challenge,
    ChallengeResult,
    FilterConfig,
    RunProvenance,
    SetupResult,
)


class TestChallenge:
    def test_challenge_creation(self) -> None:
        """Create a Challenge with all fields and verify serialization."""
        challenge = Challenge(
            id="XBEN-001-24",
            name="Test Challenge",
            description="A test challenge",
            level=2,
            tags=["xss", "sqli"],
            win_condition="flag",
            compose_dir=Path("/tmp/challenge"),
        )

        assert challenge.id == "XBEN-001-24"
        assert challenge.name == "Test Challenge"
        assert challenge.description == "A test challenge"
        assert challenge.level == 2
        assert challenge.tags == ["xss", "sqli"]
        assert challenge.win_condition == "flag"
        assert challenge.compose_dir == Path("/tmp/challenge")

        data = challenge.model_dump()
        assert data["id"] == "XBEN-001-24"
        assert data["level"] == 2
        assert data["tags"] == ["xss", "sqli"]

    def test_challenge_flag_pattern(self) -> None:
        """Verify flag_pattern returns correct regex that matches FLAG{hex}."""
        challenge = Challenge(
            id="XBEN-001-24",
            name="Test",
            description="Test",
            level=1,
            tags=["xss"],
            compose_dir=Path("/tmp"),
        )

        pattern = challenge.flag_pattern
        assert isinstance(pattern, re.Pattern)
        assert pattern.search("Found FLAG{deadbeef01}")
        assert pattern.search("FLAG{0123456789abcdef}")
        assert not pattern.search("FLAG{UPPER}")
        assert not pattern.search("FLAG{not-hex!}")
        assert not pattern.search("no flag here")


class TestChallengeResult:
    def test_challenge_result_defaults(self) -> None:
        """ChallengeResult with minimal fields has correct defaults."""
        result = ChallengeResult(
            challenge_id="XBEN-001-24",
            challenge_name="Test",
            level=1,
            tags=["xss"],
            passed=False,
        )

        assert result.flag_captured is None
        assert result.duration_seconds == 0.0
        assert result.error is None
        assert result.passed is False


class TestBenchmarkReport:
    def test_benchmark_report_serialization(self) -> None:
        """Create BenchmarkReport and verify model_dump/model_validate round-trip."""
        now = datetime.now(timezone.utc)
        report = BenchmarkReport(
            provider_name="xbow",
            provenance={
                "schema_version": 1,
                "run_id": "run-20260830-001",
                "captured_at": "2026-08-30T12:00:00Z",
                "context_mode": "hinted",
                "source_commit": "31e1c8e786c83bb20f5c3d9ebc482cf9fb8ffa06",
                "source_dirty": False,
                "benchmark_revisions": {
                    "benchmark/xbow-validation-benchmarks": (
                        "1c15c3289c6496446065adc254dd68104a2e372f"
                    )
                },
                "model_profile": "max",
                "model_assignments": {
                    "decepticon": ["anthropic/claude-opus-4-8"],
                },
                "artifact_hashes": {
                    "agent-prompts": "a" * 64,
                    "skill-corpus": "b" * 64,
                },
                "container_images": {
                    "langgraph": "ghcr.io/purpleailab/decepticon-langgraph@sha256:" + "c" * 64,
                },
                "config": {"provider": "xbow", "timeout": 1800},
                "python_version": "3.13.7",
                "platform": "Linux-x86_64",
            },
            total=2,
            passed=1,
            failed=1,
            pass_rate=0.5,
            by_level={1: {"total": 2, "passed": 1, "pass_rate": 0.5}},
            by_tag={"xss": {"total": 1, "passed": 1, "pass_rate": 1.0}},
            results=[
                ChallengeResult(
                    challenge_id="XBEN-001-24",
                    challenge_name="Test 1",
                    level=1,
                    tags=["xss"],
                    passed=True,
                    flag_captured="FLAG{abc123}",
                ),
                ChallengeResult(
                    challenge_id="XBEN-002-24",
                    challenge_name="Test 2",
                    level=1,
                    tags=["sqli"],
                    passed=False,
                ),
            ],
            started_at=now,
            completed_at=now,
            duration_seconds=120.0,
        )

        data = report.model_dump()
        restored = BenchmarkReport.model_validate(data)

        assert restored.provider_name == "xbow"
        assert restored.total == 2
        assert restored.passed == 1
        assert restored.failed == 1
        assert restored.pass_rate == 0.5
        assert restored.provenance.source_commit == "31e1c8e786c83bb20f5c3d9ebc482cf9fb8ffa06"
        assert restored.provenance.context_mode == "hinted"
        assert restored.provenance.model_assignments["decepticon"] == ["anthropic/claude-opus-4-8"]
        assert len(restored.results) == 2
        assert restored.results[0].passed is True
        assert restored.results[1].passed is False

    def test_run_provenance_rejects_empty_mandatory_identity_maps(self) -> None:
        with pytest.raises(ValidationError, match="container_images"):
            RunProvenance(
                run_id="run-001",
                captured_at=datetime(2026, 8, 30, tzinfo=timezone.utc),
                context_mode="hinted",
                source_commit="1" * 40,
                source_dirty=False,
                benchmark_revisions={},
                model_profile="max",
                model_assignments={"decepticon": ["anthropic/claude-opus-4-8"]},
                artifact_hashes={"agent-prompts": "2" * 64},
                container_images={},
                config={"provider": "xbow"},
                python_version="3.13.7",
                platform="linux-x86_64",
            )


class TestFilterConfig:
    def test_filter_config_defaults(self) -> None:
        """FilterConfig with no args has empty lists and None ranges."""
        fc = FilterConfig()

        assert fc.levels == []
        assert fc.tags == []
        assert fc.range_start is None
        assert fc.range_end is None


class TestSetupResult:
    def test_setup_result_success_and_failure(self) -> None:
        """Both success=True and success=False cases."""
        success = SetupResult(target_url="http://localhost:8080", success=True)
        assert success.success is True
        assert success.target_url == "http://localhost:8080"
        assert success.error is None
        assert success.container_ids == []

        failure = SetupResult(
            target_url="",
            success=False,
            error="Docker build failed",
        )
        assert failure.success is False
        assert failure.error == "Docker build failed"
        assert failure.target_url == ""
