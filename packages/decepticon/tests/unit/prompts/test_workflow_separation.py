from pathlib import Path

import pytest

from decepticon.agents.prompts import PromptBuilder
from decepticon.skillogy.builder.skills import emit_skill_records
from decepticon.workflows import WorkflowCatalog

ROOT = Path(__file__).resolve().parents[3] / "decepticon"


@pytest.mark.parametrize(
    "role", [path.stem for path in (ROOT / "agents/prompts/standard").glob("*.md")]
)
def test_static_prompts_do_not_load_operational_workflows(role: str) -> None:
    prompt = PromptBuilder(role).build()
    assert "<AGENT_WORKFLOW>" not in prompt
    assert "<workflow-contract>" not in prompt


def test_migrated_methods_are_discoverable_skillogy_assets() -> None:
    nodes, _ = emit_skill_records(ROOT / "skills")
    methods = list((ROOT / "skills/standard").glob("*/specialist-methods/SKILL.md"))
    assert len(methods) == 16
    for path in methods:
        canonical = "/skills/" + path.relative_to(ROOT / "skills").as_posix()
        assert any(node.properties.get("path") == canonical for node in nodes)


@pytest.mark.parametrize(
    "workflow_id", ["bug-bounty", "red-team", "pentest", "vulnerability-research"]
)
def test_builtin_workflow_quality_dependencies_and_role_isolation(workflow_id: str) -> None:
    document = WorkflowCatalog.default().resolve(workflow_id).document
    stages = {stage.id: stage for stage in document.metadata.stages}
    assert stages["verify"].specialist == "finding_verifier"
    assert stages["report"].depends_on == ("verify",)
    assert stages["report"].specialist == "finding_reporter"
    assert stages["report"].required_artifacts
    orchestrator = dict(document.agents)["decepticon"]
    assert orchestrator in document.compile("decepticon")
    assert orchestrator not in document.compile("recon")


@pytest.mark.parametrize(
    "workflow_id,proof_stage",
    [("bug-bounty", "validate"), ("pentest", "assess"), ("red-team", "prove")],
)
def test_supplied_evidence_can_bypass_discovery_without_bypassing_quality(
    workflow_id: str, proof_stage: str
) -> None:
    document = WorkflowCatalog.default().resolve(workflow_id).document
    stages = {stage.id: stage for stage in document.metadata.stages}
    assert stages[proof_stage].depends_on == ("scope",)
    assert stages["report"].depends_on == ("verify",)
    assert stages["verify"].depends_on == (proof_stage,)


def test_legacy_orchestration_skills_cannot_restore_business_lifecycle() -> None:
    nodes, _ = emit_skill_records(ROOT / "skills")
    paths = {node.properties.get("path") for node in nodes}
    assert "/skills/standard/decepticon/engagement-startup/SKILL.md" not in paths
    assert "/skills/standard/decepticon/engagement-lifecycle/SKILL.md" not in paths


@pytest.mark.parametrize(
    "workflow_id,stage_id,role",
    [
        ("bug-bounty", "validate", "cloud_hunter"),
        ("bug-bounty", "validate", "analyst"),
        ("bug-bounty", "discover", "osint_operator"),
        ("pentest", "assess", "ad_operator"),
        ("red-team", "prove", "postexploit"),
        ("red-team", "prove", "cloud_hunter"),
        ("vulnerability-research", "research", "reverser"),
        ("vulnerability-research", "reproduce", "contract_auditor"),
    ],
)
def test_stages_accept_domain_capabilities_without_self_verification(
    workflow_id: str, stage_id: str, role: str
) -> None:
    document = WorkflowCatalog.default().resolve(workflow_id).document
    stages = {stage.id: stage for stage in document.metadata.stages}
    assert stages[stage_id].allows(role)
    assert f'"id":"{stage_id}"' in document.compile(role)
    assert not stages["verify"].allows(role)
    assert stages["verify"].specialists == ()
    assert stages["report"].specialists == ()


def test_red_team_supplied_access_does_not_force_initial_access_or_expansion() -> None:
    document = WorkflowCatalog.default().resolve("red-team").document
    stages = {stage.id: stage for stage in document.metadata.stages}
    assert "access" not in stages and "objectives" not in stages
    assert stages["prove"].depends_on == ("scope",)
    assert stages["prove"].allows("postexploit")
    assert stages["verify"].depends_on == ("prove",)
    assert "findings/objective-results.md" in stages["prove"].required_artifacts
