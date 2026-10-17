"""Server-owned catalog; caller selection never becomes a filesystem path."""

from __future__ import annotations

import os
from collections.abc import Iterable
from pathlib import Path

from .models import WorkflowError, WorkflowSnapshot


class WorkflowCatalog:
    """Immutable lookup built from explicitly registered documents."""

    def __init__(self, snapshots: Iterable[WorkflowSnapshot]) -> None:
        self._snapshots: dict[tuple[str, str], WorkflowSnapshot] = {}
        for snapshot in snapshots:
            key = (snapshot.id, snapshot.version)
            if key in self._snapshots:
                raise WorkflowError(f"duplicate workflow registration: {key}")
            self._snapshots[key] = snapshot

    @classmethod
    def from_directories(cls, directories: Iterable[Path]) -> WorkflowCatalog:
        """Load operator-approved directories, never model-specified paths."""
        return cls(
            WorkflowSnapshot.from_source(path.read_text(encoding="utf-8"))
            for directory in directories
            for path in sorted(directory.glob("*/WORKFLOW.md"))
        )

    @classmethod
    def default(cls) -> WorkflowCatalog:
        directories = [Path(__file__).with_name("catalog")]
        extra = os.environ.get("DECEPTICON_WORKFLOW_DIR")
        if extra:
            directory = Path(extra)
            if not directory.is_dir():
                raise WorkflowError(f"workflow catalog directory missing: {directory}")
            directories.append(directory)
        return cls.from_directories(directories)

    def resolve(self, workflow_id: str, version: str | None = None) -> WorkflowSnapshot:
        """Require an exact match; an unversioned ID must be unambiguous."""
        candidates = [
            snapshot
            for (candidate_id, candidate_version), snapshot in self._snapshots.items()
            if candidate_id == workflow_id and (version is None or version == candidate_version)
        ]
        if len(candidates) != 1:
            raise WorkflowError(f"unknown or ambiguous workflow: {workflow_id}@{version or '*'}")
        return candidates[0]
