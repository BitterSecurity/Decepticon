"""LangChain ``@tool`` wrappers that expose the research package to agents.

These are the surfaces the Analyst (and optionally the Orchestrator)
exercise to drive vulnerability research:

- ``kg_*``         — CRUD + query over the knowledge graph
- ``cve_*``        — NVD/OSV/EPSS intelligence lookup
- ``ingest_sarif`` — lift any SARIF file on disk into the graph
- ``plan_attack_chains`` — ranked multi-hop exploit paths
- ``fuzz_*``       — harness synthesis + crash recording helpers

Every tool returns a compact, JSON-serialisable string so it fits the
LangChain tool return contract and keeps LLM token usage low.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import defusedxml.ElementTree as ET
from langchain_core.tools import tool

from decepticon.tools.contracts.patterns import scan_solidity_source
from decepticon.tools.contracts.slither import ingest_slither_file
from decepticon.tools.research import cve as cve_mod
from decepticon.tools.research import fuzz as fuzz_mod
from decepticon.tools.research.chain import critical_path_score, plan_chains, promote_chain
from decepticon.tools.research.dedupe import kg_dedupe_findings
from decepticon.tools.research.health import backend_health
from decepticon.tools.research.osint import OSINT_TOOLS
from decepticon.tools.research.patch import PATCH_TOOLS
from decepticon.tools.research.sarif import ingest_sarif_file
from decepticon.tools.research.scanner_tools import SCANNER_TOOLS
from decepticon.tools.research.secret_scanner import scan_secrets
from decepticon.tools.research.tech_detection import detect_tech_stack
from decepticon.tools.reversing.binary import identify_binary
from decepticon.tools.reversing.packer import detect_packer
from decepticon.tools.reversing.strings import extract_strings, group_by_category
from decepticon.tools.reversing.symbols import summarize_symbols
from decepticon.tools.web.jwt import parse_token
from decepticon.tools.web.oauth import analyze_oauth_callback
from decepticon.tools.web.session import analyze_cookie
from decepticon_core.types.kg import (
    SEVERITY_SCORE,
    Edge,
    EdgeKind,
    KnowledgeGraph,
    Node,
    NodeKind,
    Severity,
)
from decepticon_core.utils.logging import get_logger

log = get_logger("research.tools")


# ── Helpers ─────────────────────────────────────────────────────────────
#
# ``_load`` / ``_save`` / ``_json`` are compatibility wrappers in
# ``_state.py`` that route through the Neo4j store. All 40+ call sites
# in this file use them unchanged.
from decepticon.tools.research._state import (  # noqa: E402
    _json,
    _kg_backend_name,
    graph_transaction,
    kg_degrades,
)


def _parse_props(props_json: str) -> dict[str, Any]:
    if not props_json:
        return {}
    try:
        parsed = json.loads(props_json)
    except json.JSONDecodeError as e:
        raise ValueError(f"props must be valid JSON: {e}") from None
    if not isinstance(parsed, dict):
        raise ValueError("props must be a JSON object")
    return parsed


def _severity_from_score(score: float) -> Severity:
    if score >= 9.0:
        return Severity.CRITICAL
    if score >= 7.0:
        return Severity.HIGH
    if score >= 4.0:
        return Severity.MEDIUM
    if score > 0.0:
        return Severity.LOW
    return Severity.INFO


def _severity_from_string(value: str | None) -> Severity:
    if not value:
        return Severity.MEDIUM
    normalized = value.strip().lower()
    mapping = {
        "critical": Severity.CRITICAL,
        "high": Severity.HIGH,
        "medium": Severity.MEDIUM,
        "low": Severity.LOW,
        "info": Severity.INFO,
        "informational": Severity.INFO,
    }
    return mapping.get(normalized, Severity.MEDIUM)


def _is_web_port(port: int) -> bool:
    return port in {80, 81, 443, 3000, 5000, 7001, 8000, 8008, 8080, 8443, 8888}


def _severity_threshold(sev: Severity) -> float:
    return SEVERITY_SCORE.get(sev, 0.0)


def _jwt_finding_severity(finding: str) -> Severity:
    text = finding.lower()
    if "alg=none" in text:
        return Severity.CRITICAL
    if "key confusion" in text or "path traversal" in text:
        return Severity.HIGH
    if "no exp" in text or "expired" in text:
        return Severity.MEDIUM
    return Severity.LOW


def _cookie_finding_severity(finding: str) -> Severity:
    text = finding.lower()
    if "predictable session" in text:
        return Severity.HIGH
    if "httponly not set" in text or "samesite" in text:
        return Severity.MEDIUM
    if "secure flag not set" in text:
        return Severity.MEDIUM
    return Severity.LOW


def _ensure_host_node(
    graph: KnowledgeGraph,
    *,
    label: str,
    key: str,
    **props: Any,
) -> Node:
    return graph.upsert_node(Node.make(NodeKind.HOST, label, key=key, **props))


def _ensure_service_node(
    graph: KnowledgeGraph,
    *,
    host: Node,
    host_label: str,
    port: int,
    proto: str,
    **props: Any,
) -> Node:
    label = f"{host_label}:{port}/{proto}"
    service = graph.upsert_node(
        Node.make(
            NodeKind.SERVICE,
            label,
            key=f"service::{host_label}:{port}/{proto}",
            host=host_label,
            port=port,
            protocol=proto,
            **props,
        )
    )
    graph.upsert_edge(Edge.make(host.id, service.id, EdgeKind.EXPOSES, weight=0.6))
    graph.upsert_edge(Edge.make(service.id, host.id, EdgeKind.HOSTS, weight=0.6))
    return service


def _ensure_entrypoint_node(
    graph: KnowledgeGraph,
    *,
    host_label: str,
    port: int,
    source: str,
) -> Node:
    scheme = "https" if port in {443, 8443} else "http"
    default_port = (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
    endpoint = f"{scheme}://{host_label}/" if default_port else f"{scheme}://{host_label}:{port}/"
    return graph.upsert_node(
        Node.make(
            NodeKind.ENTRYPOINT,
            endpoint,
            key=f"entrypoint::{endpoint}",
            source=source,
            host=host_label,
            port=port,
            scheme=scheme,
        )
    )


def _iter_requirements(path: Path) -> list[tuple[str, str, str]]:
    deps: list[tuple[str, str, str]] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        line = line.split(";", 1)[0].strip()
        line = line.split("#", 1)[0].strip()
        m = re.match(r"([A-Za-z0-9_.\-]+)\s*==\s*([A-Za-z0-9_.\-+]+)$", line)
        if m:
            deps.append((m.group(1), m.group(2), "PyPI"))
    return deps


def _iter_package_lock(path: Path) -> list[tuple[str, str, str]]:
    deps: list[tuple[str, str, str]] = []
    payload = json.loads(path.read_text(encoding="utf-8"))

    packages = payload.get("packages")
    if isinstance(packages, dict):
        for pkg_path, meta in packages.items():
            if not pkg_path.startswith("node_modules/"):
                continue
            if not isinstance(meta, dict):
                continue
            name = meta.get("name") or pkg_path.rsplit("node_modules/", 1)[-1]
            version = meta.get("version")
            if isinstance(name, str) and isinstance(version, str):
                deps.append((name, version, "npm"))
        return deps

    # npm lockfile v1 fallback
    stack = [payload.get("dependencies", {})]
    while stack:
        cur = stack.pop()
        if not isinstance(cur, dict):
            continue
        for name, meta in cur.items():
            if not isinstance(meta, dict):
                continue
            version = meta.get("version")
            if isinstance(name, str) and isinstance(version, str):
                deps.append((name, version, "npm"))
            nested = meta.get("dependencies")
            if isinstance(nested, dict):
                stack.append(nested)
    return deps


def _iter_go_sum(path: Path) -> list[tuple[str, str, str]]:
    deps: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str]] = set()
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        module = parts[0]
        version = parts[1]
        if module.endswith("/go.mod"):
            module = module[: -len("/go.mod")]
        if version.endswith("/go.mod"):
            version = version[: -len("/go.mod")]
        key = (module, version)
        if key in seen:
            continue
        seen.add(key)
        deps.append((module, version, "Go"))
    return deps


def _iter_cargo_lock(path: Path) -> list[tuple[str, str, str]]:
    deps: list[tuple[str, str, str]] = []
    # Cargo.lock is TOML-like; avoid external deps with a tiny parser.
    current_name: str | None = None
    current_ver: str | None = None
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line == "[[package]]":
            if current_name and current_ver:
                deps.append((current_name, current_ver, "crates.io"))
            current_name = None
            current_ver = None
            continue
        if line.startswith("name = "):
            current_name = line.split("=", 1)[1].strip().strip('"')
        elif line.startswith("version = "):
            current_ver = line.split("=", 1)[1].strip().strip('"')
    if current_name and current_ver:
        deps.append((current_name, current_ver, "crates.io"))
    return deps


def _parse_dependencies(path: Path) -> list[tuple[str, str, str]]:
    name = path.name.lower()
    if name == "requirements.txt":
        return _iter_requirements(path)
    if name == "package-lock.json":
        return _iter_package_lock(path)
    if name == "go.sum":
        return _iter_go_sum(path)
    if name == "cargo.lock":
        return _iter_cargo_lock(path)
    return []


def _dependency_provenance(
    dep: tuple[str, str, str],
    *,
    manifest_path: str | None,
    chains: dict[tuple[str, str, str], list[str]],
) -> dict[str, Any]:
    name, version, ecosystem = dep
    level = "declared" if ecosystem == "PyPI" else "resolved"
    props: dict[str, Any] = {
        "reachability_level": level,
        "dependency_chain": chains.get(dep, [f"{name}@{version}"]),
    }
    if manifest_path is not None:
        props["manifest_path"] = manifest_path
    return props


_EVIDENCE_SEVERITY_CEILING = {
    "declared": Severity.LOW,
    "resolved": Severity.MEDIUM,
    "symbol-matched": Severity.HIGH,
    "call-path": Severity.HIGH,
    "runtime-observed": Severity.CRITICAL,
}


def _reported_severity(score: float, evidence_level: str) -> tuple[Severity, Severity]:
    raw = _severity_from_score(score)
    ceiling = _EVIDENCE_SEVERITY_CEILING[evidence_level]
    return raw, raw if SEVERITY_SCORE[raw] <= SEVERITY_SCORE[ceiling] else ceiling


def _dependency_chains(path: Path) -> dict[tuple[str, str, str], list[str]]:
    if path.name.lower() != "package-lock.json":
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    packages = payload.get("packages")
    if not isinstance(packages, dict):
        return {}
    chains: dict[tuple[str, str, str], list[str]] = {}
    for package_path, meta in packages.items():
        if not package_path.startswith("node_modules/") or not isinstance(meta, dict):
            continue
        name = meta.get("name") or package_path.rsplit("node_modules/", 1)[-1]
        version = meta.get("version")
        if not isinstance(name, str) or not isinstance(version, str):
            continue
        chain = [part.strip("/") for part in package_path.split("node_modules/")[1:] if part]
        chains[(name, version, "npm")] = chain or [f"{name}@{version}"]
    return chains


def _manifest_path(path: Path) -> str | None:
    workspace = Path(os.getenv("DECEPTICON_WORKSPACE_PATH", Path.cwd())).resolve()
    try:
        return path.resolve().relative_to(workspace).as_posix()
    except ValueError:
        return None


# ── Knowledge graph tools ───────────────────────────────────────────────


@tool
@kg_degrades
def kg_add_node(kind: str, label: str, props: str = "{}") -> str:
    """Insert or update a node in the engagement knowledge graph.

    WHEN TO USE: Every time you observe an asset, vulnerability, credential,
    entrypoint, crown jewel, or code location. The graph persists across
    Ralph iterations, so a node you add now is queryable by the next
    fresh-context agent.

    NODE KINDS: host, service, url, repo, file, code_location, vulnerability,
    cve, finding, credential, secret, user, entrypoint, crown_jewel, chain,
    hypothesis.

    IMPORTANT: Use ``props`` to store severity, file path, port, cwe, cvss,
    etc. Supply a deterministic ``key`` inside props for deduplication
    (e.g. ``"key": "10.0.0.1:443/tcp"``).

    Args:
        kind: Node type (see NODE KINDS above).
        label: Human-readable label shown in graph summaries.
        props: JSON object with extra fields. Example:
            ``{"severity": "high", "cwe": ["CWE-89"], "file": "app.py", "line": 42}``

    Returns:
        JSON with the created/updated node id and stats.
    """
    try:
        node_kind = NodeKind(kind)
    except ValueError:
        return _json({"error": f"unknown kind: {kind}", "valid": [k.value for k in NodeKind]})
    parsed = _parse_props(props)
    with graph_transaction() as graph:
        node = graph.upsert_node(Node.make(node_kind, label, **parsed))
        return _json(
            {"id": node.id, "kind": node.kind.value, "label": node.label, "stats": graph.stats()}
        )


@tool
@kg_degrades
def kg_add_edge(src: str, dst: str, kind: str, weight: float = 1.0) -> str:
    """Connect two nodes with a typed, weighted edge.

    WHEN TO USE: After adding nodes, connect them to express relationships
    the chain planner can walk: ``runs_on``, ``has_vuln``, ``enables``,
    ``leaks``, ``grants``, ``chains_to``, etc.

    WEIGHT guides the chain planner — lower = easier to exploit. Defaults
    to 1.0. Use 0.3 for trivial wins, 2.0 for painful pivots.

    EDGE KINDS: runs_on, exposes, has_vuln, defined_in, located_at,
    affected_by, mapped_to, auth_as, grants, leaks, enables, chains_to,
    reaches, starts_at, contains, validates.

    Args:
        src: Source node id (from kg_add_node return value).
        dst: Destination node id.
        kind: Edge type.
        weight: Traversal cost (lower = easier exploitation).

    Returns:
        JSON with edge id and updated graph stats.
    """
    try:
        edge_kind = EdgeKind(kind)
    except ValueError:
        return _json({"error": f"unknown edge kind: {kind}", "valid": [k.value for k in EdgeKind]})
    with graph_transaction() as graph:
        if src not in graph.nodes or dst not in graph.nodes:
            return _json(
                {
                    "error": "src or dst not in graph",
                    "src_present": src in graph.nodes,
                    "dst_present": dst in graph.nodes,
                }
            )
        edge = graph.upsert_edge(Edge.make(src, dst, edge_kind, weight=weight))
        return _json({"id": edge.id, "kind": edge.kind.value, "stats": graph.stats()})


@tool
@kg_degrades
def kg_query(kind: str = "", min_severity: str = "", limit: int = 25) -> str:
    """Query the knowledge graph for nodes matching kind / severity.

    WHEN TO USE: At the start of any iteration to discover what's already
    known. Before running a scanner, check if the target is already
    enumerated. Before exploiting, check for existing finding nodes.

    Args:
        kind: Node kind filter (empty = all kinds).
        min_severity: For vulnerability nodes only. Empty, low, medium,
            high, or critical. If set, only vulns meeting the bar are
            returned.
        limit: Max nodes to return (default 25).

    Returns:
        JSON list of matching nodes with their core fields and id.
    """
    with graph_transaction() as graph:
        if min_severity:
            try:
                sev = Severity(min_severity.lower())
            except ValueError:
                return _json({"error": f"bad severity: {min_severity}"})
            nodes = graph.vulnerabilities_by_severity(sev)
        elif kind:
            try:
                node_kind = NodeKind(kind)
            except ValueError:
                return _json({"error": f"unknown kind: {kind}"})
            nodes = graph.by_kind(node_kind)
        else:
            nodes = list(graph.nodes.values())

        return _json(
            {
                "total": len(nodes),
                "returned": min(len(nodes), limit),
                "nodes": [
                    {
                        "id": n.id,
                        "kind": n.kind.value,
                        "label": n.label,
                        "props": n.props,
                    }
                    for n in nodes[:limit]
                ],
            }
        )


@tool
@kg_degrades
def kg_neighbors(node_id: str, direction: str = "out", edge_kind: str = "") -> str:
    """Walk one hop out from a node to see what it connects to.

    Args:
        node_id: Source node id.
        direction: "out" (default), "in", or "both".
        edge_kind: Optional edge-kind filter.

    Returns:
        JSON list of {edge, neighbor} pairs.
    """
    with graph_transaction() as graph:
        if node_id not in graph.nodes:
            return _json({"error": "node not found", "id": node_id})
        filter_kind: EdgeKind | None = None
        if edge_kind:
            try:
                filter_kind = EdgeKind(edge_kind)
            except ValueError:
                return _json({"error": f"unknown edge kind: {edge_kind}"})
        neighbors = graph.neighbors(node_id, edge_kind=filter_kind, direction=direction)
        return _json(
            [
                {
                    "edge_kind": e.kind.value,
                    "edge_weight": e.weight,
                    "neighbor_id": n.id,
                    "neighbor_kind": n.kind.value,
                    "neighbor_label": n.label,
                }
                for e, n in neighbors
            ]
        )


@tool
@kg_degrades
def kg_stats() -> str:
    """Return counts of nodes and edges by kind. Cheapest way to sanity check
    graph state at iteration start. Returns JSON stats dict."""
    with graph_transaction() as graph:
        return _json({"backend": _kg_backend_name(), **graph.stats()})


@tool
@kg_degrades
def kg_backend_health() -> str:
    """Report KnowledgeGraph backend health/startup diagnostics.

    Use at session start (or when graph writes fail) to verify whether the
    configured backend is reachable and returning graph stats.
    """
    return _json(backend_health())


# ── CVE intelligence ────────────────────────────────────────────────────


@tool
async def cve_lookup(cve_ids: str) -> str:
    """Look up CVEs against NVD + EPSS with real-world exploitability scoring.

    WHEN TO USE: Whenever you find a service version (nmap -sV),
    dependency (package.json, requirements.txt, Cargo.lock), or CVE ID
    from any source. Returns a ranked list: CVEs with high CVSS *and*
    high EPSS (or KEV listing) bubble to the top.

    The composite ``score`` blends:
    - CVSS base (0-10)
    - EPSS probability (log-scaled)
    - CISA KEV membership (floors score at 9.0)

    Args:
        cve_ids: Comma-separated CVE IDs, e.g. ``"CVE-2024-12345,CVE-2023-99999"``.

    Returns:
        JSON list of exploitability records, highest score first.
    """
    ids = [c.strip() for c in cve_ids.split(",") if c.strip()]
    if not ids:
        return _json({"error": "no CVE IDs provided"})
    records = await cve_mod.lookup_cves(ids)
    return _json([r.to_dict() for r in records])


@tool
async def cve_by_package(package: str, version: str, ecosystem: str = "PyPI") -> str:
    """Query OSV for CVEs affecting ``package@version`` in an ecosystem.

    WHEN TO USE: After reading a manifest file (requirements.txt,
    package.json, go.sum, Cargo.lock). Pair with ``cve_lookup`` to score
    the results and prioritise bounty-worthy targets.

    Args:
        package: Package name (exact, case-sensitive).
        version: Installed version string.
        ecosystem: One of PyPI, npm, crates.io, Go, Maven, RubyGems,
            NuGet, Packagist, Pub, Hex.

    Returns:
        JSON list of vulnerability IDs (CVE/GHSA). Empty if the package
        version is clean (or the OSV API was unreachable).
    """
    ids = await cve_mod.lookup_package(package, version, ecosystem)
    return _json({"package": package, "version": version, "ecosystem": ecosystem, "ids": ids})


@tool
async def cve_enrich_dependencies(path: str, limit: int = 100, min_score: float = 7.0) -> str:
    """Parse a lockfile/manifest and enrich the graph with ranked CVE findings.

    Supported files:
      - requirements.txt
      - package-lock.json
      - go.sum
      - Cargo.lock
    """
    dep_path = Path(path)
    if not dep_path.exists():
        return _json({"error": f"file not found: {path}"})

    try:
        deps = _parse_dependencies(dep_path)
    except (OSError, ValueError, json.JSONDecodeError) as e:
        return _json({"error": f"dependency parse failed: {e}"})
    manifest_path = _manifest_path(dep_path)
    chains = _dependency_chains(dep_path)

    # Deduplicate and cap work for bounded runtime.
    dedup: dict[tuple[str, str, str], None] = {}
    for dep in deps:
        dedup[dep] = None
    planned = list(dedup.keys())[: max(limit, 1)]
    if not planned:
        return _json({"error": f"unsupported or empty dependency file: {dep_path.name}"})

    added = 0
    kept: list[dict[str, Any]] = []

    semaphore = asyncio.Semaphore(8)

    async def _lookup(
        dep: tuple[str, str, str],
    ) -> tuple[tuple[str, str, str], list[dict[str, Any]]]:
        name, version, ecosystem = dep
        async with semaphore:
            vuln_ids = await cve_mod.lookup_package(name, version, ecosystem)
        cve_ids = sorted(
            {vid for vid in vuln_ids if isinstance(vid, str) and vid.startswith("CVE-")}
        )
        if not cve_ids:
            return dep, []
        records = await cve_mod.lookup_cves(cve_ids, concurrency=6)
        return dep, [r.to_dict() for r in records if r.score >= min_score]

    results = await asyncio.gather(*[_lookup(dep) for dep in planned])

    with graph_transaction() as graph:
        for dep, records in results:
            name, version, ecosystem = dep
            provenance = _dependency_provenance(
                dep,
                manifest_path=manifest_path,
                chains=chains,
            )
            dep_node = graph.upsert_node(
                Node.make(
                    NodeKind.SERVICE,
                    f"{name}@{version}",
                    key=f"dependency::{ecosystem}::{name}@{version}",
                    component_type="dependency",
                    package=name,
                    version=version,
                    ecosystem=ecosystem,
                    source="dependency-enricher",
                    **provenance,
                )
            )

            for rec in records:
                cve_id = str(rec.get("cve_id") or "")
                if not cve_id.startswith("CVE-"):
                    continue
                score = float(rec.get("score") or 0.0)
                raw_severity, severity = _reported_severity(
                    score, str(provenance["reachability_level"])
                )
                cve_node = graph.upsert_node(
                    Node.make(
                        NodeKind.CVE,
                        cve_id,
                        key=f"cve::{cve_id}",
                        cvss=rec.get("cvss"),
                        epss=rec.get("epss"),
                        kev=rec.get("kev"),
                        score=score,
                        source="nvd+epss+osv",
                    )
                )
                vuln = graph.upsert_node(
                    Node.make(
                        NodeKind.VULNERABILITY,
                        f"{name}@{version} affected by {cve_id}",
                        key=f"dep-vuln::{ecosystem}::{name}@{version}::{cve_id}",
                        package=name,
                        version=version,
                        ecosystem=ecosystem,
                        cve_id=cve_id,
                        severity=severity.value,
                        raw_severity=raw_severity.value,
                        cvss=rec.get("cvss"),
                        cvss_vector=rec.get("cvss_vector"),
                        epss=rec.get("epss"),
                        epss_percentile=rec.get("epss_percentile"),
                        kev=rec.get("kev"),
                        score=score,
                        summary=rec.get("summary", ""),
                        references=rec.get("references", []),
                        source="dependency-enricher",
                        **provenance,
                    )
                )
                graph.upsert_edge(Edge.make(dep_node.id, cve_node.id, EdgeKind.AFFECTS, weight=0.5))
                graph.upsert_edge(Edge.make(dep_node.id, vuln.id, EdgeKind.HAS_VULN, weight=0.5))
                graph.upsert_edge(Edge.make(vuln.id, cve_node.id, EdgeKind.MAPS_TO, weight=0.5))
                kept.append(
                    {
                        "dependency": f"{name}@{version}",
                        "ecosystem": ecosystem,
                        "cve": cve_id,
                        "score": score,
                        "severity": severity.value,
                        "raw_severity": raw_severity.value,
                        "kev": bool(rec.get("kev")),
                        "dependency_chain": provenance["dependency_chain"],
                        "reachability_level": provenance["reachability_level"],
                    }
                )
                added += 1

    kept.sort(key=lambda x: x["score"], reverse=True)
    response: dict[str, Any] = {
        "dependencies_scanned": len(planned),
        "high_signal_records": added,
        "results": kept[:100],
        "stats": graph.stats(),
    }
    if manifest_path is not None:
        response["manifest_path"] = manifest_path
    return _json(response)


# ── Static analysis ingestion ───────────────────────────────────────────


@tool
@kg_degrades
def kg_ingest_sarif(path: str, scanner_hint: str = "") -> str:
    """Ingest a SARIF report (semgrep, bandit, gitleaks, trivy, codeql) into the graph.

    WHEN TO USE: After running any SARIF-emitting scanner. Lifts every
    ``result`` in the file into a Vulnerability node linked to its
    CodeLocation and File, so the chain planner can reason about
    source-level bugs.

    EXAMPLES:
        semgrep --sarif --config=auto /workspace/src > /workspace/semgrep.sarif
        bandit -r /workspace/src -f sarif -o /workspace/bandit.sarif
        gitleaks detect --source /workspace/src --report-format sarif --report-path /workspace/gitleaks.sarif

    Args:
        path: Absolute path to the SARIF file (must be inside the sandbox
            workspace or host bind mount).
        scanner_hint: Override the scanner name. Useful when the SARIF
            driver name is anonymised or mislabeled.

    Returns:
        JSON with the ingested result count and updated graph stats.
    """
    hint = scanner_hint or None
    with graph_transaction() as graph:
        n = ingest_sarif_file(path, graph, scanner_hint=hint)
        return _json({"ingested": n, "stats": graph.stats()})


# ── Recon report ingestion ──────────────────────────────────────────────


@tool
@kg_degrades
def kg_ingest_nmap_xml(path: str, scanner_hint: str = "nmap") -> str:
    """Ingest Nmap XML output into the knowledge graph.

    This creates host/service nodes and adds entrypoint nodes for common web
    ports so chain planning can start from externally reachable surfaces.
    """
    with graph_transaction() as graph:
        try:
            root = ET.parse(path).getroot()
        except (OSError, ET.ParseError) as e:
            return _json({"error": f"failed to parse nmap xml: {e}"})
        if root is None:
            return _json({"error": "failed to parse nmap xml: empty document"})

        hosts_added = 0
        services_added = 0
        entrypoints_added = 0

        for host_el in root.findall("host"):
            status = host_el.find("status")
            if status is not None and status.get("state") not in {None, "up"}:
                continue

            addr_el = host_el.find("address[@addrtype='ipv4']")
            if addr_el is None:
                addr_el = host_el.find("address")
            if addr_el is None:
                continue
            ip = addr_el.get("addr")
            if not ip:
                continue

            hostname_el = host_el.find("hostnames/hostname")
            hostname = hostname_el.get("name") if hostname_el is not None else ""
            host_label = hostname or ip
            host = _ensure_host_node(
                graph,
                label=host_label,
                key=f"host::{ip}",
                ip=ip,
                hostname=hostname,
                source=scanner_hint,
            )
            hosts_added += 1

            for port_el in host_el.findall("ports/port"):
                state_el = port_el.find("state")
                if state_el is None or state_el.get("state") != "open":
                    continue
                try:
                    port = int(port_el.get("portid", "0"))
                except ValueError:
                    continue
                proto = port_el.get("protocol", "tcp")
                service_el = port_el.find("service")
                service_name = service_el.get("name") if service_el is not None else "unknown"
                product = service_el.get("product") if service_el is not None else ""
                version = service_el.get("version") if service_el is not None else ""

                service = _ensure_service_node(
                    graph,
                    host=host,
                    host_label=host_label,
                    port=port,
                    proto=proto,
                    source=scanner_hint,
                    service=service_name,
                    product=product,
                    version=version,
                )
                services_added += 1

                if _is_web_port(port):
                    ep = _ensure_entrypoint_node(
                        graph,
                        host_label=hostname or ip,
                        port=port,
                        source=scanner_hint,
                    )
                    graph.upsert_edge(Edge.make(service.id, ep.id, EdgeKind.EXPOSES, weight=0.5))
                    graph.upsert_edge(Edge.make(ep.id, service.id, EdgeKind.HOSTS, weight=0.5))
                    entrypoints_added += 1

        return _json(
            {
                "ingested": {
                    "hosts": hosts_added,
                    "services": services_added,
                    "entrypoints": entrypoints_added,
                },
                "stats": graph.stats(),
            }
        )


@tool
@kg_degrades
def kg_ingest_nuclei_jsonl(path: str, scanner_hint: str = "nuclei") -> str:
    """Ingest Nuclei JSONL output into the knowledge graph.

    Expected format is one JSON object per line (`nuclei -jsonl` output).
    """
    p = Path(path)
    if not p.exists():
        return _json({"error": f"file not found: {path}"})

    with graph_transaction() as graph:
        parsed = 0
        skipped = 0

        for raw_line in p.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                skipped += 1
                continue

            info = record.get("info") if isinstance(record.get("info"), dict) else {}
            severity = _severity_from_string(info.get("severity"))
            rule_id = str(record.get("template-id") or "unknown-template")
            target = str(record.get("matched-at") or record.get("host") or "unknown-target")
            parsed += 1

            vuln = graph.upsert_node(
                Node.make(
                    NodeKind.VULNERABILITY,
                    f"[{scanner_hint}:{rule_id}] {target}",
                    key=f"{scanner_hint}::{rule_id}::{target}",
                    scanner=scanner_hint,
                    rule_id=rule_id,
                    severity=severity.value,
                    type=record.get("type"),
                    matcher_name=record.get("matcher-name"),
                    message=record.get("matched-at") or target,
                    tags=(info.get("tags") if isinstance(info.get("tags"), list) else []),
                )
            )

            parsed_target = urlparse(target)
            if parsed_target.scheme and parsed_target.netloc:
                url_node = graph.upsert_node(
                    Node.make(
                        NodeKind.URL,
                        target,
                        key=f"url::{target}",
                        source=scanner_hint,
                    )
                )
                target_node = graph.upsert_node(
                    Node.make(
                        NodeKind.ENTRYPOINT,
                        target,
                        key=f"entrypoint::{target}",
                        source=scanner_hint,
                        scheme=parsed_target.scheme,
                        host=parsed_target.hostname,
                        port=parsed_target.port,
                    )
                )
                graph.upsert_edge(
                    Edge.make(target_node.id, url_node.id, EdgeKind.HOSTS, weight=0.5)
                )
                graph.upsert_edge(
                    Edge.make(url_node.id, target_node.id, EdgeKind.EXPOSES, weight=0.5)
                )
            else:
                host_label = target.split(":", 1)[0]
                target_node = _ensure_host_node(
                    graph,
                    label=host_label,
                    key=f"host::{host_label}",
                    source=scanner_hint,
                )
            graph.upsert_edge(Edge.make(target_node.id, vuln.id, EdgeKind.HAS_VULN, weight=0.4))

            classification = info.get("classification")
            if isinstance(classification, dict):
                cve_ids = classification.get("cve-id")
                if isinstance(cve_ids, str):
                    cve_ids = [cve_ids]
                if isinstance(cve_ids, list):
                    for cve_id in cve_ids:
                        if not isinstance(cve_id, str):
                            continue
                        cid = cve_id.strip().upper()
                        if not cid.startswith("CVE-"):
                            continue
                        cve_node = graph.upsert_node(
                            Node.make(NodeKind.CVE, cid, key=f"cve::{cid}", source=scanner_hint)
                        )
                        graph.upsert_edge(Edge.make(vuln.id, cve_node.id, EdgeKind.MAPS_TO))

        return _json({"parsed": parsed, "skipped": skipped, "stats": graph.stats()})


@tool
@kg_degrades
def kg_ingest_subfinder(path: str, root_domain: str = "") -> str:
    """Ingest subfinder plaintext output (one subdomain per line).

    Creates host nodes and HTTP/HTTPS entrypoints so chain planning can
    target internet-exposed surfaces directly.
    """
    p = Path(path)
    if not p.exists():
        return _json({"error": f"file not found: {path}"})

    with graph_transaction() as graph:
        domains_added = 0
        entrypoints_added = 0
        root = root_domain.strip().lower()

        for raw_line in p.read_text(encoding="utf-8").splitlines():
            domain = raw_line.strip().lower().rstrip(".")
            if not domain or " " in domain:
                continue
            if root and not domain.endswith(root):
                continue

            host = _ensure_host_node(
                graph,
                label=domain,
                key=f"host::{domain}",
                source="subfinder",
                root_domain=root or None,
            )
            domains_added += 1

            for scheme in ("https", "http"):
                url = f"{scheme}://{domain}/"
                ep = graph.upsert_node(
                    Node.make(
                        NodeKind.ENTRYPOINT,
                        url,
                        key=f"entrypoint::{url}",
                        source="subfinder",
                        host=domain,
                        scheme=scheme,
                    )
                )
                graph.upsert_edge(Edge.make(host.id, ep.id, EdgeKind.EXPOSES, weight=0.5))
                graph.upsert_edge(Edge.make(ep.id, host.id, EdgeKind.HOSTS, weight=0.5))
                entrypoints_added += 1

        return _json(
            {
                "domains_added": domains_added,
                "entrypoints_added": entrypoints_added,
                "stats": graph.stats(),
            }
        )


@tool
@kg_degrades
def kg_ingest_httpx_jsonl(path: str, scanner_hint: str = "httpx") -> str:
    """Ingest httpx JSONL output into host/service/entrypoint graph nodes."""
    p = Path(path)
    if not p.exists():
        return _json({"error": f"file not found: {path}"})

    with graph_transaction() as graph:
        parsed = 0
        skipped = 0
        entrypoints = 0
        service_links = 0

        for raw_line in p.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                skipped += 1
                continue

            url = str(row.get("url") or row.get("input") or "").strip()
            if not url:
                skipped += 1
                continue
            parsed += 1

            parsed_url = urlparse(url)
            host_value = (
                str(row.get("host") or parsed_url.hostname or row.get("input") or "")
                .strip()
                .lower()
            )
            if not host_value:
                skipped += 1
                continue

            port = row.get("port") or parsed_url.port
            try:
                port_int = (
                    int(port) if port is not None else (443 if parsed_url.scheme == "https" else 80)
                )
            except (TypeError, ValueError):
                port_int = 443 if parsed_url.scheme == "https" else 80

            scheme = parsed_url.scheme or ("https" if port_int in {443, 8443} else "http")
            status_code = row.get("status-code")
            title = row.get("title")
            webserver = row.get("webserver")
            technologies = row.get("tech") if isinstance(row.get("tech"), list) else []

            host = _ensure_host_node(
                graph,
                label=host_value,
                key=f"host::{host_value}",
                source=scanner_hint,
            )

            service = _ensure_service_node(
                graph,
                host=host,
                host_label=host_value,
                port=port_int,
                proto="tcp",
                source=scanner_hint,
                service="http",
                scheme=scheme,
                status_code=status_code,
                webserver=webserver,
                technologies=technologies,
            )

            ep = graph.upsert_node(
                Node.make(
                    NodeKind.ENTRYPOINT,
                    url,
                    key=f"entrypoint::{url}",
                    source=scanner_hint,
                    host=host_value,
                    scheme=scheme,
                    port=port_int,
                    status_code=status_code,
                    title=title,
                    webserver=webserver,
                    technologies=technologies,
                )
            )
            url_node = graph.upsert_node(
                Node.make(
                    NodeKind.URL,
                    url,
                    key=f"url::{url}",
                    source=scanner_hint,
                    status_code=status_code,
                    title=title,
                    webserver=webserver,
                    technologies=technologies,
                )
            )
            graph.upsert_edge(Edge.make(host.id, ep.id, EdgeKind.EXPOSES, weight=0.5))
            graph.upsert_edge(Edge.make(ep.id, host.id, EdgeKind.HOSTS, weight=0.5))
            graph.upsert_edge(Edge.make(service.id, ep.id, EdgeKind.EXPOSES, weight=0.4))
            graph.upsert_edge(Edge.make(ep.id, service.id, EdgeKind.HOSTS, weight=0.4))
            graph.upsert_edge(Edge.make(ep.id, url_node.id, EdgeKind.EXPOSES, weight=0.3))
            graph.upsert_edge(Edge.make(url_node.id, ep.id, EdgeKind.HOSTS, weight=0.3))
            entrypoints += 1
            service_links += 1

            if isinstance(status_code, int) and status_code >= 500:
                vuln = graph.upsert_node(
                    Node.make(
                        NodeKind.VULNERABILITY,
                        f"[{scanner_hint}] unstable endpoint {url}",
                        key=f"{scanner_hint}::http-5xx::{url}",
                        scanner=scanner_hint,
                        rule_id="http-5xx",
                        severity=Severity.LOW.value,
                        status_code=status_code,
                    )
                )
                graph.upsert_edge(Edge.make(ep.id, vuln.id, EdgeKind.HAS_VULN, weight=0.7))

        return _json(
            {
                "parsed": parsed,
                "skipped": skipped,
                "entrypoints": entrypoints,
                "service_links": service_links,
                "stats": graph.stats(),
            }
        )


# ── Web/Auth signal ingestion ──────────────────────────────────────────


@tool
@kg_degrades
def kg_analyze_jwt(token: str, source: str = "") -> str:
    """Parse a JWT and lift suspicious indicators into graph vulnerabilities."""
    parsed = parse_token(token)
    token_hash = hashlib.sha1(token.encode("utf-8"), usedforsecurity=False).hexdigest()[:12]

    with graph_transaction() as graph:
        entrypoint: Node | None = None

        if source:
            parsed_source = urlparse(source)
            if parsed_source.scheme and parsed_source.netloc:
                entrypoint = graph.upsert_node(
                    Node.make(
                        NodeKind.ENTRYPOINT,
                        source,
                        key=f"entrypoint::{source}",
                        source="jwt-analysis",
                        scheme=parsed_source.scheme,
                        host=parsed_source.hostname,
                        port=parsed_source.port,
                    )
                )
            else:
                entrypoint = graph.upsert_node(
                    Node.make(
                        NodeKind.FINDING,
                        source,
                        key=f"context::{source}",
                        source="jwt-analysis",
                    )
                )

        created = 0
        finding_nodes: list[dict[str, Any]] = []
        for idx, finding in enumerate(parsed.findings, start=1):
            severity = _jwt_finding_severity(finding)
            vuln = graph.upsert_node(
                Node.make(
                    NodeKind.VULNERABILITY,
                    f"[jwt] {finding}",
                    key=f"jwt::{token_hash}::{idx}",
                    scanner="jwt-analysis",
                    severity=severity.value,
                    finding=finding,
                    source=source or None,
                    alg=parsed.header.alg,
                    kid=parsed.header.kid,
                    jku=parsed.header.jku,
                )
            )
            if entrypoint is not None:
                graph.upsert_edge(Edge.make(entrypoint.id, vuln.id, EdgeKind.HAS_VULN, weight=0.4))
            finding_nodes.append(
                {
                    "id": vuln.id,
                    "severity": severity.value,
                    "finding": finding,
                }
            )
            created += 1

        return _json(
            {
                "token_hash": token_hash,
                "header": parsed.header.to_dict(),
                "claims": parsed.claims.to_dict(),
                "findings": parsed.findings,
                "ingested_vulnerabilities": created,
                "nodes": finding_nodes,
                "stats": graph.stats(),
            }
        )


@tool
@kg_degrades
def kg_analyze_oauth_callback(
    callback_url: str,
    initial_request_url: str = "",
    public_client: bool = False,
    source: str = "",
) -> str:
    """Analyze OAuth/OIDC callback flow and ingest findings."""
    findings = analyze_oauth_callback(
        callback_url=callback_url,
        initial_request_url=initial_request_url or None,
        public_client=public_client,
    )

    with graph_transaction() as graph:
        entry_label = source or callback_url
        parsed_source = urlparse(entry_label)
        entrypoint = graph.upsert_node(
            Node.make(
                NodeKind.ENTRYPOINT,
                entry_label,
                key=f"entrypoint::{entry_label}",
                source="oauth-analysis",
                scheme=parsed_source.scheme or None,
                host=parsed_source.hostname or None,
                port=parsed_source.port,
            )
        )

        ingested: list[dict[str, Any]] = []
        for finding in findings:
            severity = _severity_from_string(finding.severity)
            vuln = graph.upsert_node(
                Node.make(
                    NodeKind.VULNERABILITY,
                    f"[oauth:{finding.id}] {finding.title}",
                    key=f"oauth::{finding.id}::{entry_label}",
                    scanner="oauth-analysis",
                    rule_id=finding.id,
                    severity=severity.value,
                    description=finding.detail,
                    recommendation=finding.recommendation,
                )
            )
            graph.upsert_edge(Edge.make(entrypoint.id, vuln.id, EdgeKind.HAS_VULN, weight=0.4))
            ingested.append(
                {
                    "id": vuln.id,
                    "rule_id": finding.id,
                    "severity": severity.value,
                    "title": finding.title,
                }
            )

        return _json(
            {
                "callback_url": callback_url,
                "findings": [f.to_dict() for f in findings],
                "ingested_vulnerabilities": len(ingested),
                "nodes": ingested,
                "stats": graph.stats(),
            }
        )


@tool
@kg_degrades
def kg_analyze_cookie_value(
    name: str,
    value: str,
    secure: bool = False,
    http_only: bool = False,
    same_site: str = "",
    source: str = "",
) -> str:
    """Analyze a cookie/session token and persist suspicious findings."""
    analysis = analyze_cookie(
        name=name,
        value=value,
        secure=secure,
        http_only=http_only,
        same_site=(same_site or None),
    )

    with graph_transaction() as graph:
        entrypoint: Node | None = None
        if source:
            parsed_source = urlparse(source)
            if parsed_source.scheme and parsed_source.netloc:
                entrypoint = graph.upsert_node(
                    Node.make(
                        NodeKind.ENTRYPOINT,
                        source,
                        key=f"entrypoint::{source}",
                        source="cookie-analysis",
                        scheme=parsed_source.scheme,
                        host=parsed_source.hostname,
                        port=parsed_source.port,
                    )
                )

        created: list[dict[str, Any]] = []
        cookie_hash = hashlib.sha1(
            f"{name}:{value}".encode("utf-8"), usedforsecurity=False
        ).hexdigest()[:12]
        for idx, finding in enumerate(analysis.findings, start=1):
            severity = _cookie_finding_severity(finding)
            vuln = graph.upsert_node(
                Node.make(
                    NodeKind.VULNERABILITY,
                    f"[cookie:{name}] {finding}",
                    key=f"cookie::{cookie_hash}::{idx}",
                    scanner="cookie-analysis",
                    severity=severity.value,
                    cookie_name=name,
                    framework=analysis.framework,
                    cookie_format=analysis.format,
                    source=source or None,
                )
            )
            if entrypoint is not None:
                graph.upsert_edge(Edge.make(entrypoint.id, vuln.id, EdgeKind.HAS_VULN, weight=0.5))
            created.append({"id": vuln.id, "severity": severity.value, "finding": finding})

        if analysis.format == "jwt" and isinstance(analysis.decoded, dict):
            secret = graph.upsert_node(
                Node.make(
                    NodeKind.SECRET,
                    f"cookie::{name}",
                    key=f"cookie-secret::{cookie_hash}",
                    source="cookie-analysis",
                    format="jwt",
                    decoded=analysis.decoded,
                )
            )
            if entrypoint is not None:
                graph.upsert_edge(Edge.make(entrypoint.id, secret.id, EdgeKind.LEAKS, weight=0.5))

        return _json(
            {
                "analysis": analysis.to_dict(),
                "ingested_vulnerabilities": len(created),
                "nodes": created,
                "stats": graph.stats(),
            }
        )


# ── Smart contract ingestion ───────────────────────────────────────────


@tool
@kg_degrades
def kg_scan_solidity(
    path: str, min_severity: str = "low", scanner_hint: str = "solidity-patterns"
) -> str:
    """Scan Solidity source with offline heuristics and ingest findings."""
    source_path = Path(path)
    if not source_path.exists():
        return _json({"error": f"file not found: {path}"})

    try:
        source = source_path.read_text(encoding="utf-8")
    except OSError as e:
        return _json({"error": f"failed to read solidity file: {e}"})

    findings = scan_solidity_source(source)
    threshold = _severity_threshold(_severity_from_string(min_severity))

    with graph_transaction() as graph:
        ingested = 0
        by_severity: dict[str, int] = {}

        file_node = graph.upsert_node(
            Node.make(
                NodeKind.SOURCE_FILE,
                str(source_path),
                key=f"file::{source_path}",
                source=scanner_hint,
                language="solidity",
            )
        )

        for finding in findings:
            if _severity_threshold(finding.severity) < threshold:
                continue
            vuln = graph.upsert_node(
                Node.make(
                    NodeKind.VULNERABILITY,
                    f"[{scanner_hint}:{finding.rule}] {source_path.name}:{finding.line}",
                    key=f"{scanner_hint}::{source_path}::{finding.rule}::{finding.line}",
                    scanner=scanner_hint,
                    rule_id=finding.rule,
                    severity=finding.severity.value,
                    description=finding.description,
                    recommendation=finding.recommendation,
                    cwe=[finding.cwe] if finding.cwe else [],
                    file=str(source_path),
                    line=finding.line,
                )
            )
            loc = graph.upsert_node(
                Node.make(
                    NodeKind.CODE_LOCATION,
                    f"{source_path}:{finding.line}",
                    key=f"{source_path}::{finding.line}",
                    file=str(source_path),
                    start_line=finding.line,
                )
            )
            graph.upsert_edge(Edge.make(vuln.id, loc.id, EdgeKind.DEFINED_IN))
            graph.upsert_edge(Edge.make(loc.id, file_node.id, EdgeKind.DEFINED_IN))
            by_severity[finding.severity.value] = by_severity.get(finding.severity.value, 0) + 1
            ingested += 1

        return _json(
            {
                "file": str(source_path),
                "matches": len(findings),
                "ingested": ingested,
                "by_severity": by_severity,
                "stats": graph.stats(),
            }
        )


@tool
@kg_degrades
def kg_ingest_slither(path: str) -> str:
    """Ingest Slither JSON output into the engagement KG.

    Writes flow directly through ``KGStore.record_observations`` —
    a single atomic batch per file. The engagement label is resolved
    from the ``EngagementContextMiddleware`` contextvar; falls back
    to the shared ``_LEGACY_ENGAGEMENT_LABEL`` outside the standard
    middleware stack (matches the convention used by the rest of
    RESEARCH_TOOLS).
    """
    from decepticon.tools.research._engagement_scope import _LEGACY_ENGAGEMENT_LABEL
    from decepticon_core.utils.engagement_scope import get_active_engagement

    engagement = get_active_engagement() or _LEGACY_ENGAGEMENT_LABEL
    try:
        ingested = ingest_slither_file(path, engagement=engagement)
    except ValueError as exc:
        return _json({"error": str(exc)})
    return _json({"ingested": ingested})


# ── Binary triage ingestion ────────────────────────────────────────────


@tool
@kg_degrades
def kg_triage_binary(path: str, max_strings: int = 400) -> str:
    """Triage a binary for exploit-relevant signals and persist graph entities."""
    binary_path = Path(path)
    if not binary_path.exists():
        return _json({"error": f"file not found: {path}"})

    try:
        blob = binary_path.read_bytes()
    except OSError as e:
        return _json({"error": f"failed to read binary: {e}"})

    info = identify_binary(binary_path)
    packer = detect_packer(blob)
    extracted = extract_strings(blob)
    if max_strings > 0:
        extracted = extracted[:max_strings]
    grouped = group_by_category(extracted)
    import_tokens: list[str] = []
    for entry in grouped.get("import", []):
        for token in re.split(r"[^A-Za-z0-9_@.]+", entry.text):
            if token:
                import_tokens.append(token)
    symbols = summarize_symbols(import_tokens)

    with graph_transaction() as graph:
        file_node = graph.upsert_node(
            Node.make(
                NodeKind.SOURCE_FILE,
                str(binary_path),
                key=f"binary::{binary_path}",
                source="binary-triage",
                binary_format=info.format,
                architecture=info.architecture,
                bitness=info.bitness,
                nx=info.nx,
                pie=info.pie,
                relro=info.relro,
                canary=info.canary,
                packed=packer.likely_packed,
                entropy=round(packer.entropy, 3),
            )
        )

        created: list[dict[str, Any]] = []

        def _add_binary_vuln(
            rule_id: str, severity: Severity, description: str, **props: Any
        ) -> None:
            vuln = graph.upsert_node(
                Node.make(
                    NodeKind.VULNERABILITY,
                    f"[binary:{rule_id}] {binary_path.name}",
                    key=f"binary::{binary_path}::{rule_id}",
                    scanner="binary-triage",
                    rule_id=rule_id,
                    severity=severity.value,
                    description=description,
                    file=str(binary_path),
                    **props,
                )
            )
            graph.upsert_edge(Edge.make(file_node.id, vuln.id, EdgeKind.HAS_VULN, weight=0.6))
            created.append({"id": vuln.id, "rule_id": rule_id, "severity": severity.value})

        if info.nx is False:
            _add_binary_vuln(
                "hardening.nx-disabled",
                Severity.MEDIUM,
                "NX appears disabled; memory corruption is easier to weaponize.",
            )
        if info.pie is False:
            _add_binary_vuln(
                "hardening.pie-disabled",
                Severity.MEDIUM,
                "PIE appears disabled; ASLR entropy is reduced for code pointers.",
            )
        if grouped.get("secret"):
            sample = [s.text[:80] for s in grouped["secret"][:5]]
            _add_binary_vuln(
                "secrets.hardcoded",
                Severity.HIGH,
                "Potential hardcoded secrets were found in binary strings.",
                sample=sample,
            )
        if symbols.command_exec and (symbols.dangerous_c or symbols.dynamic_code):
            sev = Severity.CRITICAL if symbols.network else Severity.HIGH
            _add_binary_vuln(
                "rce.primitives",
                sev,
                "Binary imports command-execution primitives plus memory-unsafe APIs.",
                command_exec=symbols.command_exec,
                dangerous_c=symbols.dangerous_c,
                dynamic_code=symbols.dynamic_code,
                network=symbols.network,
            )

        if packer.likely_packed:
            hypothesis = graph.upsert_node(
                Node.make(
                    NodeKind.HYPOTHESIS,
                    f"Packed binary candidate: {binary_path.name}",
                    key=f"binary-packed::{binary_path}",
                    source="binary-triage",
                    entropy=round(packer.entropy, 3),
                    signatures=packer.signatures,
                    notes=packer.notes,
                )
            )
            graph.upsert_edge(Edge.make(file_node.id, hypothesis.id, EdgeKind.CONTAINS, weight=1.2))

        entrypoints_added = 0
        for s in grouped.get("url", [])[:25]:
            url_node = graph.upsert_node(
                Node.make(
                    NodeKind.URL,
                    s.text,
                    key=f"url::{s.text}",
                    source="binary-triage",
                    offset=s.offset,
                )
            )
            ep = graph.upsert_node(
                Node.make(
                    NodeKind.ENTRYPOINT,
                    s.text,
                    key=f"entrypoint::{s.text}",
                    source="binary-triage",
                )
            )
            graph.upsert_edge(Edge.make(file_node.id, url_node.id, EdgeKind.CONTAINS, weight=0.8))
            graph.upsert_edge(Edge.make(url_node.id, ep.id, EdgeKind.EXPOSES, weight=0.7))
            entrypoints_added += 1

        return _json(
            {
                "binary": info.to_dict(),
                "packer": packer.to_dict(),
                "string_category_counts": {k: len(v) for k, v in grouped.items()},
                "symbol_report": symbols.to_dict(),
                "created_vulnerabilities": created,
                "entrypoints_added": entrypoints_added,
                "stats": graph.stats(),
            }
        )


# ── Chain planner ──────────────────────────────────────────────────────


@tool
def plan_attack_chains(
    max_depth: int = 8, max_cost: float = 20.0, top_k: int = 10, promote: bool = False
) -> str:
    """Enumerate multi-hop exploit chains from entrypoints to crown jewels.

    WHEN TO USE: After you've added ENTRYPOINT nodes (exposed public
    surfaces) and CROWN_JEWEL nodes (bounty-worthy targets) and connected
    vulns between them with ``enables``/``leaks``/``grants`` edges. The
    planner walks the graph with Dijkstra and returns the cheapest
    complete paths.

    COST MODEL: lower is better. Critical vulns shrink cost (0.4x),
    validated PoCs shrink further (0.5x), high edge weight grows it.

    Args:
        max_depth: Max hops per chain (default 8).
        max_cost: Discard paths exceeding this total cost (default 20).
        top_k: Return the top-K cheapest chains (default 10).
        promote: If true, persist each computed chain as a ``chain`` node
            in the graph so future queries can reference it.

    Returns:
        JSON list of chains with entrypoint, crown jewel, hop sequence,
        and total cost.
    """
    chains = plan_chains(max_depth=max_depth, max_cost=max_cost, top_k=top_k)
    promoted_ids: list[str] = []
    if promote:
        for chain in chains:
            promoted_ids.append(promote_chain(chain))
    return _json(
        {
            "count": len(chains),
            "promoted": promoted_ids if promote else [],
            "chains": [c.to_dict() for c in chains],
        }
    )


@tool
def suggest_objectives_from_chains(
    top_k: int = 5,
    max_depth: int = 8,
    max_cost: float = 20.0,
) -> str:
    """Convert top-ranked attack chains into OPPLAN-ready objective drafts.

    This does not mutate OPPLAN; it returns draft payloads for the
    orchestrator's `commit_opplan` tool.
    """
    chains = plan_chains(top_k=max(top_k, 1), max_depth=max_depth, max_cost=max_cost)
    if not chains:
        return _json({"count": 0, "objectives": []})

    ranked = sorted(chains, key=critical_path_score, reverse=True)
    drafts: list[dict[str, Any]] = []

    for idx, chain in enumerate(ranked[:top_k], start=1):
        chain_score = critical_path_score(chain)

        phase = "initial-access"
        if any(step.node_kind in {NodeKind.CREDENTIAL, NodeKind.SECRET} for step in chain.steps):
            phase = "post-exploit"
        elif (
            "admin" in chain.crown_jewel_label.lower()
            or "domain" in chain.crown_jewel_label.lower()
        ):
            phase = "post-exploit"

        title = f"Exploit chain {idx}: {chain.entrypoint_label} -> {chain.crown_jewel_label}"
        acceptance = [
            f"Demonstrate path from {chain.entrypoint_label} to {chain.crown_jewel_label}.",
            "Capture evidence for each hop (commands, outputs, and impacted asset IDs).",
            "Validate the highest-risk step with PoC evidence or explain why blocked.",
        ]
        drafts.append(
            {
                "priority": idx,
                "phase": phase,
                "title": title,
                "description": chain.summary(),
                "acceptance_criteria": acceptance,
                "mitre": [],
                "opsec": "standard",
                "notes": {
                    "chain_total_cost": chain.total_cost,
                    "chain_score": chain_score,
                    "path": chain.path_labels,
                },
            }
        )

    return _json({"count": len(drafts), "objectives": drafts})


# ── Fuzzing ─────────────────────────────────────────────────────────────


@tool
def fuzz_classify(root: str) -> str:
    """Classify a source tree and recommend a fuzzer engine.

    Returns the best-guess language, the default fuzz engine for it, and
    up to 20 candidate entry functions (files matching main/parse/decode/
    deserialize/handle/fuzz).

    Args:
        root: Absolute path to the source root (repo checkout or tarball
            extraction dir).
    """
    tp = fuzz_mod.classify_target(root)
    return _json(
        {
            "root": str(tp.root),
            "language": tp.language,
            "engine": tp.engine.value if tp.engine else None,
            "entry_candidates": [str(p) for p in tp.entry_candidates],
            "notes": tp.notes,
        }
    )


@tool
def fuzz_harness(engine: str, target: str, entry: str = "parse") -> str:
    """Emit a minimal starter harness for a target + engine pair.

    ENGINES: libfuzzer, afl++, honggfuzz, jazzer, atheris, cargo-fuzz,
    go-fuzz, boofuzz. Returns ready-to-compile/run source code.

    Args:
        engine: Fuzzer engine name.
        target: Module / library under test (used in template strings).
        entry: Entry function / symbol to attach the harness to.
    """
    try:
        eng = fuzz_mod.Engine(engine)
    except ValueError:
        return _json(
            {"error": f"unknown engine: {engine}", "valid": [e.value for e in fuzz_mod.Engine]}
        )
    try:
        src = fuzz_mod.harness_for(eng, target, entry)
    except ValueError as e:
        return _json({"error": str(e)})
    return _json({"engine": eng.value, "source": src})


@tool
def fuzz_record_crash(log: str, engine: str) -> str:
    """Parse an ASan/UBSan log, extract the crash, and persist it as a vuln.

    WHEN TO USE: Immediately after a fuzzer reports a crash. Paste the
    last ~1K lines of sanitizer output as ``log``. The parser extracts
    the crash kind (heap-buffer-overflow, double-free, etc.), severity,
    file:line, and the first 15 stack frames, then writes a Vulnerability
    + CodeLocation pair into the graph.

    Args:
        log: Raw sanitizer output from the fuzzer run.
        engine: Fuzzer engine that produced the crash.

    Returns:
        JSON record of the parsed crash or an error if no crash signature
        was recognised.
    """
    try:
        eng = fuzz_mod.Engine(engine)
    except ValueError:
        return _json({"error": f"unknown engine: {engine}"})
    crash = fuzz_mod.parse_asan(log)
    if crash is None:
        return _json({"error": "no ASan/UBSan signature found in log"})
    with graph_transaction() as graph:
        vuln = fuzz_mod.record_crash(graph, crash, engine=eng)
        return _json(
            {
                "vuln_id": vuln.id,
                "severity": crash.severity.value,
                "sanitizer": crash.sanitizer,
                "kind": crash.kind,
                "file": crash.file,
                "line": crash.line,
                "stack_depth": len(crash.stack),
            }
        )


# ── PoC validation ─────────────────────────────────────────────────────


@tool
async def validate_finding(
    vuln_id: str,
    poc_command: str,
    success_patterns: str,
    negative_command: str = "",
    negative_patterns: str = "",
    cvss_vector: str = "",
) -> str:
    """Run a PoC inside the sandbox and mark the vuln validated on hit.

    WHEN TO USE: After identifying a vulnerability, craft a minimal
    reproducer and run it here. The validator applies ZFP (zero false
    positives) by requiring a negative control: if the same request
    without the payload *also* fires the success pattern, the result is
    demoted.

    SUCCESS PATTERNS are Python regexes (DOTALL + IGNORECASE). Use simple
    substrings when you don't need regex power.

    CVSS_VECTOR example: ``"CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H"``
    If provided, the base score is computed and written back onto the
    vuln node.

    Args:
        vuln_id: Graph id of the vulnerability node to validate.
        poc_command: Bash command that exercises the vulnerability.
        success_patterns: Comma-separated list of regexes to match in stdout.
        negative_command: Optional baseline command (same request without payload).
        negative_patterns: Comma-separated regexes expected in the baseline.
        cvss_vector: Optional CVSS v3.1 vector string.

    Returns:
        JSON validation record including success signals, negative
        control hits, stdout excerpt, and CVSS score if provided.
    """
    from decepticon.tools.bash.bash import get_sandbox
    from decepticon.tools.research.poc import (
        CVSSVector,
        parse_cvss_vector,
        sandbox_runner,
        validate_poc,
    )

    sandbox = get_sandbox()
    if sandbox is None:
        return _json({"error": "HTTPSandbox not initialized"})

    def _split(s: str) -> list[str]:
        return [p.strip() for p in s.split(",") if p.strip()]

    cvss: CVSSVector | None = None
    if cvss_vector:
        try:
            cvss = parse_cvss_vector(cvss_vector)
        except ValueError as exc:
            return _json({"error": f"bad CVSS vector: {exc}"})

    with graph_transaction() as graph:
        runner = sandbox_runner(sandbox)
        result = await validate_poc(
            vuln_id=vuln_id,
            poc_command=poc_command,
            success_patterns=_split(success_patterns),
            runner=runner,
            negative_command=negative_command or None,
            negative_patterns=_split(negative_patterns) if negative_patterns else None,
            cvss=cvss,
            graph=graph,
        )
        return _json(result.to_dict())


# ── Tier 2 ingesters (Kali tool output → knowledge graph) ─────────────


@tool
@kg_degrades
def kg_ingest_dnsx(path: str) -> str:
    """Ingest dnsx JSONL output (one resolution record per line).

    Creates host nodes for each resolved name and adds a short note on
    the record type (A / AAAA / CNAME) when present.
    """
    p = Path(path)
    if not p.exists():
        return _json({"error": f"file not found: {path}"})

    with graph_transaction() as graph:
        hosts_added = 0
        for raw in p.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            host_value = str(row.get("host") or row.get("name") or "").strip().lower().rstrip(".")
            if not host_value:
                continue
            a = row.get("a") or []
            aaaa = row.get("aaaa") or []
            cname = row.get("cname") or []
            host = _ensure_host_node(
                graph,
                label=host_value,
                key=f"host::{host_value}",
                source="dnsx",
                a_records=a if isinstance(a, list) else [],
                aaaa_records=aaaa if isinstance(aaaa, list) else [],
                cname_records=cname if isinstance(cname, list) else [],
            )
            hosts_added += 1
            # Link CNAME targets as separate host nodes. CNAME is an
            # "exposes" relationship in reverse — the alias host surfaces
            # the canonical host to the outside world. We also add the
            # reverse ``runs_on`` edge so the chain planner can traverse
            # aliases in either direction.
            for target in cname if isinstance(cname, list) else []:
                target_label = str(target).lower().rstrip(".")
                if not target_label:
                    continue
                target_host = _ensure_host_node(
                    graph,
                    label=target_label,
                    key=f"host::{target_label}",
                    source="dnsx-cname",
                )
                graph.upsert_edge(
                    Edge.make(
                        host.id,
                        target_host.id,
                        EdgeKind.EXPOSES,
                        weight=0.5,
                        key=f"cname::{host_value}->{target_label}",
                    )
                )
                graph.upsert_edge(
                    Edge.make(
                        target_host.id,
                        host.id,
                        EdgeKind.HOSTS,
                        weight=0.5,
                        key=f"cname-rev::{target_label}->{host_value}",
                    )
                )

        return _json({"hosts_added": hosts_added, "stats": graph.stats()})


@tool
@kg_degrades
def kg_ingest_katana(path: str) -> str:
    """Ingest katana JSONL crawl output as URL / entrypoint nodes."""
    p = Path(path)
    if not p.exists():
        return _json({"error": f"file not found: {path}"})

    with graph_transaction() as graph:
        urls_added = 0
        for raw in p.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            endpoint = (
                row.get("endpoint")
                or row.get("request", {}).get("endpoint")
                or row.get("url")
                or ""
            )
            if not endpoint:
                continue
            parsed_url = urlparse(endpoint)
            host_value = (parsed_url.hostname or "").lower()
            if not host_value:
                continue
            host = _ensure_host_node(
                graph,
                label=host_value,
                key=f"host::{host_value}",
                source="katana",
            )
            url_node = graph.upsert_node(
                Node.make(
                    NodeKind.URL,
                    endpoint,
                    key=f"url::{endpoint}",
                    source="katana",
                    method=row.get("method") or row.get("request", {}).get("method") or "GET",
                )
            )
            ep = graph.upsert_node(
                Node.make(
                    NodeKind.ENTRYPOINT,
                    endpoint,
                    key=f"entrypoint::{endpoint}",
                    source="katana",
                    host=host_value,
                )
            )
            graph.upsert_edge(Edge.make(host.id, url_node.id, EdgeKind.EXPOSES, weight=0.5))
            graph.upsert_edge(Edge.make(url_node.id, host.id, EdgeKind.HOSTS, weight=0.5))
            graph.upsert_edge(Edge.make(host.id, ep.id, EdgeKind.EXPOSES, weight=0.4))
            graph.upsert_edge(Edge.make(ep.id, host.id, EdgeKind.HOSTS, weight=0.4))
            graph.upsert_edge(Edge.make(ep.id, url_node.id, EdgeKind.EXPOSES, weight=0.3))
            graph.upsert_edge(Edge.make(url_node.id, ep.id, EdgeKind.HOSTS, weight=0.3))
            urls_added += 1

        return _json({"urls_added": urls_added, "stats": graph.stats()})


@tool
@kg_degrades
def kg_ingest_masscan(path: str) -> str:
    """Ingest masscan JSON output into host + service nodes.

    Masscan writes a JSON array where each entry has ``ip`` and
    ``ports: [{port, proto, status}]``. State is usually ``open``.
    """
    p = Path(path)
    if not p.exists():
        return _json({"error": f"file not found: {path}"})

    try:
        raw = p.read_text(encoding="utf-8").strip()
        if not raw:
            return _json({"error": "masscan output empty"})
        # masscan -oJ emits a list with trailing commas sometimes; wrap
        # in brackets if needed
        if raw.startswith("["):
            entries = json.loads(
                raw.rstrip(",\n") + ("]" if not raw.rstrip().endswith("]") else "")
            )
        else:
            entries = [json.loads(line.rstrip(",")) for line in raw.splitlines() if line.strip()]
    except (OSError, json.JSONDecodeError) as e:
        return _json({"error": f"failed to parse masscan json: {e}"})

    with graph_transaction() as graph:
        hosts_added = 0
        services_added = 0
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            ip = str(entry.get("ip") or "").strip()
            if not ip:
                continue
            host = _ensure_host_node(graph, label=ip, key=f"host::{ip}", ip=ip, source="masscan")
            hosts_added += 1
            for port_row in entry.get("ports") or []:
                try:
                    port = int(port_row.get("port", 0))
                except (TypeError, ValueError):
                    continue
                if not port:
                    continue
                proto = port_row.get("proto", "tcp")
                if port_row.get("status") and port_row.get("status") != "open":
                    continue
                _ensure_service_node(
                    graph,
                    host=host,
                    host_label=ip,
                    port=port,
                    proto=proto,
                    source="masscan",
                    service="unknown",
                    product="",
                    version="",
                )
                services_added += 1

        return _json(
            {
                "hosts_added": hosts_added,
                "services_added": services_added,
                "stats": graph.stats(),
            }
        )


@tool
@kg_degrades
def kg_ingest_ffuf(path: str) -> str:
    """Ingest ffuf JSON output as URL nodes anchored to a host.

    Each hit becomes an ENTRYPOINT/URL pair with the matched status
    code recorded as a property.
    """
    p = Path(path)
    if not p.exists():
        return _json({"error": f"file not found: {path}"})

    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        return _json({"error": f"failed to parse ffuf json: {e}"})

    results = data.get("results") or []

    with graph_transaction() as graph:
        urls_added = 0
        entrypoints_added = 0
        for row in results:
            url = row.get("url") or ""
            if not url:
                continue
            try:
                status = int(row.get("status") or 0)
            except (ValueError, TypeError):
                status = 0
            try:
                length = int(row.get("length") or 0)
            except (ValueError, TypeError):
                length = 0
            parsed_url = urlparse(url)
            host_value = (parsed_url.hostname or "").lower()
            host = None
            if host_value:
                host = _ensure_host_node(
                    graph, label=host_value, key=f"host::{host_value}", source="ffuf"
                )
            url_node = graph.upsert_node(
                Node.make(
                    NodeKind.URL,
                    url,
                    key=f"url::{url}",
                    source="ffuf",
                    status=status,
                    length=length,
                )
            )
            ep = graph.upsert_node(
                Node.make(
                    NodeKind.ENTRYPOINT,
                    url,
                    key=f"entrypoint::{url}",
                    source="ffuf",
                    host=host_value,
                    status=status,
                )
            )
            if host is not None:
                graph.upsert_edge(Edge.make(host.id, url_node.id, EdgeKind.EXPOSES, weight=0.5))
                graph.upsert_edge(Edge.make(url_node.id, host.id, EdgeKind.HOSTS, weight=0.5))
                graph.upsert_edge(Edge.make(host.id, ep.id, EdgeKind.EXPOSES, weight=0.4))
                graph.upsert_edge(Edge.make(ep.id, host.id, EdgeKind.HOSTS, weight=0.4))
            graph.upsert_edge(Edge.make(ep.id, url_node.id, EdgeKind.EXPOSES, weight=0.3))
            graph.upsert_edge(Edge.make(url_node.id, ep.id, EdgeKind.HOSTS, weight=0.3))
            urls_added += 1
            entrypoints_added += 1

        return _json(
            {
                "urls_added": urls_added,
                "entrypoints_added": entrypoints_added,
                "stats": graph.stats(),
            }
        )


@tool
@kg_degrades
def kg_ingest_testssl(path: str, target: str = "") -> str:
    """Ingest testssl.sh JSON output as TLS vulnerability nodes.

    testssl.sh emits an array of findings with ``id``, ``severity``,
    ``finding`` fields. We map the severity labels onto the graph's
    ``Severity`` enum and create a VULNERABILITY node per HIGH/CRIT
    finding, linked to a HOST node derived from ``target`` (``host`` or
    ``host:port``) so the chain planner can follow testssl findings.

    If ``target`` is empty, we try to read it from the testssl JSON
    envelope (``targetHost``/``target``). Vulnerability nodes are still
    created when no host is resolvable, but they won't participate in
    attack-chain traversal — prefer to pass ``target`` explicitly.
    """
    p = Path(path)
    if not p.exists():
        return _json({"error": f"file not found: {path}"})

    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        return _json({"error": f"failed to parse testssl json: {e}"})

    rows: list[dict[str, Any]] = []
    envelope_target = ""
    if isinstance(data, list):
        rows = [r for r in data if isinstance(r, dict)]
    elif isinstance(data, dict):
        envelope_target = str(data.get("targetHost") or data.get("target") or "").strip()
        # Newer testssl wraps everything in {"scanResult": [...]}
        scan_result = data.get("scanResult")
        if isinstance(scan_result, list):
            for entry in scan_result:
                if not isinstance(entry, dict):
                    continue
                if not envelope_target:
                    envelope_target = str(
                        entry.get("targetHost") or entry.get("target") or ""
                    ).strip()
                section = entry.get("vulnerabilities") or entry.get("serverDefaults") or []
                if isinstance(section, list):
                    rows.extend(r for r in section if isinstance(r, dict))

    # Resolve the host label: explicit arg > envelope target > none
    host_label = (target or envelope_target).strip()

    with graph_transaction() as graph:
        host_node = None
        if host_label:
            # Strip port suffix for the graph host key
            bare_host = host_label.split(":", 1)[0].lower()
            host_node = _ensure_host_node(
                graph,
                label=bare_host,
                key=f"host::{bare_host}",
                source="testssl",
            )

        severity_map = {
            "CRITICAL": Severity.CRITICAL,
            "HIGH": Severity.HIGH,
            "MEDIUM": Severity.MEDIUM,
            "LOW": Severity.LOW,
            "WARN": Severity.LOW,
            "INFO": Severity.INFO,
            "OK": Severity.INFO,
        }
        vulns_added = 0
        linked = 0
        for row in rows:
            sev_raw = str(row.get("severity") or "INFO").upper()
            severity = severity_map.get(sev_raw, Severity.INFO)
            if severity in {Severity.INFO, Severity.LOW}:
                continue
            rule_id = str(row.get("id") or "testssl.finding")
            finding = str(row.get("finding") or "").strip()
            scope = host_label or "unscoped"
            key = f"testssl::{scope}::{rule_id}::{finding[:48]}"
            vuln = graph.upsert_node(
                Node.make(
                    NodeKind.VULNERABILITY,
                    f"[testssl:{rule_id}] {finding[:80]}",
                    key=key,
                    scanner="testssl",
                    rule_id=rule_id,
                    severity=severity.value,
                    description=finding,
                    target=host_label,
                )
            )
            vulns_added += 1
            if host_node is not None:
                graph.upsert_edge(Edge.make(host_node.id, vuln.id, EdgeKind.HAS_VULN, weight=0.6))
                linked += 1

        return _json(
            {
                "vulns_added": vulns_added,
                "linked_to_host": linked,
                "host": host_label,
                "stats": graph.stats(),
            }
        )


@tool
@kg_degrades
def kg_ingest_crackmapexec(path: str, protocol: str = "smb", target: str = "") -> str:
    """Ingest a crackmapexec / netexec log file as credential leads.

    CME log format is loose, so we use line regexes to find ``[+]``
    success rows carrying ``DOMAIN\\user:pass`` or NTLM hashes, then
    create ``CREDENTIAL`` nodes + optional ``USER`` nodes in the graph.
    """
    p = Path(path)
    if not p.exists():
        return _json({"error": f"file not found: {path}"})

    text = p.read_text(encoding="utf-8", errors="replace")
    success_re = re.compile(r"\[\+\].*?([A-Za-z0-9._-]+)[\\/]([A-Za-z0-9._-]+):(\S+)")
    admin_re = re.compile(r"\(Pwn3d!?\)")

    with graph_transaction() as graph:
        creds_added = 0
        admins_added = 0
        for line in text.splitlines():
            if "[+]" not in line:
                continue
            m = success_re.search(line)
            if not m:
                continue
            domain, user, secret = m.group(1), m.group(2), m.group(3)
            is_admin = bool(admin_re.search(line))
            cred = graph.upsert_node(
                Node.make(
                    NodeKind.CREDENTIAL,
                    f"{domain}\\{user}",
                    key=f"cred::{domain}\\{user}",
                    source="crackmapexec",
                    protocol=protocol,
                    target=target,
                    secret_type=("ntlm" if ":" in secret and len(secret) >= 32 else "password"),
                    admin=is_admin,
                )
            )
            user_node = graph.upsert_node(
                Node.make(
                    NodeKind.USER,
                    f"{domain}\\{user}",
                    key=f"user::{domain}\\{user}",
                    source="crackmapexec",
                    domain=domain,
                )
            )
            graph.upsert_edge(
                Edge.make(cred.id, user_node.id, EdgeKind.AUTHENTICATES_TO, weight=0.5)
            )
            creds_added += 1
            if is_admin:
                admins_added += 1

        return _json(
            {
                "creds_added": creds_added,
                "admin_creds_added": admins_added,
                "stats": graph.stats(),
            }
        )


@tool
@kg_degrades
def kg_ingest_asrep_hashes(path: str, domain: str = "") -> str:
    """Ingest an ``impacket-GetNPUsers`` output file as credential leads.

    AS-REP hashes look like ``$krb5asrep$23$user@DOMAIN:...``. Each
    line creates a CREDENTIAL node tagged for hashcat mode 18200.
    """
    p = Path(path)
    if not p.exists():
        return _json({"error": f"file not found: {path}"})

    with graph_transaction() as graph:
        added = 0
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line.startswith("$krb5asrep$"):
                continue
            # Format: $krb5asrep$23$USER@DOMAIN:SALT$ENCRYPTED_TIMESTAMP
            # split("$", 4) →
            #   [0]=''  [1]='krb5asrep'  [2]='23'
            #   [3]='USER@DOMAIN:SALT'   [4]='ENCRYPTED_TIMESTAMP'
            after_dollars = line.split("$", 4)
            if len(after_dollars) < 5:
                continue
            user_part = after_dollars[3].split(":", 1)[0]
            user = user_part.split("@", 1)[0]
            dom = user_part.split("@", 1)[1] if "@" in user_part else domain
            label = f"{dom}\\{user}" if dom else user
            graph.upsert_node(
                Node.make(
                    NodeKind.CREDENTIAL,
                    label,
                    key=f"cred-asrep::{label}",
                    source="impacket-GetNPUsers",
                    secret_type="krb5asrep",
                    hashcat_mode=18200,
                    hash=line,
                )
            )
            added += 1

        return _json({"asrep_hashes_added": added, "stats": graph.stats()})


# ── Public tool list ────────────────────────────────────────────────────

RESEARCH_TOOLS = [
    kg_add_node,
    kg_add_edge,
    kg_query,
    kg_neighbors,
    kg_stats,
    kg_backend_health,
    kg_dedupe_findings,
    kg_ingest_nmap_xml,
    kg_ingest_nuclei_jsonl,
    kg_ingest_subfinder,
    kg_ingest_httpx_jsonl,
    kg_ingest_dnsx,
    kg_ingest_katana,
    kg_ingest_masscan,
    kg_ingest_ffuf,
    kg_ingest_testssl,
    kg_ingest_crackmapexec,
    kg_ingest_asrep_hashes,
    kg_ingest_sarif,
    kg_analyze_jwt,
    kg_analyze_oauth_callback,
    kg_analyze_cookie_value,
    kg_scan_solidity,
    kg_ingest_slither,
    kg_triage_binary,
    cve_lookup,
    cve_by_package,
    cve_enrich_dependencies,
    plan_attack_chains,
    suggest_objectives_from_chains,
    fuzz_classify,
    fuzz_harness,
    fuzz_record_crash,
    validate_finding,
    scan_secrets,
    detect_tech_stack,
    *SCANNER_TOOLS,
    *PATCH_TOOLS,
    *OSINT_TOOLS,
]
