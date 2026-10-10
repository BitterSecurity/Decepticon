"""Scoped report/import IO through the maintained sandbox protocol."""
from __future__ import annotations

import json
from typing import Any

from langchain_core.runnables import RunnableConfig
from decepticon.backends.factory import build_sandbox_backend
from decepticon.middleware.filesystem import EngagementFilesystemBackend
from decepticon.middleware.kg_internal.ingest import _adapt_sarif
from decepticon.middleware.kg_internal.store import KGStore
from decepticon.tools.reporting.sarif import render_sarif
from decepticon_core.utils.engagement_scope import is_valid_engagement_label

MAX_IMPORT_BYTES = 65536
MAX_OUTPUT_BYTES = 1048576

class ReportingScopeError(ValueError):
    """Report IO has no matching trusted workspace and graph scope."""


def resolve_owned_scope(state: dict[str, Any], config: RunnableConfig | None = None) -> tuple[str, str]:
    if not isinstance(state, dict):
        raise ReportingScopeError('trusted engagement state missing')
    label = state.get('engagement_name')
    if not isinstance(label, str) or label != label.strip() or not is_valid_engagement_label(label):
        raise ReportingScopeError('trusted engagement label invalid')
    kg_label = state.get('kg_engagement') or label
    if not isinstance(kg_label, str) or kg_label != kg_label.strip() or not is_valid_engagement_label(kg_label):
        raise ReportingScopeError('trusted graph engagement label invalid')
    workspace = state.get('workspace_path')
    if workspace not in {'/workspace', '/workspace/' + label}:
        raise ReportingScopeError('engagement workspace mismatch')
    configurable = (config or {}).get('configurable', {})
    if not isinstance(configurable, dict):
        raise ReportingScopeError('run config invalid')
    for key, expected in [('engagement_name', label), ('kg_engagement', kg_label), ('workspace_path', workspace)]:
        if key in configurable and configurable[key] != expected:
            raise ReportingScopeError('run config and engagement state mismatch')
    return kg_label, workspace


class _SarifBytes:
    """Existing SARIF adapter's read_text boundary, with bounded UTF-8 bytes."""
    def __init__(self, content: bytes) -> None:
        if not isinstance(content, bytes) or len(content) > MAX_IMPORT_BYTES:
            raise ReportingScopeError('SARIF byte budget exceeded')
        self.text = content.decode('utf-8', errors='strict')

    def read_text(self, encoding: str = 'utf-8') -> str:
        if encoding != 'utf-8':
            raise ReportingScopeError('unsupported encoding')
        return self.text



def ingest_sarif_scoped(path: str, *, store: KGStore, state: dict[str, Any], config: RunnableConfig | None, created_by: str, source_episode_id: str) -> dict[str, Any]:
    engagement, workspace = resolve_owned_scope(state, config)
    backend = EngagementFilesystemBackend(build_sandbox_backend(config), workspace)
    replies = backend.download_files([path])
    if len(replies) != 1 or replies[0].path != path or replies[0].error:
        raise ReportingScopeError("sandbox read failed: " + str(replies[0].error if replies else "missing response"))
    sarif_source = _SarifBytes(replies[0].content)
    ingest_summary = _adapt_sarif(sarif_source, store, engagement, created_by, source_episode_id)
    return {"scanner": "sarif", "path": path, **ingest_summary}


def render_sarif_scoped(engagement_id: str, output_path: str, *, state: dict[str, Any], config: RunnableConfig | None) -> dict[str, Any]:
    from decepticon.tools.reporting.kg_adapter import load_engagement_graph
    engagement, workspace = resolve_owned_scope(state, config)
    backend = build_sandbox_backend(config)
    real_path = EngagementFilesystemBackend(backend, workspace)._real(output_path)
    graph = load_engagement_graph(engagement)
    text = render_sarif(graph, engagement_id=engagement_id)
    sarif_bytes = text.encode("utf-8")
    if len(sarif_bytes) > MAX_OUTPUT_BYTES:
        raise ReportingScopeError("SARIF output byte budget exceeded")
    replies = backend.upload_files([(real_path, sarif_bytes)])
    if len(replies) != 1 or replies[0].path != real_path or replies[0].error:
        raise ReportingScopeError("sandbox write failed: " + str(replies[0].error if replies else "missing response"))
    return {"engagement_id": engagement_id, "path": output_path, "bytes": len(sarif_bytes), "results": len(json.loads(text)["runs"][0]["results"])}
