"""End-to-end tests for KGStore + migration runner against live Neo4j.

Exercised classes:

* :class:`TestMigrationsLive` — applies migrations against the live DB
  and confirms the named constraints + indexes exist.
* :class:`TestRecordObservationsLive` — single-host, dedup-merge,
  node+edges atomic batch, provenance auto-injection.
* :class:`TestSnapshotAndRevisionLive` — revision advances on write,
  snapshot returns an engagement-scoped subgraph.
* :class:`TestAttackGraphProtocolLive` — KGStore satisfies the
  Protocol structural contract at runtime against real driver objects.
* :class:`TestParallelWriteStress` — Q4 from the design notes: 16
  concurrent writers (a) writing distinct keys land 16 nodes, (b)
  writing the same deterministic key collapse to one.

All tests skip cleanly when the Neo4j stack is unreachable (see
``conftest.py``). Run live with::

    docker compose up -d neo4j
    DECEPTICON_NEO4J_URI=bolt://localhost:7687 \
    DECEPTICON_NEO4J_USER=neo4j \
    DECEPTICON_NEO4J_PASSWORD=decepticon-graph \
    uv run pytest packages/decepticon/tests/integration/kg/ -v
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest

from decepticon.middleware.kg_internal.migration_runner import (
    apply_migrations,
    list_applied,
)
from decepticon.middleware.kg_internal.store import KGStore
from decepticon.runtime.cart import AttackGraphProtocol, EngagementSnapshot

# ── Migrations ──────────────────────────────────────────────────────────


class TestMigrationsLive:
    def test_apply_migrations_creates_expected_constraints(self, kgstore: KGStore) -> None:
        # First call: applies whatever is pending. May return [] if
        # the migration was already applied by a prior test session.
        apply_migrations(kgstore)
        rows = kgstore.execute_read(
            "SHOW CONSTRAINTS YIELD name WHERE name IN $names RETURN name",
            {
                "names": [
                    "host_key_engagement",
                    "service_key_engagement",
                    "vulnerability_key_engagement",
                    "finding_key_engagement",
                    "migration_log_name",
                ]
            },
            engagement="schema",
        )
        present = {row["name"] for row in rows}
        assert "host_key_engagement" in present
        assert "service_key_engagement" in present
        assert "vulnerability_key_engagement" in present
        assert "finding_key_engagement" in present
        assert "migration_log_name" in present

    def test_apply_migrations_creates_v002_indexes(self, kgstore: KGStore) -> None:
        apply_migrations(kgstore)
        rows = kgstore.execute_read(
            "SHOW INDEXES YIELD name WHERE name IN $names RETURN name",
            {
                "names": [
                    "engagement_host_explored",
                    "engagement_vuln_severity",
                    "engagement_finding_status",
                    "vuln_embedding",
                ]
            },
            engagement="schema",
        )
        present = {row["name"] for row in rows}
        assert "engagement_host_explored" in present
        assert "engagement_vuln_severity" in present
        assert "engagement_finding_status" in present
        # Vector index — Q5 = yes, schema-only in V002.
        assert "vuln_embedding" in present

    def test_apply_migrations_records_in_migration_log(self, kgstore: KGStore) -> None:
        apply_migrations(kgstore)
        applied = list_applied(kgstore)
        assert "V001__initial_schema" in applied
        assert "V002__engagement_composite_indexes_and_provenance" in applied

    def test_apply_migrations_idempotent_no_double_work(self, kgstore: KGStore) -> None:
        # First call may run pending migrations or no-op. The second
        # call MUST be a no-op regardless of state.
        apply_migrations(kgstore)
        second = apply_migrations(kgstore)
        assert second == []


# ── record_observations ─────────────────────────────────────────────────


class TestRecordObservationsLive:
    def test_record_single_host(self, kgstore: KGStore, engagement: str) -> None:
        result = kgstore.record_observations(
            [
                {
                    "kind": "Host",
                    "key": f"host::single::{engagement}",
                    "label": "10.0.0.1",
                    "props": {"ip": "10.0.0.1", "explored": False},
                }
            ],
            engagement=engagement,
            created_by="test_record",
            source_episode_id="ep-single",
        )
        assert result["created"] == 1
        assert result["merged"] == 0
        assert result["edges"] == 0
        assert isinstance(result["revision"], str)

    def test_record_same_key_merges_idempotent(self, kgstore: KGStore, engagement: str) -> None:
        obs: dict[str, Any] = {
            "kind": "Host",
            "key": f"host::merge::{engagement}",
            "label": "10.0.0.2",
            "props": {"ip": "10.0.0.2"},
        }
        first = kgstore.record_observations(
            [obs], engagement=engagement, created_by="t", source_episode_id="ep-1"
        )
        second = kgstore.record_observations(
            [obs], engagement=engagement, created_by="t", source_episode_id="ep-2"
        )
        assert first["created"] == 1
        assert second["created"] == 0
        assert second["merged"] == 1

    def test_record_nodes_with_outgoing_edges(self, kgstore: KGStore, engagement: str) -> None:
        host_key = f"host::e::{engagement}"
        svc_key = f"service::e::{engagement}"
        result = kgstore.record_observations(
            [
                {
                    "kind": "Host",
                    "key": host_key,
                    "label": "host-e",
                    "edges_out": [{"to_key": svc_key, "kind": "HOSTS", "weight": 0.5}],
                },
                {
                    "kind": "Service",
                    "key": svc_key,
                    "label": "service-e",
                },
            ],
            engagement=engagement,
            created_by="t",
            source_episode_id="ep-edges",
        )
        assert result["created"] == 2
        assert result["edges"] == 1

        # Verify the edge exists in the graph.
        rows = kgstore.execute_read(
            "MATCH (h:Host {key: $hk, engagement: $eng})-[r:HOSTS]->(s:Service {key: $sk, engagement: $eng}) "
            "RETURN r.weight AS weight",
            {"hk": host_key, "sk": svc_key, "eng": engagement},
            engagement=engagement,
        )
        assert rows
        assert rows[0]["weight"] == pytest.approx(0.5)

    def test_provenance_auto_injected(self, kgstore: KGStore, engagement: str) -> None:
        kgstore.record_observations(
            [
                {
                    "kind": "Host",
                    "key": f"host::prov::{engagement}",
                    "label": "h",
                }
            ],
            engagement=engagement,
            created_by="analyst",
            source_episode_id="tc-real",
        )
        rows = kgstore.execute_read(
            "MATCH (n:Host) WHERE n.engagement = $eng AND n.key = $key "
            "RETURN n.engagement AS engagement, "
            "       n.created_by AS created_by, "
            "       n.source_episode_id AS sep, "
            "       n.firstseen AS firstseen, "
            "       n.lastupdated AS lastupdated",
            {"eng": engagement, "key": f"host::prov::{engagement}"},
            engagement=engagement,
        )
        assert rows
        row = rows[0]
        assert row["engagement"] == engagement
        assert row["created_by"] == "analyst"
        assert row["sep"] == "tc-real"
        assert isinstance(row["firstseen"], int) and row["firstseen"] > 0
        assert row["lastupdated"] >= row["firstseen"]

    def test_provenance_cannot_be_forged_by_agent(self, kgstore: KGStore, engagement: str) -> None:
        """Even if the agent puts engagement/firstseen/created_by in
        props, the trusted middleware-supplied values win."""
        kgstore.record_observations(
            [
                {
                    "kind": "Host",
                    "key": f"host::forge::{engagement}",
                    "label": "h",
                    "props": {
                        "engagement": "MALICIOUS",
                        "firstseen": 0,
                        "created_by": "spoofed",
                        "source_episode_id": "spoofed",
                    },
                }
            ],
            engagement=engagement,
            created_by="analyst",
            source_episode_id="tc-trusted",
        )
        rows = kgstore.execute_read(
            "MATCH (n:Host) WHERE n.engagement = $eng AND n.key = $key "
            "RETURN n.engagement AS engagement, "
            "       n.created_by AS created_by, "
            "       n.source_episode_id AS sep, "
            "       n.firstseen AS firstseen",
            {"eng": engagement, "key": f"host::forge::{engagement}"},
            engagement=engagement,
        )
        assert rows
        row = rows[0]
        assert row["engagement"] == engagement  # trusted value
        assert row["created_by"] == "analyst"  # not "spoofed"
        assert row["sep"] == "tc-trusted"
        assert row["firstseen"] > 0  # not 0


# ── snapshot / revision ─────────────────────────────────────────────────


class TestSnapshotAndRevisionLive:
    def test_revision_advances_after_write(self, kgstore: KGStore, engagement: str) -> None:
        rev_before = kgstore.revision(engagement=engagement)
        kgstore.record_observations(
            [{"kind": "Host", "key": f"host::rev::{engagement}", "label": "h"}],
            engagement=engagement,
            created_by="t",
            source_episode_id="ep",
        )
        rev_after = kgstore.revision(engagement=engagement)
        assert rev_before != rev_after

    def test_snapshot_returns_engagement_scoped_subgraph(
        self, kgstore: KGStore, engagement: str
    ) -> None:
        host_key = f"host::snap::{engagement}"
        svc_key = f"service::snap::{engagement}"
        kgstore.record_observations(
            [
                {
                    "kind": "Host",
                    "key": host_key,
                    "label": "h-snap",
                    "edges_out": [{"to_key": svc_key, "kind": "HOSTS", "weight": 0.5}],
                },
                {"kind": "Service", "key": svc_key, "label": "s-snap"},
            ],
            engagement=engagement,
            created_by="t",
            source_episode_id="ep",
        )
        snap = kgstore.snapshot(engagement=engagement)
        assert isinstance(snap, EngagementSnapshot)
        kinds = {snk.kind for snk in snap.nodes}
        assert "host" in kinds
        assert "service" in kinds
        # At least one HOSTS edge.
        edge_kinds = {kind for (_, _, kind) in snap.edges}
        assert "hosts" in edge_kinds


# ── AttackGraphProtocol structural conformance ─────────────────────────


class TestAttackGraphProtocolLive:
    def test_kgstore_is_attack_graph_protocol(self, kgstore: KGStore) -> None:
        """Confirm the runtime-checkable Protocol matches the live class."""
        assert isinstance(kgstore, AttackGraphProtocol)


# ── 16-agent parallel write stress (Q4) ────────────────────────────────


class TestParallelWriteStress:
    """Simulate 16 specialist agents writing in parallel within one
    engagement. The Neo4j driver's MVCC + transaction retry must
    handle this without a Python-level lock."""

    def test_sixteen_unique_keys_all_land(self, kgstore: KGStore, engagement: str) -> None:
        n = 16

        def write_one(i: int) -> dict[str, Any]:
            return kgstore.record_observations(
                [
                    {
                        "kind": "Host",
                        "key": f"host::parallel::{i}::{engagement}",
                        "label": f"agent-{i}",
                    }
                ],
                engagement=engagement,
                created_by=f"agent-{i}",
                source_episode_id=f"ep-{i}",
            )

        with ThreadPoolExecutor(max_workers=n) as pool:
            results = list(pool.map(write_one, range(n)))

        assert all(r["created"] == 1 for r in results)
        rows = kgstore.execute_read(
            "MATCH (n:Host) WHERE n.engagement = $eng "
            "AND n.key STARTS WITH 'host::parallel::' RETURN count(n) AS c",
            {"eng": engagement},
            engagement=engagement,
        )
        assert rows[0]["c"] == n

    def test_sixteen_same_key_collapses_to_one(self, kgstore: KGStore, engagement: str) -> None:
        n = 16
        shared_key = f"host::shared::{engagement}"

        def write_one(i: int) -> dict[str, Any]:
            return kgstore.record_observations(
                [
                    {
                        "kind": "Host",
                        "key": shared_key,
                        "label": "shared",
                        "props": {f"prop_from_agent_{i}": True},
                    }
                ],
                engagement=engagement,
                created_by=f"agent-{i}",
                source_episode_id=f"ep-{i}",
            )

        with ThreadPoolExecutor(max_workers=n) as pool:
            list(pool.map(write_one, range(n)))

        # End state: exactly one node with this key.
        rows = kgstore.execute_read(
            "MATCH (n:Host) WHERE n.engagement = $eng AND n.key = $key RETURN count(n) AS c",
            {"eng": engagement, "key": shared_key},
            engagement=engagement,
        )
        assert rows[0]["c"] == 1


# ── delete_stale (Cartography pattern) ─────────────────────────────────


class TestDeleteStale:
    def test_delete_stale_removes_old_nodes(self, kgstore: KGStore, engagement: str) -> None:
        kgstore.record_observations(
            [{"kind": "Host", "key": f"host::stale::{engagement}", "label": "old"}],
            engagement=engagement,
            created_by="t",
            source_episode_id="ep",
        )
        # Pretend we are far in the future — cutoff well after now.
        future_cutoff = int(time.time()) + 3600
        deleted = kgstore.delete_stale(engagement=engagement, before_unix=future_cutoff)
        assert deleted >= 1

    def test_delete_stale_keeps_recent_nodes(self, kgstore: KGStore, engagement: str) -> None:
        kgstore.record_observations(
            [{"kind": "Host", "key": f"host::fresh::{engagement}", "label": "new"}],
            engagement=engagement,
            created_by="t",
            source_episode_id="ep",
        )
        # Cutoff in the past — nothing should be deleted.
        past_cutoff = int(time.time()) - 3600
        deleted = kgstore.delete_stale(engagement=engagement, before_unix=past_cutoff)
        assert deleted == 0
