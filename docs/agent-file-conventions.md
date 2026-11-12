# Agent File & Reporting Conventions

> How Decepticon's agents write to disk, hand work off to each other, and
> report back to the orchestrator. For process and authorization ownership,
> use the [extension contract](design/extension-contract.md). Live objective
> schemas and pinned workflow artifacts govern each execution.

Decepticon's multi-agent loop is **file-mediated, not memory-mediated**.
Each specialist agent is spawned with a fresh context window (no parent
transcript), does its work, writes its results to agreed-upon paths in the
engagement workspace, and returns a short summary. The orchestrator reads
those files on the next turn to decide what to dispatch next. Findings
survive context summarization, agent restarts, and process crashes because
they live on disk, not in the LangGraph message history.

This document covers:

1. [Where files physically live](#1-where-files-physically-live)
2. [The workspace directory layout](#2-the-workspace-directory-layout)
3. [The file-writing tools agents have](#3-the-file-writing-tools-agents-have)
4. [Per-phase artifact contracts](#4-per-phase-artifact-contracts) (recon / exploit / post-exploit / analyst)
5. [Terminal-state handoff artifacts](#5-terminal-state-handoff-artifacts)
6. [The orchestrator read protocol](#6-the-orchestrator-read-protocol)
7. [Cross-agent shared conventions](#7-cross-agent-shared-conventions)
8. [Planning documents (Soundwave)](#8-planning-documents-soundwave)
9. [Final report generation](#9-final-report-generation)
10. [The knowledge graph as a parallel channel](#10-the-knowledge-graph-as-a-parallel-channel)
11. [Benchmark / CTF mode differences](#11-benchmark--ctf-mode-differences)
12. [Client-side surfacing](#12-client-side-surfacing)
13. [Lifecycle and checkpointing](#13-lifecycle-and-checkpointing)
14. [Writing a plugin agent that fits the contract](#14-writing-a-plugin-agent-that-fits-the-contract)

---

## 1. Where files physically live

Agents see a single virtual root: **`/workspace`**. Three layers sit
between that virtual path and the host disk:

| Layer | Component | Responsibility |
|-------|-----------|----------------|
| Agent-facing | `FilesystemMiddleware` (`middleware/filesystem.py`) | Exposes `ls / read / write / edit / grep / glob / download_files`. No `execute` tool — shell goes through the bash tool. |
| Engagement scoping | `EngagementFilesystemBackend` | Maps virtual `/workspace` → real `/workspace/<engagement-slug>` and rejects path traversal (`/workspace/../etc`). Writes a `.engagement` marker at the root on first use. |
| Transport | `HTTPSandbox` (`backends/http_sandbox.py`) | Forwards every file op over HTTP to the sandbox daemon (`/upload_files`, `/download_files`, …). |

Inside the sandbox container, the daemon (`sandbox_server/app.py` wrapping
`sandbox_kernel/`) executes the actual disk operations. The container's
`/workspace` is **bind-mounted** from the host:

```
~/.decepticon/workspace/<engagement-slug>/   (host)
        ⇅  docker volume bind
/workspace/                                   (container, what the agent sees)
```

So a write to `/workspace/findings/FIND-001.md` lands at
`~/.decepticon/workspace/<slug>/findings/FIND-001.md` on the host, where
operators and host tools can read it directly.

**Engagement isolation is physical, not state-level.** Each engagement is
its own subdirectory; the scoping backend prevents any agent from reaching
another engagement's tree. This is why state-merge reducers between agents
are minimal — agents don't pass files through LangGraph state, they share a
bound directory.

Two side-channel logs live alongside the workspace and are written by
middleware, not agents:

- `events.jsonl` — append-only structured event log (`EventLogMiddleware`,
  `runtime/event_log.py`): engagement start, agent turns, LLM/tool calls,
  findings, OPPLAN updates. Failures here never break the agent.
  Every RoE pass/refuse is stamped with `seq`, `hash`, `prev_hash`, `hmac`;
  tampering with record N invalidates all later records.

---

## 2. The workspace directory layout

```
/workspace/                              ← virtual root (real: /workspace/<slug>)
├── plan/                                ← scope, optional planning bundle and execution OPPLAN (JSON)
│   ├── roe.json                         ← Rules of Engagement (read before every dispatch)
│   ├── conops.json                      ← Concept of Operations
│   ├── deconfliction.json
│   ├── threat-profile.json
│   ├── data-handling.json
│   ├── cleanup.json
│   ├── contact.json
│   ├── abort.json                       ← required only for full Plan publication
│   └── opplan.json                      ← Operational Plan (objectives + status)
│
├── recon/                              ← reconnaissance artifacts (lazy)
│   ├── SUMMARY.md                       ← MANDATORY terminal handoff (terminal token inside)
│   ├── report_<target>.md               ← detailed markdown recon report
│   └── probed.txt                       ← HTTP probe dedup log (append-only)
│
├── exploit/                            ← initial-access / exploitation artifacts (lazy)
│   ├── shells.json                      ← active session inventory
│   ├── creds/
│   │   ├── initial.json                 ← captured credentials (structured)
│   │   └── credentials.md               ← verbatim high-value secrets (written first, always)
│   ├── ACTIVE.md                        ← vector lock during an active attempt
│   ├── CONVERGED.md                     ← convergence terminal state
│   ├── PIVOT.md                         ← pivot terminal state
│   └── EXIT_REPORT.md                   ← exit-report terminal state
│
├── post-exploit/                       ← post-exploitation artifacts (lazy)
│   └── creds/                           ← extends exploit/creds/ across the kill chain
│
├── findings/                           ← cross-phase findings namespace
│   ├── FIND-001.md                      ← one finding per file (operational tier)
│   ├── FIND-002.md
│   └── evidence/                        ← raw evidence backing findings
│
├── report/                             ← final deliverables (generated at close)
│   ├── executive-summary.md
│   ├── technical-report.md
│   └── finding-NNN.md                   ← deliverable-tier promotions of FIND-NNN.md
│
├── timeline.jsonl                       ← append-only activity/finding timeline
├── .sessions/                           ← per-session tmux logs (<session>.log)
└── .engagement                          ← scoping marker
```

**Lazy creation is a hard rule.** Agents create a parent directory only
immediately before writing a required artifact. Scaffolding empty
directories or placeholder files (`README`, `INDEX`, `SUMMARY` stubs,
`ASSESSMENT`) is explicitly forbidden across all agent prompts — it wastes
context and provides no operational value.

**Markdown for deliverables, JSON for operational data.** Every
human-facing document (findings, reports, summaries) is Markdown. JSON is
reserved for machine-read operational state: `opplan.json`, `shells.json`,
`creds/initial.json`, `timeline.jsonl`.

---

## 3. The file-writing tools agents have

The filesystem middleware exposes these tools to every agent that mounts it:

| Tool | Use |
|------|-----|
| `write_file(path, content)` | Create/overwrite a workspace file |
| `read_file(path)` | Read a workspace file (orchestrator uses this to read sub-agent output) |
| `edit_file(path, …)` | In-place edit |
| `ls(path)` | List a workspace directory |
| `glob(pattern)` | Find files by pattern (e.g. `findings/*.md`) |
| `grep(pattern, …)` | Search workspace file contents |
| `download_files(...)` | Pull files out of the sandbox |

There is no `execute`/shell tool in the filesystem middleware — all command
execution flows through the four bash tools using `HTTPSandbox` and the sandbox kernel,
which runs in persistent tmux sessions and logs to `.sessions/<name>.log`.

**The `${WORKSPACE}` anchor (hard rule).** Operational agents'
first bash action sets and exports the workspace root:

```bash
WORKSPACE="$(pwd)"
export WORKSPACE
```

All subsequent artifact writes use `"${WORKSPACE}/recon/..."`,
`"${WORKSPACE}/findings/..."`, never bare relative paths. This prevents path
drift when sub-shells, background jobs, or tool wrappers change the working
directory mid-task. Agents must not assume `pwd` equals the engagement root
after any `cd`.

**The orchestrator (decepticon) has no bash tool at all.** It only has
versioned OPPLAN tools (`commit_opplan`, `update_objective`, `get_objective`,
`list_objectives`, plan-fact operations) plus the workspace file tools (`read_file`, `write_file`,
`ls`) for reading sub-agent artifacts. All offensive work is delegated via
`task(subagent, prompt)`. The filesystem tools on the orchestrator are for
workspace artifacts only — `grep`/`glob`/`ls`/`read_file` against a remote
URL or domain is forbidden; remote recon goes through `task("recon", ...)`.

---

## 4. Per-phase artifact contracts

Specialists write the artifacts required by their assigned objective and pinned
workflow. Common locations include `recon/`, `exploit/`, `post-exploit/`,
`findings/` and `report/`, but these names do not require executing every phase.
Read the active capability's technical templates for its local evidence format.
A universal file cap or an old prompt-line inventory must not override the live
acceptance contract.

## 5. Terminal-state handoff artifacts

A specialist returns a bounded outcome and the paths of actual evidence. Preserve
blockers, negative results and untested coverage. Historical terminal tokens such
as `RECON_BLOCKED` are method-specific conventions, not a second OPPLAN status
machine. The orchestrator must inspect the artifacts and live task schema rather
than infer completion from a token or filename alone.

## 6. The orchestrator read protocol

Inspect the current objective, dependencies, scope and relevant evidence before
dispatch or mutation. Preserve the pinned workflow across child execution and
resume. Read returned evidence before accepting the result; do not equate a tool
failure or empty return with a negative security finding.

OPPLAN status and typed outcome are separate. Completed work may establish a
finding, no finding or an objective met; each requires real evidence. Blocked
work records its reason. Use the live OPPLAN tools and revision checks described
in [the DAG contract](red-team/opplan-dag.md), not historical `passed`/`failed`
status examples. Engagement closure follows the selected workflow and retains
limitations for unfinished coverage.

---

## 7. Cross-agent shared conventions

These conventions are shared by all phase agents, injected through shared
skills and the prompt builder.

- **Finding protocol** — `/skills/shared/finding-protocol/SKILL.md` defines
  the operational `FIND-{NNN}.md` template: YAML frontmatter (`id`,
  `severity`, `title`, `agent`, `objective_id`, `discovered_at`,
  `evidence_pointer`) plus `## Description`, `## Evidence`, `## Next`
  sections. One vulnerability per file; the next ID is `ls findings/*.md |
  wc -l + 1`. The `FIND-NNN` namespace is **shared across all phases** —
  recon, exploit, and post-exploit all draw from one counter.
- **Timeline** — a single append-only `timeline.jsonl` records real activity
  and finding events for the whole engagement; never initialized as a
  placeholder.
- **Inventory linking** — every `SHELL-NNN` in `shells.json` and every
  `CRED-NNN` in `creds/initial.json` must carry a `finding_id` pointing at
  the `FIND-NNN.md` that explains it.
- **Cross-cutting prompt fragments** — the prompt builder
  (`agents/prompts/builder.py`) injects shared sections into operational
  agents: faithful reporting (report observations exactly), a verification
  gate (verify CRITICAL/HIGH findings a second way), a finding-protocol
  pointer, output discipline (1–2 sentence inter-tool text, full structure
  only in the final artifact), and an analyst mindset. The orchestrator gets
  a subset (no verification gate). Static sections sit before a prompt-cache
  boundary; dynamic sections (date, skills metadata, engagement context)
  after it.

### Scoped sub-agent dispatch (`task_spec.py`)

Delegation is a deterministic, scoped handoff — the child receives no parent
transcript. `SubAgentTaskSpec` carries: `objective`, `scope` (RoE limits),
`inputs` (tool-arg key/values the parent gathered), `expected_outputs`
(artifact names expected back), `parent_artifacts` (workspace paths the
child may *read* — never inlined content), `engagement_id`, and
`parent_agent`. `render()` sorts dict keys so identical inputs produce
identical text, enabling prompt caching for the child run.

---

## 8. Planning documents (Soundwave)

Plan is optional. An operator provisions `plan/roe.json`; Soundwave reads that
scope and can publish the seven additional planning documents. Publication uses
the canonical Pydantic schemas and leaves the operator in Plan. It does not grant
execution approval or generate OPPLAN.

| File | Owner |
|------|-------|
| `plan/roe.json` | Authorized operator; read-only to Soundwave |
| `plan/threat-profile.json`, `plan/conops.json`, `plan/deconfliction.json` | Optional Soundwave planning workflow |
| `plan/contact.json`, `plan/data-handling.json`, `plan/abort.json`, `plan/cleanup.json` | Optional Soundwave planning workflow |
| `plan/opplan.json` | Execution orchestrator through OPPLAN tools |

See [artifact ownership](agent-file-mapping.md), [optional Plan](design/planning-workflow.md)
and [OPPLAN DAG](red-team/opplan-dag.md). The live objective schema and selected
workflow determine acceptance artifacts and dependencies; phases do not impose a
mandatory sequence.

---

## 9. Final report generation

The selected workflow defines reporting dependencies and required deliverables.
Independent verification precedes a per-finding report. Preserve the operational
`FIND-NNN` identity in deliverable frontmatter; current reporting templates use
human-readable severity/slug filenames rather than the retired
`report/finding-NNN.md` scheme.

The orchestrator synthesizes coverage, verification summaries and engagement
reports from inspected evidence. Reusable final-report skills supply technical
models and formatting, not an unconditional mission sequence. With no candidates,
report explicit no-findings coverage and limitations. Cleanup is governed by the
approved scope and selected workflow; an optional CONOPS is not a prerequisite
for every report.

---

## 10. The knowledge graph as a parallel channel

Alongside flat files, agents share structured state through a Neo4j-backed
knowledge graph (`KGMiddleware` builds `KGStore` + the `kg_*` tools). The KG
is the one intentionally shared service across both Docker networks, scoped
per engagement via a `set_active_engagement(label)` contextvar so all writes
carry the engagement label.

- Analyst writes `Host / Service / Vulnerability / Finding / Credential /
  Secret / Entrypoint / CrownJewel / Hypothesis / AttackPath` nodes via
  `kg_record` / `kg_ingest`.
- The chain planner queries the KG for attack paths.
- The optional legacy `blue_cell_scan` replay tool can record detection nodes
  using AutoBot rule/tap modules. This is separate from the current AutoBot Blue
  observation graph; selecting Blue does not itself populate detection edges.

Flat files are the human-facing, grep-friendly handoff channel; the KG is
the queryable, relationship-aware channel. Findings exist in both: a
`FIND-NNN.md` on disk and a `Finding` node in the graph.

---

## 11. Benchmark / CTF mode differences

When `benchmark/SKILL.md` is active, the deliverable is a flag string, and
two generic rules are intentionally suspended:

- **No planning bundle** — no `roe`/`conops`/`deconfliction` required (the
  flag is the deliverable).
- **No final report** — no executive summary; the flag string *is* the
  report.

On a verified flag/flag-equivalent credential, the orchestrator marks
remaining applicable objectives using the live OPPLAN lifecycle and its **very next response must re-echo the
flag verbatim** (e.g. `Flag captured: FLAG{...}`) — the harness scans only
the orchestrator's final message. CTF flag-path conventions
(`/tmp/flag_sweep.txt` from a single batched filesystem sweep) live **only**
in `/skills/benchmark/` — the generic exploit skills under
`/skills/standard/exploit/` deliberately exclude flag conventions so the
core capability stays benchmark-agnostic.

---

## 12. Client-side surfacing

The host-mounted workspace tree is what the clients render.

- **CLI** (`clients/cli`): no dedicated file browser; sub-agent tool calls
  and results stream through `StreamingRunnable` (LangGraph SDK), so findings
  appear inline in the event flow as they are written.

---

## 13. Lifecycle and checkpointing

- **Lazy directories** — every phase directory is created on first write,
  never scaffolded ahead of time.
- **Survival across summarization** — agents write SUMMARY/credentials/probe
  logs to disk *before* anything else precisely so the next cold-context
  cycle can resume from files rather than memory.
- **Shutdown helper** — `runtime/shutdown.py` provides bounded best-effort
  flushing for callers that explicitly install its handler/state provider. It
  is not wired into the current server startup; do not assume every SIGINT or
  SIGTERM produces a partial report or flushes in-flight evidence.
- **Orphan reaping** — on daemon startup, decepticon-named tmux sockets from
  a crashed prior run are reaped before new sessions start.

---

## 14. Writing a plugin agent that fits the contract

A community/downstream plugin agent (registered under the `decepticon.subagents`
entry-point group, declaring `parent_agents=("decepticon",)`) inherits the
file contract for free if it:

1. **Mounts `FilesystemMiddleware`** so it gets the scoped `/workspace`
   tools — never write outside `/workspace`.
2. **Anchors `${WORKSPACE}`** in its first bash call and uses absolute
   workspace paths for every artifact.
3. **Writes findings via the finding protocol** — `load_skill(
   "/skills/shared/finding-protocol/SKILL.md")`, one `findings/FIND-NNN.md`
   per discovery, evidence under `findings/evidence/`, drawing from the
   shared `FIND-NNN` counter.
4. **Returns a terminal handoff artifact** — define the equivalent of
   recon's `SUMMARY.md` token or exploit's terminal files so the orchestrator
   can detect completion state by grep, and document it in the agent's
   prompt. Returning with no exit artifact reads as a crash.
5. **Keeps deliverables Markdown, operational data JSON**, and observes an
   output-file cap to avoid context bloat.

Because dispatch is file-mediated and context is fresh per objective, a
plugin agent that honors these five points is indistinguishable from a
built-in specialist to the orchestrator — no changes to the OSS core are
needed.

---

### See also

- [`docs/engagement-workflow.md`](engagement-workflow.md) — the phase
  walkthrough and the `## Outputs` workspace summary.
- [`docs/agents.md`](agents.md) — agent roster, middleware stacks, and the
  fresh-context model.
- [`docs/architecture.md`](architecture.md) — network topology, container
  boundaries, and the single-objective data-flow diagram.
- [`docs/skill-schema.md`](skill-schema.md) — `SKILL.md` frontmatter and
  validation rules.
