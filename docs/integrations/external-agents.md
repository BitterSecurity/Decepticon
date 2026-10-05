# External agents — Claude Code, Codex, OpenClaw & Hermes

Decepticon ships an **engagement MCP server** so external agent runtimes can
drive it like its CLI — from a phone, via those agents' chat channels. It
exposes the full interactive loop over the Model Context Protocol: discover and
resume engagements, launch one, **steer it by chatting**, **watch progress**,
inspect the OPPLAN, and pull findings.

This makes Decepticon usable from Claude Code, Codex,
[OpenClaw](https://github.com/openclaw/openclaw), and
[Hermes](https://github.com/NousResearch/hermes-agent).

```
Coding agent  ──MCP stdio──▶  decepticon mcp serve  ──container exec──▶  Decepticon server
```

The bridge is a thin control plane. The red-team work runs inside the
Decepticon LangGraph server (full RoE enforcement, sandbox, knowledge-graph
persistence); the MCP layer translates tool calls into LangGraph runs
(`decepticon.mcp_server`) and reads persisted transcript/state/findings back.

## Tools

| Tool | Purpose |
|------|---------|
| `decepticon_list_graphs` | Discover engagement graphs (decepticon, recon, soundwave, …) |
| `decepticon_list_engagements` | Browse / resume recent engagements |
| `decepticon_start_engagement` | Launch a background engagement (targets + scope/RoE) |
| `decepticon_send_message` | Steer / answer the coordinator / `/model` switch mid-run |
| `decepticon_transcript` | Read the orchestrator narrative incrementally (watch) |
| `decepticon_watch` | Tail the live sub-agent stream for a few seconds |
| `decepticon_engagement_state` | Inspect OPPLAN / objectives / scope / phase |
| `decepticon_engagement_status` | Latest run status + whether findings exist |
| `decepticon_engagement_findings` | Findings summary / full SARIF v2.1.0 |
| `decepticon_cancel_engagement` | Stop the active run |

Every tool is keyed by the `thread_id` returned from `decepticon_start_engagement`
(or listed by `decepticon_list_engagements`) — no run-id juggling, just like the CLI.

## 1. Claude Code and Codex with the installed CLI

The installed `decepticon` launcher can expose MCP tools through the running
LangGraph container. This uses the same LangGraph instance as the interactive
CLI, with no separate Python installation on the host. Start the stack with
`decepticon start` first. `decepticon mcp serve` is a stdio server
for coding agents; do not run it in an interactive terminal expecting a prompt.

Register the launcher with either coding agent:

```bash
# Claude Code: user scope makes it available across projects.
claude mcp add --scope user decepticon -- decepticon mcp serve

# Codex: MCP server configuration is shared by the CLI and IDE extension.
codex mcp add decepticon -- decepticon mcp serve
```

The command after `--` must resolve to the installed launcher. If `decepticon`
is not on the coding agent's `PATH`, substitute its absolute path. Check the
registration with `claude mcp get decepticon` or `codex mcp get decepticon`,
then ask the agent to list Decepticon graphs before starting an engagement.
The launcher keeps MCP stdout reserved for JSON-RPC and sends runtime errors
to stderr. A missing install or stopped stack produces an MCP startup error;
run `decepticon onboard` or `decepticon start`, respectively.

The optional [Decepticon Agent Skill](../../integrations/agent-skills/decepticon/SKILL.md)
teaches either coding agent the engagement loop and the required scope check.
From a source checkout, install the whole skill directory:

```bash
mkdir -p ~/.claude/skills ~/.agents/skills
cp -R integrations/agent-skills/decepticon ~/.claude/skills/
cp -R integrations/agent-skills/decepticon ~/.agents/skills/
```

Restart the coding agent after registering MCP or installing the skill. A
plugin is not required for this integration: the same MCP server and Agent
Skill are usable independently in Claude Code and Codex.

## 2. Python package or source checkout

```bash
# Install Decepticon with the MCP server extra
pip install 'decepticon[mcp]'        # or: uv sync --extra mcp

# Start the Decepticon LangGraph server (one of):
langgraph dev                        # dev server on http://localhost:2024
# or the Docker stack — see docs/deployment

# Smoke-test the bridge over stdio (Ctrl-C to exit):
DECEPTICON_SKIP_BOOT=1 decepticon-mcp --transport stdio
```

> **`DECEPTICON_SKIP_BOOT=1`** — always set this for the bridge. It drives a
> *separate* LangGraph server and never builds agents in-process, so the eager
> framework boot is pure cold-start overhead. With it, the server starts in a
> few seconds instead of ~30s; the wiring below sets it for you. It does **not**
> weaken Rules-of-Engagement: RoE is enforced inside the LangGraph server the
> bridge talks to, not in this process, so skipping the in-process boot leaves
> scope enforcement untouched.

The bridge connects to `DECEPTICON_API_URL` (default `http://localhost:2024`).
Override with `--langgraph-url` or the env var.

## 3. OpenClaw

```bash
# Register the engagement MCP server (stdio)
openclaw mcp set decepticon '{
  "command": "decepticon-mcp",
  "args": ["--transport", "stdio"],
  "env": { "DECEPTICON_API_URL": "http://localhost:2024", "DECEPTICON_SKIP_BOOT": "1" }
}'

# Install the agent skill (clone the repo first, then point at the skill dir)
openclaw skills install ./Decepticon/integrations/agent-skills/decepticon --as decepticon --global
openclaw gateway restart
```

Now message your OpenClaw agent (dashboard or any connected channel, e.g.
Telegram for phone): *"Start a Decepticon recon engagement against
`https://test.example.com` — scope only that host — then watch it and summarise
findings."* The agent calls `decepticon_start_engagement`, polls
`decepticon_transcript`, and reports findings.

## 4. Hermes

```yaml
# ~/.hermes/config.yaml
mcp_servers:
  decepticon:
    command: decepticon-mcp
    args: ["--transport", "stdio"]
    env:
      DECEPTICON_API_URL: "http://localhost:2024"
      DECEPTICON_SKIP_BOOT: "1"
```

```bash
# Install the skill for Hermes (copy the skill folder into Hermes' skills dir)
cp -r ./Decepticon/integrations/agent-skills/decepticon ~/.hermes/skills/red-teaming/decepticon
```

Restart Hermes; the `decepticon` skill and `decepticon_*` tools become available.

### The bundled skill

The skill at `integrations/agent-skills/decepticon/` is one canonical AgentSkill
that loads in **both** OpenClaw and Hermes (same `SKILL.md` format), using
progressive disclosure so the always-loaded context stays lean:

- `SKILL.md` — the playbook (mental model, authorization, the core loop,
  polling cadence, result interpretation, error recovery).
- `reference.md` — exact params, defaults, clamps, and return schemas per tool.
- `examples.md` — worked end-to-end tool-call sequences (bug bounty from a
  phone, recon-only, steering, resume, live burst, failure handling).

Both install commands above copy the whole directory, so the reference files
come along automatically. OpenClaw installs it globally as `decepticon`; Hermes
auto-discovers it under the `red-teaming` category.

## 5. CLI-like workflow (what the agent does)

1. `decepticon_start_engagement(targets=[...], instruction="In scope: …; Out of scope: …")`
   → keep the `thread_id`.
2. `decepticon_transcript(thread_id, after_index=…)` — poll to narrate progress
   (operator prompts, coordinator replies, `task()` delegations to specialists).
   `decepticon_watch(thread_id)` tails the live sub-agent feed for a few seconds.
3. `decepticon_send_message(thread_id, "focus on the API, skip the marketing site")`
   — steer mid-engagement, answer the coordinator, or `/model anthropic/claude-opus-4-8`.
4. `decepticon_engagement_state(thread_id)` — check the OPPLAN / phase.
5. `decepticon_engagement_findings(engagement_name, include_sarif=true)` — pull results.
6. Later, `decepticon_list_engagements()` to resume any thread by `thread_id`.

## 6. Remote / networked use (optional)

The bridge can launch authorized engagements, so the `streamable-http`
transport **refuses to bind a non-loopback `--host` without authentication** —
a bare `--host 0.0.0.0` exits with an error unless one of the modes below is
configured. Auth is enforced by the MCP SDK's bearer backend (a missing or
invalid `Authorization: Bearer` token gets a 401 before any tool runs).

**Shared-secret (OSS / single operator).** The token is read only from the
environment, never from argv:

```bash
DECEPTICON_MCP_TOKEN=$(openssl rand -hex 32) \
DECEPTICON_SKIP_BOOT=1 decepticon-mcp --transport streamable-http \
  --host 0.0.0.0 --port 8765 --langgraph-url http://decepticon-host:2024
```

Clients send `Authorization: Bearer <token>`.

**JWT (OAuth 2.1 resource server — multi-tenant / shared deployments).** Validates the
bearer JWT's signature, `iss`, and `aud` against your identity provider's JWKS
(or a static public key via `--auth-public-key`). This is the posture the MCP
June-2025 spec prescribes for remote servers:

```bash
DECEPTICON_SKIP_BOOT=1 decepticon-mcp --transport streamable-http \
  --host 0.0.0.0 --port 8765 --langgraph-url http://decepticon-host:2024 \
  --issuer https://issuer.example.com --audience decepticon-mcp \
  --jwks-uri https://issuer.example.com/.well-known/jwks.json \
  --required-scope engage
```

Point the agent's MCP client at `http://<bridge-host>:8765/mcp`. Loopback
(`--host 127.0.0.1`) stays auth-free for local stdio-equivalent use.

## Authorization

Engagements run under Decepticon's Rules-of-Engagement enforcement. The calling
agent **must** pass scope (in / out of scope) in the `instruction` argument and
only target assets the operator is authorized to test. See the bundled
`integrations/agent-skills/decepticon/SKILL.md` for the agent-facing contract.
