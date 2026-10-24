"""Web drafts/cursors must survive replacement without crossing installations."""

from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[4]


def test_web_client_state_uses_a_private_project_scoped_volume():
    compose = yaml.safe_load((REPO / "docker-compose.yml").read_text())
    mounts = compose["services"]["web"]["volumes"]
    state = [mount for mount in mounts if mount.endswith(":/var/lib/decepticon-web")]
    assert len(state) == 1, "Web CLI state must survive container replacement"
    source = state[0].split(":")[0]
    assert source in compose["volumes"], "Client state must use a declared named volume"
    volume = compose["volumes"][source] or {}
    assert not volume.get("external") and "name" not in volume, (
        "Independent installations must not share operator drafts or notification cursors"
    )
