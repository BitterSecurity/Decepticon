[![English](https://img.shields.io/badge/Language-English-blue?style=for-the-badge)](README.md)
[![한국어](https://img.shields.io/badge/Language-한국어-red?style=for-the-badge)](README_KO.md)

<div align="center">
  <img src="assets/logo_banner.png" alt="Decepticon Logo">
</div>

<h1 align="center">Decepticon — Autonomous Red Team Agent</h1>

<p align="center"><i>"Another AI hacker? Let us guess — it runs nmap and writes a report."</i></p>

<div align="center">

<a href="https://trendshift.io/repositories/15987?utm_source=repository-badge&amp;utm_medium=badge&amp;utm_campaign=badge-repository-15987" target="_blank" rel="noopener noreferrer"><img src="https://trendshift.io/api/badge/repositories/15987" alt="BitterSecurity%2FDecepticon | Trendshift" width="250" height="55"/></a>
<a href="https://trendshift.io/repositories/15987?utm_source=trendshift-badge&amp;utm_medium=badge&amp;utm_campaign=badge-trendshift-15987" target="_blank" rel="noopener noreferrer"><img src="https://trendshift.io/api/badge/trendshift/repositories/15987/daily?language=Python" alt="BitterSecurity%2FDecepticon | Trendshift" width="250" height="55"/></a>

<br/>

<a href="https://github.com/BitterSecurity/Decepticon/blob/main/LICENSE">
  <img src="https://img.shields.io/github/license/BitterSecurity/Decepticon?style=for-the-badge&color=blue" alt="License: Apache 2.0">
</a>
<a href="https://github.com/BitterSecurity/Decepticon/stargazers">
  <img src="https://img.shields.io/github/stars/BitterSecurity/Decepticon?style=for-the-badge&color=yellow" alt="Stargazers">
</a>
<a href="https://github.com/BitterSecurity/Decepticon/graphs/contributors">
  <img src="https://img.shields.io/github/contributors/BitterSecurity/Decepticon?style=for-the-badge&color=orange" alt="Contributors">
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
  <p><strong>Commercial product demo</strong></p>
  <video src="https://github.com/user-attachments/assets/7a9b233c-fd72-4633-a408-03953c345a15" width="800" controls></video>
</div>


<div align="center">

### ☁️ Don't want to self-host? **Decepticon is live in the cloud.**

Skip the Docker setup — run autonomous red-team engagements right from your browser.

<a href="https://app.decepticon.red">
  <img src="https://img.shields.io/badge/Launch%20the%20Live%20App-app.decepticon.red-FF2D55?logo=rocket&logoColor=white&style=for-the-badge" alt="Launch the live app at app.decepticon.red">
</a>

</div>

<div align="center">
  <p><strong>Open-source CLI demo</strong></p>
  <video src="https://github.com/user-attachments/assets/b3fd40d8-e859-4a39-97f4-bd825694ad96" width="800" controls></video>
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

The default start brings up the core management plane (LiteLLM, PostgreSQL, Neo4j, Skillogy, LangGraph, sandbox) and launches the terminal CLI. Specialist workloads (BloodHound CE, Sliver C2, REA/Ghidra, …) and the web dashboard come up on demand — the orchestrator spawns specialists via `ops_start("ad")` etc., and you bring up the dashboard from inside the CLI with `/web` (see [Web Dashboard](docs/web-dashboard.md)).

**Windows (PowerShell, native)**
```powershell
irm https://decepticon.red/install.ps1 | iex
decepticon onboard
decepticon
```

→ **[Quick start](docs/getting-started.md)** · **[Full setup walkthrough](docs/setup-guide.md)**

### Inside the interactive CLI

Type `/` to browse commands, select with Up/Down, then press Enter. Tab completes
the selected command; aliases also appear in help and autocomplete.

| Command | Use |
|---------|-----|
| `/help`, `/shortcuts` | Discover commands or keyboard shortcuts |
| `/model`, `/status` | Choose a configured model or inspect session connections |
| `/plan` | Enter optional Plan mode, then send your planning message |
| `/mode red`, `/mode blue` | Return to Red or select Blue investigation |
| `/new` | Start a fresh conversation in the same engagement and mode when idle |
| `/sessions`, `/resume` | Browse saved conversations or continue paused work |
| `/version` (`/about`), `/pwd` (`/cwd`) | Check the running version and workspace paths |
| `/quit`, `/exit` | Leave the chat; services and server work keep running |

Enter sends; Shift+Enter, Alt+Enter or Ctrl+J adds a line. Up/Down recalls input
history at the first/last input row; Ctrl+R searches it. Esc closes focused UI
first, then pauses active work; `/resume` continues it. Ctrl+A moves to the start
of the current line. Use `/queue` to review waiting messages before `/new`.

`/new` preserves saved conversations and terminal scrollback; it does not create
another engagement or change target authorization. `/plan` and `/new` take no
arguments. In the web terminal, use the dashboard's mode controls.

The new shortcuts and slash conveniences above describe current `main`, including
unreleased changes. A source merge does not update installed images. See
[version history](CHAGELOG.md), the [complete CLI reference](docs/cli-reference.md#slash-commands)
and [upgrade behavior](docs/cli-experience.md#upgrade-behavior).

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
| [XBOW validation-benchmarks](https://github.com/BitterSecurity/xbow-validation-benchmarks) | Easy (Level 1)   | **45 / 45** (100 %) |
| [XBOW validation-benchmarks](https://github.com/BitterSecurity/xbow-validation-benchmarks) | Medium (Level 2) | **50 / 51** (98.0 %) |
| [XBOW validation-benchmarks](https://github.com/BitterSecurity/xbow-validation-benchmarks) | Hard (Level 3)   | **7 / 8** (87.5 %) |
| [XBOW validation-benchmarks](https://github.com/BitterSecurity/xbow-validation-benchmarks) | **All levels**   | **102 / 104** (98.08 %) |

- **[Full per-challenge index, attack-class matrix, and LangSmith traces](benchmark/results/README.md)**
- **[Comparison vs other AI pentest agents (Strix, PentestGPT, MAPTA, Cyber-AutoAgent, XBOW commercial, …)](docs/benchmark-comparison.md)**

---

## What is Decepticon?

The "AI + hacking" space is full of demos that run nmap and print a report. That's not what this is.

**Decepticon is a professional autonomous Red Team agent.** It executes realistic attack chains — reconnaissance, exploitation, privilege escalation, lateral movement, C2 — the way a real adversary would, not the way a scanner does.

Every Red engagement starts with an operator-approved target and RoE. Optional Plan mode adds Soundwave's discussion and seven planning documents alongside the read-only RoE. The execution orchestrator owns OPPLAN; Plan is not required for Red.

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

Two-network design. The **always-on** management plane (LiteLLM, PostgreSQL, Skillogy, LangGraph) and the always-on sandbox plane stay up across the whole engagement; everything else is **dynamic-spawn** — the Web dashboard comes up on `/web` from the CLI, and specialist workloads (BloodHound CE, Sliver C2, REA/Ghidra, …) come up only when the orchestrator calls `ops_start(...)` (see [ADR-0006](docs/adr/0006-agent-driven-container-lifecycle.md)). Networks: management on `decepticon-net`; sandbox + C2 server + targets on `sandbox-net`. Neo4j is dual-homed so the agent (on management) can persist findings written from inside the sandbox.

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
| REA/Ghidra reverse engineering | [REA Reversing](docs/features/rea-reversing.md) |
| Local defensive monitoring | [Blue Cell](docs/features/blue-cell.md) |
| Model profiles and fallback chain | [Models](docs/models.md) |
| Skill system and format spec | [Skills](docs/skills.md) |
| Web dashboard features and setup | [Web Dashboard](docs/web-dashboard.md) |
| Extending capabilities, skills, workflows and clients | [Extension contract](docs/design/extension-contract.md) |
| System architecture and network isolation | [Architecture](docs/architecture.md) |
| Neo4j knowledge graph | [Knowledge Graph](docs/knowledge-graph.md) |
| End-to-end engagement workflow | [Engagement Workflow](docs/engagement-workflow.md) |
| Offensive Vaccine loop | [Offensive Vaccine](docs/offensive-vaccine.md) |
| Release history | [Changelog](CHAGELOG.md) |
| Contributing to Decepticon | [Contributing](docs/contributing.md) |

---

## Contributing

```bash
git clone https://github.com/BitterSecurity/Decepticon.git
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

## Acknowledgements

Thanks to the maintainers and contributors of the projects Decepticon builds on,
integrates, and learns from.

| Project | Role in Decepticon |
|---------|--------------------|
| [REA](https://github.com/morluto/rea) and [Ghidra](https://github.com/NationalSecurityAgency/ghidra) | Optional reverse engineering workload and evidence-backed binary analysis. [Integration details](docs/features/rea-reversing.md). |
| [Deep Agents](https://github.com/langchain-ai/deepagents) and [LangGraph](https://github.com/langchain-ai/langgraph) | Agent harness, orchestration, streaming and persistent execution state. |
| [LiteLLM](https://github.com/BerriAI/litellm) | Model gateway and provider routing. |
| [insane-search](https://github.com/fivetaku/insane-search) | Upstream engine adapted for sandbox web access. [Adaptation notice](packages/decepticon/decepticon/sandbox_web/NOTICE.md). |
| [Playwright](https://github.com/microsoft/playwright) | Browser automation for web investigation. |
| [Caddy](https://github.com/caddyserver/caddy) and [Fluent Bit](https://github.com/fluent/fluent-bit) | Local Blue sensor ingress and log collection. [Blue Cell architecture](docs/features/blue-cell.md). |
| [Ink](https://github.com/vadimdemedes/ink), [Bubble Tea](https://github.com/charmbracelet/bubbletea) and [Huh](https://github.com/charmbracelet/huh) | Interactive CLI rendering and launcher forms. |
| [PayloadsAllTheThings](https://github.com/swisskyrepo/PayloadsAllTheThings), [h4cker](https://github.com/The-Art-of-Hacking/h4cker) and [Awesome-Hacking](https://github.com/Hack-with-Github/Awesome-Hacking) | Security knowledge and learning references. [Attribution details](THIRD_PARTY_LICENSES.md). |
| [XBOW validation benchmarks](https://github.com/XBOW-engineering/validation-benchmarks) | Evaluation tasks used by the benchmark integration. [Results and methodology](benchmark/results/README.md). |

For CLI interaction design, we also studied
[Claude Code](https://code.claude.com/docs/en/interactive-mode),
[Codex CLI](https://github.com/openai/codex) and
[Gemini CLI](https://github.com/google-gemini/gemini-cli).
The [CLI reference review](docs/cli-experience.md) records the resulting choices.

Upstream licenses and attribution notices remain applicable; see
[third-party licenses](THIRD_PARTY_LICENSES.md) and the notices alongside adapted code.
