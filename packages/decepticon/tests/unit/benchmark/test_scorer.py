"""Tests for benchmark.scorer.Scorer."""

from __future__ import annotations

from datetime import datetime, timezone

from benchmark.schemas import ChallengeResult, RunProvenance
from benchmark.scorer import Scorer


def _make_result(
    challenge_id: str,
    level: int,
    tags: list[str],
    passed: bool,
) -> ChallengeResult:
    return ChallengeResult(
        challenge_id=challenge_id,
        challenge_name=f"Challenge {challenge_id}",
        level=level,
        tags=tags,
        passed=passed,
    )


def _provenance() -> RunProvenance:
    return RunProvenance(
        run_id="run-001",
        captured_at=datetime(2026, 8, 30, tzinfo=timezone.utc),
        context_mode="hinted",
        source_commit="1" * 40,
        source_dirty=False,
        benchmark_revisions={},
        model_profile="test",
        model_assignments={"decepticon": ["openai/gpt-5-nano"]},
        artifact_hashes={"agent-prompts": "2" * 64},
        container_images={"langgraph": "sha256:" + "3" * 64},
        config={"provider": "test"},
        python_version="3.13.7",
        platform="linux-x86_64",
    )


class TestScorer:
    def _times(self) -> tuple[datetime, datetime]:
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        end = datetime(2026, 1, 1, 0, 10, tzinfo=timezone.utc)
        return start, end

    def test_score_mixed_results(self) -> None:
        """Mixed passed/failed results produce correct totals."""
        start, end = self._times()
        results = [
            _make_result("C-001", 1, ["xss"], True),
            _make_result("C-002", 1, ["sqli"], False),
            _make_result("C-003", 2, ["xss"], True),
        ]

        provenance = _provenance()
        report = Scorer.score(results, "test", start, end, provenance=provenance)

        assert report.total == 3
        assert report.passed == 2
        assert report.failed == 1
        assert report.pass_rate == 2 / 3
        assert report.provenance is provenance

    def test_score_by_level(self) -> None:
        """Verify by_level breakdown is computed correctly."""
        start, end = self._times()
        results = [
            _make_result("C-001", 1, ["xss"], True),
            _make_result("C-002", 1, ["sqli"], True),
            _make_result("C-003", 1, ["rce"], False),
            _make_result("C-004", 2, ["idor"], True),
        ]

        report = Scorer.score(results, "test", start, end, provenance=_provenance())

        assert report.by_level[1]["total"] == 3
        assert report.by_level[1]["passed"] == 2
        assert report.by_level[1]["pass_rate"] == 2 / 3
        assert report.by_level[2]["total"] == 1
        assert report.by_level[2]["passed"] == 1
        assert report.by_level[2]["pass_rate"] == 1.0

    def test_score_by_tag(self) -> None:
        """Verify by_tag breakdown handles challenges with multiple tags."""
        start, end = self._times()
        results = [
            _make_result("C-001", 1, ["xss", "web"], True),
            _make_result("C-002", 1, ["sqli", "web"], False),
        ]

        report = Scorer.score(results, "test", start, end, provenance=_provenance())

        assert report.by_tag["xss"]["total"] == 1
        assert report.by_tag["xss"]["passed"] == 1
        assert report.by_tag["xss"]["pass_rate"] == 1.0
        assert report.by_tag["sqli"]["total"] == 1
        assert report.by_tag["sqli"]["passed"] == 0
        assert report.by_tag["sqli"]["pass_rate"] == 0.0
        assert report.by_tag["web"]["total"] == 2
        assert report.by_tag["web"]["passed"] == 1
        assert report.by_tag["web"]["pass_rate"] == 0.5

    def test_score_empty_results(self) -> None:
        """Empty list produces report with total=0, pass_rate=0.0."""
        start, end = self._times()
        report = Scorer.score([], "test", start, end, provenance=_provenance())

        assert report.total == 0
        assert report.passed == 0
        assert report.failed == 0
        assert report.pass_rate == 0.0
        assert report.by_level == {}
        assert report.by_tag == {}

    def test_score_all_passed(self) -> None:
        """All passed -> pass_rate=1.0."""
        start, end = self._times()
        results = [
            _make_result("C-001", 1, ["xss"], True),
            _make_result("C-002", 2, ["sqli"], True),
        ]

        report = Scorer.score(results, "test", start, end, provenance=_provenance())

        assert report.pass_rate == 1.0
        assert report.passed == 2
        assert report.failed == 0

    def test_score_all_failed(self) -> None:
        """All failed -> pass_rate=0.0."""
        start, end = self._times()
        results = [
            _make_result("C-001", 1, ["xss"], False),
            _make_result("C-002", 2, ["sqli"], False),
        ]

        report = Scorer.score(results, "test", start, end, provenance=_provenance())

        assert report.pass_rate == 0.0
        assert report.passed == 0
        assert report.failed == 2

    def test_total_cost_aggregates_per_challenge(self) -> None:
        """total_cost_usd sums non-None cost_usd entries."""
        start, end = self._times()
        a = _make_result("C-001", 1, ["xss"], True)
        a.cost_usd = 0.123
        b = _make_result("C-002", 1, ["sqli"], False)
        b.cost_usd = 0.001
        report = Scorer.score([a, b], "test", start, end, provenance=_provenance())
        assert report.total_cost_usd is not None
        assert abs(report.total_cost_usd - 0.124) < 1e-9

    def test_total_cost_partial_capture_still_rolls_up(self) -> None:
        """Mixed None/float — total reflects the captured subset."""
        start, end = self._times()
        a = _make_result("C-001", 1, ["xss"], True)
        a.cost_usd = 0.05
        b = _make_result("C-002", 1, ["sqli"], False)  # cost_usd stays None
        report = Scorer.score([a, b], "test", start, end, provenance=_provenance())
        assert report.total_cost_usd == 0.05

    def test_total_cost_none_when_all_missing(self) -> None:
        """All cost_usd None -> total_cost_usd is None (not 0.0)."""
        start, end = self._times()
        results = [
            _make_result("C-001", 1, ["xss"], True),
            _make_result("C-002", 1, ["sqli"], False),
        ]
        report = Scorer.score(results, "test", start, end, provenance=_provenance())
        assert report.total_cost_usd is None
