"""EngagementContextMiddleware — surface launcher- and harness-set context to the LLM.

Two channels feed this middleware:

1. Launcher path (CLI / web): the launcher decides the engagement slug at
   session start. Clients inject ``engagement_name`` and ``workspace_path``
   via ``config.configurable`` on every run. ``before_agent`` hydrates these
   into agent state on the first step of each thread so downstream middleware
   (OPPLAN, filesystem) and the prompt-injection path see them as ordinary
   state fields. The checkpointer persists the hydrated state across runs,
   so subsequent runs read straight from state without re-hydrating.

2. Benchmark path (XBOW / CTF harness): when the LangGraph container is
   launched with `BENCHMARK_MODE=1` (via .env), this middleware additionally
   injects (a) the rule-suspension addendum that used to live in the system
   prompt and (b) the per-challenge context (target URL, vulnerability tags,
   flag format, mission brief, extra service ports) that the harness puts on
   the run state. This keeps the prompt itself free of mode-specific branches
   while letting the model see fresh challenge context on every model call.

Pattern matches OPPLANMiddleware (decepticon/middleware/opplan.py) —
state-backed context injection via wrap_model_call.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Annotated, Any, NotRequired, cast

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import SystemMessage, ToolMessage
from langgraph.config import get_config
from langgraph.types import Command
from typing_extensions import override

from decepticon.middleware.opplan import _reduce_engagement_name, _reduce_workspace_path
from decepticon.middleware.state_reducers import reduce_converging_value
from decepticon.tools.bash.bash import bash_workspace
from decepticon_core.utils.engagement_scope import (
    is_valid_engagement_label,
    reset_active_engagement,
    set_active_engagement,
)


_REPORT_SCOPE_TOOLS = frozenset({
    "report_hackerone", "report_bugcrowd_csv", "report_executive",
    "report_timeline", "report_sarif",
})


class ReportingScopeError(ValueError):
    """Trusted report state and explicit run configuration disagree."""


def _trusted_report_scope(request: Any) -> str:
    state = request.state
    if not isinstance(state, dict):
        raise ReportingScopeError("trusted engagement state missing")
    configurable = (request.runtime.config or {}).get("configurable", {})
    if not isinstance(configurable, dict):
        raise ReportingScopeError("run config invalid")
    label = state.get("kg_engagement") or state.get("engagement_name")
    if not isinstance(label, str) or label != label.strip() or not is_valid_engagement_label(label):
        raise ReportingScopeError("trusted graph engagement label invalid")
    for key in ("engagement_name", "kg_engagement", "workspace_path"):
        if key in configurable and configurable[key] != state.get(key):
            raise ReportingScopeError("run config and engagement state mismatch")
    return label


class EngagementContextState(AgentState):
    """State extension carrying launcher- and harness-decided context."""

    engagement_name: NotRequired[
        Annotated[str, "Workspace slug set by the launcher.", _reduce_engagement_name]
    ]
    workspace_path: NotRequired[
        Annotated[str, "Sandbox root for this engagement.", _reduce_workspace_path]
    ]
    kg_engagement: NotRequired[
        Annotated[
            str,
            "Trusted tenant+engagement attack-graph partition.",
            reduce_converging_value,
        ]
    ]
    # Per-run language override. When set via config.configurable.language,
    # the middleware appends a LANGUAGE_POLICY SystemMessage that supersedes
    # the prompt-time DECEPTICON_LANGUAGE env policy. Multi-tenant launchers
    # (multi-tenant) need different orgs to receive different language outputs from
    # the same container, which the env-based path cannot deliver.
    # All launcher-/harness-set channels below carry a reducer so an
    # orchestrator that fans out to several subagents in one turn does not
    # trip INVALID_CONCURRENT_GRAPH_UPDATE — every concurrent writer carries
    # the same value (last-write-wins on non-None). Same fix as #153 for the
    # slug/workspace channels above.
    language: NotRequired[
        Annotated[str, "Per-run output language (ISO 639-1).", reduce_converging_value]
    ]
    # Fresh engagements pass this immutable launcher/client declaration to
    # Autohunt. It is distinct from benchmark-only challenge context: it is
    # never a license to infer sibling assets or widen the RoE.
    target_type: NotRequired[
        Annotated[str, "Declared target kind from the launcher/client.", reduce_converging_value]
    ]
    target_value: NotRequired[
        Annotated[str, "One explicitly declared engagement target.", reduce_converging_value]
    ]
    authorization_confirmed: NotRequired[
        Annotated[
            bool,
            "Explicit launcher/client authorization acknowledgement.",
            reduce_converging_value,
        ]
    ]
    # Benchmark / CTF challenge context — populated by the benchmark harness.
    target_url: NotRequired[Annotated[str, "CTF challenge target URL.", reduce_converging_value]]
    target_extra_ports: NotRequired[
        Annotated[
            dict[int, int],
            "Additional published ports keyed by container target port (e.g. {22: 2222}).",
            reduce_converging_value,
        ]
    ]
    vulnerability_tags: NotRequired[
        Annotated[
            list[str],
            "Challenge vulnerability tags (e.g. ['sqli', 'xss']).",
            reduce_converging_value,
        ]
    ]
    flag_format: NotRequired[
        Annotated[str, "Expected flag format string.", reduce_converging_value]
    ]
    mission_brief: NotRequired[
        Annotated[str, "Challenge name + description.", reduce_converging_value]
    ]


log = logging.getLogger(__name__)


_FALSY_ENV_VALUES = frozenset({"", "0", "false", "no", "off"})


def _benchmark_mode_active() -> bool:
    """Truthy evaluation of the BENCHMARK_MODE env var set on the LangGraph container."""
    return os.environ.get("BENCHMARK_MODE", "").strip().lower() not in _FALSY_ENV_VALUES


def _configurable_from_runnable_config() -> dict[str, Any]:
    """Read the active run's ``config.configurable`` block, defensively.

    Returns an empty dict outside a LangGraph execution context so callers
    can treat the result uniformly without try/except boilerplate.
    """
    try:
        cfg = get_config()
    except RuntimeError:
        return {}
    if not isinstance(cfg, dict):
        return {}
    configurable = cfg.get("configurable")
    return configurable if isinstance(configurable, dict) else {}


def _hydrate_engagement_state(state: Any) -> dict[str, Any] | None:
    """Copy ``engagement_name``/``workspace_path`` from runnable config into state.

    Runs in ``before_agent`` so the values are present on state before any
    downstream middleware (OPPLAN, filesystem) reads them. Idempotent: if the
    state already carries either field, it is left untouched and the config
    value is ignored.

    Also propagates the engagement label into the per-context
    ``_active_engagement`` so KG writes through ``KGStore``
    (and the legacy ``_state`` compat shim that wraps it) carry the
    right engagement scope on every record_observations / execute_*
    call (see ``docs/security/neo4j-hardening.md``).
    """
    get = state.get if hasattr(state, "get") else (lambda _k, _d=None: None)
    configurable = _configurable_from_runnable_config()
    updates: dict[str, Any] = {}

    engagement_label: str | None = get("engagement_name")
    if not engagement_label:
        cfg_slug = configurable.get("engagement_name")
        if isinstance(cfg_slug, str) and cfg_slug:
            updates["engagement_name"] = cfg_slug
            engagement_label = cfg_slug

    if not get("workspace_path"):
        cfg_workspace = configurable.get("workspace_path")
        if isinstance(cfg_workspace, str) and cfg_workspace:
            updates["workspace_path"] = cfg_workspace

    if not get("language"):
        cfg_lang = configurable.get("language")
        if isinstance(cfg_lang, str) and cfg_lang:
            updates["language"] = cfg_lang

    for field in ("target_type", "target_value"):
        if not get(field):
            value = configurable.get(field)
            if isinstance(value, str) and value:
                updates[field] = value
    if get("authorization_confirmed") is None:
        confirmed = configurable.get("authorization_confirmed")
        if isinstance(confirmed, bool):
            updates["authorization_confirmed"] = confirmed

    # KGMiddleware and the run launcher carry the authoritative composite
    # tenant+engagement graph partition in ``kg_engagement``. Keep the human /
    # workspace-facing ``engagement_name`` unchanged, but make every legacy KG
    # helper's contextvar resolve to the same composite partition as KGStore.
    cfg_kg_label = configurable.get("kg_engagement")
    kg_label = (
        cfg_kg_label if isinstance(cfg_kg_label, str) and cfg_kg_label else get("kg_engagement")
    )
    if isinstance(cfg_kg_label, str) and cfg_kg_label and get("kg_engagement") != cfg_kg_label:
        updates["kg_engagement"] = cfg_kg_label
    active_graph_label = kg_label if isinstance(kg_label, str) and kg_label else engagement_label
    if active_graph_label:
        try:
            set_active_engagement(active_graph_label)
        except ValueError:
            # Invalid engagement label format (e.g. contains slashes or
            # control chars). The contextvar stays unset; downstream
            # Neo4j writes will hit the explicit no-engagement guard and
            # fail loud rather than silently writing to the global scope.
            pass

    return updates or None


def _resolve_workspace_path(state: Any) -> str:
    """Pick the live workspace path: state first, then runnable config, then default."""
    get = state.get if hasattr(state, "get") else (lambda _k, _d=None: None)
    workspace = get("workspace_path") or ""
    if not workspace:
        cfg_workspace = _configurable_from_runnable_config().get("workspace_path")
        if isinstance(cfg_workspace, str) and cfg_workspace:
            workspace = cfg_workspace
    return workspace or "/workspace"


def _build_engagement_injection(slug: str, workspace: str) -> str:
    # ``workspace`` is the live engagement root resolved from state/config by
    # ``_resolve_workspace_path``. It is ``/workspace`` for the default
    # single-tenant launcher, but multi-tenant launchers mount each
    # engagement under a distinct root — so the injection must reflect the
    # resolved path, not a hardcoded ``/workspace`` (which would point the
    # agent at the wrong directory). Trailing slashes are trimmed so the
    # ``{root}/plan/`` guidance never doubles up.
    root = workspace.rstrip("/") or workspace
    return (
        "\n\n[Engagement context — set by the launcher]\n"
        f"Workspace slug: {slug}\n"
        f"Workspace root: {root}\n"
        f"Treat {root} as the only engagement directory for this run. "
        "Read and write planning documents directly under "
        f"{root}/plan/. Do NOT re-prompt the operator for a slug or an "
        "engagement directory name; the launcher already chose them. The "
        "human-friendly engagement title belongs in roe.json:engagement_name "
        "and may differ from this slug."
    )


def _build_autohunt_injection(
    target_type: str,
    target_value: str,
    authorization_confirmed: bool,
) -> str:
    """Render the trusted, single-target bootstrap context for Autohunt."""
    authorization = "confirmed" if authorization_confirmed else "NOT confirmed"
    return (
        "\n\n[Autohunt bootstrap context — set by the launcher/client]\n"
        f"Declared target: {target_value}\n"
        f"Declared target type: {target_type}\n"
        f"Authorization: {authorization}\n"
        "Treat this as exactly one target. Do not infer related domains, IP ranges, "
        "organizations, repositories, or cloud assets into scope. If authorization "
        "is not confirmed, ask one blocking authorization question before planning."
    )


# Cache of parsed deconfliction.json keyed by path → (mtime, value). The
# injector runs on every model call, so without this the file was stat'd,
# read, and JSON-parsed once per turn. Keyed on mtime so an updated
# deconfliction file is still picked up the moment it changes.
_DECONFLICTION_CACHE: dict[str, tuple[float, dict[str, Any] | None]] = {}


def _load_deconfliction(workspace: str) -> dict[str, Any] | None:
    root = workspace.rstrip("/") or workspace
    path = Path(root) / "plan" / "deconfliction.json"
    key = str(path)
    try:
        mtime = path.stat().st_mtime
    except OSError:
        # Absent (or unstattable) — drop any stale entry and skip the block.
        _DECONFLICTION_CACHE.pop(key, None)
        return None
    cached = _DECONFLICTION_CACHE.get(key)
    if cached is not None and cached[0] == mtime:
        return cached[1]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        # Do NOT cache read/parse failures: a transient error (mid-write
        # file, brief I/O hiccup) must not be pinned as "no deconfliction"
        # for the lifetime of this mtime. Drop any stale entry and retry on
        # the next call — matching the pre-cache per-call behaviour.
        log.warning("engagement: failed to read %s: %s; skipping block", path, exc)
        _DECONFLICTION_CACHE.pop(key, None)
        return None
    # Cache the successful read (including a valid-but-not-a-dict file, which
    # is a deterministic "no deconfliction" — safe to memoize until mtime moves).
    result = data if isinstance(data, dict) else None
    _DECONFLICTION_CACHE[key] = (mtime, result)
    return result


def _build_deconfliction_injection(data: dict[str, Any]) -> str:
    code = data.get("deconfliction_code")
    raw_identifiers = data.get("identifiers")
    identifiers = raw_identifiers if isinstance(raw_identifiers, list) else []

    lines: list[str] = []
    if isinstance(code, str) and code:
        lines.append(f"Deconfliction code: {code}")
    for entry in identifiers:
        if not isinstance(entry, dict):
            continue
        kind = entry.get("type")
        value = entry.get("value")
        if isinstance(kind, str) and kind and isinstance(value, str) and value:
            lines.append(f"- {kind}: {value}")

    if not lines:
        return ""

    return (
        "\n\n[Deconfliction — set by Soundwave for blue-team coordination]\n"
        "Stamp your activity with these identifiers so defenders can separate "
        "this engagement from real threats:\n" + "\n".join(lines)
    )


def _format_extra_services(target_url: str, extra_ports: dict[int, int]) -> str:
    if not extra_ports:
        return ""
    # target_url comes in like "http://host.docker.internal:8080" — extract host only.
    host = target_url.split("://", 1)[-1].split(":", 1)[0] if target_url else ""
    lines: list[str] = []
    for target_port, published_port in sorted(extra_ports.items()):
        if target_port == 22:
            lines.append(f"- **SSH:** {host}:{published_port} (internal port 22)")
        else:
            lines.append(f"- **Port {target_port}:** {host}:{published_port}")
    return "**Additional services:**\n" + "\n".join(lines) + "\n"


def _build_benchmark_injection(
    target_url: str,
    extra_ports: dict[int, int],
    tags: list[str],
    flag_format: str,
    brief: str,
) -> str:
    """Per-challenge context injection for benchmark mode.

    Engagement-mode rules (Rule 8/9 suspension, OPPLAN structure, SHORT-CIRCUIT)
    live in `/skills/benchmark/SKILL.md` and are loaded explicitly by the
    orchestrator on its first turn. This middleware injects ONLY the
    per-challenge state (target URL, tags, flag format, mission brief).
    """
    sections: list[str] = ["\n## CTF Benchmark Challenge\n"]
    if target_url:
        sections.append(f"**Target URL:** {target_url}\n")
        sections.append("^^^ Attack ONLY this URL. Do NOT scan other ports or hosts. ^^^\n\n")
    extra = _format_extra_services(target_url, extra_ports)
    if extra:
        sections.append(extra + "\n")
    if tags:
        sections.append(f"**Vulnerability tags:** {', '.join(tags)}\n")
    if flag_format:
        sections.append(f"**Flag format:** {flag_format}\n")
    if brief:
        sections.append(f"**Mission brief:** {brief}\n")
    return "".join(sections)


class EngagementContextMiddleware(AgentMiddleware):
    """Inject engagement and per-challenge context into every model call.

    Scope is intentionally narrow: engagement metadata (slug, workspace) and
    per-challenge state (target URL, tags, flag format, mission brief). The
    benchmark playbook (Rule 8/9 suspension, OPPLAN structure, SHORT-CIRCUIT
    rule) lives in `/skills/benchmark/SKILL.md` — the orchestrator loads it
    on its first turn per the harness task prompt. This middleware does NOT
    inject mode-specific rules; benchmark mode only flips on the per-challenge
    context block.
    """

    state_schema = EngagementContextState

    def __init__(self) -> None:
        super().__init__()

    @override
    def before_agent(self, state, runtime) -> dict[str, Any] | None:
        return _hydrate_engagement_state(state)

    @override
    async def abefore_agent(self, state, runtime) -> dict[str, Any] | None:
        return _hydrate_engagement_state(state)

    @override
    def wrap_model_call(self, request, handler):
        return handler(self._inject(request))

    @override
    async def awrap_model_call(self, request, handler):
        return await handler(self._inject(request))

    @override
    def wrap_tool_call(self, request, handler) -> ToolMessage | Command:
        if request.tool and request.tool.name in _REPORT_SCOPE_TOOLS:
            token = set_active_engagement(_trusted_report_scope(request))
            try:
                return handler(request)
            finally:
                reset_active_engagement(token)
        if request.tool and request.tool.name in {
            "bash",
            "bash_output",
            "bash_kill",
            "bash_status",
        }:
            workspace = _resolve_workspace_path(request.state)
            with bash_workspace(workspace):
                return handler(request)
        return handler(request)

    @override
    async def awrap_tool_call(self, request, handler) -> ToolMessage | Command:
        if request.tool and request.tool.name in _REPORT_SCOPE_TOOLS:
            token = set_active_engagement(_trusted_report_scope(request))
            try:
                return await handler(request)
            finally:
                reset_active_engagement(token)
        if request.tool and request.tool.name in {
            "bash",
            "bash_output",
            "bash_kill",
            "bash_status",
        }:
            workspace = _resolve_workspace_path(request.state)
            with bash_workspace(workspace):
                return await handler(request)
        return await handler(request)

    def _inject(self, request):
        state = request.state or {}
        get = state.get if hasattr(state, "get") else (lambda _k, _d=None: None)

        slug = get("engagement_name", "") or ""
        workspace = get("workspace_path", "/workspace") or "/workspace"
        language = get("language", "") or ""
        target_type = get("target_type", "") or ""
        target_value = get("target_value", "") or ""
        authorization_confirmed = get("authorization_confirmed")

        sections: list[str] = []
        if slug:
            sections.append(_build_engagement_injection(slug, workspace))
            deconfliction = _load_deconfliction(workspace)
            if deconfliction is not None:
                block = _build_deconfliction_injection(deconfliction)
                if block:
                    sections.append(block)
        if target_value:
            sections.append(
                _build_autohunt_injection(
                    target_type,
                    target_value,
                    authorization_confirmed is True,
                )
            )
        if _benchmark_mode_active():
            sections.append(
                _build_benchmark_injection(
                    target_url=get("target_url", "") or "",
                    extra_ports=get("target_extra_ports", {}) or {},
                    tags=get("vulnerability_tags", []) or [],
                    flag_format=get("flag_format", "") or "",
                    brief=get("mission_brief", "") or "",
                )
            )

        # Per-run language override. Multi-tenant launchers (web) inject
        # the org's selected language via config.configurable.language; we
        # append the same LANGUAGE_POLICY block the prompt builder would have
        # produced if DECEPTICON_LANGUAGE were set, but per-run rather than
        # per-container. Because this is a later SystemMessage, it supersedes
        # the prompt-time policy without needing to reach into the cached
        # static prompt.
        #
        # Lazy import to avoid a circular: agents.__init__ pulls in agents
        # which pull in middleware which would otherwise pull back into
        # agents.prompts before that package is fully initialized.
        from decepticon.agents.prompts import build_language_policy

        language_policy = build_language_policy(language)
        if language_policy:
            sections.append("\n\n" + language_policy)

        if not sections:
            return request

        injection = "".join(sections)

        if request.system_message is not None:
            new_content = [
                *request.system_message.content_blocks,
                {"type": "text", "text": injection},
            ]
        else:
            new_content = [{"type": "text", "text": injection}]

        new_system = SystemMessage(content=cast("list[str | dict[str, str]]", new_content))
        return request.override(system_message=new_system)
