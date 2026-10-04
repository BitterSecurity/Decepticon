"""Reproducible, service-level Skillogy retrieval evaluation for OSS."""

from __future__ import annotations

import argparse
import json
import math
import os
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, model_validator


class EvaluationCase(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    query: str
    allowed_path_prefixes: tuple[str, ...]
    expected_paths: tuple[str, ...] = ()
    forbidden_paths: tuple[str, ...] = ()
    abstain: bool = False

    @model_validator(mode="after")
    def validate_outcome(self) -> EvaluationCase:
        if not self.id.strip() or not self.query.strip():
            raise ValueError("case id and query must not be empty")
        if not self.allowed_path_prefixes or any(
            not prefix.startswith("/skills/") for prefix in self.allowed_path_prefixes
        ):
            raise ValueError("case must have /skills/ path prefixes")
        if self.abstain == bool(self.expected_paths):
            raise ValueError("case must either abstain or declare expected paths")
        if set(self.expected_paths) & set(self.forbidden_paths):
            raise ValueError("expected and forbidden paths must not overlap")
        for path in (*self.expected_paths, *self.forbidden_paths):
            if not path.startswith("/skills/") or not path.endswith("/SKILL.md"):
                raise ValueError(f"invalid skill path: {path}")
            if not any(path.startswith(prefix) for prefix in self.allowed_path_prefixes):
                raise ValueError(f"skill path outside case allowlist: {path}")
        return self


class EvaluationSuite(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    version: Literal[1]
    cases: tuple[EvaluationCase, ...]

    @model_validator(mode="after")
    def validate_cases(self) -> EvaluationSuite:
        if not self.cases or len({case.id for case in self.cases}) != len(self.cases):
            raise ValueError("suite must contain cases with unique ids")
        return self


class CaseResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    returned_paths: tuple[str, ...]
    predicted_abstention: bool
    forbidden_at_3: bool
    latency_ms: float
    loaded_skill_bytes: int


class EvaluationMetrics(BaseModel):
    model_config = ConfigDict(frozen=True)

    recall_at_1: float
    recall_at_3: float
    recall_at_10: float
    abstention_precision: float
    abstention_recall: float
    forbidden_at_3_rate: float
    p95_latency_ms: float
    loaded_skill_bytes: int
    estimated_loaded_tokens: int


class EvaluationReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    dataset_version: Literal[1]
    source_revision: str
    metrics: EvaluationMetrics
    cases: tuple[CaseResult, ...]


class EvaluationBackend(Protocol):
    def find_skill(
        self,
        *,
        query: str | None = None,
        limit: int = 20,
        allowed_path_prefixes: list[str] | None = None,
    ) -> list[dict[str, Any]]: ...

    def load_skill(
        self,
        path: str,
        *,
        allowed_path_prefixes: list[str] | None = None,
    ) -> dict[str, Any] | None: ...


def validate_dataset_paths(suite: EvaluationSuite, skills_root: Path) -> None:
    """Fail if labels point to paths absent from this OSS skill tree."""
    for case in suite.cases:
        for path in (*case.expected_paths, *case.forbidden_paths):
            relative = Path(path.removeprefix("/skills/"))
            if ".." in relative.parts or not (skills_root / relative).is_file():
                raise ValueError(f"case {case.id}: skill does not exist: {path}")


def evaluate_suite(
    backend: EvaluationBackend,
    suite: EvaluationSuite,
    *,
    source_revision: str,
) -> EvaluationReport:
    results = tuple(_evaluate_case(backend, case) for case in suite.cases)
    positives = tuple(
        (case, result)
        for case, result in zip(suite.cases, results, strict=True)
        if not case.abstain
    )
    negatives = tuple(
        (case, result) for case, result in zip(suite.cases, results, strict=True) if case.abstain
    )
    predicted = tuple(
        (case, result)
        for case, result in zip(suite.cases, results, strict=True)
        if result.predicted_abstention
    )
    total_bytes = sum(result.loaded_skill_bytes for result in results)
    metrics = EvaluationMetrics(
        recall_at_1=_recall_at(positives, 1),
        recall_at_3=_recall_at(positives, 3),
        recall_at_10=_recall_at(positives, 10),
        abstention_precision=_ratio(sum(case.abstain for case, _ in predicted), len(predicted)),
        abstention_recall=_ratio(
            sum(result.predicted_abstention for _, result in negatives), len(negatives)
        ),
        forbidden_at_3_rate=_ratio(
            sum(result.forbidden_at_3 for _, result in positives), len(positives)
        ),
        p95_latency_ms=_percentile_95(tuple(result.latency_ms for result in results)),
        loaded_skill_bytes=total_bytes,
        estimated_loaded_tokens=math.ceil(total_bytes / 4),
    )
    return EvaluationReport(
        dataset_version=suite.version,
        source_revision=source_revision,
        metrics=metrics,
        cases=results,
    )


def _evaluate_case(backend: EvaluationBackend, case: EvaluationCase) -> CaseResult:
    allowed = list(case.allowed_path_prefixes)
    start = time.perf_counter()
    hits = backend.find_skill(query=case.query, limit=10, allowed_path_prefixes=allowed)
    latency_ms = (time.perf_counter() - start) * 1000
    paths = tuple(_path(hit) for hit in hits)
    if any(not path or not any(path.startswith(prefix) for prefix in allowed) for path in paths):
        raise ValueError(f"case {case.id}: backend returned invalid or out-of-scope path")
    first = backend.load_skill(paths[0], allowed_path_prefixes=allowed) if paths else None
    return CaseResult(
        id=case.id,
        returned_paths=paths,
        predicted_abstention=not paths,
        forbidden_at_3=bool(set(paths[:3]) & set(case.forbidden_paths)),
        latency_ms=latency_ms,
        loaded_skill_bytes=_loaded_bytes(first),
    )


def _path(hit: Mapping[str, Any]) -> str:
    value = hit.get("path")
    return value if isinstance(value, str) else ""


def _loaded_bytes(skill: Mapping[str, Any] | None) -> int:
    body = skill.get("body") if skill else None
    return len(body.encode("utf-8")) if isinstance(body, str) else 0


def _recall_at(positives: tuple[tuple[EvaluationCase, CaseResult], ...], rank: int) -> float:
    return _ratio(
        sum(
            bool(set(result.returned_paths[:rank]) & set(case.expected_paths))
            for case, result in positives
        ),
        len(positives),
    )


def _ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def _percentile_95(values: tuple[float, ...]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[math.ceil(len(ordered) * 0.95) - 1]


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate OSS Skillogy retrieval")
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--skills-root", type=Path, required=True)
    parser.add_argument("--source-revision", required=True)
    parser.add_argument(
        "--url", default=os.getenv("DECEPTICON_SKILLOGY_URL", "http://localhost:9100")
    )
    parser.add_argument("--api-key", default=os.getenv("SKILLOGY_API_KEY"))
    parser.add_argument("--check-dataset", action="store_true")
    args = parser.parse_args()
    suite = EvaluationSuite.model_validate_json(args.dataset.read_text(encoding="utf-8"))
    validate_dataset_paths(suite, args.skills_root)
    if args.check_dataset:
        print(json.dumps({"version": suite.version, "case_count": len(suite.cases)}))
        return 0
    from decepticon.skillogy.client.rest import RestSkillogyClient  # noqa: PLC0415

    client = RestSkillogyClient(base_url=args.url, api_key=args.api_key)
    try:
        report = evaluate_suite(client, suite, source_revision=args.source_revision)
        print(report.model_dump_json(indent=2))
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
