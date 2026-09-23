from __future__ import annotations

from datetime import datetime

from benchmark.schemas import BenchmarkReport, ChallengeResult, RunProvenance


class Scorer:
    @staticmethod
    def score(
        results: list[ChallengeResult],
        provider_name: str,
        started_at: datetime,
        completed_at: datetime,
        *,
        provenance: RunProvenance,
    ) -> BenchmarkReport:
        """Aggregate challenge results into a BenchmarkReport."""
        total = len(results)
        passed = sum(1 for r in results if r.passed)
        failed = total - passed
        pass_rate = passed / total if total > 0 else 0.0

        by_level: dict[int, dict] = {}
        for r in results:
            entry = by_level.setdefault(r.level, {"total": 0, "passed": 0})
            entry["total"] += 1
            if r.passed:
                entry["passed"] += 1
        for entry in by_level.values():
            entry["pass_rate"] = entry["passed"] / entry["total"] if entry["total"] > 0 else 0.0

        by_tag: dict[str, dict] = {}
        for r in results:
            for tag in r.tags:
                entry = by_tag.setdefault(tag, {"total": 0, "passed": 0})
                entry["total"] += 1
                if r.passed:
                    entry["passed"] += 1
        for entry in by_tag.values():
            entry["pass_rate"] = entry["passed"] / entry["total"] if entry["total"] > 0 else 0.0

        duration_seconds = (completed_at - started_at).total_seconds()

        # Roll up per-challenge cost into a batch total. None when no
        # challenge captured cost (LiteLLM unreachable across the
        # board); otherwise sum the available subtotals so a partial
        # capture still surfaces in the report.
        costs = [r.cost_usd for r in results if r.cost_usd is not None]
        total_cost_usd = sum(costs) if costs else None

        return BenchmarkReport(
            provider_name=provider_name,
            provenance=provenance,
            total=total,
            passed=passed,
            failed=failed,
            pass_rate=pass_rate,
            by_level=by_level,
            by_tag=by_tag,
            results=results,
            started_at=started_at,
            completed_at=completed_at,
            duration_seconds=duration_seconds,
            total_cost_usd=total_cost_usd,
        )
