# Library Consumer Guide

For agents, skills, business workflows and client adapters, start with the
[current extension contract](design/extension-contract.md).

Audience: a commercial product or downstream framework that embeds
Decepticon — e.g., a dashboard, a B2B API service, or a research
toolkit. Library consumers compose agents, override middleware, and
ship custom plugins layered on top of the OSS stack.

## 1. Install

```bash
pip install decepticon
```

For the full Docker stack (with sandbox container + LiteLLM proxy +
Neo4j) see the umbrella [`README.md`](../README.md).

## 2. Use the public API

Spec §6.2 defines the SemVer-stable surface plugin authors and
library consumers can rely on from v1.1.2 onward (the current
release series — see [CHANGELOG.md](../CHANGELOG.md)):

```python
from decepticon.agents import (
    create_decepticon_agent,
    create_soundwave_agent,
    create_recon_agent,
    # ... 16 factories total
    build_middleware,
    build_tools,
)
from decepticon.backends import (
    HTTPSandbox,
    build_sandbox_backend,
    make_agent_backend,
)
from decepticon.middleware import (
    EngagementContextMiddleware,
    FilesystemMiddleware,
    OPPLANMiddleware,
    SandboxNotificationMiddleware,
    SkillsMiddleware,
    ModelOverrideMiddleware,
)
from decepticon.llm import LLMFactory, create_llm
```

Plugin contracts come from `decepticon-core` (or via the
`decepticon-sdk` re-export):

```python
from decepticon_sdk import (
    BackendProtocol, MiddlewareProtocol, ToolProtocol,
    PluginBundle, SubAgentSpec,
    RoleRegistry, PluginRegistry,
    ToolContribution, MiddlewareContribution, PromptContribution,
)
```

## 3. Compose an agent

The simplest path — use an OSS factory and pass it your own sandbox:

```python
from decepticon.backends import build_sandbox_backend, make_agent_backend
from decepticon.agents import create_recon_agent

sandbox = build_sandbox_backend()
backend = make_agent_backend(sandbox)
agent = create_recon_agent(backend=backend)
```

## 4. Override middleware

The 16 agent factories accept langchain-style kwargs that fully
replace the OSS baseline for that surface. Useful when your product
ships a Slack-aware human-in-the-loop tool instead of OSS's CLI
prompt:

```python
agent = create_recon_agent(
    tools=[my_custom_tool, ...],          # full replace OSS tools
    middleware=[my_audit_middleware, ...], # full replace OSS middleware
    system_prompt="...",                   # full replace OSS prompt
)
```

For fine-grained slot replacement (without rewriting the full
stack), use a `PluginBundle`:

```python
from decepticon_sdk import PluginBundle


VENDOR_OVERLAY = PluginBundle(
    name="vendor-overlay",
    activation_label="vendor",
    # Drop OSS prompt caching; ship a vendor-aware one in its place
    middleware=...,  # MiddlewareContribution
)
```

Ship the bundle via the `decepticon.bundles` entry-point group; the
framework's `build_middleware` picks it up.

## 5. Mount per-tenant assets — `make_agent_backend(extra_routes=)`

For multi-tenant or B2B Enterprise deployments, mount your own
asset trees onto the agent's filesystem:

```python
from decepticon.backends import make_agent_backend
from deepagents.backends import FilesystemBackend


def make_tenant_backend(sandbox, tenant_id: str):
    return make_agent_backend(
        sandbox,
        extra_routes={
            f"/skills/tenant/{tenant_id}/": FilesystemBackend(
                root_dir=f"/data/tenants/{tenant_id}/skills",
                virtual_mode=True,
            ),
            f"/credentials/{tenant_id}/": tenant_credentials_backend,
        },
    )
```

Routes are sorted by descending prefix length — a longer tenant
path always wins over the generic `/skills/` default (spec §16.4
#5 — load-bearing for the future B2B Enterprise API tier).

## 6. Register custom roles — `RoleRegistry.register()`

Add a new agent role to the framework's catalog. The LLM factory
and middleware assembler will see it as first-class:

```python
from decepticon_sdk import RoleRegistry, MiddlewareSlot

RoleRegistry.register(
    "apt",
    slots=frozenset({
        MiddlewareSlot.ENGAGEMENT_CONTEXT,
        MiddlewareSlot.SKILLS,
        MiddlewareSlot.FILESYSTEM,
        MiddlewareSlot.MODEL_FALLBACK,
        MiddlewareSlot.PROMPT_CACHING,
    }),
    skill_sources=("/skills/apt/", "/skills/shared/"),
    llm_role_fallback="decepticon",   # falls back to OSS routing if no apt-specific model
)
```

Idempotent on identical parameters — safe to call from every worker's
startup hook.

## 7. Audit and observability

`PluginRegistry.introspect_role()` returns a frozen `RoleResolution`
snapshot — hashable, cacheable, suitable as an audit record key per
run ID:

```python
from decepticon_sdk import PluginRegistry

reg = PluginRegistry.load()

# Collision detection — surface plugin conflicts at boot
for collision in reg.detect_collisions():
    metrics.incr("decepticon.plugin.collision", tags=[
        f"kind:{collision.kind}",
        f"key:{collision.key}",
        f"loser:{collision.previous_owner}",
        f"winner:{collision.owner}",
    ])

# Role introspection — what's actually loaded for an agent
resolution = reg.introspect_role("recon")
if resolution is not None:
    audit_log.write({
        "run_id": run_id,
        "role": "recon",
        "middleware_stack": [m.name for m in resolution.middleware_stack],
        "tools": [t.name for t in resolution.tool_list],
        "skill_sources": list(resolution.skill_sources),
        "overrides_applied": [
            {"kind": o.kind, "key": o.key, "owner": o.owner, "action": o.action}
            for o in resolution.overrides_applied
        ],
    })
```

## 8. Patterns for Enterprise customers

Spec §16 documents the forward-compat design that keeps the contract
layer suitable for B2B Enterprise integration:

- Pin to `decepticon-core==X.Y.*` for stable integration code (zero
  runtime deps, survives framework refactors for years).
- Use `RoleRegistry` to ship per-tenant role catalogs without forking.
- Use `make_agent_backend(extra_routes=...)` for per-tenant asset
  isolation (longest-prefix-wins).
- Use `SafetyDeclaration` to lock down tool / middleware names that
  your tenant policy classifies as safety-critical.

## See also

- Plugin author guide:
  [`docs/plugin-author-guide.md`](plugin-author-guide.md).
- Contributor architecture guide:
  [`docs/contributor-architecture.md`](contributor-architecture.md).
- Spec:
  [current extension contract](design/extension-contract.md).
