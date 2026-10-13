"""Transfer SARIF through the run's sandbox using trusted workspace and graph scope."""

from __future__ import annotations

import json
from typing import Any

from langchain_core.runnables import RunnableConfig

from decepticon.backends.factory import build_sandbox_backend
from decepticon.middleware.filesystem import LOCKED_ROE_ERROR, EngagementFilesystemBackend
from decepticon.middleware.kg_internal.ingest import _ingest_sarif_text
from decepticon.middleware.kg_internal.store import KGStore
from decepticon.tools.reporting.sarif import render_sarif
from decepticon_core.utils.engagement_scope import is_valid_engagement_label

MAX_IMPORT_BYTES = 64 * 1024
MAX_OUTPUT_BYTES = 1024 * 1024


class ReportingScopeError(ValueError):
    """SARIF scope or sandbox transfer did not satisfy the reporting contract."""


def resolve_owned_scope(state: dict[str, Any], config: RunnableConfig) -> tuple[str, str]:
    if not isinstance(state, dict):
        raise ReportingScopeError("trusted engagement state missing")
    label = state.get("engagement_name")
    graph_label = state.get("kg_engagement", label)
    for value in (label, graph_label):
        if (
            not isinstance(value, str)
            or value != value.strip()
            or not is_valid_engagement_label(value)
        ):
            raise ReportingScopeError("trusted engagement label invalid")
    workspace = state.get("workspace_path")
    if workspace not in ("/workspace", f"/workspace/{label}"):
        raise ReportingScopeError("engagement workspace mismatch")
    configurable = config.get("configurable", {})
    if not isinstance(configurable, dict):
        raise ReportingScopeError("run config invalid")
    expected = {"engagement_name": label, "kg_engagement": graph_label, "workspace_path": workspace}
    if any(key in configurable and configurable[key] != value for key, value in expected.items()):
        raise ReportingScopeError("run config and engagement state mismatch")
    return graph_label, workspace


def ingest_sarif_scoped(
    path: str,
    *,
    store: KGStore,
    state: dict[str, Any],
    config: RunnableConfig,
    created_by: str,
    source_episode_id: str,
) -> dict[str, Any]:
    engagement, workspace = resolve_owned_scope(state, config)
    backend = build_sandbox_backend(config)
    real_path = EngagementFilesystemBackend(backend, workspace)._real(path)
    replies = backend.download_files([real_path])
    if len(replies) != 1 or replies[0].path != real_path:
        raise ReportingScopeError("sandbox read response mismatch")
    reply = replies[0]
    if reply.error:
        raise ReportingScopeError(f"sandbox read failed: {reply.error}")
    if reply.content is None:
        raise ReportingScopeError("sandbox read returned no content")
    if len(reply.content) > MAX_IMPORT_BYTES:
        raise ReportingScopeError("SARIF input exceeds 64 KiB")
    summary = _ingest_sarif_text(
        reply.content.decode("utf-8"),
        store,
        engagement,
        created_by,
        source_episode_id,
    )
    return {"scanner": "sarif", "path": path, **summary}


def render_sarif_scoped(
    engagement_id: str,
    output_path: str,
    *,
    state: dict[str, Any],
    config: RunnableConfig,
) -> dict[str, Any]:
    from decepticon.tools.reporting.kg_adapter import load_engagement_graph

    engagement, workspace = resolve_owned_scope(state, config)
    backend = build_sandbox_backend(config)
    scoped = EngagementFilesystemBackend(backend, workspace)
    real_path = scoped._real(output_path)
    if scoped._roe_locked(real_path):
        raise ReportingScopeError(LOCKED_ROE_ERROR)
    graph = load_engagement_graph(engagement)
    text = render_sarif(graph, engagement_id=engagement_id)
    content = text.encode("utf-8")
    if len(content) > MAX_OUTPUT_BYTES:
        raise ReportingScopeError("SARIF output exceeds 1 MiB")
    replies = backend.upload_files([(real_path, content)])
    if len(replies) != 1 or replies[0].path != real_path:
        raise ReportingScopeError("sandbox write response mismatch")
    if replies[0].error:
        raise ReportingScopeError(f"sandbox write failed: {replies[0].error}")
    return {
        "engagement_id": engagement_id,
        "path": output_path,
        "bytes": len(content),
        "results": len(json.loads(text)["runs"][0]["results"]),
    }
