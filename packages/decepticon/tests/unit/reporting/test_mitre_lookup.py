"""ATT&CK lookup uses indexed STIX facts, never a format-only guess."""

from __future__ import annotations

import json

from decepticon.tools import mitre


def test_lookup_active_ids_and_reject_revoked_ids(tmp_path, monkeypatch):
    bundle = tmp_path / "enterprise.json"
    bundle.write_text(
        json.dumps(
            {
                "objects": [
                    {
                        "type": "x-mitre-tactic",
                        "id": "tactic--one",
                        "name": "Initial Access",
                        "x_mitre_shortname": "initial-access",
                        "external_references": [
                            {"source_name": "mitre-attack", "external_id": "TA0001"}
                        ],
                    },
                    {
                        "type": "attack-pattern",
                        "id": "attack-pattern--one",
                        "name": "Public App",
                        "description": "A test description",
                        "kill_chain_phases": [
                            {"kill_chain_name": "mitre-attack", "phase_name": "initial-access"},
                        ],
                        "external_references": [
                            {"source_name": "mitre-attack", "external_id": "T1190"}
                        ],
                    },
                    {
                        "type": "intrusion-set",
                        "id": "intrusion-set--one",
                        "name": "Test Group",
                        "external_references": [
                            {"source_name": "mitre-attack", "external_id": "G0001"}
                        ],
                    },
                    {
                        "type": "attack-pattern",
                        "id": "attack-pattern--old",
                        "name": "Old",
                        "revoked": True,
                        "external_references": [
                            {"source_name": "mitre-attack", "external_id": "T9999"}
                        ],
                    },
                    {
                        "type": "relationship",
                        "relationship_type": "uses",
                        "source_ref": "intrusion-set--one",
                        "target_ref": "attack-pattern--one",
                    },
                ]
            }
        )
    )
    monkeypatch.setattr(mitre, "ensure_stix_bundle", lambda: bundle)
    mitre._index.cache_clear()
    try:
        technique = json.loads(mitre.mitre_lookup_technique.invoke({"technique_id": "T1190"}))
        tactic = json.loads(mitre.mitre_lookup_tactic.invoke({"tactic_id": "TA0001"}))
        group = json.loads(mitre.mitre_lookup_group.invoke({"group_id": "G0001"}))
        assert technique["tactics"] == ["initial-access"]
        assert tactic["name"] == "Initial Access"
        assert group["technique_ids"] == ["T1190"]
        assert "error" in json.loads(mitre.mitre_lookup_technique.invoke({"technique_id": "T9999"}))
    finally:
        mitre._index.cache_clear()


def test_tool_roles():
    assert len(mitre.get_mitre_tools("finding_reporter")) == 3
    assert len(mitre.get_mitre_tools("exploit")) == 3
    assert not mitre.get_mitre_tools("recon")
