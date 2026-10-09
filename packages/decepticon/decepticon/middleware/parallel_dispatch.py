"""Parallel sub-agent dispatch middleware.

Enables concurrent execution of independent OPPLAN objectives by analyzing
objective dependencies and dispatching non-dependent objectives in parallel
via LangGraph's Send() mechanism.
"""

from __future__ import annotations

import logging
from typing import Any

from langchain.agents.middleware import AgentMiddleware

log = logging.getLogger(__name__)


class ParallelDispatchMiddleware(AgentMiddleware):
    """Analyze OPPLAN objective dependencies and mark parallelizable groups.

    This middleware runs BEFORE the orchestrator's dispatch logic. It reads
    the OPPLAN's objective list and their dependency graph, then annotates
    the state with ``parallel_groups`` — lists of objective IDs that can be
    executed concurrently.

    The orchestrator's dispatch loop can then use this annotation to Send()
    multiple sub-agents simultaneously instead of sequentially.
    """

    def __init__(self, max_parallel: int = 4):
        self.max_parallel = max_parallel

    @staticmethod
    def build_dependency_graph(objectives: list[dict]) -> dict[str, set[str]]:
        """Build adjacency list of objective dependencies."""
        deps: dict[str, set[str]] = {}
        for obj in objectives:
            obj_id = obj.get("id", "")
            depends_on = set(obj.get("depends_on", []) or [])
            deps[obj_id] = depends_on
        return deps

    @staticmethod
    def topological_groups(deps: dict[str, set[str]]) -> list[list[str]]:
        """Compute parallel execution waves via topological sort.

        Returns groups where all objectives within a group have their
        dependencies satisfied by prior groups. Objectives in the same
        group can run concurrently.
        """
        remaining = dict(deps)
        completed: set[str] = set()
        groups: list[list[str]] = []

        while remaining:
            # Find all objectives whose dependencies are satisfied
            ready = [
                obj_id for obj_id, obj_deps in remaining.items() if obj_deps.issubset(completed)
            ]

            if not ready:
                # Cycle detected — break by taking the first remaining
                ready = [next(iter(remaining))]
                log.warning("parallel_dispatch: cycle detected, forcing: %s", ready[0])

            groups.append(sorted(ready))
            for obj_id in ready:
                completed.add(obj_id)
                del remaining[obj_id]

        return groups

    async def __call__(self, state: dict, config: dict, **kwargs: Any) -> dict:
        opplan = state.get("opplan") or {}
        objectives = opplan.get("objectives", [])

        if not objectives:
            return state

        deps = self.build_dependency_graph(objectives)
        groups = self.topological_groups(deps)

        # Cap group sizes
        capped_groups = []
        for group in groups:
            for i in range(0, len(group), self.max_parallel):
                capped_groups.append(group[i : i + self.max_parallel])

        state["parallel_groups"] = capped_groups
        state["parallel_dispatch_enabled"] = True

        log.info(
            "parallel_dispatch: %d objectives -> %d waves (max %d concurrent)",
            len(objectives),
            len(capped_groups),
            self.max_parallel,
        )

        return state
