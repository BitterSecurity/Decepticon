"""Unit tests for bug bounty tools — pure functions and constants.

NOTE: The full tool surface (``bounty_scope_check``, ``format_bounty_report``)
requires the ``deepagents`` runtime and Docker sandbox. These tests validate
the pure-function helpers and data structures that can run without the full
backend.
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

# ── Stub heavy transitive imports ────────────────────────────────────────
# ``decepticon.tools.research.bounty`` transitively pulls in
# ``decepticon.backends.http_transport`` which requires ``deepagents``.
# We stub just enough of the import chain so the pure functions load.


def _ensure_stubs() -> None:
    """Insert lightweight stubs for modules that require runtime infra."""
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

from decepticon.tools.research.bounty import (  # noqa: E402
    _COMMONLY_EXCLUDED,
    BOUNTY_TOOLS,
    _normalize_class,
    _severity_label,
)
from decepticon_core.types.kg import (  # noqa: E402
    Edge,
    EdgeKind,
    KnowledgeGraph,
    Node,
    NodeKind,
)


class TestNormalizeClass:
    def test_basic_normalization(self) -> None:
        assert _normalize_class("SQL Injection") == "sql-injection"
        assert _normalize_class("  Self XSS  ") == "self-xss"
        assert _normalize_class("Rate_Limiting") == "rate-limiting"

    def test_already_normalized(self) -> None:
        assert _normalize_class("ssrf") == "ssrf"
        assert _normalize_class("path-traversal") == "path-traversal"

    def test_empty_string(self) -> None:
        assert _normalize_class("") == ""


class TestSeverityLabel:
    def test_critical(self) -> None:
        assert _severity_label(9.8) == "Critical"
        assert _severity_label(9.0) == "Critical"

    def test_high(self) -> None:
        assert _severity_label(8.5) == "High"
        assert _severity_label(7.0) == "High"

    def test_medium(self) -> None:
        assert _severity_label(6.9) == "Medium"
        assert _severity_label(4.0) == "Medium"

    def test_low(self) -> None:
        assert _severity_label(3.9) == "Low"
        assert _severity_label(0.1) == "Low"

    def test_info(self) -> None:
        assert _severity_label(0.0) == "Informational"

    def test_boundary_values(self) -> None:
        assert _severity_label(10.0) == "Critical"
        assert _severity_label(7.0) == "High"  # exactly 7.0 is High
        assert _severity_label(4.0) == "Medium"  # exactly 4.0 is Medium


class TestCommonlyExcluded:
    def test_self_xss_excluded(self) -> None:
        assert "self-xss" in _COMMONLY_EXCLUDED

    def test_dos_excluded(self) -> None:
        assert "dos" in _COMMONLY_EXCLUDED
        assert "denial-of-service" in _COMMONLY_EXCLUDED

    def test_clickjacking_excluded(self) -> None:
        assert "clickjacking" in _COMMONLY_EXCLUDED

    def test_high_impact_classes_not_excluded(self) -> None:
        """RCE, SQLi, SSRF, XSS etc should never be in the exclusion set."""
        assert "rce" not in _COMMONLY_EXCLUDED
        assert "sqli" not in _COMMONLY_EXCLUDED
        assert "sql-injection" not in _COMMONLY_EXCLUDED
        assert "ssrf" not in _COMMONLY_EXCLUDED
        assert "xss" not in _COMMONLY_EXCLUDED
        assert "idor" not in _COMMONLY_EXCLUDED
        assert "path-traversal" not in _COMMONLY_EXCLUDED
        assert "rce" not in _COMMONLY_EXCLUDED


class TestFindLinkedVuln:
    """Test _find_linked_vuln with real graph objects."""

    def test_finds_via_validates_edge(self) -> None:
        from decepticon.tools.research.bounty import _find_linked_vuln

        graph = KnowledgeGraph()
        vuln = Node.make(NodeKind.VULNERABILITY, "test vuln", key="v1")
        finding = Node.make(NodeKind.FINDING, "test finding", key="f1")
        graph.upsert_node(vuln)
        graph.upsert_node(finding)
        graph.upsert_edge(Edge.make(finding.id, vuln.id, EdgeKind.VALIDATES))

        result = _find_linked_vuln(graph, finding.id)
        assert result is not None
        assert result.id == vuln.id

    def test_finds_via_maps_to_edge(self) -> None:
        from decepticon.tools.research.bounty import _find_linked_vuln

        graph = KnowledgeGraph()
        vuln = Node.make(NodeKind.VULNERABILITY, "test vuln", key="v2")
        finding = Node.make(NodeKind.FINDING, "test finding", key="f2")
        graph.upsert_node(vuln)
        graph.upsert_node(finding)
        graph.upsert_edge(Edge.make(finding.id, vuln.id, EdgeKind.MAPS_TO))

        result = _find_linked_vuln(graph, finding.id)
        assert result is not None
        assert result.id == vuln.id

    def test_returns_none_when_no_link(self) -> None:
        from decepticon.tools.research.bounty import _find_linked_vuln

        graph = KnowledgeGraph()
        finding = Node.make(NodeKind.FINDING, "orphan", key="f3")
        graph.upsert_node(finding)

        result = _find_linked_vuln(graph, finding.id)
        assert result is None


class TestBountyToolsExport:
    def test_bounty_tools_has_two_entries(self) -> None:
        assert len(BOUNTY_TOOLS) == 2

    def test_tool_names(self) -> None:
        names = {t.name for t in BOUNTY_TOOLS}
        assert "bounty_scope_check" in names
        assert "format_bounty_report" in names
