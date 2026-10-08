"""SkillogyMiddleware — Phase 1a Brain Anatomy thin wrapper around the
skillogy service (Neo4j-backed). Replaces the file-system-backed
SkillsMiddleware in agents wired with ``DECEPTICON_SKILL_BACKEND=
skillogy_brain``.

Agent tool surface (Phase 1a, three tools — see Amendment v0.2.2)
-----------------------------------------------------------------
- ``find_skill(query?, subdomain?, mitre_id?, tag?, tactic_id?,
  limit=20)`` — relationship-aware discovery. AND-combined filters.
  Returns each match's name, path, subdomain, description, plus the
  matched MITRE IDs and tags so the agent sees *why* the skill came
  back.
- ``load_skill(name_or_path)`` — fetch the body of one ``:Skill`` node
  (metadata is search-side, returned by ``find_skill``). Accepts either a unique ``name`` or the
  canonical ``/skills/.../SKILL.md`` path.
- ``traverse(from_path, edge_types?, depth=2)`` — explicit graph
  walking from a Skill seed along a whitelisted edge set.

``run_cypher_read`` was removed from the agent surface in v0.2.2 — its
purpose (associative navigation) is fully covered by ``find_skill``
AND-combining over the five edge types and ``traverse`` doing
variable-length BFS. ``Neo4jBackend.run_cypher_read`` and its
read-only enforcement layers are kept in the server backend for
internal diagnostics, Phase 1b's ``recall()`` implementation, and test
fixtures — they are simply not exposed as an agent tool.

Architecture
------------
Phase 1a v0.2.1 service-architecture pivot, completed in Amendment
v0.2.2: this middleware is a *thin REST client* of the standalone
skillogy container. The agent process holds no Neo4j Bolt connection
of its own and the langgraph image carries no ``neo4j`` driver
dependency. ``RestSkillogyClient`` mirrors ``Neo4jBackend``'s method
surface so unit tests can swap the implementation behind the same
duck-typed contract.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import SystemMessage
from langchain_core.tools import tool
from typing_extensions import override

log = logging.getLogger(__name__)


class SkillogyScopeError(RuntimeError):
    """A role's Skillogy path allowlist could not be resolved safely."""


def _checked_scope(prefixes: list[str], *, role: str | None) -> list[str]:
    if not prefixes or any(
        not isinstance(prefix, str)
        or not prefix.startswith("/skills/")
        or prefix == "/skills/"
        or not prefix.endswith("/")
        or ".." in prefix
        for prefix in prefixes
    ):
        raise SkillogyScopeError(f"Skillogy requires a nonempty restricted scope for {role!r}")
    return prefixes


_DEFAULT_SKILLOGY_URL = "http://skillogy:9100"

_POLICY_PROMPT = """

[Skillogy access]
Skillogy is optional specialist knowledge for the current task. Engagement
startup, authorization, OPPLAN, finding rules, and role procedures come from
the system prompt and workflows. Do not retrieve a skill merely to start a run.
Continue with the available instructions when no specialist skill is needed.

Graph schema: Skills are stored in Neo4j. Skill nodes have name, path, subdomain,
description, when_to_use, and body. They connect to Phase via IN_PHASE, Tag
via TAGGED, Technique via IMPLEMENTS, and MoC via BELONGS_TO. Tactics connect
to Techniques via HAS_TECHNIQUE. Declared Skill-to-Skill relations include
REQUIRES, VALIDATED_BY, COMPOSES_WITH, SPECIALIZES, ALTERNATIVE_TO and
CONFLICTS_WITH. Traverse related skills when prerequisites or validators matter;
a relation never overrides the role's skill path allowlist.

find_skill(query?, subdomain?, mitre_id?, tag?, tactic_id?, limit=20):
  Finds candidates. Filters AND together. subdomain, tag, mitre_id and
  tactic_id follow the graph relationships above. An ATT&CK ID belongs in
  mitre_id for exact filtering. query searches skill names, usage triggers,
  tags and other metadata, and body text. The highest weights go to name and
  when_to_use. Use concise domain terms, for example:
  find_skill(query="lsass dump credential memory", subdomain="post-exploit")
  English terms have the best lexical recall; semantic search may find other
  languages when embeddings are available. Results include matched_by:
  lexical, semantic, both, or structured for a filter-only query. Both search
  legs matching is a stronger signal. Read descriptions and choose the skill
  that fits the task; do not select solely by rank. Retry with other terms if
  none fit and specialist guidance is still useful.

load_skill(name_or_path): fetch the chosen SKILL.md body by exact name or path.
  Load directly when the exact name or path is already known. Discovery is
  useful when the right skill is unknown; it is not a prerequisite for load.
  Only SKILL.md bodies are available through this graph; sibling references/
  files are not. A missing reference is not a reason to stop.
traverse(from_path, edge_types?, depth=2): explore related graph nodes.
Only trust skill bodies returned by the scoped load_skill tool. Skillogy does
not grant execution authorization. A retrieval miss or unavailable backend
must not stop the task; continue with the current workflow.
"""


# Role → :Phase.name mapping. Threaded from ``build_middleware(role=...)``
# through ``maybe_install_skillogy(role=role)`` to the middleware so the
# per-phase MoC summary block stays scoped to the agent's actual phase.
# Roles not in this map run without a phase block — they still get the
# static schema cheat-sheet and the three tools.
_PHASE_FOR_ROLE: dict[str, str] = {
    "recon": "reconnaissance",
    "exploit": "web-exploitation",
    "finding_verifier": "reporting",
    "finding_reporter": "reporting",
    "postexploit": "post-exploit",
    "ad_operator": "active-directory",
    "cloud_hunter": "cloud",
    "mobile_operator": "mobile",
    "wireless_operator": "wireless",
    "phisher": "phishing",
    "analyst": "analyst",
    "contract_auditor": "smart-contracts",
    "reverser": "reverse-engineering",
    "osint_operator": "osint",
    "iot_operator": "iot",
    "ics_operator": "ics-ot",
    "forensicator": "dfir",
    "supply_chain_operator": "supply-chain",
    # Blue Cell is the purple-team detection-validation agent (defensive sibling
    # of the Red Cell); adversary-emulation is the seeded meta phase for the
    # red/blue validation bridge — there is no dedicated blue-team phase.
    "blue_cell": "adversary-emulation",
    "soundwave": "planning",
    "autohunt": "planning",
    "decepticon": "orchestration",
}

_COMMON_SKILLS: dict[str, tuple[tuple[str, str], ...]] = {
    "orchestration": (
        (
            "/skills/standard/decepticon/kill-chain-analysis/SKILL.md",
            "Analyze supported attack paths",
        ),
    ),
    "reconnaissance": (
        ("/skills/standard/recon/passive-recon/SKILL.md", "Public-source reconnaissance"),
        ("/skills/standard/recon/web-recon/SKILL.md", "Web surface discovery"),
        ("/skills/standard/recon/active-recon/SKILL.md", "Bounded active reconnaissance"),
    ),
    "web-exploitation": (
        ("/skills/standard/exploit/web/SKILL.md", "Web technique routing"),
        ("/skills/standard/exploit/ad/SKILL.md", "Active Directory exploitation"),
    ),
    "post-exploit": (
        ("/skills/standard/post-exploit/credential-access/SKILL.md", "Credential access methods"),
        ("/skills/standard/post-exploit/c2-sliver/SKILL.md", "Sliver operations"),
    ),
    "reverse-engineering": (
        ("/skills/standard/reverser/triage/SKILL.md", "Binary triage"),
        ("/skills/standard/reverser/ghidra/SKILL.md", "Ghidra analysis"),
    ),
    "active-directory": (
        ("/skills/standard/ad/netexec/SKILL.md", "AD service assessment"),
        ("/skills/standard/ad/adcs-esc1/SKILL.md", "ADCS ESC1 validation"),
    ),
    "cloud": (
        ("/skills/standard/cloud/aws-iam-enum/SKILL.md", "AWS IAM enumeration"),
        ("/skills/standard/cloud/entra-enum/SKILL.md", "Entra ID enumeration"),
    ),
    "planning": (
        (
            "/skills/standard/soundwave/threat-profile/emulation/SKILL.md",
            "Adversary emulation profiles",
        ),
    ),
}


def _resolve_skillogy_url() -> str:
    return os.environ.get("DECEPTICON_SKILLOGY_URL", _DEFAULT_SKILLOGY_URL)


def _resolve_skillogy_api_key() -> str | None:
    return os.environ.get("DECEPTICON_SKILLOGY_API_KEY") or None


_USE_SKILLOGY_FALSY: frozenset[str] = frozenset({"0", "false", "no", "off"})


def _is_enabled() -> bool:
    # Skillogy is the canonical skill-retrieval backend; the in-process
    # FilesystemBackend stays available as an explicit opt-out for
    # standalone library use and pytest. Treat an unset / blank
    # ``DECEPTICON_USE_SKILLOGY`` as enabled; honor an explicit falsy
    # value as a disable. Backward compat: the legacy
    # ``DECEPTICON_SKILL_BACKEND=skillogy_brain`` rail still flips it on
    # even when ``DECEPTICON_USE_SKILLOGY=0`` (explicit user request via
    # the new env name wins).
    if os.environ.get("DECEPTICON_SKILL_BACKEND", "").strip().lower() == "skillogy_brain":
        return True
    raw = os.environ.get("DECEPTICON_USE_SKILLOGY", "").strip().lower()
    return raw not in _USE_SKILLOGY_FALSY


def _backend_factory():
    """Build the default REST client used when the middleware is
    activated without an explicit ``backend=`` injection.

    Phase 1a v0.2.1 service-architecture pivot: the agent process talks
    to the standalone skillogy container over REST and does not import
    the neo4j driver. The client mirrors the ``Neo4jBackend`` surface
    so unit tests can swap in either implementation behind the same
    duck-typed contract.
    """
    from decepticon.skillogy.client.rest import RestSkillogyClient  # noqa: PLC0415

    return RestSkillogyClient(
        base_url=_resolve_skillogy_url(),
        api_key=_resolve_skillogy_api_key(),
    )


def _make_load_skill_tool(backend, allowed_path_prefixes: list[str] | None = None):
    # Per ADR-0008 — when the closure has no allowlist (library / pytest
    # path), omit the kwarg entirely so legacy fakes that don't accept
    # ``**kwargs`` still work. When the allowlist is populated we forward
    # it and rely on the backend (Neo4jBackend, RestSkillogyClient, or a
    # role-aware fake) to honor it.
    _acl_kwargs: dict[str, Any] = (
        {"allowed_path_prefixes": allowed_path_prefixes} if allowed_path_prefixes else {}
    )

    @tool
    def load_skill(name_or_path: str) -> str:
        """Fetch one SKILL.md's body from the skillogy graph.

        Accepts either a unique frontmatter ``name`` (e.g. 'kerberoasting')
        or the canonical ``/skills/.../SKILL.md`` path. Returns the skill
        BODY with a minimal name/path header — the other node properties
        (subdomain, when_to_use, tags, MITRE/aatmf, allowed_tools …) exist to
        FIND the skill via ``find_skill``; once it is loaded the agent only
        needs the content, so they are omitted to keep the reading context
        lean. Mirrors the filesystem ``load_skill`` (which strips frontmatter
        and returns the body) so both backends read identically.
        """
        try:
            if name_or_path.startswith("/skills/"):
                props = backend.load_skill(name_or_path, **_acl_kwargs)
            else:
                # Resolve by name via a single-shot find. This keeps
                # load_skill's signature agent-friendly; the agent does
                # not need to remember paths.
                hits = backend.find_skill(query=name_or_path, limit=10, **_acl_kwargs)
                exact = [h for h in hits if h.get("name") == name_or_path]
                if not exact:
                    return json.dumps(
                        {"error": f"no Skill with name or path matching {name_or_path!r}"}
                    )
                props = backend.load_skill(exact[0]["path"], **_acl_kwargs)
            if props is None:
                return json.dumps({"error": f"no Skill at path {name_or_path!r}"})
            # Body only (+ minimal header). Metadata props are search-side
            # (find_skill); dumping them into the agent's context on every load
            # is noise. Header mirrors the filesystem load_skill's format.
            body = str(props.get("body") or "")
            path = str(props.get("path") or name_or_path)
            base_dir = path.rsplit("/", 1)[0] if "/" in path else "/"
            name = str(props.get("name") or "")
            description = str(props.get("description") or "").strip()
            header = f"Base directory for this skill: {base_dir}\nSkill: {name}" + (
                f" — {description}" if description else ""
            )
            return f"{header}\n\n{body.rstrip()}\n"
        except Exception as exc:  # noqa: BLE001 — surface as ToolMessage payload
            return json.dumps({"error": f"load_skill failed: {exc!r}"})

    return load_skill


def _make_find_skill_tool(backend, allowed_path_prefixes: list[str] | None = None):
    _acl_kwargs: dict[str, Any] = (
        {"allowed_path_prefixes": allowed_path_prefixes} if allowed_path_prefixes else {}
    )

    @tool
    def find_skill(
        query: str | None = None,
        subdomain: str | None = None,
        mitre_id: str | None = None,
        tag: str | None = None,
        tactic_id: str | None = None,
        limit: int = 20,
    ) -> str:
        """Relationship-aware skill discovery in the skillogy graph.

        Filters AND-combine. Pass at least one.

        ``query`` is a weighted term search over skill names, metadata and
        body. Each query term is matched independently; name and usage-trigger
        matches have the highest weights.

        So expand your intent into domain vocabulary — tools, technique names,
        protocols, artefacts — instead of writing a prose sentence. Relevant
        terms improve precision; common English function words are ignored.
        Terms are stemmed so "kerberoast" finds "kerberoasting"::

            good: "kerberoast kerberos TGS service ticket SPN"
            good: "lsass dump credential memory minidump comsvcs"
            bad:  "how do I steal credentials from the machine"

        English domain terms give the best lexical recall. Non-English input
        may work through semantic retrieval when embeddings are available.
        Pass an ATT&CK ID as ``mitre_id`` for an exact graph filter;
        ``query`` searches the ID text and may return other matches.

        THE RESULT IS A CANDIDATE LIST, NOT AN ANSWER. Read each hit's
        ``description`` and choose the one that fits your situation — do not
        load the first hit reflexively. Use concise domain terms. If nothing
        fits, re-query with different terms or ``traverse`` from the closest
        hit instead of settling.

        ``subdomain`` follows IN_PHASE. ``mitre_id`` follows IMPLEMENTS to a
        Technique. ``tag`` follows TAGGED. ``tactic_id`` (e.g. 'TA0001' for
        Initial Access) ladders via IMPLEMENTS → HAS_TECHNIQUE. Combining a
        term ``query`` with a structured filter is the strongest form.

        Returns each hit's name, path, subdomain, description, matched_mitre,
        matched_tags, and matched_by — which legs matched (["lexical"],
        ["semantic"], or both). Found by both = strongest match.
        """
        try:
            hits = backend.find_skill(
                query=query,
                subdomain=subdomain,
                mitre_id=mitre_id,
                tag=tag,
                tactic_id=tactic_id,
                limit=limit,
                **_acl_kwargs,
            )
            return json.dumps({"count": len(hits), "hits": hits}, ensure_ascii=False, default=str)
        except ValueError as exc:
            return json.dumps({"error": str(exc)})
        except Exception as exc:  # noqa: BLE001
            return json.dumps({"error": f"find_skill failed: {exc!r}"})

    return find_skill


def _make_traverse_tool(backend, allowed_path_prefixes: list[str] | None = None):
    _acl_kwargs: dict[str, Any] = (
        {"allowed_path_prefixes": allowed_path_prefixes} if allowed_path_prefixes else {}
    )

    @tool
    def traverse(
        from_path: str,
        edge_types: list[str] | None = None,
        depth: int = 2,
    ) -> str:
        """Variable-length BFS from a Skill seed along the relationship whitelist.

        ``from_path`` is the canonical /skills/.../SKILL.md path of the
        starting Skill. ``edge_types`` defaults to the spec-§5.7.2
        whitelist (IN_PHASE, IMPLEMENTS, TAGGED, BELONGS_TO, RELATED_TO,
        HAS_TECHNIQUE, HAS_SUBTECHNIQUE). ``depth`` ≤ 5. Returns each
        neighbour's label, key, depth, and the edge-type chain that
        connected it.
        """
        try:
            rows = backend.traverse(
                from_path,
                edge_types=edge_types,
                depth=depth,
                **_acl_kwargs,
            )
            return json.dumps({"count": len(rows), "rows": rows}, ensure_ascii=False, default=str)
        except Exception as exc:  # noqa: BLE001
            return json.dumps({"error": f"traverse failed: {exc!r}"})

    return traverse


class SkillogyMiddleware(AgentMiddleware):
    """Wire the agent to the skillogy knowledge graph (Neo4j).

    Activation: **on by default.** Skillogy is the canonical
    skill-retrieval backend; the in-process ``FilesystemBackend`` stays
    available as an explicit opt-out for standalone library use and
    pytest via ``DECEPTICON_USE_SKILLOGY=0`` (or ``false`` / ``no`` /
    ``off``). The legacy ``DECEPTICON_SKILL_BACKEND=skillogy_brain`` rail
    still flips it on even when ``DECEPTICON_USE_SKILLOGY=0`` (explicit
    user request via the new env name wins). The agent factory's
    ``maybe_install_skillogy`` swaps ``SkillsMiddleware`` for this class
    and threads the agent's role through so the per-phase MoC summary
    fires for the correct phase.

    The injected system-prompt block has two parts:

    * **Static policy** (``_POLICY_PROMPT``) describes scoped, optional
      discovery and direct loading.
    * **Role-scoped quick reference** (``_render_phase_block``) lists a
      few known skills without a backend lookup at agent startup.
    """

    def __init__(
        self,
        *,
        agent_phase: str | None = None,
        backend: Any = None,
        append_policy_to_system: bool = True,
        allowed_path_prefixes: list[str] | None = None,
    ) -> None:
        super().__init__()
        self._backend = backend or _backend_factory()
        self._phase = agent_phase
        self._append_policy = append_policy_to_system
        # ADR-0008 — per-role path-prefix ACL. Carries the legacy
        # ``FilesystemBackend`` contract (``skills_sources_for(role)``)
        # forward so the two skill backends are interchangeable from an
        # authorization standpoint. ``None`` keeps the library /
        # standalone-CLI path unrestricted, which is how the underlying
        # backend interprets the kwarg as well.
        self._allowed_path_prefixes = (
            _checked_scope(list(allowed_path_prefixes), role=None)
            if allowed_path_prefixes is not None
            else None
        )
        self.tools = [
            _make_find_skill_tool(self._backend, self._allowed_path_prefixes),
            _make_load_skill_tool(self._backend, self._allowed_path_prefixes),
            _make_traverse_tool(self._backend, self._allowed_path_prefixes),
        ]
        self._phase_block: str = self._render_phase_block() if self._phase else ""

    @classmethod
    def from_env(
        cls,
        *,
        agent_phase: str | None = None,
        allowed_path_prefixes: list[str] | None = None,
    ) -> SkillogyMiddleware:
        return cls(
            agent_phase=agent_phase,
            allowed_path_prefixes=allowed_path_prefixes,
        )

    def _render_phase_block(self) -> str:
        if not self._phase:
            return ""
        entries = (
            (path, purpose)
            for path, purpose in _COMMON_SKILLS.get(self._phase, ())
            if self._allowed_path_prefixes is None
            or any(path.startswith(prefix) for prefix in self._allowed_path_prefixes)
        )
        lines = ["", "", "[Skillogy quick reference]"]
        for path, purpose in entries:
            lines.append(f"- {path} — {purpose}")
        if len(lines) == 3:
            return ""
        lines.append(
            "These are examples, not required startup calls. Use find_skill for other specialist knowledge."
        )
        return "\n".join(lines)

    @override
    def wrap_model_call(self, request, handler):
        return handler(self._inject(request))

    @override
    async def awrap_model_call(self, request, handler):
        return await handler(self._inject(request))

    def _inject(self, request):
        if not self._append_policy:
            return request
        injected_text = _POLICY_PROMPT + self._phase_block
        if request.system_message is not None:
            new_content = [
                *request.system_message.content_blocks,
                {"type": "text", "text": injected_text},
            ]
        else:
            new_content = [{"type": "text", "text": injected_text}]
        new_system = SystemMessage(content=new_content)
        return request.override(system_message=new_system)


def maybe_install_skillogy(
    middleware_stack: list[Any],
    *,
    role: str | None = None,
    skill_sources: list[str] | None = None,
) -> list[Any]:
    """Substitute ``SkillogyMiddleware`` for ``SkillsMiddleware`` when the
    backend flag is set. Idempotent; swap-only (does not append).

    Args:
        middleware_stack: ordered middleware list from ``build_middleware``.
        role: agent role (e.g. ``"recon"``) — resolved to its
            ``:Phase.name`` via ``_PHASE_FOR_ROLE`` and threaded into the
            new ``SkillogyMiddleware`` so its MoC summary block is scoped
            to the agent's phase. ``None`` (or an unknown role) yields a
            middleware with no phase block — the agent still gets the
            schema cheat-sheet and the three tools.
        skill_sources: per-role path-prefix allowlist (ADR-0008). Same
            list ``_make_skills`` threads into the legacy
            ``SkillsMiddleware``: e.g.
            ``["/skills/standard/recon/", "/skills/shared/"]``. When
            ``None`` is passed but a ``role`` is known, the helper falls
            back to ``skills_sources_for(role)`` so the two skill
            backends share one source-of-truth for "what does this role
            see". When neither is supplied (library use, pytest), the
            ACL stays unrestricted to match the unwrapped backend.
    """
    if not _is_enabled():
        return middleware_stack
    try:
        from decepticon.middleware.skills import SkillsMiddleware  # noqa: PLC0415
    except ImportError:
        return middleware_stack
    if not any(isinstance(mw, SkillsMiddleware) for mw in middleware_stack):
        return middleware_stack
    phase = _PHASE_FOR_ROLE.get(role) if role else None
    prefixes = _resolve_allowed_path_prefixes(role=role, skill_sources=skill_sources)
    out: list[Any] = []
    for mw in middleware_stack:
        if isinstance(mw, SkillsMiddleware):
            out.append(
                SkillogyMiddleware.from_env(
                    agent_phase=phase,
                    allowed_path_prefixes=prefixes,
                )
            )
        else:
            out.append(mw)
    return out


def _resolve_allowed_path_prefixes(
    *,
    role: str | None,
    skill_sources: list[str] | None,
) -> list[str] | None:
    """Resolve the path-prefix ACL the middleware should enforce.

    Priority order, mirroring how the legacy ``SkillsMiddleware``
    ``sources`` argument is handled:

    1. Explicit ``skill_sources`` from the caller wins (lets benchmark
       mode and plugin extensions inject extra paths).
    2. ``role`` falls back to ``skills_sources_for(role)`` so the two
       skill backends share one role → sources contract.
    3. Otherwise ``None`` — the ACL stays disabled, matching how the
       backend interprets the kwarg when no role context exists.
    """
    if skill_sources is not None:
        return _checked_scope(list(skill_sources), role=role)
    if role is None:
        return None
    try:
        from decepticon.agents.middleware_slots import skills_sources_for  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001
        raise SkillogyScopeError(f"Skillogy scope resolver unavailable for {role!r}") from exc
    try:
        return _checked_scope(list(skills_sources_for(role)), role=role)
    except SkillogyScopeError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise SkillogyScopeError(f"Skillogy scope resolution failed for {role!r}") from exc
