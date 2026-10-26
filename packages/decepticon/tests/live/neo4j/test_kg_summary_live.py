"""Live integration tests for ``build_summary`` against compose Neo4j.

Populates an engagement with a representative graph (hosts, services,
vulnerabilities, entrypoints, crown jewels) via ``kgstore.record_observations``
and asserts ``build_summary`` produces an LLM-ready markdown block.

Skips when Neo4j is unreachable — same convention as the rest of
``tests/integration/kg``.
"""

from __future__ import annotations

from typing import Any

from decepticon.middleware.kg_internal.store import KGStore
from decepticon.middleware.kg_internal.summary import (
    MAX_ENTRYPOINTS,
    MAX_VULNS,
    build_summary,
)


def _populate(store: KGStore, engagement: str) -> None:
    """Seed a representative engagement: 2 hosts, 2 services, 3 vulns,
    2 entrypoints (1 unexplored), 1 crown jewel reachable from
    entrypoint via a single edge chain."""
    observations: list[dict[str, Any]] = [
        # Hosts
        {
            "kind": "Host",
            "key": f"host::10.0.0.1::{engagement}",
            "label": "10.0.0.1",
            "props": {"ip": "10.0.0.1", "explored": True},
        },
        # Services
        {
            "kind": "Service",
            "key": f"service::10.0.0.1:80::{engagement}",
            "label": "10.0.0.1:80",
            "props": {"port": 80, "product": "nginx"},
        },
        # Vulnerabilities — mixed severity so ordering is testable
        {
            "kind": "Vulnerability",
            "key": f"vuln::ssti-search::{engagement}",
            "label": "SSTI in /search",
            "props": {"severity": "critical", "cwe": "CWE-1336"},
        },
        {
            "kind": "Vulnerability",
            "key": f"vuln::sqli-login::{engagement}",
            "label": "SQLi in /login",
            "props": {"severity": "high", "cwe": "CWE-89"},
        },
        {
            "kind": "Vulnerability",
            "key": f"vuln::open-redirect::{engagement}",
            "label": "Open redirect on /next",
            "props": {"severity": "low"},
        },
        # Entrypoint (vulnerable) — has HAS_VULN edge so it is NOT
        # listed in 'unexplored entrypoints'.
        {
            "kind": "Entrypoint",
            "key": f"entrypoint::vuln::{engagement}",
            "label": "https://app/login",
            "edges_out": [
                {
                    "to_key": f"vuln::sqli-login::{engagement}",
                    "kind": "HAS_VULN",
                    "weight": 0.4,
                }
            ],
        },
        # Entrypoint (unexplored)
        {
            "kind": "Entrypoint",
            "key": f"entrypoint::unexplored::{engagement}",
            "label": "https://app/admin",
        },
        # Crown jewel reachable via a single edge for path counting
        {
            "kind": "CrownJewel",
            "key": f"crown::admin-panel::{engagement}",
            "label": "admin_panel",
        },
    ]
    store.record_observations(
        observations,
        engagement=engagement,
        created_by="test_summary",
        source_episode_id="ep-summary",
    )
    # Wire the unexplored entrypoint to the crown jewel so it counts
    # as a viable path.
    store.execute_write(
        (
            "MATCH (e:Entrypoint {key: $ek, engagement: $eng}) "
            "MATCH (c:CrownJewel {key: $ck, engagement: $eng}) "
            "MERGE (e)-[r:LEADS_TO]->(c) "
            "ON CREATE SET r.engagement = $eng, r.firstseen = 1, r.lastupdated = 1"
        ),
        {
            "ek": f"entrypoint::unexplored::{engagement}",
            "ck": f"crown::admin-panel::{engagement}",
            "eng": engagement,
        },
        engagement=engagement,
    )


def test_summary_empty_engagement_shows_header_only(kgstore: KGStore, engagement: str) -> None:
    """A brand-new engagement gets the header + stats line, nothing else."""
    out = build_summary(kgstore, engagement=engagement)
    assert f"engagement={engagement}" in out
    assert "Nodes**: 0" in out
    assert "Top vulnerabilities" not in out
    assert "Unexplored entrypoints" not in out
    assert "Crown jewels" not in out


def test_summary_populated_engagement_contains_all_sections(
    kgstore: KGStore, engagement: str
) -> None:
    _populate(kgstore, engagement)
    out = build_summary(kgstore, engagement=engagement)
    assert f"engagement={engagement}" in out
    assert "Top vulnerabilities" in out
    assert "Unexplored entrypoints" in out
    assert "Crown jewels" in out


def test_summary_vulns_ordered_by_severity(kgstore: KGStore, engagement: str) -> None:
    """Critical before high before low in the rendered block."""
    _populate(kgstore, engagement)
    out = build_summary(kgstore, engagement=engagement)
    crit_pos = out.find("SSTI in /search")
    high_pos = out.find("SQLi in /login")
    low_pos = out.find("Open redirect")
    assert 0 < crit_pos < high_pos < low_pos


def test_summary_unexplored_entrypoint_listed_explored_omitted(
    kgstore: KGStore, engagement: str
) -> None:
    _populate(kgstore, engagement)
    out = build_summary(kgstore, engagement=engagement)
    # The unexplored entrypoint MUST appear in the entrypoints section.
    assert "https://app/admin" in out
    # The vulnerable entrypoint must NOT appear in entrypoints (it has
    # a HAS_VULN edge).
    ep_section_start = out.find("Unexplored entrypoints")
    if ep_section_start >= 0:
        next_section = out.find("\n\n", ep_section_start + 1)
        ep_section = (
            out[ep_section_start:next_section] if next_section > 0 else out[ep_section_start:]
        )
        assert "https://app/login" not in ep_section


def test_summary_crown_jewel_path_count_correct(kgstore: KGStore, engagement: str) -> None:
    _populate(kgstore, engagement)
    out = build_summary(kgstore, engagement=engagement)
    # The crown jewel was linked to the unexplored entrypoint via one
    # LEADS_TO edge, so paths = 1.
    assert "admin_panel (1 viable path)" in out


def test_summary_advances_with_revision(kgstore: KGStore, engagement: str) -> None:
    """The summary's revision token changes after a write."""
    before = build_summary(kgstore, engagement=engagement)
    _populate(kgstore, engagement)
    after = build_summary(kgstore, engagement=engagement)
    # The revision substring should differ.
    before_rev = before.split("Revision**:")[1].split("\n")[0]
    after_rev = after.split("Revision**:")[1].split("\n")[0]
    assert before_rev != after_rev


def test_summary_respects_vuln_cap(kgstore: KGStore, engagement: str) -> None:
    """When more than MAX_VULNS vulns exist, the block truncates."""
    observations: list[dict[str, Any]] = []
    for i in range(MAX_VULNS * 2):
        observations.append(
            {
                "kind": "Vulnerability",
                "key": f"vuln::cap-{i}::{engagement}",
                "label": f"vuln-{i}",
                "props": {"severity": "medium"},
            }
        )
    kgstore.record_observations(
        observations,
        engagement=engagement,
        created_by="test_summary",
        source_episode_id="ep-cap",
    )
    out = build_summary(kgstore, engagement=engagement)
    # Each vuln rendered as "- `[SEV]`"
    bullet_count = out.count("- `[")
    assert bullet_count == MAX_VULNS


def test_summary_respects_entrypoint_cap(kgstore: KGStore, engagement: str) -> None:
    observations: list[dict[str, Any]] = []
    for i in range(MAX_ENTRYPOINTS * 2):
        observations.append(
            {
                "kind": "Entrypoint",
                "key": f"entrypoint::cap-{i}::{engagement}",
                "label": f"https://ep-{i}/",
            }
        )
    kgstore.record_observations(
        observations,
        engagement=engagement,
        created_by="test_summary",
        source_episode_id="ep-cap-eps",
    )
    out = build_summary(kgstore, engagement=engagement)
    ep_section_start = out.find("Unexplored entrypoints")
    ep_section = out[ep_section_start:]
    # Each entrypoint is one "- " bullet.
    bullet_count = ep_section.count("- ")
    assert bullet_count == MAX_ENTRYPOINTS
