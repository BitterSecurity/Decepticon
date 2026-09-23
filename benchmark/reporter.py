from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from benchmark.schemas import BenchmarkReport, ChallengeResult


class Reporter:
    """Writes benchmark reports to disk in JSON and Markdown formats.

    Layout per run (always the same shape, single or batch):

      results/
        <challenge_id>/                       ← one directory per challenge, persistent
          <UTC_timestamp>/                    ← one wrapper per execution
            report.json                       ← full ChallengeResult for THIS run
            report.md                         ← human-readable evidence for THIS run
            evidence/summary.{json,md}        ← legacy-format aliases
          (subdirs accumulate; never overwrites)
        batch-<UTC_timestamp>/                ← one directory per Reporter instance
          report.json                         ← BenchmarkReport aggregate
          report.md                           ← markdown table aggregate
          index.json                          ← cross-reference of per-challenge paths

    Re-running the same challenge id appends a new ``<UTC_timestamp>/``
    sub-directory under ``results/<id>/``; the prior runs stay intact so
    the loop's Observer can compare across cycles. The batch directory
    snapshots the aggregate for the run that produced these files.

    The timestamp is fixed at Reporter construction and shared across
    write_json / write_markdown / write_evidence so all artifacts produced by
    one run land under a single batch directory and pick up the same per-id
    suffix.
    """

    def __init__(self, results_dir: Path) -> None:
        self.results_dir = results_dir
        self._timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

    @property
    def _batch_dir(self) -> Path:
        return self.results_dir / f"batch-{self._timestamp}"

    def _challenge_dir(self, challenge_id: str) -> Path:
        return self.results_dir / challenge_id

    def write_json(self, report: BenchmarkReport) -> Path:
        """Write the batch aggregate JSON. Returns the path."""
        self._batch_dir.mkdir(parents=True, exist_ok=True)
        path = self._batch_dir / "report.json"
        path.write_text(
            json.dumps(report.model_dump(mode="json"), indent=2, default=str),
            encoding="utf-8",
        )
        return path

    def write_markdown(self, report: BenchmarkReport) -> Path:
        """Write the batch aggregate Markdown. Returns the path."""
        self._batch_dir.mkdir(parents=True, exist_ok=True)
        path = self._batch_dir / "report.md"

        lines: list[str] = []
        lines.append(f"# Benchmark Report — {report.provider_name}")
        lines.append("")
        lines.append("## Reproducibility")
        lines.append("")
        lines.append("| Identity | Value |")
        lines.append("|----------|-------|")
        lines.append(f"| Run ID | `{report.provenance.run_id}` |")
        lines.append(f"| Source Commit | `{report.provenance.source_commit}` |")
        lines.append(f"| Source Dirty | {str(report.provenance.source_dirty).lower()} |")
        lines.append(f"| Context Mode | {report.provenance.context_mode} |")
        lines.append(f"| Model Profile | {report.provenance.model_profile} |")
        lines.append("")
        lines.append("## Summary")
        lines.append("")
        lines.append("| Metric | Value |")
        lines.append("|--------|-------|")
        lines.append(f"| Total | {report.total} |")
        lines.append(f"| Passed | {report.passed} |")
        lines.append(f"| Failed | {report.failed} |")
        lines.append(f"| Pass Rate | {report.pass_rate:.1%} |")
        lines.append(f"| Duration | {report.duration_seconds:.1f}s |")
        # Cost is rendered with 4-decimal precision (≈ tenths of a cent),
        # matching LiteLLM's /spend/logs precision. ``n/a`` is reserved
        # for the "no challenge captured cost" state — distinct from an
        # all-zero capture (e.g. only OAuth routes with $0 model_info).
        if report.total_cost_usd is not None:
            lines.append(f"| Total Cost (USD) | ${report.total_cost_usd:.4f} |")
        else:
            lines.append("| Total Cost (USD) | n/a |")
        lines.append("")
        lines.append("## Results by Level")
        lines.append("")
        lines.append("| Level | Total | Passed | Pass Rate |")
        lines.append("|-------|-------|--------|-----------|")
        for level in sorted(report.by_level):
            entry = report.by_level[level]
            lines.append(
                f"| {level} | {entry['total']} | {entry['passed']} | {entry['pass_rate']:.1%} |"
            )
        lines.append("")
        lines.append("## Results by Tag")
        lines.append("")
        lines.append("| Tag | Total | Passed | Pass Rate |")
        lines.append("|-----|-------|--------|-----------|")
        for tag in sorted(report.by_tag):
            entry = report.by_tag[tag]
            lines.append(
                f"| {tag} | {entry['total']} | {entry['passed']} | {entry['pass_rate']:.1%} |"
            )
        lines.append("")
        lines.append("## Individual Results")
        lines.append("")
        lines.append("| ID | Name | Level | Result | Duration | Cost (USD) | Tokens | Error |")
        lines.append("|----|------|-------|--------|----------|------------|--------|-------|")
        for r in report.results:
            result_str = "PASS" if r.passed else "FAIL"
            error_str = r.error or ""
            cost_str = f"${r.cost_usd:.4f}" if r.cost_usd is not None else "n/a"
            tokens_str = f"{r.token_count:,}" if r.token_count is not None else "n/a"
            lines.append(
                f"| {r.challenge_id} | {r.challenge_name} | {r.level} "
                f"| {result_str} | {r.duration_seconds:.1f}s | {cost_str} "
                f"| {tokens_str} | {error_str} |"
            )
        lines.append("")

        path.write_text("\n".join(lines), encoding="utf-8")
        return path

    def write_evidence(self, report: BenchmarkReport) -> Path:
        """Write per-challenge evidence files and the batch index.

        Per-challenge files: ``results/<challenge_id>/<UTC_timestamp>/{report.json,
        report.md, evidence/summary.{json,md}}``
          - report.json: the full ChallengeResult model dump (preserves trace_id,
            token_count, cancel_outcome, terminal_status_at_teardown — i.e.
            everything the Observer needs to map a result to its LangSmith
            trace and infrastructure outcome).
          - report.md: human-readable evidence card.
          - evidence/summary.{json,md}: legacy-format aliases of the same
            payload, kept so existing tools continue to find the file they
            grep for.

        Batch index: ``results/batch-<UTC_timestamp>/index.json`` — provider
        metadata plus a list of ``{id, name, level, passed, duration_seconds,
        trace_id, evidence_path}`` so consumers can navigate from the batch
        aggregate to each per-challenge directory without scanning.

        Returns the batch directory path.
        """
        for result in report.results:
            challenge_dir = self._challenge_dir(result.challenge_id)
            challenge_dir.mkdir(parents=True, exist_ok=True)
            self._write_challenge_evidence(challenge_dir, result, stem=self._timestamp)

        self._batch_dir.mkdir(parents=True, exist_ok=True)
        index = {
            "provider": report.provider_name,
            "timestamp": self._timestamp,
            "provenance": report.provenance.model_dump(mode="json"),
            "total": report.total,
            "passed": report.passed,
            "pass_rate": report.pass_rate,
            "challenges": [
                {
                    "id": r.challenge_id,
                    "name": r.challenge_name,
                    "level": r.level,
                    "passed": r.passed,
                    "duration_seconds": r.duration_seconds,
                    "trace_id": r.trace_id,
                    "token_count": r.token_count,
                    "evidence_path": str(
                        self._challenge_dir(r.challenge_id) / self._timestamp / "report.json"
                    ),
                }
                for r in report.results
            ],
        }
        (self._batch_dir / "index.json").write_text(
            json.dumps(index, indent=2, default=str), encoding="utf-8"
        )
        return self._batch_dir

    def _write_challenge_evidence(
        self, challenge_dir: Path, result: ChallengeResult, *, stem: str
    ) -> None:
        """Write per-run snapshot under ``<challenge_dir>/<stem>/``.

        Layout mirrors the legacy Level-3 directory shape (`report.json`,
        `report.md`, `evidence/summary.{json,md}`) so consumers used to
        the L3 format work unchanged. The timestamp lives in the wrapper
        directory name (`stem`) so re-runs of the same challenge accumulate
        side-by-side instead of overwriting.

        ``report.json`` is the FULL ChallengeResult model dump (so newly
        added fields surface automatically without reporter changes).
        ``evidence/summary.json`` carries the same payload — kept as a
        legacy alias so older tooling that grepped for it still works.
        """
        run_dir = challenge_dir / stem
        run_dir.mkdir(parents=True, exist_ok=True)
        evidence_dir = run_dir / "evidence"
        evidence_dir.mkdir(parents=True, exist_ok=True)

        payload = json.dumps(result.model_dump(mode="json"), indent=2, default=str)
        (run_dir / "report.json").write_text(payload, encoding="utf-8")
        (evidence_dir / "summary.json").write_text(payload, encoding="utf-8")

        lines = [
            f"# {result.challenge_id}: {result.challenge_name}",
            "",
            f"**Result:** {'PASS' if result.passed else 'FAIL'}",
            f"**Level:** {result.level}",
            f"**Tags:** {', '.join(result.tags)}",
            f"**Duration:** {result.duration_seconds:.1f}s",
        ]
        if result.setup_seconds is not None:
            lines.append(f"**Setup overhead:** {result.setup_seconds:.1f}s")
        if result.flag_captured:
            lines.append(f"**Flag:** `{result.flag_captured}`")
        if result.trace_id:
            lines.append(f"**Trace ID:** `{result.trace_id}`")
        if result.token_count is not None:
            lines.append(f"**Tokens:** {result.token_count:,}")
        if result.cost_usd is not None:
            lines.append(f"**Cost (USD):** ${result.cost_usd:.4f}")
        if result.cancel_outcome:
            lines.append(f"**Cancel outcome:** {result.cancel_outcome}")
        if result.terminal_status_at_teardown:
            lines.append(f"**Terminal status at teardown:** {result.terminal_status_at_teardown}")
        if result.error:
            lines.append(f"**Error:** {result.error}")
        if result.agent_summary:
            lines.extend(["", "## Agent Summary", "", result.agent_summary])
        lines.append("")

        md_text = "\n".join(lines)
        (run_dir / "report.md").write_text(md_text, encoding="utf-8")
        (evidence_dir / "summary.md").write_text(md_text, encoding="utf-8")
