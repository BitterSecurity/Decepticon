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

Use the installed version’s public exports and the compatibility policy in
[RELEASE.md](../RELEASE.md). The following imports match current source:

```python
from decepticon.agents import (
    create_decepticon_agent,
    create_soundwave_agent,
    create_recon_agent,
)
from decepticon.agents.build import build_middleware, build_tools
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
)
from decepticon.middleware.model_override import ModelOverrideMiddleware
from decepticon.llm import LLMFactory, create_llm
```

### Host integration surface

A host runs agents in its own process and routes each run to its own
sandbox — a dashboard, an API service, or a multi-tenant product. Import the
runtime integration from one stable module instead of framework internals:

```bash
pip install 'decepticon-sdk[host]'
```

```python
from decepticon_sdk.host import (
    HostRunConfigurable,          # typed config["configurable"] keys the framework reads
    build_sandbox_backend,        # per-run sandbox from configurable.sandbox_url/_token
    make_agent_backend,
    configure_openshell_resolver, # install the trusted OpenShell admission resolver
    is_openshell_sandbox,         # type-based check for the kernel-boundary transport
    rebind_sandbox_per_run,       # re-resolve a composed backend for the current run
    workspace_from_runtime,
    build_middleware, build_tools, resolve_role_model, iter_override_bundles,
    create_soundwave_agent,       # imported lazily; builds nothing until accessed
    LLMFactory,
    ModelOverridesState, ProxyKeyState, SubagentTranscriptState, reduce_converging_value,
)
```

Every name in `decepticon_sdk.host.__all__` is in the SDK stability manifest:
removing or renaming one is a breaking change under [RELEASE.md](../RELEASE.md).
The module paths behind them are internal and may move. `HostRunConfigurable`
lists only keys the framework actually reads; values are trusted input, so a
host must authenticate and validate anything it forwards from end users.

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

Agent factories accept langchain-style kwargs that fully
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
    bundle="vendor",
    roles=("recon",),
    disabled_middleware=("prompt-caching",),
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

- Pin compatible core/framework/SDK versions and qualify upgrades. Core avoids
  framework dependencies but still depends on Pydantic and typing support.
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
