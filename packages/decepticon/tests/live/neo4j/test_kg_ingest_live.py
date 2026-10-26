"""Live integration tests for the kg_ingest dispatcher + adapters.

Writes scanner-output fixture files to ``tmp_path``, invokes
:func:`ingest` against the live Neo4j, and asserts the expected nodes
land in the graph. Skips when Neo4j is unreachable.
"""

from __future__ import annotations

import json
from pathlib import Path

from decepticon.middleware.kg_internal.ingest import ingest
from decepticon.middleware.kg_internal.store import KGStore


def test_ingest_nmap_xml_creates_host_service_entrypoint(
    kgstore: KGStore, engagement: str, tmp_path: Path
) -> None:
    fixture = tmp_path / "nmap.xml"
    fixture.write_text(
        """<?xml version="1.0"?>
        <nmaprun>
          <host>
            <status state="up"/>
            <address addr="10.0.0.1" addrtype="ipv4"/>
            <hostnames><hostname name="live.test"/></hostnames>
            <ports>
              <port portid="80" protocol="tcp">
                <state state="open"/>
                <service name="http" product="nginx"/>
              </port>
            </ports>
          </host>
        </nmaprun>
        """,
        encoding="utf-8",
    )
    result = ingest(
        "nmap_xml",
        fixture,
        store=kgstore,
        engagement=engagement,
        created_by="test_recon",
        source_episode_id="ep-nmap",
    )
    assert result["scanner"] == "nmap_xml"
    assert result["hosts"] == 1
    assert result["services"] == 1
    assert result["entrypoints"] == 1

    # Verify in graph
    rows = kgstore.execute_read(
        "MATCH (h:Host) WHERE h.engagement = $eng RETURN h.label AS label",
        {"eng": engagement},
        engagement=engagement,
    )
    assert any(row["label"] == "live.test" for row in rows)

    svc_rows = kgstore.execute_read(
        "MATCH (s:Service) WHERE s.engagement = $eng RETURN s.port AS port",
        {"eng": engagement},
        engagement=engagement,
    )
    assert any(row["port"] == 80 for row in svc_rows)

    ep_rows = kgstore.execute_read(
        "MATCH (h:Host)-[:HOSTS]->(s:Service) WHERE h.engagement = $eng RETURN count(*) AS c",
        {"eng": engagement},
        engagement=engagement,
    )
    assert ep_rows[0]["c"] >= 1


def test_ingest_nuclei_jsonl_creates_vuln_linked_to_entrypoint(
    kgstore: KGStore, engagement: str, tmp_path: Path
) -> None:
    fixture = tmp_path / "nuclei.jsonl"
    fixture.write_text(
        json.dumps(
            {
                "template-id": "ssrf-detect",
                "info": {"severity": "critical", "tags": ["ssrf"]},
                "matched-at": "https://live.test/api",
                "host": "live.test",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    result = ingest(
        "nuclei_jsonl",
        fixture,
        store=kgstore,
        engagement=engagement,
        created_by="test_analyst",
        source_episode_id="ep-nuclei",
    )
    assert result["scanner"] == "nuclei_jsonl"
    assert result["parsed"] == 1

    vuln_rows = kgstore.execute_read(
        "MATCH (v:Vulnerability) WHERE v.engagement = $eng "
        "RETURN v.severity AS sev, v.rule_id AS rule_id",
        {"eng": engagement},
        engagement=engagement,
    )
    assert vuln_rows
    assert vuln_rows[0]["sev"] == "critical"
    assert vuln_rows[0]["rule_id"] == "ssrf-detect"

    # Entrypoint has HAS_VULN edge to the vulnerability.
    edge_rows = kgstore.execute_read(
        "MATCH (e:Entrypoint)-[:HAS_VULN]->(v:Vulnerability) "
        "WHERE e.engagement = $eng RETURN count(*) AS c",
        {"eng": engagement},
        engagement=engagement,
    )
    assert edge_rows[0]["c"] == 1


def test_ingest_httpx_jsonl_creates_host_service_entrypoint(
    kgstore: KGStore, engagement: str, tmp_path: Path
) -> None:
    fixture = tmp_path / "httpx.jsonl"
    fixture.write_text(
        json.dumps(
            {
                "url": "https://live.test:8443/admin",
                "host": "live.test",
                "port": 8443,
                "status-code": 200,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    result = ingest(
        "httpx_jsonl",
        fixture,
        store=kgstore,
        engagement=engagement,
        created_by="test_recon",
        source_episode_id="ep-httpx",
    )
    assert result["parsed"] == 1
    rows = kgstore.execute_read(
        "MATCH (e:Entrypoint) WHERE e.engagement = $eng RETURN e.label AS label",
        {"eng": engagement},
        engagement=engagement,
    )
    assert any("https://live.test:8443/admin" in (row["label"] or "") for row in rows)


def test_ingest_sarif_creates_vuln_linked_to_code_location(
    kgstore: KGStore, engagement: str, tmp_path: Path
) -> None:
    fixture = tmp_path / "scan.sarif"
    sarif = {
        "runs": [
            {
                "tool": {"driver": {"name": "semgrep"}},
                "results": [
                    {
                        "ruleId": "python.lang.security.audit.sqli",
                        "level": "error",
                        "message": {"text": "SQLi"},
                        "locations": [
                            {
                                "physicalLocation": {
                                    "artifactLocation": {"uri": "app/views.py"},
                                    "region": {"startLine": 42},
                                }
                            }
                        ],
                    }
                ],
            }
        ]
    }
    fixture.write_text(json.dumps(sarif), encoding="utf-8")
    result = ingest(
        "sarif",
        fixture,
        store=kgstore,
        engagement=engagement,
        created_by="test_analyst",
        source_episode_id="ep-sarif",
    )
    assert result["scanner"] == "sarif"
    assert result["results_processed"] == 1

    edge_rows = kgstore.execute_read(
        "MATCH (v:Vulnerability)-[:DEFINED_IN]->(c:CodeLocation) "
        "WHERE v.engagement = $eng RETURN v.severity AS sev, c.file AS file, c.start_line AS line",
        {"eng": engagement},
        engagement=engagement,
    )
    assert edge_rows
    assert edge_rows[0]["sev"] == "high"  # SARIF level=error → high
    assert edge_rows[0]["file"] == "app/views.py"
    assert edge_rows[0]["line"] == 42


def test_ingest_idempotent_rerun_same_file_merges(
    kgstore: KGStore, engagement: str, tmp_path: Path
) -> None:
    """Re-ingesting the same scanner output is idempotent — same nodes,
    same keys, no duplicates."""
    fixture = tmp_path / "nmap.xml"
    fixture.write_text(
        """<?xml version="1.0"?>
        <nmaprun>
          <host>
            <status state="up"/>
            <address addr="10.0.0.42" addrtype="ipv4"/>
            <ports>
              <port portid="443" protocol="tcp">
                <state state="open"/>
                <service name="https"/>
              </port>
            </ports>
          </host>
        </nmaprun>
        """,
        encoding="utf-8",
    )
    ingest(
        "nmap_xml",
        fixture,
        store=kgstore,
        engagement=engagement,
        created_by="recon",
        source_episode_id="ep-1",
    )
    ingest(
        "nmap_xml",
        fixture,
        store=kgstore,
        engagement=engagement,
        created_by="recon",
        source_episode_id="ep-2",
    )

    rows = kgstore.execute_read(
        "MATCH (h:Host) WHERE h.engagement = $eng AND h.key = 'host::10.0.0.42' "
        "RETURN count(h) AS c",
        {"eng": engagement},
        engagement=engagement,
    )
    assert rows[0]["c"] == 1  # dedup MERGE works
