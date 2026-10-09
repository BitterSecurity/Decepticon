"""Vulnerability research package — Neo4j-native attack graph.

High-value capabilities for 0-day discovery and exploit chain construction:

- ``graph``  — KnowledgeGraph: Pydantic model for attack graph nodes and edges
- ``neo4j_store`` — Neo4j persistence with MERGE-based upserts and native labels
- ``_state`` — Singleton Neo4jStore access for tool modules
- ``cve``    — CVE/OSV/EPSS intelligence lookup with EPSS-weighted scoring
- ``sarif``  — SARIF ingestion (semgrep, bandit, gitleaks, trivy, nuclei)
- ``chain``  — Attack path planner (Cypher-native graph search)
- ``poc``    — PoC reproducer validator with CVSS estimation
- ``fuzz``   — Fuzzing orchestration (libFuzzer, AFL++, jazzer, boofuzz)
- ``tools``  — LangChain @tool wrappers exposing all of the above to agents

State is managed exclusively through Neo4j. Configure via environment:
  DECEPTICON_NEO4J_URI, DECEPTICON_NEO4J_USER, DECEPTICON_NEO4J_PASSWORD
"""

from __future__ import annotations

from decepticon.tools.research.subdomain_takeover import SUBDOMAIN_TAKEOVER_TOOLS
from decepticon_core.types.kg import (
    Edge,
    EdgeKind,
    KnowledgeGraph,
    Node,
    NodeKind,
    Severity,
)

__all__ = [
    "Edge",
    "EdgeKind",
    "KnowledgeGraph",
    "Node",
    "NodeKind",
    "Severity",
    "SUBDOMAIN_TAKEOVER_TOOLS",
]
