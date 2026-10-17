"""Typed, immutable workflow documents and checkpoint snapshots."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Annotated, Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

Slug = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_-]*$")]
Nonempty = Annotated[str, Field(min_length=1)]


class WorkflowError(ValueError):
    """Invalid workflow input or incompatible checkpoint selection."""

    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)


class WorkflowStage(BaseModel):
    """OPPLAN stage contract; OPPLAN remains the execution state owner."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    id: Slug
    specialist: Slug
    specialists: tuple[Slug, ...] = ()
    depends_on: tuple[Slug, ...] = ()
    required_artifacts: tuple[Nonempty, ...] = ()

    def allows(self, role: str) -> bool:
        """Allow the primary specialist and catalog-declared alternatives."""
        return role == self.specialist or role in self.specialists


class WorkflowMetadata(BaseModel):
    """Validated catalog frontmatter."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    id: Slug
    version: Nonempty
    description: Nonempty
    purpose: Literal["execution", "planning"] = "execution"
    stages: tuple[WorkflowStage, ...] = ()

    @model_validator(mode="after")
    def check_graph(self) -> Self:
        by_id = {stage.id: stage for stage in self.stages}
        if len(by_id) != len(self.stages):
            raise WorkflowError("duplicate workflow stage ID")
        resolved: set[str] = set()
        for stage in self.stages:
            if len(set(stage.depends_on)) != len(stage.depends_on):
                raise WorkflowError(f"duplicate prerequisites for {stage.id}")
            if set(stage.depends_on) - by_id.keys():
                raise WorkflowError(f"unknown prerequisite for {stage.id}")
        while len(resolved) < len(by_id):
            ready = {s.id for s in self.stages if set(s.depends_on) <= resolved} - resolved
            if not ready:
                raise WorkflowError("cyclic workflow stage prerequisites")
            resolved.update(ready)
        return self


@dataclass(frozen=True, slots=True)
class WorkflowDocument:
    """Parsed sections kept separate so specialists receive scoped context."""

    metadata: WorkflowMetadata
    common: str
    agents: tuple[tuple[str, str], ...]

    def compile(self, role: str) -> str:
        """Compile role-scoped policy plus machine-readable OPPLAN metadata."""
        sections = [self.common]
        sections.extend(text for agent, text in self.agents if agent == role)
        stages = self.metadata.stages
        if role != "decepticon":
            stages = tuple(stage for stage in stages if stage.allows(role))
        contract = self.metadata.model_copy(update={"stages": stages}).model_dump_json()
        return "\n\n".join([f"<workflow-contract>{contract}</workflow-contract>", *sections])


def parse_workflow(source: str) -> WorkflowDocument:
    """Parse a registered WORKFLOW.md; refuse ambiguous or unscoped sections."""
    parts = source.split("---", 2)
    if len(parts) != 3 or parts[0].strip():
        raise WorkflowError("WORKFLOW.md requires YAML frontmatter")
    try:
        metadata = WorkflowMetadata.model_validate(yaml.safe_load(parts[1]))
    except yaml.YAMLError as exc:
        raise WorkflowError(f"invalid workflow YAML: {exc}") from exc
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in parts[2].splitlines():
        if line.startswith("## "):
            heading = line[3:].strip()
            if heading != "Common" and not re.fullmatch(r"Agent: [a-z][a-z0-9_-]*", heading):
                raise WorkflowError(f"unsupported workflow section: {heading}")
            if heading in sections:
                raise WorkflowError(f"duplicate workflow section: {heading}")
            current = heading
            sections[current] = []
        elif current is not None:
            sections[current].append(line)
        elif line.strip():
            raise WorkflowError("workflow content must belong to Common or Agent sections")
    return WorkflowDocument(
        metadata=metadata,
        common="\n".join(sections.get("Common", [])).strip(),
        agents=tuple(
            (key.removeprefix("Agent: "), "\n".join(lines).strip())
            for key, lines in sections.items()
            if key.startswith("Agent: ")
        ),
    )


class WorkflowSnapshot(BaseModel):
    """Full content pin retained in checkpoints even when catalogs are updated."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    id: Slug
    version: Nonempty
    sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    source: Nonempty

    @model_validator(mode="after")
    def check_content(self) -> Self:
        document = parse_workflow(self.source)
        if (self.id, self.version) != (document.metadata.id, document.metadata.version):
            raise WorkflowError("workflow snapshot identity does not match content")
        if self.sha256 != hashlib.sha256(self.source.encode()).hexdigest():
            raise WorkflowError("workflow snapshot content hash mismatch")
        return self

    @property
    def document(self) -> WorkflowDocument:
        return parse_workflow(self.source)

    @classmethod
    def from_source(cls, source: str) -> Self:
        metadata = parse_workflow(source).metadata
        return cls(
            id=metadata.id,
            version=metadata.version,
            source=source,
            sha256=hashlib.sha256(source.encode()).hexdigest(),
        )
