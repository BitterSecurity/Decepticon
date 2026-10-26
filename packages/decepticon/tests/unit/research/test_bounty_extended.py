"""Extended unit tests for bounty.py — scope check and report generation.

Mocks KG _load/_save via monkeypatch so no Neo4j or Docker is needed.
"""

from __future__ import annotations

import json
import sys
from typing import Any
from unittest.mock import MagicMock

# ── Stub heavy transitive imports (same pattern as test_bounty.py) ────────


def _ensure_stubs() -> None:
    stubs = [
        "deepagents",
        "deepagents.middleware",
        "deepagents.middleware.patch_tool_calls",
        "deepagents.middleware.summarization",
        "deepagents.middleware.subagents",
        "deepagents.backends",
        "deepagents.backends.protocol",
        "deepagents.backends.sandbox",
        "langchain",
        "langchain.agents",
        "langchain.agents.middleware",
        "langchain_anthropic",
        "langchain_anthropic.middleware",
        "docker",
        "docker.models",
        "docker.models.containers",
        "docker.errors",
        "neo4j",
    ]
    for name in stubs:
        if name not in sys.modules:
            sys.modules[name] = MagicMock()


_ensure_stubs()

import pytest  # noqa: E402

from decepticon.tools.research import _state as state  # noqa: E402
from decepticon.tools.research.bounty import bounty_scope_check, format_bounty_report  # noqa: E402
from decepticon_core.types.kg import Edge, EdgeKind, KnowledgeGraph, Node, NodeKind  # noqa: E402

# ── Fixtures ──────────────────────────────────────────────────────────────


def _configure_kg(monkeypatch: pytest.MonkeyPatch) -> KnowledgeGraph:
    graph = KnowledgeGraph()

    class _FakeStore:
        def load_graph(self) -> KnowledgeGraph:
            return graph.model_copy(deep=True)

        def batch_upsert_nodes(self, nodes: list[Any]) -> int:
            for n in nodes:
                graph.upsert_node(n)
            return len(nodes)

        def batch_upsert_edges(self, edges: list[Any]) -> int:
            for e in edges:
                graph.upsert_edge(e)
            return len(edges)

        def ensure_schema(self) -> None:
            pass

        def close(self) -> None:
            pass

        def revision(self) -> float:
            return 0.0

        def stats(self) -> Any:
            return graph.stats()

        def upsert_node(self, node: Any) -> None:
            graph.upsert_node(node)

        def upsert_edge(self, edge: Any) -> None:
            graph.upsert_edge(edge)

        def query_custom(self, cypher: str, params: dict[str, Any]) -> list[Any]:
            return []

    monkeypatch.setattr(state, "_store", _FakeStore())
    return graph


# ── bounty_scope_check ───────────────────────────────────────────────────


class TestBountyScopeCheck:
    def test_clean_target_and_class_is_in_scope(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _configure_kg(monkeypatch)
        raw = bounty_scope_check.invoke(
            {
                "target": "api.example.com",
                "vuln_class": "sqli",
            }
        )
        result = json.loads(raw)
        assert result["in_scope"] is True
        assert result["warnings"] == []

    def test_explicitly_excluded_class_is_out_of_scope(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _configure_kg(monkeypatch)
        raw = bounty_scope_check.invoke(
            {
                "target": "example.com",
                "vuln_class": "xss",
                "excluded_classes": '["xss", "dos"]',
            }
        )
        result = json.loads(raw)
        assert result["in_scope"] is False
        assert any("excluded" in w for w in result["warnings"])

    def test_commonly_excluded_class_adds_warning(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _configure_kg(monkeypatch)
        raw = bounty_scope_check.invoke(
            {
                "target": "example.com",
                "vuln_class": "clickjacking",
            }
        )
        result = json.loads(raw)
        # Commonly excluded but not explicitly — adds warning, not out of scope
        assert any("commonly excluded" in w for w in result["warnings"])

    def test_domain_mismatch_is_out_of_scope(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _configure_kg(monkeypatch)
        raw = bounty_scope_check.invoke(
            {
                "target": "evil.com",
                "vuln_class": "rce",
                "in_scope_domains": '["*.example.com", "api.example.com"]',
            }
        )
        result = json.loads(raw)
        assert result["in_scope"] is False
        assert any("does not match" in w for w in result["warnings"])

    def test_wildcard_domain_match(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _configure_kg(monkeypatch)
        raw = bounty_scope_check.invoke(
            {
                "target": "sub.example.com",
                "vuln_class": "ssrf",
                "in_scope_domains": '["*.example.com"]',
            }
        )
        result = json.loads(raw)
        assert result["in_scope"] is True

    @pytest.mark.parametrize("target", ["example.com", "a.b.example.com"])
    def test_wildcard_rejects_apex_and_deeper_hosts(
        self, monkeypatch: pytest.MonkeyPatch, target: str
    ) -> None:
        _configure_kg(monkeypatch)
        raw = bounty_scope_check.invoke(
            {
                "target": target,
                "vuln_class": "ssrf",
                "in_scope_domains": '["*.example.com"]',
            }
        )
        assert json.loads(raw)["in_scope"] is False

    def test_exact_domain_match(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _configure_kg(monkeypatch)
        raw = bounty_scope_check.invoke(
            {
                "target": "api.example.com",
                "vuln_class": "idor",
                "in_scope_domains": '["api.example.com"]',
            }
        )
        result = json.loads(raw)
        assert result["in_scope"] is True

    def test_normalised_vuln_class_matching(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _configure_kg(monkeypatch)
        raw = bounty_scope_check.invoke(
            {
                "target": "example.com",
                "vuln_class": "SQL Injection",
                "excluded_classes": '["sql-injection"]',
            }
        )
        result = json.loads(raw)
        assert result["in_scope"] is False

    def test_invalid_json_exclusions_treated_as_empty(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _configure_kg(monkeypatch)
        # Should not raise on malformed JSON
        raw = bounty_scope_check.invoke(
            {
                "target": "example.com",
                "vuln_class": "xss",
                "excluded_classes": "not-valid-json",
            }
        )
        result = json.loads(raw)
        assert "in_scope" in result

    def test_result_includes_node_id(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _configure_kg(monkeypatch)
        raw = bounty_scope_check.invoke(
            {
                "target": "example.com",
                "vuln_class": "rce",
            }
        )
        result = json.loads(raw)
        assert "node_id" in result
        assert isinstance(result["node_id"], str)

    def test_low_impact_class_adds_warning(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _configure_kg(monkeypatch)
        raw = bounty_scope_check.invoke(
            {
                "target": "example.com",
                "vuln_class": "version-disclosure",
            }
        )
        result = json.loads(raw)
        assert any("Low-impact" in w for w in result["warnings"])


# ── format_bounty_report ──────────────────────────────────────────────────


class TestFormatBountyReport:
    def _make_validated_finding(
        self,
        graph: KnowledgeGraph,
        cvss_score: float = 9.1,
        cvss_vector: str = "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H",
    ) -> tuple[Node, Node]:
        """Create a validated FINDING node + linked VULNERABILITY node."""
        vuln = Node.make(
            NodeKind.VULNERABILITY,
            "SQL Injection in login endpoint",
            key="vuln-sqli-1",
            cvss_score=cvss_score,
            cvss_vector=cvss_vector,
            cwe=["CWE-89"],
            file="app/login.py",
            line=42,
        )
        finding = Node.make(
            NodeKind.FINDING,
            "validated: SQL Injection in login endpoint",
            key="finding-sqli-1",
            validated=True,
            stdout_excerpt="1' OR '1'='1",
        )
        graph.upsert_node(vuln)
        graph.upsert_node(finding)
        graph.upsert_edge(Edge.make(finding.id, vuln.id, EdgeKind.VALIDATES))
        return finding, vuln

    def test_missing_finding_returns_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _configure_kg(monkeypatch)
        raw = format_bounty_report.invoke({"finding_id": "nonexistent-id-xyz"})
        result = json.loads(raw)
        assert "error" in result
        assert "not found" in result["error"]

    def test_non_finding_node_returns_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        graph = _configure_kg(monkeypatch)
        host = Node.make(NodeKind.HOST, "web server", key="host-1")
        graph.upsert_node(host)
        raw = format_bounty_report.invoke({"finding_id": host.id})
        result = json.loads(raw)
        assert "error" in result
        assert "not finding" in result["error"]

    def test_unvalidated_finding_returns_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        graph = _configure_kg(monkeypatch)
        finding = Node.make(
            NodeKind.FINDING,
            "unvalidated XSS",
            key="f-unval",
            validated=False,
        )
        graph.upsert_node(finding)
        raw = format_bounty_report.invoke({"finding_id": finding.id})
        result = json.loads(raw)
        assert "error" in result
        assert "not validated" in result["error"]

    def _invoke_with_mocked_path(
        self,
        finding_id: str,
        platform: str = "hackerone",
        program_name: str = "",
        component_name: str = "",
    ) -> dict[str, Any]:
        """Invoke format_bounty_report with the filesystem Path mocked out."""
        from unittest.mock import patch as _patch

        with _patch("decepticon.tools.research.bounty.Path") as mock_path_cls:
            mock_report_path = MagicMock()
            mock_path_cls.return_value = mock_report_path
            raw = format_bounty_report.invoke(
                {
                    "finding_id": finding_id,
                    "platform": platform,
                    "program_name": program_name,
                    "component_name": component_name,
                }
            )
        return json.loads(raw)

    def test_validated_finding_produces_report(self, monkeypatch: pytest.MonkeyPatch) -> None:
        graph = _configure_kg(monkeypatch)
        finding, _ = self._make_validated_finding(graph)
        result = self._invoke_with_mocked_path(
            finding.id,
            platform="hackerone",
            program_name="AcmeCorp",
            component_name="Login",
        )
        assert "title" in result
        assert "Login" in result["title"]
        assert result["severity"] == "Critical"
        assert result["cvss_score"] == 9.1

    def test_severity_critical_for_high_score(self, monkeypatch: pytest.MonkeyPatch) -> None:
        graph = _configure_kg(monkeypatch)
        finding, _ = self._make_validated_finding(graph, cvss_score=9.5)
        result = self._invoke_with_mocked_path(finding.id, component_name="API")
        assert result["severity"] == "Critical"

    def test_report_preview_included(self, monkeypatch: pytest.MonkeyPatch) -> None:
        graph = _configure_kg(monkeypatch)
        finding, _ = self._make_validated_finding(graph, cvss_score=7.5)
        result = self._invoke_with_mocked_path(finding.id)
        assert "preview" in result
        assert len(result["preview"]) <= 500

    def test_validated_prefix_stripped_from_title(self, monkeypatch: pytest.MonkeyPatch) -> None:
        graph = _configure_kg(monkeypatch)
        finding, _ = self._make_validated_finding(graph)
        result = self._invoke_with_mocked_path(finding.id, component_name="App")
        assert "validated:" not in result["title"]
        assert "rejected:" not in result["title"]
