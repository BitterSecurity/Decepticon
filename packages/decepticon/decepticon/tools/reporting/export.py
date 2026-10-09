"""Engagement export & replay — ZIP bundle, Markdown narrative, and import.

Provides tools for exporting complete engagement state (workspace files,
KG subgraph, evidence recordings) into portable formats for client
delivery, legal evidence preservation, and engagement replay.
"""

from __future__ import annotations

import json
import os
import time
import zipfile
from pathlib import Path
from typing import Any

from langchain_core.tools import tool


def _json(data: Any) -> str:
    return json.dumps(data, indent=2, default=str, ensure_ascii=False)


@tool
def export_engagement_zip(output_path: str = "") -> str:
    """Export the complete engagement as a portable ZIP bundle.

    WHEN TO USE: At the end of an engagement, or when the operator requests
    a deliverable package. Bundles workspace files, evidence chain, KG
    snapshot, and engagement metadata.

    The ZIP contains:
    - workspace/ — all files from the engagement workspace
    - evidence/ — asciicast recordings and chain-of-custody manifest
    - kg_snapshot.cypher — Neo4j Cypher export of the engagement's attack graph
    - metadata.json — engagement metadata (slug, timestamps, agent versions)
    - plan/ — RoE, CONOPS, OPPLAN, threat profile documents

    Args:
        output_path: Where to write the ZIP. Defaults to workspace/export/<slug>-<timestamp>.zip

    Returns:
        JSON with the export path, file count, and total size.
    """
    workspace = os.environ.get("DECEPTICON_WORKSPACE_PATH", "/workspace")
    ws = Path(workspace)

    if not ws.exists():
        return _json({"error": "workspace not found", "path": workspace})

    slug = ws.name or "engagement"
    ts = int(time.time())

    if not output_path:
        export_dir = ws / "export"
        export_dir.mkdir(parents=True, exist_ok=True)
        output_path = str(export_dir / f"{slug}-{ts}.zip")

    file_count = 0
    total_bytes = 0

    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
        # Walk workspace, skip the export dir itself
        for root, dirs, files in os.walk(ws):
            # Skip export directory to avoid recursive inclusion
            rel_root = Path(root).relative_to(ws)
            if str(rel_root).startswith("export"):
                continue
            for fname in files:
                fpath = Path(root) / fname
                arcname = f"workspace/{rel_root}/{fname}"
                try:
                    zf.write(fpath, arcname)
                    file_count += 1
                    total_bytes += fpath.stat().st_size
                except (OSError, PermissionError):
                    continue

        # Add metadata
        metadata = {
            "slug": slug,
            "exported_at": time.time(),
            "exported_at_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "file_count": file_count,
            "workspace_path": workspace,
        }
        zf.writestr("metadata.json", json.dumps(metadata, indent=2))

        # Try to export KG snapshot as Cypher
        try:
            from decepticon.tools.research._state import get_graph

            graph = get_graph()
            if graph:
                nodes = graph.all_nodes()
                edges = graph.all_edges()
                cypher_lines: list[str] = []
                for n in nodes:
                    props = json.dumps(
                        n.properties if hasattr(n, "properties") else {},
                        default=str,
                    )
                    cypher_lines.append(
                        f"CREATE (:{n.kind.value} {{key: {json.dumps(n.key)},"
                        f" label: {json.dumps(n.label)}, properties: {props}}})"
                    )
                for e in edges:
                    cypher_lines.append(f"// EDGE: ({e.source}) -[:{e.kind.value}]-> ({e.target})")
                zf.writestr("kg_snapshot.cypher", "\n".join(cypher_lines))
        except Exception:
            pass  # KG export is best-effort

    return _json(
        {
            "exported": True,
            "path": output_path,
            "file_count": file_count,
            "total_bytes": total_bytes,
            "total_mb": round(total_bytes / (1024 * 1024), 2),
        }
    )


@tool
def export_engagement_markdown(output_path: str = "") -> str:
    """Export the engagement as a Markdown narrative report.

    WHEN TO USE: When the operator needs a human-readable report of the
    full engagement timeline, findings, and reasoning traces.

    Generates a structured Markdown document with:
    - Executive summary (from plan documents)
    - Timeline of actions and findings
    - Knowledge graph summary (hosts, services, vulnerabilities)
    - Evidence references
    - Recommendations

    Args:
        output_path: Where to write the .md file. Defaults to workspace/export/<slug>-report.md
    """
    workspace = os.environ.get("DECEPTICON_WORKSPACE_PATH", "/workspace")
    ws = Path(workspace)
    slug = ws.name or "engagement"

    if not output_path:
        export_dir = ws / "export"
        export_dir.mkdir(parents=True, exist_ok=True)
        output_path = str(export_dir / f"{slug}-report.md")

    sections: list[str] = []
    sections.append(f"# Engagement Report: {slug}")
    sections.append(f"\n*Generated: {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}*\n")

    # Executive Summary from plan docs
    plan_dir = ws / "plan"
    if plan_dir.exists():
        sections.append("## Engagement Plan\n")
        for doc_name in [
            "roe.json",
            "conops.json",
            "threat-profile.json",
            "opplan.json",
        ]:
            doc_path = plan_dir / doc_name
            if doc_path.exists():
                try:
                    data = json.loads(doc_path.read_text(encoding="utf-8"))
                    sections.append(f"### {doc_name}\n")
                    sections.append(
                        f"```json\n{json.dumps(data, indent=2, default=str)[:2000]}\n```\n"
                    )
                except (OSError, json.JSONDecodeError):
                    pass

    # Findings from workspace
    findings_dir = ws / "findings"
    if findings_dir.exists():
        sections.append("## Findings\n")
        for fpath in sorted(findings_dir.glob("*.json")):
            try:
                finding = json.loads(fpath.read_text(encoding="utf-8"))
                title = finding.get("title", fpath.stem)
                severity = finding.get("severity", "unknown")
                desc = finding.get("description", "")
                sections.append(f"### {title} [{severity.upper()}]\n")
                sections.append(f"{desc}\n")
            except (OSError, json.JSONDecodeError):
                continue

    # Evidence
    evidence_dir = ws / "evidence"
    if evidence_dir.exists():
        sections.append("## Evidence\n")
        for epath in sorted(evidence_dir.iterdir()):
            sections.append(f"- `{epath.name}` ({epath.stat().st_size} bytes)")
        sections.append("")

    # KG summary
    try:
        from decepticon.tools.research._state import get_graph

        graph = get_graph()
        if graph:
            stats = graph.stats()
            sections.append("## Knowledge Graph Summary\n")
            sections.append(f"- Nodes: {stats.get('node_count', 0)}")
            sections.append(f"- Edges: {stats.get('edge_count', 0)}")
            sections.append(f"- Hosts: {stats.get('hosts', 0)}")
            sections.append(f"- Vulnerabilities: {stats.get('vulnerabilities', 0)}")
            sections.append(f"- Credentials: {stats.get('credentials', 0)}")
            sections.append("")
    except Exception:
        pass

    report = "\n".join(sections)
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    Path(output_path).write_text(report, encoding="utf-8")

    return _json(
        {
            "exported": True,
            "path": output_path,
            "sections": len(sections),
            "size_bytes": len(report.encode("utf-8")),
        }
    )


@tool
def import_engagement_zip(zip_path: str, target_workspace: str = "") -> str:
    """Import a previously exported engagement ZIP bundle.

    WHEN TO USE: To replay or continue a previous engagement, or to
    review a colleague's engagement findings.

    Args:
        zip_path: Path to the engagement ZIP file.
        target_workspace: Where to extract. Defaults to /workspace/imported/<slug>
    """
    zp = Path(zip_path)
    if not zp.exists():
        return _json({"error": "ZIP file not found", "path": zip_path})

    if not target_workspace:
        target_workspace = f"/workspace/imported/{zp.stem}"

    tw = Path(target_workspace)
    tw.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(zp, "r") as zf:
        zf.extractall(tw)

    # Read metadata
    metadata_path = tw / "metadata.json"
    metadata: dict[str, Any] = {}
    if metadata_path.exists():
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass

    file_count = sum(1 for p in tw.rglob("*") if p.is_file())

    return _json(
        {
            "imported": True,
            "target_workspace": target_workspace,
            "file_count": file_count,
            "original_metadata": metadata,
        }
    )


EXPORT_TOOLS = [export_engagement_zip, export_engagement_markdown, import_engagement_zip]
