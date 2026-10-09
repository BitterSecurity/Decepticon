"""Parallel execution helper tools for the orchestrator."""

from __future__ import annotations

import json
from typing import Any

from langchain_core.tools import tool


def _json(data: Any) -> str:
    return json.dumps(data, indent=2, default=str, ensure_ascii=False)


@tool
def analyze_objective_parallelism(opplan_json: str) -> str:
    """Analyze an OPPLAN's objectives for parallel execution opportunities.

    WHEN TO USE: Before dispatching objectives to sub-agents. Identifies
    which objectives can run concurrently based on their dependency graph.

    Args:
        opplan_json: JSON string of the OPPLAN containing objectives with
                     'id' and 'depends_on' fields.

    Returns:
        JSON with parallel execution waves, estimated speedup, and
        the dependency graph visualization.
    """
    try:
        opplan = json.loads(opplan_json)
    except json.JSONDecodeError:
        return _json({"error": "invalid JSON"})

    objectives = opplan.get("objectives", [])
    if not objectives:
        return _json({"error": "no objectives found", "waves": []})

    from decepticon.middleware.parallel_dispatch import ParallelDispatchMiddleware

    deps = ParallelDispatchMiddleware.build_dependency_graph(objectives)
    groups = ParallelDispatchMiddleware.topological_groups(deps)

    # Calculate theoretical speedup
    sequential_steps = len(objectives)
    parallel_steps = len(groups)
    speedup = round(sequential_steps / max(parallel_steps, 1), 2)

    waves = []
    for i, group in enumerate(groups):
        wave_objectives = []
        for obj_id in group:
            obj = next((o for o in objectives if o.get("id") == obj_id), {})
            wave_objectives.append(
                {
                    "id": obj_id,
                    "name": obj.get("name", obj_id),
                    "depends_on": list(deps.get(obj_id, set())),
                }
            )
        waves.append({"wave": i + 1, "objectives": wave_objectives})

    return _json(
        {
            "total_objectives": len(objectives),
            "total_waves": len(groups),
            "theoretical_speedup": f"{speedup}x",
            "waves": waves,
        }
    )


@tool
def track_parallel_execution(wave_json: str) -> str:
    """Track the status of a parallel execution wave.

    WHEN TO USE: During parallel objective execution to monitor which
    objectives in the current wave have completed.

    Args:
        wave_json: JSON with objective IDs and their statuses.
    """
    try:
        wave = json.loads(wave_json)
    except json.JSONDecodeError:
        return _json({"error": "invalid JSON"})

    objectives = wave.get("objectives", [])
    completed = [o for o in objectives if o.get("status") == "completed"]
    failed = [o for o in objectives if o.get("status") == "failed"]
    running = [o for o in objectives if o.get("status") == "running"]
    pending = [o for o in objectives if o.get("status") in ("pending", None)]

    all_done = len(completed) + len(failed) == len(objectives)

    return _json(
        {
            "wave_complete": all_done,
            "total": len(objectives),
            "completed": len(completed),
            "failed": len(failed),
            "running": len(running),
            "pending": len(pending),
            "can_proceed_to_next_wave": all_done,
            "failed_objectives": [o.get("id") for o in failed],
        }
    )


PARALLEL_TOOLS = [analyze_objective_parallelism, track_parallel_execution]
