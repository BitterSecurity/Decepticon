"""Agent-facing KG tools — ``kg_record`` and ``kg_ingest``.

Built by :func:`build_kg_tools` and attached to ``KGMiddleware.tools``
so ``langchain.agents.create_agent`` merges them into the agent's
toolset at compile time. The tools never accept ``engagement`` from
the LLM; the middleware injects it via ``InjectedState`` and the store
re-pulls it inside ``record_observations`` / ``ingest`` so an
``InjectedState`` override bug (open issue
``langchain-ai/langchain#31688``) cannot escape the engagement scope.

Surface (minimal per the design notes):

  - ``kg_record(observations)``           — atomic batch write of nodes
                                            and outgoing edges.
  - ``kg_ingest(scanner_kind, path)``     — dispatcher into the scanner
                                            adapter registry.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Annotated, Any

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import InjectedToolCallId, tool
from langgraph.prebuilt import InjectedState

from decepticon.middleware.kg_internal.ingest import ingest as _ingest_dispatch
from decepticon.middleware.kg_internal.store import KGStore

DEFAULT_KG_TOOLS = frozenset({"kg_record", "kg_ingest"})

_ENGAGEMENT_UNSET = (
    "kg_engagement is not set on agent state; middleware before_agent did not hydrate"
)


def _resolve_engagement(state: dict[str, Any] | None) -> str:
    """Pull the engagement label from state with a fall-back to
    ``engagement_name`` (the upstream EngagementContextMiddleware field)."""
    if not isinstance(state, dict):
        return ""
    return str(state.get("kg_engagement") or state.get("engagement_name") or "").strip()


def _resolve_created_by(state: dict[str, Any] | None) -> str:
    """Best-effort agent identity for provenance. Falls back to a
    constant string so the trusted ``record_observations`` invariant
    holds even when the agent role is missing from state."""
    if not isinstance(state, dict):
        return "agent"
    role = state.get("role") or state.get("agent_name") or state.get("kg_created_by")
    if isinstance(role, str) and role:
        return role
    return "agent"


def _err(message: str) -> str:
    return json.dumps({"error": message})


def build_kg_tools(
    store: KGStore,
    enabled: Iterable[str] | None = None,
) -> list[Any]:
    """Construct the agent-facing KG tools bound to a single store.

    ``enabled`` selects which tools land in the returned list. Default
    is ``DEFAULT_KG_TOOLS`` (kg_record + kg_ingest). Plugin authors
    that ship a third KG tool can pass ``{"kg_record", "kg_ingest",
    "kg_my_custom_tool"}`` in conjunction with subclassing
    ``KGMiddleware`` to append the extra tool factory.
    """
    enabled_set: set[str] = set(enabled) if enabled is not None else set(DEFAULT_KG_TOOLS)
    tools: list[Any] = []

    if "kg_record" in enabled_set:

        @tool
        def kg_record(
            observations: str,
            state: Annotated[dict, InjectedState],
            tool_call_id: Annotated[str, InjectedToolCallId] = "",
        ) -> str:
            """Record one or more graph observations atomically.

            The middleware injects engagement scope + provenance — you
            only pass the observations. All observations in one call
            land in a single Neo4j transaction; partial failure rolls
            back the batch.

            ``observations`` is a JSON-encoded list. Each entry:

              {
                "kind": "Host" | "Service" | "Vulnerability" | ... ,
                "key": "host::10.0.0.1",        # deterministic dedup key
                "label": "10.0.0.1",
                "props": {"ip": "10.0.0.1", "explored": false, ...},
                "edges_out": [
                  {"to_key": "service::10.0.0.1:80",
                   "kind": "HOSTS", "weight": 0.5}
                ]
              }

            Reserved provenance keys (engagement, firstseen, lastupdated,
            created_by, source_episode_id) are stripped from your props
            silently — the middleware sets them.

            Returns JSON: {"created": N, "merged": M, "edges": E,
            "revision": "..."}.
            """
            engagement = _resolve_engagement(state)
            if not engagement:
                return _err(_ENGAGEMENT_UNSET)

            try:
                obs_list = json.loads(observations)
            except (TypeError, ValueError) as exc:
                return _err(f"observations must be valid JSON list: {exc}")
            if not isinstance(obs_list, list):
                return _err("observations must be a JSON list of observation dicts")

            try:
                result = store.record_observations(
                    obs_list,
                    engagement=engagement,
                    created_by=_resolve_created_by(state),
                    source_episode_id=tool_call_id or "no-tool-call-id",
                )
            except ValueError as exc:
                return _err(str(exc))
            except Exception as exc:  # noqa: BLE001 — surface to LLM
                return _err(f"kg_record failed: {exc}")
            return json.dumps(result)

        tools.append(kg_record)

    if "kg_ingest" in enabled_set:

        @tool
        def kg_ingest(
            scanner_kind: str,
            path: str,
            state: Annotated[dict, InjectedState],
            tool_call_id: Annotated[str, InjectedToolCallId] = "",
            *,
            config: RunnableConfig,
        ) -> str:
            """Ingest a scanner output file into the engagement graph.

            ``scanner_kind`` selects an adapter from the registry. Built-ins:
              nmap_xml, nuclei_jsonl, httpx_jsonl, sarif.

            ``path`` is an absolute file path inside the sandbox / host
            (the adapter reads it directly). Examples:
              kg_ingest("nmap_xml", "/workspace/scan.xml")
              kg_ingest("sarif",    "/workspace/semgrep.sarif")

            Returns JSON: {"scanner": "...", "path": "...",
            "ingested": ...} on success, or
            {"error": "...", "available": [...]} when ``scanner_kind``
            is unknown.
            """
            engagement = _resolve_engagement(state)
            if not engagement:
                return _err(_ENGAGEMENT_UNSET)

            try:
                if scanner_kind == "sarif":
                    from decepticon.middleware.scoped_reporting_io import ingest_sarif_scoped
                    result = ingest_sarif_scoped(
                        path, store=store, state=state, config=config,
                        created_by=_resolve_created_by(state),
                        source_episode_id=tool_call_id or "no-tool-call-id",
                    )
                else:
                    result = _ingest_dispatch(
                        scanner_kind,
                        path,
                        store=store,
                        engagement=engagement,
                        created_by=_resolve_created_by(state),
                        source_episode_id=tool_call_id or "no-tool-call-id",
                    )
            except Exception as exc:  # noqa: BLE001 — surface to LLM
                return _err(f"kg_ingest failed: {exc}")
            return json.dumps(result)

        tools.append(kg_ingest)

    return tools
