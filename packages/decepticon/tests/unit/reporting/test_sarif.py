"""Tests for the SARIF v2.1.0 renderer + ``report_sarif`` tool."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Annotated, Any, TypedDict
from unittest.mock import patch

import pytest
from deepagents.backends.filesystem import FilesystemBackend
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode

from decepticon.middleware.scoped_reporting_io import ReportingScopeError
from decepticon.tools.reporting.sarif import (
    SARIF_SCHEMA,
    SARIF_VERSION,
    _partial_fingerprints,
    _sarif_level,
    _stable_rule_id,
    render_sarif,
)
from decepticon.tools.reporting.tools import report_sarif
from decepticon_core.types.kg import KnowledgeGraph, Node, NodeKind


def _seeded_graph() -> KnowledgeGraph:
    g = KnowledgeGraph()
    g.upsert_node(
        Node.make(
            NodeKind.VULNERABILITY,
            "SSRF in /proxy",
            severity="critical",
            cvss_score=9.8,
            cvss_vector="CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:C/C:H/I:H/A:H",
            summary="Server fetches arbitrary URLs allowing IMDS access.",
            description="Detailed description of SSRF exploit chain.",
            poc_command="curl https://target/proxy?url=http://169.254.169.254/",
            cwe=["CWE-918"],
            mitre=["T1190"],
            url="/proxy",
            file="src/proxy/handler.py",
            line=42,
        )
    )
    g.upsert_node(
        Node.make(
            NodeKind.FINDING,
            "Open redirect on /login",
            severity="medium",
            cvss_score=5.4,
            url="/login?next=//attacker",
            summary="Next-param redirects off-site.",
        )
    )
    g.upsert_node(Node.make(NodeKind.CVE, "CVE-2024-1234", score=9.8))
    return g


class TestRenderSarifShape:
    def test_top_level_structure_is_sarif_2_1_0(self) -> None:
        g = _seeded_graph()
        payload = json.loads(render_sarif(g, engagement_id="eng-1"))
        assert payload["version"] == SARIF_VERSION
        assert payload["$schema"] == SARIF_SCHEMA
        assert len(payload["runs"]) == 1
        run = payload["runs"][0]
        assert run["tool"]["driver"]["name"] == "Decepticon"
        assert run["automationDetails"]["id"] == "eng-1/sarif"
        assert len(run["results"]) == 2

    def test_only_vulnerability_and_finding_nodes_emitted(self) -> None:
        g = _seeded_graph()
        payload = json.loads(render_sarif(g))
        run = payload["runs"][0]
        rule_names = [rule["name"] for rule in run["tool"]["driver"]["rules"]]
        result_messages = [r["message"]["text"] for r in run["results"]]
        combined = " ".join(rule_names) + " ||| " + " ".join(result_messages)
        assert "SSRF" in combined
        assert "Open redirect" in combined
        assert "CVE-2024-1234" not in combined

    def test_driver_rules_include_one_entry_per_unique_rule(self) -> None:
        g = _seeded_graph()
        payload = json.loads(render_sarif(g))
        rules = payload["runs"][0]["tool"]["driver"]["rules"]
        rule_ids = {r["id"] for r in rules}
        assert "CWE-918" in rule_ids
        assert len(rules) == len({r["id"] for r in rules})


class TestSarifLevelMapping:
    @pytest.mark.parametrize(
        "severity,expected",
        [
            ("critical", "error"),
            ("CRITICAL", "error"),
            ("high", "error"),
            ("medium", "warning"),
            ("low", "note"),
            ("info", "note"),
            ("informational", "note"),
        ],
    )
    def test_severity_strings_map_correctly(self, severity: str, expected: str) -> None:
        assert _sarif_level(severity, None) == expected

    @pytest.mark.parametrize(
        "score,expected",
        [
            (9.8, "error"),
            (7.0, "error"),
            (6.9, "warning"),
            (4.0, "warning"),
            (3.9, "note"),
            (0.0, "note"),
        ],
    )
    def test_cvss_fallback_when_severity_missing(self, score: float, expected: str) -> None:
        assert _sarif_level(None, score) == expected

    def test_severity_takes_precedence_over_cvss(self) -> None:
        assert _sarif_level("low", 9.9) == "note"

    def test_unknown_severity_falls_back_to_note(self) -> None:
        assert _sarif_level("weird", None) == "note"

    def test_non_numeric_cvss_falls_back_to_note(self) -> None:
        assert _sarif_level(None, "not-a-number") == "note"


class TestResultMapping:
    def test_existing_fields_populate_result(self) -> None:
        g = _seeded_graph()
        payload = json.loads(render_sarif(g))
        results = payload["runs"][0]["results"]
        ssrf = next(r for r in results if "SSRF" in r["message"]["text"])

        assert ssrf["ruleId"] == "CWE-918"
        assert ssrf["level"] == "error"
        loc = ssrf["locations"][0]["physicalLocation"]
        assert loc["artifactLocation"]["uri"] == "src/proxy/handler.py"
        assert loc["region"]["startLine"] == 42
        tags = ssrf["properties"]["tags"]
        assert "severity:critical" in tags
        assert "CWE-918" in tags
        assert "T1190" in tags
        assert any(t.startswith("CVSS:3.1") for t in tags)

    def test_url_fallback_when_no_file(self) -> None:
        g = _seeded_graph()
        payload = json.loads(render_sarif(g))
        run = payload["runs"][0]
        rules = run["tool"]["driver"]["rules"]
        redirect_rule = next(r for r in rules if "redirect" in r["name"].lower())
        results = run["results"]
        redirect = next(r for r in results if r["ruleId"] == redirect_rule["id"])
        loc_uri = redirect["locations"][0]["physicalLocation"]["artifactLocation"]["uri"]
        assert loc_uri == "/login?next=//attacker"

    def test_fingerprints_are_deterministic_and_stable(self) -> None:
        g = _seeded_graph()
        node = g.by_kind(NodeKind.VULNERABILITY)[0]

        fp1 = _partial_fingerprints(node)
        fp2 = _partial_fingerprints(node)

        assert fp1 == fp2
        assert "decepticonFindingId" in fp1
        assert "decepticonStableHash" in fp1
        assert len(fp1["decepticonStableHash"]) == 64

    def test_workspace_finding_uri_used_when_no_file_or_url(self) -> None:
        g = KnowledgeGraph()
        node = Node.make(NodeKind.VULNERABILITY, "abstract issue", severity="low")
        g.upsert_node(node)
        payload = json.loads(render_sarif(g))
        result = payload["runs"][0]["results"][0]
        loc_uri = result["locations"][0]["physicalLocation"]["artifactLocation"]["uri"]
        assert loc_uri == f"workspace/findings/{node.id}.md"


class TestStableRuleId:
    def test_first_cwe_wins(self) -> None:
        g = KnowledgeGraph()
        node = Node.make(
            NodeKind.VULNERABILITY,
            "Cmd injection in /run",
            severity="high",
            cwe=["CWE-78", "CWE-77"],
        )
        g.upsert_node(node)
        assert _stable_rule_id(node) == "CWE-78"

    def test_falls_back_to_title(self) -> None:
        node = Node.make(NodeKind.VULNERABILITY, "Stored XSS in /comments")
        assert "Stored-XSS-in-comments" in _stable_rule_id(node)

    def test_falls_back_to_node_id_when_title_normalizes_empty(self) -> None:
        node = Node.make(NodeKind.VULNERABILITY, "***")
        rid = _stable_rule_id(node)
        assert rid == node.id or rid == "rule"


class _SarifToolState(TypedDict, total=False):
    messages: Annotated[list[BaseMessage], add_messages]
    engagement_name: str
    kg_engagement: str
    workspace_path: str


def _invoke_report_tool(state: dict[str, Any], config: RunnableConfig | None = None) -> Any:
    """A compiled native graph supplies the runtime used by current ToolNode."""
    graph = StateGraph(_SarifToolState)
    graph.add_node("tools", ToolNode([report_sarif], handle_tool_errors=False))
    graph.add_edge(START, "tools")
    graph.add_edge("tools", END)
    return asyncio.run(graph.compile().ainvoke(state, config=config))


class TestReportSarifTool:
    @staticmethod
    def _dispatch(tmp_path: Path, output_path: str, engagement_id: str) -> str:
        """Use real tool-state injection and file IO under an owned test root."""
        backend = FilesystemBackend(root_dir=tmp_path, virtual_mode=True)
        state = {
            "engagement_name": "sarif-case",
            "kg_engagement": "sarif-graph",
            "workspace_path": "/workspace/sarif-case",
            "messages": [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "report_sarif",
                            "args": {"engagement_id": engagement_id, "output_path": output_path},
                            "id": "sarif-call",
                            "type": "tool_call",
                        }
                    ],
                ),
            ],
        }
        config = {
            "configurable": {
                key: state[key] for key in ("engagement_name", "kg_engagement", "workspace_path")
            }
        }
        with (
            patch(
                "decepticon.middleware.scoped_reporting_io.build_sandbox_backend",
                return_value=backend,
            ) as select_backend,
            patch(
                "decepticon.tools.reporting.kg_adapter.load_engagement_graph",
                return_value=_seeded_graph(),
            ) as load_graph,
        ):
            result = _invoke_report_tool(state, config=config)
        load_graph.assert_called_once_with("sarif-graph")
        assert select_backend.call_args.args[0]["configurable"]["workspace_path"] == (
            "/workspace/sarif-case"
        )
        message = result["messages"][-1]
        assert message.tool_call_id == "sarif-call"
        assert message.name == "report_sarif"
        assert message.status == "success"
        assert isinstance(message.content, str)
        return message.content

    def test_writes_sarif_to_disk_and_reports_metadata(self, tmp_path: Path) -> None:
        out = tmp_path / "workspace" / "sarif-case" / "out" / "decepticon.sarif"
        result = self._dispatch(tmp_path, "/workspace/out/decepticon.sarif", "eng-X")

        assert out.exists()
        payload = json.loads(out.read_text(encoding="utf-8"))
        assert payload["version"] == SARIF_VERSION
        info = json.loads(result)
        assert info["engagement_id"] == "eng-X"
        assert info["path"] == "/workspace/out/decepticon.sarif"
        assert info["bytes"] > 0
        assert info["results"] == 2

    def test_creates_parent_directories(self, tmp_path: Path) -> None:
        out = tmp_path / "workspace" / "sarif-case" / "nested" / "deeper" / "decepticon.sarif"
        self._dispatch(tmp_path, "/workspace/nested/deeper/decepticon.sarif", "eng-Y")
        assert out.exists()

    def test_report_cannot_replace_operator_confirmed_roe(self, tmp_path: Path) -> None:
        roe = tmp_path / "workspace" / "sarif-case" / "plan" / "roe.json"
        roe.parent.mkdir(parents=True)
        approved = b'{"authorization_reference":"owned-approval"}\n'
        roe.write_bytes(approved)
        with pytest.raises(ReportingScopeError, match="read-only"):
            self._dispatch(tmp_path, "/workspace/plan/roe.json", "eng-X")
        assert roe.read_bytes() == approved

    def test_model_schema_exposes_only_public_arguments(self) -> None:
        schema = report_sarif.tool_call_schema.model_json_schema()
        assert set(schema["properties"]) == {"engagement_id", "output_path"}
        assert set(schema["required"]) == {"engagement_id", "output_path"}

    @pytest.mark.parametrize(
        "scope",
        [
            {},
            {"engagement_name": "sarif-case", "workspace_path": "/workspace/other-case"},
        ],
    )
    def test_missing_or_foreign_scope_refuses_before_io(self, scope: dict) -> None:
        state = {
            **scope,
            "messages": [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "report_sarif",
                            "args": {
                                "engagement_id": "eng-X",
                                "output_path": "/workspace/out.sarif",
                            },
                            "id": "refused-call",
                            "type": "tool_call",
                        }
                    ],
                )
            ],
        }
        with (
            patch("decepticon.middleware.scoped_reporting_io.build_sandbox_backend") as backend,
            patch("decepticon.tools.reporting.kg_adapter.load_engagement_graph") as graph,
            pytest.raises(ReportingScopeError),
        ):
            _invoke_report_tool(state)
        backend.assert_not_called()
        graph.assert_not_called()

    def test_run_config_disagreement_refuses_before_io(self) -> None:
        with (
            patch("decepticon.middleware.scoped_reporting_io.build_sandbox_backend") as backend,
            patch("decepticon.tools.reporting.kg_adapter.load_engagement_graph") as graph,
            pytest.raises(ReportingScopeError, match="run config and engagement state mismatch"),
        ):
            asyncio.run(
                report_sarif.ainvoke(
                    {
                        "engagement_id": "eng-X",
                        "output_path": "/workspace/out.sarif",
                        "state": {
                            "engagement_name": "sarif-case",
                            "workspace_path": "/workspace/sarif-case",
                        },
                    },
                    config={"configurable": {"workspace_path": "/workspace/other-case"}},
                )
            )
        backend.assert_not_called()
        graph.assert_not_called()
