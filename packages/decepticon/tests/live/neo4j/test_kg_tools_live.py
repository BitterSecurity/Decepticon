"""Live integration tests for the agent-facing KG tools.

Builds ``kg_record`` and ``kg_ingest`` against the compose Neo4j and
invokes them with the OPPLAN-style ToolCall envelope. Verifies the
tools' results match what the LLM would see.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from decepticon.middleware.kg_internal.store import KGStore
from decepticon.middleware.kg_internal.tools import build_kg_tools


def _call(tool: Any, args: dict[str, Any], state: dict[str, Any]) -> str:
    payload = {
        "name": getattr(tool, "name", "tool"),
        "type": "tool_call",
        "id": "live-tool-call",
        "args": {**args, "state": state},
    }
    return tool.invoke(payload)


def test_kg_record_writes_through_live_store(kgstore: KGStore, engagement: str) -> None:
    [kg_record, _] = build_kg_tools(kgstore)
    obs = [
        {
            "kind": "Host",
            "key": f"host::tool-record::{engagement}",
            "label": "host-from-tool",
            "props": {"ip": "10.0.0.7"},
        }
    ]
    out_msg = _call(
        kg_record,
        {"observations": json.dumps(obs)},
        state={"kg_engagement": engagement, "role": "analyst"},
    )
    payload = json.loads(out_msg.content)
    assert payload["created"] == 1
    assert payload["merged"] == 0

    rows = kgstore.execute_read(
        "MATCH (h:Host) WHERE h.engagement = $eng AND h.key = $key "
        "RETURN h.label AS label, h.created_by AS created_by, "
        "       h.source_episode_id AS sep",
        {"eng": engagement, "key": f"host::tool-record::{engagement}"},
        engagement=engagement,
    )
    assert rows
    assert rows[0]["label"] == "host-from-tool"
    assert rows[0]["created_by"] == "analyst"
    assert rows[0]["sep"] == "live-tool-call"


def test_kg_record_atomic_batch_with_edges_live(kgstore: KGStore, engagement: str) -> None:
    [kg_record, _] = build_kg_tools(kgstore)
    obs = [
        {
            "kind": "Host",
            "key": f"host::batch::{engagement}",
            "label": "batch-host",
            "edges_out": [
                {
                    "to_key": f"service::batch::{engagement}",
                    "kind": "HOSTS",
                    "weight": 0.5,
                }
            ],
        },
        {
            "kind": "Service",
            "key": f"service::batch::{engagement}",
            "label": "batch-service",
        },
    ]
    _call(
        kg_record,
        {"observations": json.dumps(obs)},
        state={"kg_engagement": engagement, "role": "recon"},
    )
    edge_rows = kgstore.execute_read(
        "MATCH (h:Host)-[r:HOSTS]->(s:Service) "
        "WHERE h.engagement = $eng AND h.key = $hk RETURN r.weight AS w",
        {"eng": engagement, "hk": f"host::batch::{engagement}"},
        engagement=engagement,
    )
    assert edge_rows
    assert edge_rows[0]["w"] == 0.5


def test_kg_ingest_routes_nuclei_jsonl_through_live_store(
    kgstore: KGStore, engagement: str, tmp_path: Path
) -> None:
    f = tmp_path / "nuclei.jsonl"
    f.write_text(
        json.dumps(
            {
                "template-id": "tool-ssrf",
                "info": {"severity": "critical", "tags": ["ssrf"]},
                "matched-at": "https://tool-test.example/api",
                "host": "tool-test.example",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    [_, kg_ingest] = build_kg_tools(kgstore)
    out_msg = _call(
        kg_ingest,
        {"scanner_kind": "nuclei_jsonl", "path": str(f)},
        state={"kg_engagement": engagement, "role": "analyst"},
    )
    payload = json.loads(out_msg.content)
    assert payload["scanner"] == "nuclei_jsonl"
    assert payload["parsed"] == 1

    rows = kgstore.execute_read(
        "MATCH (v:Vulnerability) WHERE v.engagement = $eng "
        "AND v.rule_id = 'tool-ssrf' "
        "RETURN v.created_by AS created_by, v.source_episode_id AS sep",
        {"eng": engagement},
        engagement=engagement,
    )
    assert rows
    assert rows[0]["created_by"] == "analyst"
    assert rows[0]["sep"] == "live-tool-call"


def test_kg_record_idempotent_across_two_calls_live(kgstore: KGStore, engagement: str) -> None:
    """Two ToolCalls with the same observation key collapse to one
    node — the deterministic key + (key, engagement) uniqueness."""
    [kg_record, _] = build_kg_tools(kgstore)
    obs = [
        {
            "kind": "Host",
            "key": f"host::idempotent::{engagement}",
            "label": "host",
        }
    ]
    first = json.loads(
        _call(
            kg_record,
            {"observations": json.dumps(obs)},
            state={"kg_engagement": engagement, "role": "analyst"},
        ).content
    )
    second = json.loads(
        _call(
            kg_record,
            {"observations": json.dumps(obs)},
            state={"kg_engagement": engagement, "role": "analyst"},
        ).content
    )
    assert first["created"] == 1
    assert second["created"] == 0
    assert second["merged"] == 1


def test_kg_record_engagement_unset_short_circuits_live(
    kgstore: KGStore,
) -> None:
    """When kg_engagement is missing, the tool returns an error and does
    NOT write anything to the live store."""
    [kg_record, _] = build_kg_tools(kgstore)
    obs = [{"kind": "Host", "key": "host::no-engagement", "label": "x"}]
    out_msg = _call(
        kg_record,
        {"observations": json.dumps(obs)},
        state={},
    )
    payload = json.loads(out_msg.content)
    assert "error" in payload
    # And confirm nothing landed under any engagement.
    rows = kgstore.execute_read(
        "MATCH (n) WHERE n.key = $key RETURN count(n) AS c",
        {"key": "host::no-engagement"},
        engagement="schema",
    )
    assert rows[0]["c"] == 0
