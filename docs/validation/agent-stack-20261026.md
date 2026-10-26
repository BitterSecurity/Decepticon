# Agent stack refresh: compatibility and validation

This proposed source change targets versions available by 2026-10-26 KST.
Validation continued after midnight on 2026-10-27. It does not publish a new
Decepticon package or change the serving SaaS image.

| Package | Previous workspace lock | Candidate |
|---|---|---|
| deepagents | 0.6.8 | 0.7.32 |
| langchain | 1.3.9 | 1.4.7 |
| langgraph | 1.2.4 | 1.2.17 |
| langchain-core | 1.4.9 | 1.7.0 |
| langgraph-checkpoint | 4.1.1 | 4.3.0 |
| langgraph-sdk | 0.4.4 | 0.4.9 |
| langsmith | 0.8.18 | 0.14.12 |

The workspace lock also records the resolver-required provider/dependency
updates. Runtime requirements constrain Deep Agents to the tested 0.7 series
and LangChain/LangGraph to their 1.x series; the lock records exact versions.

## Why upgrade

The maintained `langchain-nvidia-openshell` 0.1.1 backend requires Deep Agents
≥0.7.25,<0.8 and OpenShell ≥0.1.4,<0.2. Updating the harness enables reuse of this
upstream execution/file adapter instead of implementing its basic operations.
It does not itself select an E2B compute driver, solve the guest kernel ABI,
provide Decepticon's tmux jobs, or admit an engagement.

The new harness adds richer filesystem permissions, content/offload handling,
backend capability selection and middleware configuration. Those features need
explicit integration with our authorization contracts. A tool-level filesystem
permission cannot replace OpenShell's process/kernel/network enforcement.
This PR preserves the existing user-visible tool surface rather than enabling
every new upstream capability automatically.

## Required compatibility work

Deep Agents 0.7 requires initialized backend **instances**, not backend
factories. Use `StateBackend()` rather than `StateBackend` or
`lambda runtime: StateBackend(runtime)`. The state backend resolves graph
context at operation time. Test doubles were updated to implement the backend
protocol instead of being callable, unconstrained mocks.

Upstream filesystem tools also stopped calling `_get_backend(runtime)` and
read their middleware's backend instance directly. Leaving our old override
unchanged would lose the per-run engagement workspace and sandbox binding.

The middleware now supplies one stable protocol instance whose operations use
a **ContextVar-bound engagement backend**. Public sync/async tool and model
wrappers establish the binding for that request and restore it in `finally`.
No shared backend field is changed during a request. Thread offloading carries
the context. The new grep `max_count` argument is forwarded and result metadata
is retained. Access without a binding follows the existing no-workspace error
contract and never reaches the construction-time sandbox.

Model requests keep state separately from their Runtime object; model-side
media/result handling uses request state and the current graph config. Tool
requests use their injected ToolRuntime, including its explicit config for
subagent transport selection. Existing virtual path mapping, Plan restrictions,
immutable `plan/roe.json` and route preservation remain in place.

Upstream added a recursive `delete` tool. Our scoped backend does not expose it
yet, because recursive deletion needs an explicit directory/RoE contract. The
tool selection applies when upstream rebuilds tools for permission-configured
middleware copies as well as at construction. Generic upstream `execute`
remains unexposed; Decepticon's bash tools retain execution ownership.

## Validation

The candidate is installed in this task worktree's own `.venv` using the existing
uv lock/store. The user's main worktree environment is unchanged.

- Initial affected suite: 1,573 passed with four compatibility failures; these
  identified the removed backend hook and an old backend-factory fixture.
- After migration, filesystem, graph registration and planning checks passed
  (101 tests before the additional concurrency/model binding regressions).
- Added real upstream file-tool dispatch checks for sequential and concurrent
  engagements, missing binding, cancellation cleanup and model-side access.
- Broad PR selection (`pytest -n 4 -m "not slow"`): **6,067 passed, 43 skipped** in 118.93 seconds. Skips include Windows-only locking and unavailable live Neo4j fixtures; these are not claimed as live-service qualification.
- Repository Ruff lint and format checks passed. Basedpyright analyzed 842 files with **zero errors**; existing warnings remain non-blocking under the repository gate.
- Final filesystem/SARIF regression selection: **88 passed**, including bounded grep, concurrent tool dispatch, cancellation, model-side binding and large-result offloading.
- Optional ML detector coverage requires transformers; absence is reported as
  a skip rather than a pass. MCP/Neo4j extras are installed for the broad lane.
- The slow benchmark harness tests deliberately wait for service health and
  are excluded by the repository's PR gate (`make ci-test`, `not slow`). An
  exploratory full sequential run reached that existing slow path and was
  stopped; it is not reported as a passing full/release suite. Main/release
  coverage gates remain unchanged.

Separately, the maintained OpenShell adapter's `test_sandbox.py` passed all
41 tests in an isolated environment containing Deep Agents 0.7.32 and OpenShell
SDK 0.1.5. Those are adapter unit tests with SDK fakes, not a live Gateway or
E2B policy test.

## SaaS adoption

The inspected SaaS source pins Deep Agents 0.6.12 and Decepticon/core/SDK 1.2.5,
and installs under the LangGraph API base image's constraints. The inspected
base constraints allow the candidate LangGraph/checkpoint/core ranges, but that
does not make the old Decepticon filesystem integration compatible with 0.7.

Publish and validate a compatible immutable OSS artifact before changing the
SaaS pin. Do not reinstall newer dependencies over old Decepticon code or use a
process-global monkeypatch to compensate. A SaaS candidate must test plugin
assembly, per-engagement sandbox binding, workflows/approval, checkpoint resume,
tool results and the exact server-image dependency set. No production upgrade
is claimed by these local tests.

## Development skill freshness

The installed LangChain family was last updated on 2026-03-12 according to
`~/.agents/.skill-lock.json`. Its source is `langchain-ai/langchain-skills`.
Compared to upstream `126c384d8552dcb476b226c244172186cc3f4328` (2026-10-26),
many differences are formatting, but middleware-hook guidance, persistence
examples and the LangGraph skill organization have changed. The old
`framework-selection` entry is absent from the current upstream catalog.

Even fresh upstream skill documents contain examples that must be checked
against the selected package: backend factories / `StateBackend(runtime)` are
incompatible with 0.7.32. Thread-scoped StateBackend data may be persisted by a
checkpointer and is not necessarily lost when one invocation ends. LangChain
has explicit middleware, not only callbacks. This task did not silently modify
the globally installed skills.

Sources: [Deep Agents release](https://pypi.org/project/deepagents/0.7.32/),
[LangChain release](https://pypi.org/project/langchain/1.4.7/),
[LangGraph release](https://pypi.org/project/langgraph/1.2.17/),
[maintained OpenShell adapter](https://github.com/langchain-ai/langchain-nvidia/tree/aa1bbf5bb36f3ce0d853b51b809112cec30a8e8d/libs/openshell),
[current development skills](https://github.com/langchain-ai/langchain-skills/tree/126c384d8552dcb476b226c244172186cc3f4328).
