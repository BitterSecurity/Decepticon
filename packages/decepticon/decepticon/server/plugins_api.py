"""Runtime plugin bundle activation API.

Mounted into the LangGraph platform via ``langgraph.json`` ``http.app``.
Lets the CLI ``/plugins`` slash command flip plugin bundles on or off
without restarting the langgraph container — by calling the same
``langgraph_api.graph.register_graph()`` the platform itself uses at
startup.

Bundle model
------------
Decepticon's graph manifest is split into two bundles by
``decepticon.graph_registry``:

  - ``standard``: official OSS agents (decepticon + 9 specialists).
    Always loaded at startup.
  - ``plugins``: vulnresearch family (orchestrator + 5 specialists).
    Off by default. Activated either via ``DECEPTICON_PLUGINS`` env
    (next-restart) or this API (immediately).

Endpoints
---------
GET  ``/_decepticon/bundles``                 — list bundles + enabled state
POST ``/_decepticon/bundles/{name}/enable``   — register every graph in bundle
POST ``/_decepticon/bundles/{name}/disable``  — drop every graph in bundle

Enable is idempotent (``register_graph`` uses ``if_exists="do_nothing"``
in the Assistants table). Disable mutates the in-memory ``GRAPHS`` dict
and removes the system-created assistant row, so the next
``/assistants/search`` no longer returns it.

Persistence
-----------
This API is **runtime-only** in this iteration. Bundles re-enabled here
do NOT persist across ``decepticon restart`` — for permanent activation
set ``DECEPTICON_PLUGINS=standard,plugins`` in ``~/.decepticon/.env``.
A future iteration will add a state file so the API also persists.
"""

# NOTE: deliberately NO ``from __future__ import annotations`` here —
# LangGraph Platform mounts this module under a synthetic name
# (``user_router_module``) and pydantic's lazy forward-ref resolution
# fails to find ``BundleStatus`` etc. when the response models are
# introspected for OpenAPI schema generation. Eager-evaluating
# annotations sidesteps the namespace mismatch.

import importlib
import logging
from typing import Annotated
from uuid import uuid5

from fastapi import FastAPI, HTTPException, Path
from pydantic import BaseModel

from decepticon.graph_registry import _BUNDLE_TO_GRAPHS  # private, but stable: bundle->graphs map
from decepticon.server.operator_contracts import router as operator_contract_router
from decepticon_core.contracts.host_integration import get_host_integration

logger = logging.getLogger(__name__)


# ── Wire models ──────────────────────────────────────────────────────────────


class BundleStatus(BaseModel):
    name: str
    enabled: bool
    graphs: list[str]


class BundlesResponse(BaseModel):
    bundles: list[BundleStatus]


class ToggleResponse(BaseModel):
    bundle: str
    enabled: bool
    graphs: list[str]
    skipped: list[str] = []


# ── Helpers ──────────────────────────────────────────────────────────────────


def _path_to_module(spec: str) -> tuple[str, str]:
    """Turn ``"./decepticon/agents/plugins/vulnresearch.py:graph"`` into
    ``("decepticon.agents.plugins.vulnresearch", "graph")``.

    The graph_registry stores entries in the file-path form LangGraph
    Platform expects in ``langgraph.json``. For runtime registration we
    need importable module + attribute, which is more robust against
    CWD changes.
    """
    path, _, variable = spec.partition(":")
    if not variable:
        raise ValueError(f"bundle spec missing ':variable' suffix: {spec!r}")
    module = path.removeprefix("./").removesuffix(".py").replace("/", ".").lstrip(".")
    return module, variable


def _load_graph(module_path: str, variable: str):
    """Import the module and pull out the compiled graph attribute."""
    module = importlib.import_module(module_path)
    try:
        return getattr(module, variable)
    except AttributeError as exc:
        raise HTTPException(
            status_code=500,
            detail=(f"module {module_path!r} does not export attribute {variable!r}"),
        ) from exc


def _resolve_bundle(name: str) -> dict[str, str]:
    """Look up a bundle by name, 404 on unknown."""
    try:
        return _BUNDLE_TO_GRAPHS[name]
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"unknown bundle: {name!r}") from exc


def _registered_graph_ids() -> set[str]:
    """Snapshot of which graph IDs the platform currently exposes.

    Imported lazily because ``langgraph_api`` is only importable inside
    the LangGraph platform process — pulling it at module load time
    would make ``decepticon.server.plugins_api`` un-importable from
    plain unit tests / scripts.
    """
    from langgraph_api.graph import GRAPHS  # noqa: PLC0415

    return set(GRAPHS.keys())


# ── App ──────────────────────────────────────────────────────────────────────


app = FastAPI(
    title="Decepticon plugin bundle API",
    summary="Runtime activation/deactivation of agent bundles on the LangGraph platform.",
)
app.include_router(operator_contract_router)
for _host_router in get_host_integration().server_routes():
    app.include_router(_host_router)


@app.get("/_decepticon/bundles", response_model=BundlesResponse)
async def list_bundles() -> BundlesResponse:
    """List every known bundle plus its current enabled state.

    A bundle is *enabled* iff every graph it contributes is currently in
    ``GRAPHS``. Partial state (some graphs registered, some not) is
    reported as enabled=False — that should never happen under normal
    operation but the check is cheap.
    """
    registered = _registered_graph_ids()
    return BundlesResponse(
        bundles=[
            BundleStatus(
                name=name,
                enabled=all(gid in registered for gid in graphs),
                graphs=list(graphs),
            )
            for name, graphs in _BUNDLE_TO_GRAPHS.items()
        ]
    )


@app.post("/_decepticon/bundles/{name}/enable", response_model=ToggleResponse)
async def enable_bundle(
    name: Annotated[str, Path(min_length=1, max_length=64)],
) -> ToggleResponse:
    """Register every graph in the named bundle into the live LangGraph platform.

    Idempotent: re-enabling an already-loaded bundle is a no-op (the
    underlying ``register_graph`` uses ``if_exists="do_nothing"`` on the
    Assistants table and re-assigning ``GRAPHS[gid]`` is harmless).
    """
    from langgraph_api.graph import GRAPHS, register_graph  # noqa: PLC0415

    graphs = _resolve_bundle(name)
    registered: list[str] = []
    skipped: list[str] = []

    for graph_id, spec in graphs.items():
        if graph_id in GRAPHS:
            skipped.append(graph_id)
            continue
        module_path, variable = _path_to_module(spec)
        graph_obj = _load_graph(module_path, variable)
        try:
            await register_graph(graph_id, graph_obj, config=None)
        except Exception as exc:  # noqa: BLE001
            logger.exception("register_graph failed for %s", graph_id)
            raise HTTPException(
                status_code=500,
                detail=f"register_graph({graph_id}) failed: {exc}",
            ) from exc
        registered.append(graph_id)
        logger.info("plugin bundle %r: registered graph %r", name, graph_id)

    return ToggleResponse(
        bundle=name,
        enabled=True,
        graphs=registered,
        skipped=skipped,
    )


@app.post("/_decepticon/bundles/{name}/disable", response_model=ToggleResponse)
async def disable_bundle(
    name: Annotated[str, Path(min_length=1, max_length=64)],
) -> ToggleResponse:
    """Drop every graph in the named bundle.

    Removes the in-memory ``GRAPHS`` entry and deletes the system-owned
    Assistants row that ``register_graph`` created at enable time. The
    UUID for that row is deterministic (``uuid5(NAMESPACE_GRAPH,
    graph_id)``) — see ``langgraph_api.graph.register_graph``.

    Refuses to disable the ``standard`` bundle: those graphs are the
    core agent set and removing them mid-session would brick the
    orchestrator.
    """
    if name == "standard":
        raise HTTPException(
            status_code=400,
            detail=(
                "'standard' bundle cannot be disabled — it carries the "
                "core orchestrator. Set DECEPTICON_PLUGINS at startup to "
                "ship a custom baseline."
            ),
        )

    from langgraph_api.graph import (  # noqa: PLC0415
        GRAPHS,
        NAMESPACE_GRAPH,
        SYSTEM_ASSISTANT_IDS,
    )
    from langgraph_runtime.database import connect  # noqa: PLC0415

    if lg_api_feature_postgres():
        from langgraph_api.grpc.ops import Assistants  # noqa: PLC0415
    else:
        from langgraph_runtime.ops import Assistants  # noqa: PLC0415

    graphs = _resolve_bundle(name)
    removed: list[str] = []
    skipped: list[str] = []

    for graph_id in graphs:
        if graph_id not in GRAPHS:
            skipped.append(graph_id)
            continue
        del GRAPHS[graph_id]
        assistant_id = uuid5(NAMESPACE_GRAPH, graph_id)
        SYSTEM_ASSISTANT_IDS.discard(str(assistant_id))
        try:
            async with connect() as conn:
                async for _ in await Assistants.delete(conn, assistant_id):
                    pass
        except Exception:  # noqa: BLE001
            # Assistants row may not exist (e.g. the user manually
            # deleted it via /assistants/{id} DELETE). The in-memory
            # mutation is what gates further usage; DB cleanup is
            # best-effort. Log and continue.
            logger.warning(
                "could not delete Assistants row for %s — proceeding",
                graph_id,
                exc_info=True,
            )
        removed.append(graph_id)
        logger.info("plugin bundle %r: unregistered graph %r", name, graph_id)

    return ToggleResponse(
        bundle=name,
        enabled=False,
        graphs=removed,
        skipped=skipped,
    )


def lg_api_feature_postgres() -> bool:
    """Mirror the same backend selector langgraph_api.graph uses."""
    from langgraph_api.feature_flags import IS_POSTGRES_OR_GRPC_BACKEND  # noqa: PLC0415

    return IS_POSTGRES_OR_GRPC_BACKEND
