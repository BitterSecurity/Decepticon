"""Evaluation metrics and dataset contracts against the OSS Skillogy API."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from decepticon.skillogy.evaluation import (
    EvaluationSuite,
    evaluate_suite,
    validate_dataset_paths,
)

PREFIX = "/skills/standard/exploit/"
A = f"{PREFIX}web/ssrf/SKILL.md"
B = f"{PREFIX}web/sqli/SKILL.md"
C = f"{PREFIX}web/xss/SKILL.md"


class FakeBackend:
    def __init__(self, hits: dict[str, list[str]]) -> None:
        self.hits = hits
        self.calls: list[tuple[str, int, list[str]]] = []
        self.loaded: list[tuple[str, list[str]]] = []

    def find_skill(
        self,
        *,
        query: str | None = None,
        limit: int = 20,
        allowed_path_prefixes: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        self.calls.append((query or "", limit, allowed_path_prefixes or []))
        return [{"path": path} for path in self.hits.get(query or "", [])]

    def load_skill(
        self, path: str, *, allowed_path_prefixes: list[str] | None = None
    ) -> dict[str, Any] | None:
        self.loaded.append((path, allowed_path_prefixes or []))
        return {"body": "évidence"}


def _suite() -> EvaluationSuite:
    return EvaluationSuite.model_validate(
        {
            "version": 1,
            "cases": [
                {
                    "id": "positive-1",
                    "query": "ssrf",
                    "allowed_path_prefixes": [PREFIX],
                    "expected_paths": [A],
                    "forbidden_paths": [B],
                },
                {
                    "id": "positive-2",
                    "query": "xss",
                    "allowed_path_prefixes": [PREFIX],
                    "expected_paths": [C],
                },
                {
                    "id": "negative-1",
                    "query": "wedding",
                    "allowed_path_prefixes": [PREFIX],
                    "abstain": True,
                },
            ],
        }
    )


def test_evaluate_counts_rank_abstention_pollution_and_utf8_bytes() -> None:
    backend = FakeBackend({"ssrf": [B, A], "xss": [], "wedding": []})
    report = evaluate_suite(backend, _suite(), source_revision="abc123")

    assert report.dataset_version == 1
    assert report.source_revision == "abc123"
    assert report.metrics.recall_at_1 == 0
    assert report.metrics.recall_at_3 == report.metrics.recall_at_10 == 0.5
    assert report.metrics.abstention_precision == 0.5
    assert report.metrics.abstention_recall == 1
    assert report.metrics.forbidden_at_3_rate == 0.5
    assert report.metrics.loaded_skill_bytes == len("évidence".encode("utf-8"))
    assert report.metrics.estimated_loaded_tokens == 3
    assert report.metrics.p95_latency_ms >= 0
    assert backend.calls == [
        ("ssrf", 10, [PREFIX]),
        ("xss", 10, [PREFIX]),
        ("wedding", 10, [PREFIX]),
    ]
    assert backend.loaded == [(B, [PREFIX])]


def test_backend_must_observe_scope() -> None:
    backend = FakeBackend({"ssrf": ["/skills/private/secret/SKILL.md"]})
    with pytest.raises(ValueError, match="out-of-scope"):
        evaluate_suite(backend, _suite(), source_revision="abc123")


@pytest.mark.parametrize(
    "case",
    [
        {"id": "x", "query": "q", "allowed_path_prefixes": [PREFIX]},
        {
            "id": "x",
            "query": "q",
            "allowed_path_prefixes": [PREFIX],
            "expected_paths": [A],
            "abstain": True,
        },
        {
            "id": "x",
            "query": "q",
            "allowed_path_prefixes": [PREFIX],
            "expected_paths": [A],
            "forbidden_paths": [A],
        },
    ],
)
def test_reject_ambiguous_cases(case: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        EvaluationSuite.model_validate({"version": 1, "cases": [case]})


def test_reject_duplicate_ids() -> None:
    case = {"id": "x", "query": "q", "allowed_path_prefixes": [PREFIX], "expected_paths": [A]}
    with pytest.raises(ValidationError, match="unique ids"):
        EvaluationSuite.model_validate({"version": 1, "cases": [case, case]})


def test_dataset_paths_must_exist(tmp_path: Path) -> None:
    suite = _suite()
    with pytest.raises(ValueError, match="skill does not exist"):
        validate_dataset_paths(suite, tmp_path)
    for skill in (A, B, C):
        path = tmp_path / skill.removeprefix("/skills/")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    validate_dataset_paths(suite, tmp_path)


def test_shipped_dataset_labels_exist() -> None:
    root = Path(__file__).resolve().parents[3] / "decepticon" / "skills"
    dataset = Path(__file__).resolve().parents[5] / "benchmark" / "skillogy" / "cases-v1.json"
    suite = EvaluationSuite.model_validate_json(dataset.read_text(encoding="utf-8"))
    validate_dataset_paths(suite, root)
    assert len(suite.cases) == 18
    assert sum(case.abstain for case in suite.cases) == 3
    assert len(json.loads(dataset.read_text(encoding="utf-8"))["cases"]) == 18
