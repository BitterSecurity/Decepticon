"""Regression tests for the final engagement coverage gate."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from decepticon.tools.orchestration.finish_gate import finish_engagement


def _finish(workspace: Path) -> dict[str, object]:
    response = finish_engagement.invoke(
        {
            "executive_summary": "Synthetic fixture only.",
            "methodology": "No network or model calls.",
            "technical_analysis": "Coverage gate regression fixture.",
            "recommendations": "Resolve outstanding coverage first.",
            "attack_chaining_considered": True,
            "attack_chaining_notes": "No findings exist in this fixture.",
        },
        config={"configurable": {"workspace_path": str(workspace)}},
    )
    return json.loads(response)


def test_finish_engagement_refuses_unresolved_coverage_follow_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DECEPTICON_WORKSPACE_PATH", str(tmp_path))
    (tmp_path / "coverage.json").write_text(
        json.dumps(
            {
                "entries": [
                    {
                        "surface": "synthetic-local-fixture",
                        "risk_area": "reporting",
                        "outcome": "needs_follow_up",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    result = _finish(tmp_path)

    assert result == {
        "error": "coverage follow-ups remain",
        "coverage_gaps": [
            {"surface": "synthetic-local-fixture", "risk_area": "reporting"}
        ],
    }
    assert not (tmp_path / "final_report.json").exists()


def test_finish_engagement_completes_after_coverage_is_resolved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DECEPTICON_WORKSPACE_PATH", str(tmp_path))
    (tmp_path / "coverage.json").write_text(
        json.dumps(
            {
                "entries": [
                    {
                        "surface": "synthetic-local-fixture",
                        "risk_area": "reporting",
                        "outcome": "no_issue_found",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    result = _finish(tmp_path)

    assert result["finished"] is True
    assert result["coverage_gaps_remaining"] == 0
    report = json.loads((tmp_path / "final_report.json").read_text(encoding="utf-8"))
    assert report["coverage_gaps"] == []
