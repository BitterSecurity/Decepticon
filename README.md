[![English](https://img.shields.io/badge/Language-English-blue?style=for-the-badge)](README.md)
[![한국어](https://img.shields.io/badge/Language-한국어-red?style=for-the-badge)](README_KO.md)

<div align="center">
  <img src="assets/logo_banner.png" alt="Decepticon Logo">
</div>

<h1 align="center">Decepticon — Autonomous Red Team Agent</h1>

<p align="center"><i>"Another AI hacker? Let us guess — it runs nmap and writes a report."</i></p>

<div align="center">

<a href="https://github.com/PurpleAILAB/Decepticon/blob/main/LICENSE">
  <img src="https://img.shields.io/github/license/PurpleAILAB/Decepticon?style=for-the-badge&color=blue" alt="License: Apache 2.0">
</a>
<a href="https://github.com/PurpleAILAB/Decepticon/stargazers">
  <img src="https://img.shields.io/github/stars/PurpleAILAB/Decepticon?style=for-the-badge&color=yellow" alt="Stargazers">
</a>
<a href="https://github.com/PurpleAILAB/Decepticon/graphs/contributors">
  <img src="https://img.shields.io/github/contributors/PurpleAILAB/Decepticon?style=for-the-badge&color=orange" alt="Contributors">
</a>

<br/>

<a href="https://discord.gg/TZUYsZgrRG">
  <img src="https://img.shields.io/badge/Discord-Join%20Us-7289DA?logo=discord&logoColor=white&style=for-the-badge" alt="Join us on Discord">
</a>
<a href="https://decepticon.red">
  <img src="https://img.shields.io/badge/Website-decepticon.red-brightgreen?logo=vercel&logoColor=white&style=for-the-badge" alt="Website">
</a>
<a href="https://docs.decepticon.red">
  <img src="https://img.shields.io/badge/Docs-docs.decepticon.red-8B5CF6?logo=bookstack&logoColor=white&style=for-the-badge" alt="Documentation">
</a>
<a href="https://app.decepticon.red">
  <img src="https://img.shields.io/badge/Live%20App-app.decepticon.red-FF2D55?logo=rocket&logoColor=white&style=for-the-badge" alt="Live hosted app">
</a>

</div>

<br/>

<div align="center">
  <video src="https://github.com/user-attachments/assets/b3fd40d8-e859-4a39-97f4-bd825694ad96" width="800" controls></video>
</div>


<div align="center">

### ☁️ Don't want to self-host? **Decepticon is live in the cloud.**

Skip the Docker setup — run autonomous red-team engagements right from your browser.

<a href="https://app.decepticon.red">
  <img src="https://img.shields.io/badge/Launch%20the%20Live%20App-app.decepticon.red-FF2D55?logo=rocket&logoColor=white&style=for-the-badge" alt="Launch the live app at app.decepticon.red">
</a>

</div>

<div align="center">
  <video src="https://github.com/user-attachments/assets/2344d6f7-fa44-42bb-9492-1c203e56df3c" width="800" controls></video>
</div>

---

## Install

**Prerequisites**: [Docker](https://docs.docker.com/get-docker/) and Docker Compose v2.
Supported on macOS (Apple Silicon + Intel), Linux (amd64 + arm64), and Windows (amd64 + arm64) — native via PowerShell or via WSL2 (Ubuntu / Kali).

**macOS / Linux / WSL2**
```bash
curl -fsSL https://decepticon.red/install | bash
decepticon onboard   # Interactive setup wizard (provider, API key, model profile)
decepticon           # Start the core stack and drop into the terminal CLI
```

The default start brings up the core management plane (LiteLLM, PostgreSQL, Neo4j, Skillogy, LangGraph, sandbox) and launches the terminal CLI. Specialist workloads (BloodHound CE, Sliver C2, Ghidra MCP, …) and the web dashboard come up on demand — the orchestrator spawns specialists via `ops_start("ad")` etc., and you bring up the dashboard from inside the CLI with `/web` (see [Web Dashboard](docs/web-dashboard.md)).

**Windows (PowerShell, native)**
```powershell
irm https://decepticon.red/install.ps1 | iex
decepticon onboard
decepticon
```

→ **[Quick start](docs/getting-started.md)** · **[Full setup walkthrough](docs/setup-guide.md)**

### Use from Claude Code or Codex

After starting Decepticon, register its MCP server in your coding agent:

```bash
claude mcp add --scope user decepticon -- decepticon mcp serve
codex mcp add decepticon -- decepticon mcp serve
```

Use the command for the coding agent you have installed, then run
`decepticon skill install` to add the operator guide to both agents. See the
[external agent guide](docs/integrations/external-agents.md) for the Agent Skill,
tool workflow, and source installation path.

### Use as a library (pip)

Building on top of the agents — a product, a research integration, or a custom orchestrator? Install the SDK from PyPI:

```bash
pip install decepticon              # core SDK
pip install "decepticon[neo4j]"     # + the knowledge-graph attack-chain tools
```

`decepticon` is a **client SDK**: it ships the agent factories, middleware, tools, and skills, and routes LLM calls and sandbox execution to runtime services over HTTP (`DECEPTICON_LLM__PROXY_URL`, `SANDBOX_URL`). Running agents still needs those services — use the Docker stack above, or point the URLs at your own equivalents. See **[Decepticon as a library](docs/library-usage.md)** for the factory override surface, declarative `PluginBundle` plugins, and the safety gate.

---

## 💖 Support Decepticon

[![Sponsor](https://img.shields.io/badge/Sponsor-Decepticon-red?style=for-the-badge&logo=github)](https://github.com/sponsors/PurpleCHOIms)

We're building Decepticon toward an **Offensive Vaccine** for the AI-driven threat landscape. If you believe in autonomous red teaming as a path to stronger defense, consider supporting the project.

---

## Benchmark

<div align="center">
  <img src="assets/benchmark/decepticon_donut.png" alt="Decepticon — XBOW pass rate 102/104 (98.08%)" width="560">
</div>

| Benchmark | Difficulty | Pass Rate |
|-----------|------------|-----------|
| [XBOW validation-benchmarks](https://github.com/PurpleAILAB/xbow-validation-benchmarks) | Easy (Level 1)   | **45 / 45** (100 %) |
| [XBOW validation-benchmarks](https://github.com/PurpleAILAB/xbow-validation-benchmarks) | Medium (Level 2) | **50 / 51** (98.0 %) |
| [XBOW validation-benchmarks](https://github.com/PurpleAILAB/xbow-validation-benchmarks) | Hard (Level 3)   | **7 / 8** (87.5 %) |
| [XBOW validation-benchmarks](https://github.com/PurpleAILAB/xbow-validation-benchmarks) | **All levels**   | **102 / 104** (98.08 %) |

- **[Full per-challenge index, attack-class matrix, and LangSmith traces](benchmark/results/README.md)**
- **[Comparison vs other AI pentest agents (Strix, PentestGPT, MAPTA, Cyber-AutoAgent, XBOW commercial, …)](docs/benchmark-comparison.md)**

---

## What is Decepticon?

The "AI + hacking" space is full of demos that run nmap and print a report. That's not what this is.

**Decepticon is a professional autonomous Red Team agent.** It executes realistic attack chains — reconnaissance, exploitation, privilege escalation, lateral movement, C2 — the way a real adversary would, not the way a scanner does.

But more importantly: it operates under the discipline that separates red teamers from script kiddies. Before a single packet leaves the wire, Decepticon generates a complete engagement package — **RoE**, **ConOps**, **Deconfliction Plan**, and **OPPLAN** with MITRE ATT&CK mapping — and every action runs inside those defined rules.

→ **[Engagement workflow deep dive](docs/engagement-workflow.md)**

---

## Why Decepticon?

**Real kill chains, not checkbox scans.** Decepticon reads an OPPLAN and pursues objectives through whatever path opens up — pivoting, adapting, chaining techniques.

**Interactive shells, actually.** Real offensive tools are interactive (`msfconsole`, `sliver-client`, `evil-winrm`). Decepticon runs every command inside persistent tmux sessions with automatic prompt detection — so when a tool drops into an interactive prompt, the agent sends follow-up commands without workarounds.

**Hardened sandbox isolation.** All commands run inside a Kali Linux sandbox on a dedicated operational network (`sandbox-net`), separate from the management plane (`decepticon-net`). LangGraph drives the sandbox via the Docker socket. → **[Architecture](docs/architecture.md)**

**Offense serves defense.** The planned [Offensive Vaccine](docs/offensive-vaccine.md) loop will turn findings into defense improvements through an attack → defend → verify cycle.

---

## Architecture

<div align="center">
  <img src="assets/decepticon_infra.svg" alt="Decepticon Infrastructure" width="680">
</div>

Two-network design. The **always-on** management plane (LiteLLM, PostgreSQL, Skillogy, LangGraph) and the always-on sandbox plane stay up across the whole engagement; everything else is **dynamic-spawn** — the Web dashboard comes up on `/web` from the CLI, and specialist workloads (BloodHound CE, Sliver C2, Ghidra MCP, …) come up only when the orchestrator calls `ops_start(...)` (see [ADR-0006](docs/adr/0006-agent-driven-container-lifecycle.md)). Networks: management on `decepticon-net`; sandbox + C2 server + targets on `sandbox-net`. Neo4j is dual-homed so the agent (on management) can persist findings written from inside the sandbox.

→ **[Architecture deep dive](docs/architecture.md)** · **[Knowledge graph](docs/knowledge-graph.md)**

---

## Agents

16 specialist agents organized by kill chain phase, with a fresh context window per objective — no accumulated noise.

Orchestration · Reconnaissance · Exploitation · Post-Exploitation · Vulnerability Research · Domain Specialists (AD, Cloud, Smart Contracts, Reversing, Analyst).

→ **[Full agent roster and middleware stack](docs/agents.md)**

---

## Models & Providers

Tier-based, credentials-aware fallback chain. You declare which credentials you have in priority order; Decepticon builds the primary→fallback chain at every tier from there.

| Profile | Tier per agent | Use case |
|---------|----------------|----------|
| **eco** (default) | Per-agent (HIGH for orchestrator/exploiter/patcher/analyst, MID for execution, LOW for recon/soundwave) | Production |
| **max** | Every agent on HIGH | High-value targets |
| **test** | Every agent on LOW | Development / CI |

**Tier-mapped providers**: Anthropic, OpenAI, Google Gemini, MiniMax, DeepSeek, xAI, Mistral, OpenRouter, Nvidia NIM, Ollama (local).
**Subscription OAuth**: Claude Max/Pro/Team, ChatGPT Pro/Plus/Team, Gemini Advanced, Copilot Pro, SuperGrok, Perplexity Pro.

Configure via `decepticon onboard`. → **[Full model reference & fallback examples](docs/models.md)**

---

## Documentation

| Topic | Doc |
|-------|-----|
| Installation and first engagement | [Getting Started](docs/getting-started.md) |
| Complete setup, OAuth, providers, dashboard | [Setup Guide](docs/setup-guide.md) |
| All CLI commands and keyboard shortcuts | [CLI Reference](docs/cli-reference.md) |
| All `make` targets | [Makefile Reference](docs/makefile-reference.md) |
| Agent roster and middleware | [Agents](docs/agents.md) |
| Model profiles and fallback chain | [Models](docs/models.md) |
| Skill system and format spec | [Skills](docs/skills.md) |
| Web dashboard features and setup | [Web Dashboard](docs/web-dashboard.md) |
| System architecture and network isolation | [Architecture](docs/architecture.md) |
| Neo4j knowledge graph | [Knowledge Graph](docs/knowledge-graph.md) |
| End-to-end engagement workflow | [Engagement Workflow](docs/engagement-workflow.md) |
| Offensive Vaccine loop | [Offensive Vaccine](docs/offensive-vaccine.md) |
| Contributing to Decepticon | [Contributing](docs/contributing.md) |

---

## Contributing

```bash
git clone https://github.com/PurpleAILAB/Decepticon.git
cd Decepticon
make dogfood  # Full OSS UX (launcher → onboard → CLI) on local code
make dev      # Backend hot-reload (compose watch) — daily dev loop
```

→ **[Contributing guide](docs/contributing.md)**

---

## Community

Join the [Discord](https://discord.gg/TZUYsZgrRG) — ask questions, share engagement logs, discuss techniques.

---

## Disclaimer

Do not use this project on any system or network without explicit written authorization from the system owner. Unauthorized access to computer systems is illegal. You are solely responsible for your actions. The authors and contributors assume no liability for misuse.

---

## License

[Apache-2.0](LICENSE)

---

<div align="center">
  <img src="assets/main.png" alt="Decepticon">
</div>
