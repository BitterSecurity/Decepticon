# Agent storage and Rust framework investigation

Status: research, reviewed 2026-11-16. This is evidence for the proposed
[local-first Rust design](../design/2026-11-16-local-first-rust.md), not a
framework selection, implementation, release, or performance certification.

## Scope and method

The OSS baseline is `1c0df39549c82f7c76e7d0b1667557f3778438f7`, fetched from
remote `main` before creating the research worktree. The investigation combines
an AST/file inventory, focused reads of construction and execution paths,
local baseline tests, official external documentation, and selected source
files from released Rust frameworks. It does not claim a line-by-line audit
of every technical recipe or test every advertised upstream feature.

Entire Search found earlier daemon, host integration, filesystem-boundary and
durable-transcript commits. The selected daemon commit had no checkpoint
trailer; no session transcript established its original intent. Brain reported
stale context and Graph reported two partial parsing failures. Current source
and tests were therefore checked directly. Explanations below describe observed
code and design inferences, not transcript-backed historical intent.

## What actually needs porting

Counts are physical lines, including comments and blanks, excluding named tests
from component groups. They are not estimates of necessary Rust code.
The [inventory](rust-port-20261116/inventory.json) and
[reproducer](rust-port-20261116/inventory.py) measure the immutable Git tree;
submodule contents are excluded.

| Component | Source files | Physical lines | Porting implication |
| --- | ---: | ---: | --- |
| Contract package | 36 | 6,563 | Validation, schemas, registries and host protocols |
| Python framework | 367 | 79,310 | Agents, middleware, tools, models, reporting and knowledge |
| Sandbox package, including JS templates | 35 | 7,778 | Daemon, sessions, browser and web acquisition |
| Python SDK | 6 | 724 | Published extension and host API compatibility |
| Launcher, including its JS readiness probe | 85 | 14,240 | Install, infrastructure, workspace lifecycle, credentials, updater |
| Ink CLI | 104 | 9,972 | Streaming, questions, resume, terminal interaction |
| Shared streaming | 6 | 265 | Event contracts and subagent identity |
| Optional telemetry Worker | 4 | 430 | Separate deployment and consent boundary |
| Provider configuration | 13 | 6,158 | Routing, authentication and protocol conversions |
| Benchmark adapters | 43 | 8,678 | Evaluation and reproducibility |

There are no tracked `.rs` files at this baseline. There are 329 runtime
`SKILL.md` documents, 21 static graph registrations, and 240 Python `@tool`
declarations; declarations are not a count of simultaneously available tools.
128 framework files directly import LangChain, LangGraph or Deep Agents.
The Python lock contains 195 entries including development/workspace packages.
Relevant pins include LangGraph 1.2.17, Deep Agents 0.7.32 and Agent Server 0.16.6.

The implementation paths that constrain a replacement are:

| Path or symbol | Observed contract |
| --- | --- |
| `agents/standard/decepticon.py`, `agents/build.py` | `create_agent`, compiled subagents, named tool composition, plugin overrides and middleware order |
| `middleware/search_runtime.py`, `artifacts/` | The default orchestrator receives search/artifact completion rules; this is not merely a ReAct tool loop |
| `middleware/opplan_dispatch.py`, `tools/opplan*.py` | Objective ownership, current revision, dependency readiness and evidence checked before dispatch |
| `middleware/workflow.py`, `workflows/models.py` | Full immutable workflow source/hash, purpose separation, role-scoped compilation and conflicting-pin rejection |
| `middleware/state_reducers.py`, `subagent_transcript_state.py` | Parallel branches need explicit merges; append-only transcripts must not become last-write-wins |
| `core/subagent_streaming.py` | Per-invocation identity, live events and bounded durable transcript copies |
| `tools/interaction/ask_user.py`, CLI `useAgent.ts` | A question suspends execution; resume has replay/deduplication semantics |
| `backends/providers.py`, `policy_compiler.py`, `openshell_local.py` | OpenShell is default; trusted admission binds approved bytes, effective policy and workspace identity |
| `middleware/roe.py` | Deprecated pass-through; historical command pacing and the old guardrail are not current enforcement |
| `middleware/time_window.py`, `step_budget.py`, `runtime/engagement_schedule.py` | Time windows, turn-scoped step limits, campaign versus fresh-thread scheduling |
| `decepticon_sandbox/kernel/`, `server/` | tmux markers, auto-background, job generations, log offsets, bounded bridge and root-published RoE handling |
| `decepticon_sdk/host.py`, core `contracts/host_integration.py` | Python callable/object-based public host and plugin contracts; not a portable wire ABI |
| `middleware/kg_internal/store.py`, `tools/research/_state.py` | Current knowledge persistence and legacy adapter use scoped Neo4j operations; the old JSON-default design note is not the current implementation |

Paths in this table are relative to `packages/decepticon/decepticon/` unless
qualified otherwise. Older comments still mention retired nftables guards,
historical role counts, and old workflow locations. Port the current call path,
not every comment literally. For example, `parallel_dispatch.py` existing in the
tree is not evidence that its proposed scheduler is installed in the default
stack; `build_middleware` and the actual `task` wrapper determine that behavior.

Removing Python requires more than replacing FastAPI. The sandbox bridge and
browser/web runners explicitly invoke Python. Deep Agents' installed
`BaseSandbox` generates Python scripts for filesystem operations. The web
fallback includes Node/Playwright templates, and `config/` includes substantial
provider handlers. Obscura is already an externally built Rust binary, despite
the absence of in-tree Rust sources.

Compose starts `langgraph dev` and preserves `/app/.langgraph_api`. The installed
`langgraph-runtime-inmem` 0.36.9 uses `.pckl` files for checkpoint/store data.
Copying that volume to a Rust image does not establish resume compatibility.
This is an installed-source observation, not a destructive inspection of user
conversation volumes.

## Filesystem versus database: compare three separate decisions

An agent can have a filesystem-shaped tool interface, a file-based knowledge
repository, and a transactional execution database at the same time. The
interface the model sees does not identify the persistence implementation.

| System | What the primary material actually establishes | Consequence for Decepticon |
| --- | --- | --- |
| Anthropic context engineering | File references, targeted search and notes support progressive context loading; this is a context-management pattern, not a specification for crash-safe execution storage. [Engineering article](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents) | Keep path-based navigation and external artifacts; do not infer that notes can replace execution checkpoints. |
| Deep Agents | The same filesystem tools can route to thread state, local files, persistent stores or sandboxes. [Backend documentation](https://docs.langchain.com/oss/python/deepagents/backends) | Decepticon already has a hybrid abstraction; Rust need not expose SQL to the model. |
| LangGraph | Checkpointers preserve thread execution state; stores preserve application data across threads. [Persistence](https://docs.langchain.com/oss/python/langgraph/persistence) | Conversation history, long-term knowledge and suspended execution have different lifecycles. |
| Pi | Released source documentation describes JSONL sessions with `id`/`parentId` branching and retained compaction history. [Session format](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/session-format.md) | A file log can implement rich sessions; SQL is not inherently required for branching. Multi-resource transactions and recovery remain a separate engineering obligation. |
| OpenClaw Memory Core | Current documentation describes files plus SQLite, with provenance stored outside model-written prose and different rules for curated and episodic memory. [Architecture](https://docs.openclaw.ai/concepts/memory-architecture) | Inspectable Markdown and protected provenance fit together; calling all SQLite state a disposable search cache would miss authority metadata. |
| Letta | Legacy stateful-agent docs describe database-persisted state. Current SDK docs use Git-backed MemFS and distinguish persistent conversations from connection-scoped resources. [Legacy model](https://docs.letta.com/v1-sdk/concepts/stateful-agents), [MemFS](https://docs.letta.com/concepts/memfs), [current durability contract](https://docs.letta.com/agent-sdk/sessions) | Version the comparison. A file-shaped or Git-backed memory can still depend on hosted infrastructure; it is not automatically local-first. |
| OpenViking | A context service exposes a filesystem namespace over source content and vector retrieval. It is a context backend, not a substitute for the complete agent harness. [Architecture](https://openviking.ai/concepts), [system implementation](https://blog.openviking.ai/post/openviking-context-database-architecture/) | Hierarchical retrieval is useful to evaluate; adopting the whole service would add a Python orchestration dependency to the proposed Rust product. |

Letta's 2025 article titled
[“Is a Filesystem All You Need?”](https://www.letta.com/blog/benchmarking-ai-agent-memory/)
does not demonstrate a database-free system beating databases. Its described
files are parsed and embedded, semantic search is available, and tool rules
shape the retrieval loop. The reported LoCoMo result concerns that model/harness
and retrieval workload. It does not test crash recovery, concurrent writes,
authorization or destructive-tool replay. No external benchmark was reproduced
in this investigation, and no universal performance winner is established.

The design inference is to make files the knowledge/artifact interface while
keeping execution, authority and commit records transactional. A file-only
alternative remains possible, but would have to implement a journal, locking,
revision checks, recovery and indexing; those costs belong in its comparison.

## Rust framework maturity: evidence rather than feature counts

The comparison baseline is the capabilities Decepticon actually obtains from
its pinned LangChain/LangGraph/Deep Agents stack, not an assertion that its
current stack has no defects. `1.0` tags, stars and “production-ready” website
language do not prove equivalent maturity.

| Candidate and inspected release | Evidence | Remaining adoption question |
| --- | --- | --- |
| Rig `v0.46.0`, 2026-10-31, MIT | Released source has serializable `AgentRun`, durable-execution test cases, provider/cassette infrastructure and a configurable harness. [Release](https://github.com/0xPlaygrounds/rig/releases/tag/v0.46.0), [durability tests](https://github.com/0xPlaygrounds/rig/blob/v0.46.0/crates/rig-cassette/tests/durable_execution.rs) | Strong first evaluation candidate. Check dynamic delegation, merge rules, recovery under real process death, version upgrades and the amount of product-specific runtime code still required. |
| Goose `v1.59.2`, 2026-11-16, Apache-2.0 | Released Rust source includes SQLx/SQLite session management, an execution state machine, tool approval and subagents. [Session manager](https://github.com/aaif-goose/goose/blob/v1.59.2/crates/goose/src/session/session_manager.rs), [subagents](https://github.com/aaif-goose/goose/blob/v1.59.2/documentation/docs/guides/context-engineering/subagents.mdx) | Mature application delivery and reusable-framework stability are different. The inspected `goose-agent` crate is `0.1.0-alpha.13`; verify the supported embedding boundary and ensure developer shell defaults cannot bypass OpenShell. |
| ADK-Rust `v2.3.0`, 2026-10-09, Apache-2.0 | Source provides graph state, SQLite checkpoints, interrupt/resume and managed-state components. [Checkpoint implementation](https://github.com/zavora-ai/adk-rust/blob/v2.3.0/adk-graph/src/checkpoint.rs), [functional context](https://github.com/zavora-ai/adk-rust/blob/v2.3.0/adk-graph/src/functional/context.rs) | Strong graph-shaped candidate, but the website labels the managed runtime experimental. Its functional-API docs also distinguish memory-only background-run records from durable checkpoints. Qualify the exact selected execution path, not the aggregate feature list. [Documentation](https://adk-rust.com/en/docs), [background limits](https://adk-rust.com/en/docs/agents/functional-api) |
| AutoAgents `v0.4.0`, 2026-07-08 | Public release and README cover actors, tools, providers, local models and design patterns. [Release](https://github.com/liquidos-ai/AutoAgents/releases/tag/v0.4.0), [README](https://github.com/liquidos-ai/AutoAgents) | No sufficient checkpoint/restart equivalence evidence was established in this review; this means unqualified, not proof of an absent feature. Keep outside the first adoption shortlist. |

There is concrete documentation drift: the retrieved Rig durable-run guide
describes `RUN_FORMAT = 2`, while inspected `v0.46.0` source declares format 5.
Use the tagged source/API and a pinned persistence format for qualification.
The source rejects incompatible formats, which is useful, but rejection alone
is not a migration implementation. [Guide](https://rig.rs/docs/concepts/runs),
[tagged state machine](https://github.com/0xPlaygrounds/rig/blob/v0.46.0/crates/rig-agent/src/run/mod.rs).

No candidate was built, exercised against real models or subjected to process
crash tests here. Their upstream tests were read, not run. No candidate can yet
be described as proven equivalent to the installed Decepticon stack. The first
evaluation should cover Rig and Goose, with ADK-Rust evaluated for graph fit.
This order is a design recommendation, not a measured ranking.

If none qualifies, a bounded Rust-native framework is a valid fallback. It
must meet the same acceptance gates; custom code does not inherit maturity
from Tokio, SQLite, or the fact that it compiles. General provider plumbing and
protocol implementations can still be reused without delegating Decepticon's
authority or lifecycle to them.

## Evidence and limits

[Local verification](rust-port-20261116/verification.md) records 3,599 passing
selected Python cases, 11 skips, a passing launcher suite and 294 passing CLI
cases in a serial run. It also preserves an initial parallel fixture race and
an initial timing-sensitive CLI failure. These are current-source baselines;
they are not Rust parity results, full-suite coverage or live engagement
qualification. No product runtime, deployment, user workspace or user volume
was changed.
