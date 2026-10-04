"""SKILL.md → :Skill nodes + explicit edges.

Reuses ``decepticon.skill_audit.frontmatter`` for parsing and
``decepticon.skill_audit.aliases`` for subdomain normalisation, so
canonical-name handling stays in one place. The Phase 0 validator has
already guaranteed every file conforms to the schema; this module
trusts that contract and does not re-validate fields.

Edge emission policy (Phase 1a)
-------------------------------
- ``IN_PHASE``     : every Skill → its canonical Phase.
- ``TAGGED``       : Skill → :Tag for each frontmatter tag.
- ``IMPLEMENTS``   : Skill → :Technique for each Enterprise-format
                      mitre_attack ID (``T\\d{4}(\\.\\d{3})?``). ICS
                      ``T0xxx`` and ATLAS ``AML.T*`` values are kept on
                      the Skill node as ``mitre_attack_raw`` but no
                      edge is produced until those matrix importers
                      land (Phase 1b/2).
- ``BELONGS_TO``   : Skill → :MoC when the skill's subdomain matches a
                      MoC's ``parent_phase``. Computed conservatively:
                      if exactly one MoC exists for that phase the
                      skill joins it; otherwise (zero or many) the
                      skill skips this edge and the MoC stays a
                      navigation marker only.

The :Tag nodes are emitted here too — tag vocabulary is free-form per
spec §1.4 EP-7, so any new tag silently introduces a new :Tag node.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from decepticon.skill_audit.aliases import resolve_subdomain
from decepticon.skill_audit.frontmatter import (
    FrontmatterParseError,
    parse_frontmatter,
)
from decepticon.skill_audit.mitre import MitreMatrix, classify_mitre_id, coerce_mitre_list
from decepticon.skillogy.builder.model import Edge, Node
from decepticon.skillogy.builder.relations import relation_edges
from decepticon.skillogy.builder.seeds import load_mocs

_SKILL_ROOT_RE = re.compile(r"(?:^|/)skills/.*$")


def _canonical_skill_path(skill_md: Path, root: Path) -> str:
    """Return the ``/skills/<...>/SKILL.md`` form used as the node key."""
    rel = skill_md.relative_to(root).as_posix()
    return "/skills/" + rel


def _coerce_str_list(value: Any) -> list[str]:
    """Tags / allowed-tools / mitre lists may be YAML lists or CSV strings."""
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, str):
        return [t.strip() for t in value.split(",") if t.strip()]
    return [str(value).strip()] if str(value).strip() else []


def _truthy_str(value: Any) -> str:
    return value.strip() if isinstance(value, str) and value.strip() else ""


def _build_moc_lookup() -> dict[str, list[str]]:
    """Group MoC names by their parent_phase."""
    out: dict[str, list[str]] = {}
    for moc in load_mocs():
        out.setdefault(moc.parent_phase, []).append(moc.name)
    return out


def emit_skill_records(
    skills_root: Path,
    *,
    commit_sha: str = "",
    built_at: datetime | None = None,
) -> tuple[list[Node], list[Edge]]:
    """Walk ``skills_root`` and emit a (nodes, edges) tuple for every SKILL.md.

    ``commit_sha`` and ``built_at`` are stamped onto every :Skill node so
    runtime tools can attribute the graph to a specific build. The CLI
    populates these from git + ``datetime.now(UTC)``; tests pass them
    explicitly for byte-identical determinism.
    """
    if not skills_root.exists():
        raise FileNotFoundError(f"skills root not found: {skills_root}")
    if built_at is None:
        built_at = datetime.now(timezone.utc)
    built_at_iso = built_at.isoformat()

    moc_lookup = _build_moc_lookup()
    nodes: list[Node] = []
    edges: list[Edge] = []
    seen_tags: set[str] = set()
    relation_blocks: list[tuple[str, Any, Path]] = []
    skill_names: set[str] = set()

    for skill_md in sorted(skills_root.rglob("SKILL.md")):
        text = skill_md.read_text(encoding="utf-8")
        try:
            meta, body = parse_frontmatter(text)
        except FrontmatterParseError as exc:
            raise RuntimeError(
                f"{skill_md}: frontmatter parse failed — Phase 0 validator should have caught this: {exc}"
            ) from exc

        name = str(meta.get("name") or "").strip()
        description = str(meta.get("description") or "").strip()
        if not name or not description:
            raise RuntimeError(
                f"{skill_md}: missing name or description (Phase 0 validator should have caught this)"
            )
        if name in skill_names:
            raise RuntimeError(f"{skill_md}: duplicate Skill name {name!r}")
        skill_names.add(name)
        metadata_raw = meta.get("metadata")
        metadata: dict[str, Any] = metadata_raw if isinstance(metadata_raw, dict) else {}
        if "skillogy" in metadata:
            relation_blocks.append((name, metadata["skillogy"], skill_md))
        raw_subdomain = str(metadata.get("subdomain") or "").strip()
        subdomain = resolve_subdomain(raw_subdomain) if raw_subdomain else ""

        path = _canonical_skill_path(skill_md, skills_root)
        body_bytes = body.encode("utf-8")
        body_sha = "sha256:" + hashlib.sha256(body_bytes).hexdigest()

        mitre_raw = coerce_mitre_list(metadata.get("mitre_attack"))
        tags_raw = _coerce_str_list(metadata.get("tags"))
        aatmf_raw = _coerce_str_list(metadata.get("aatmf_tactic"))
        upstream_raw = _truthy_str(metadata.get("upstream_ref"))
        when_to_use = _truthy_str(metadata.get("when_to_use"))
        allowed_tools = _coerce_str_list(meta.get("allowed-tools") or meta.get("allowed_tools"))

        # === :Skill node ===
        props: dict[str, Any] = {
            "name": name,
            "path": path,
            "description": description,
            "body": body,
            "content_sha256": body_sha,
            "size_bytes": len(body_bytes),
            "subdomain": subdomain,
            "when_to_use": when_to_use,
            "allowed_tools": allowed_tools,
            "mitre_attack_raw": mitre_raw,
            "tags_raw": tags_raw,
            "aatmf_tactic_raw": aatmf_raw,
            "upstream_ref_raw": upstream_raw,
            "commit_sha": commit_sha,
            "built_at": built_at_iso,
        }
        nodes.append(Node(label="Skill", key_field="name", properties=props))

        # === IN_PHASE (when subdomain is set) ===
        if subdomain:
            edges.append(
                Edge(
                    edge_type="IN_PHASE",
                    from_label="Skill",
                    from_key_field="name",
                    from_key=name,
                    to_label="Phase",
                    to_key_field="name",
                    to_key=subdomain,
                )
            )
            # === BELONGS_TO (single MoC under this phase, if unambiguous) ===
            mocs_here = moc_lookup.get(subdomain, [])
            if len(mocs_here) == 1:
                edges.append(
                    Edge(
                        edge_type="BELONGS_TO",
                        from_label="Skill",
                        from_key_field="name",
                        from_key=name,
                        to_label="MoC",
                        to_key_field="name",
                        to_key=mocs_here[0],
                    )
                )

        # === TAGGED + :Tag nodes ===
        for tag in tags_raw:
            if tag not in seen_tags:
                seen_tags.add(tag)
                nodes.append(Node(label="Tag", key_field="name", properties={"name": tag}))
            edges.append(
                Edge(
                    edge_type="TAGGED",
                    from_label="Skill",
                    from_key_field="name",
                    from_key=name,
                    to_label="Tag",
                    to_key_field="name",
                    to_key=tag,
                )
            )

        # === IMPLEMENTS (Enterprise / Mobile T1xxx only in Phase 1a) ===
        for mitre_id in mitre_raw:
            if classify_mitre_id(mitre_id) is MitreMatrix.ENTERPRISE_OR_MOBILE:
                edges.append(
                    Edge(
                        edge_type="IMPLEMENTS",
                        from_label="Skill",
                        from_key_field="name",
                        from_key=name,
                        to_label="Technique",
                        to_key_field="id",
                        to_key=mitre_id,
                    )
                )

    for name, block, skill_md in relation_blocks:
        try:
            edges.extend(relation_edges(name, block, skill_names))
        except ValueError as exc:
            raise RuntimeError(f"{skill_md}: {exc}") from exc

    return nodes, edges
