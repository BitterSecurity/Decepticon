"""Policy-only pins for protected OPPLAN checkpoints created before workflows."""

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

from .models import WorkflowSnapshot


class LegacyObjective(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")
    workflow_stage: str | None = None


class LegacyPlan(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")
    objectives: tuple[LegacyObjective, ...] = ()
    workflow_task_bindings: dict[str, str] = Field(default_factory=dict)

    @property
    def unbound_checkpoint(self) -> bool:
        return (
            bool(self.objectives)
            and not self.workflow_task_bindings
            and all(row.workflow_stage is None for row in self.objectives)
        )


def legacy_snapshot(selected: WorkflowSnapshot) -> WorkflowSnapshot:
    document = selected.document
    metadata = document.metadata.model_copy(
        update={
            "version": selected.version + "-legacy",
            "stages": (),
        }
    )
    precedence = (
        Path(__file__).with_name("compatibility").joinpath("LEGACY.md").read_text(encoding="utf-8")
    )
    sections = ["## Common\n" + precedence + "\n" + document.common]
    sections.extend(f"## Agent: {role}\n{text}" for role, text in document.agents)
    source = (
        "---\n"
        + yaml.safe_dump(metadata.model_dump(mode="json"), sort_keys=False)
        + "---\n"
        + "\n\n".join(sections)
    )
    return WorkflowSnapshot.from_source(source)
