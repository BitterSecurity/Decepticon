"""Operator-only import of a reviewed RoE into an existing engagement."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Annotated

import typer
from pydantic import ValidationError

from decepticon_core.types.engagement import RoE

app = typer.Typer(add_completion=False)


@app.command()
def main(
    workspace: Annotated[Path | None, typer.Option(help="Existing engagement directory.")] = None,
    file: Annotated[Path | None, typer.Option(help="Operator-reviewed RoE JSON file.")] = None,
    confirm: Annotated[
        bool, typer.Option(help="Attest that you are authorized to provision this RoE.")
    ] = False,
    schema: Annotated[
        bool, typer.Option(help="Print the canonical planning RoE JSON Schema.")
    ] = False,
) -> None:
    """Create missing plan/roe.json without replacing plans or approving Red execution."""
    if schema:
        typer.echo(json.dumps(RoE.model_json_schema(), indent=2))
        return
    if workspace is None or file is None or not confirm:
        raise typer.BadParameter("Provide --workspace, --file, and explicit operator --confirm.")
    try:
        payload = file.read_bytes()
        roe = RoE.model_validate_json(payload)
        if not roe.engagement_name.strip() or not roe.authorization_reference.strip():
            raise typer.BadParameter(
                "RoE requires a non-empty engagement_name and authorization_reference."
            )
        if not roe.in_scope or any(not entry.target.strip() for entry in roe.in_scope):
            raise typer.BadParameter("RoE requires at least one non-empty in_scope target.")
        plan = workspace / "plan"
        if not workspace.is_dir() or not plan.is_dir() or plan.is_symlink():
            raise typer.BadParameter("Select an existing engagement with a real plan directory.")
        destination = plan / "roe.json"
        if destination.exists() or destination.is_symlink():
            raise typer.BadParameter(
                "plan/roe.json already exists; provisioning never overwrites authorization."
            )
        # Link a complete, private file exclusively: readers never see partial JSON,
        # and an existing authorization (including a symlink) cannot be replaced.
        with tempfile.NamedTemporaryFile(dir=plan, prefix=".roe-", delete=True) as staged:
            staged.write(payload)
            staged.flush()
            os.fsync(staged.fileno())
            for marker in (".red-approved", ".planning-draft-ready"):
                (workspace / marker).unlink(missing_ok=True)
            os.link(staged.name, destination)
    except (OSError, ValidationError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(f"Provisioned {destination}. Continue Plan; review and approve Red separately.")


if __name__ == "__main__":
    app()
