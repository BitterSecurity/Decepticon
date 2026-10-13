"""Keep LangGraph's dev state outside the replaceable container layer."""

from pathlib import Path

import yaml

COMPOSE = Path(__file__).resolve().parents[1] / "docker-compose.yml"


def test_langgraph_state_uses_a_project_scoped_named_volume() -> None:
    compose = yaml.safe_load(COMPOSE.read_text())
    mounts = compose["services"]["langgraph"]["volumes"]
    state_mounts = [mount for mount in mounts if mount.endswith(":/app/.langgraph_api")]
    assert len(state_mounts) == 1, "Container recreation must preserve LangGraph threads and checkpoints"
    volume_name = state_mounts[0].split(":")[0]
    assert volume_name in compose["volumes"], "State must use a declared named volume"
    volume = compose["volumes"][volume_name] or {}
    assert "name" not in volume and not volume.get("external"), (
        "Independent Compose projects must not share engagement history"
    )
