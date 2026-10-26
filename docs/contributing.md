# Contributing

For agents, skills, business workflows and client adapters, start with the
[current extension contract](design/extension-contract.md).

For version selection, local product gates, publication, update channels and
recovery, follow the [OSS release policy](../RELEASE.md). Every PR, including
docs/maintenance, updates the single root [CHANGELOG.md](../CHANGELOG.md),
preserving full history, before opening or updating the PR.

Contributions are welcome — whether you're a security researcher, AI engineer, or someone who cares about making defense better through offense.

---

## Development Setup

**Prerequisites**: Docker, Docker Compose v2, and [uv](https://docs.astral.sh/uv/) (for Python tooling locally).

```bash
git clone https://github.com/BitterSecurity/Decepticon.git
cd Decepticon

# Copy and configure environment
cp .env.example .env
# Edit .env — set at least one provider key, or set OLLAMA_API_BASE + OLLAMA_MODEL for local Ollama

# Start services with hot-reload (daily dev loop)
make dev

# Or run the full OSS UX (launcher → onboard → CLI) on local code
make dogfood
```

`make dev` uses `docker compose watch` — source changes sync into containers automatically without rebuilding. `make dogfood` is the release-shape verification path; see [makefile-reference.md](makefile-reference.md) for the full target list.

---

## Project Structure

```
packages/decepticon/decepticon/  # Framework Python package (LangGraph agents, middleware, tools)
├── agents/          # Agent factory functions (create_*_agent)
├── core/            # Config, engagement document schemas, logging, streaming helpers
├── llm/             # Model profiles, LiteLLM configuration
├── middleware/       # Skills, filesystem, OPPLAN, safe command, fallback, etc.
└── tools/           # Bash, research (KG, CVE, chain planning), reporting

packages/decepticon/decepticon/skills/  # Reusable capability knowledge

clients/
├── cli/             # TypeScript/Ink terminal UI
├── web/             # Next.js 16 web dashboard
└── shared/          # Shared streaming utilities (@decepticon/streaming)

config/              # LiteLLM proxy config (litellm.yaml)
containers/          # Dockerfile per service
```

---

## Quality Gates

Before opening a PR, run the quality checks:

```bash
make quality         # Full gate: Python + CLI + Web (run before opening a PR)

make lint            # Python only: ruff check + ruff format --check + basedpyright
make lint-fix        # Auto-fix Python lint and formatting
make quality-cli     # CLI: typecheck + build + vitest
make web-lint        # Web dashboard ESLint

make test            # Python tests in Docker
make test-local      # Python tests locally (requires uv sync --dev)
```

Minimum Python version: **3.13**

---

## Adding an Agent

1. Create the agent module. Pick a bundle:
   - `packages/decepticon/decepticon/agents/standard/{name}.py` for OSS-blessed agents shipped by default
   - `packages/decepticon/decepticon/agents/plugins/{name}.py` to demonstrate the community-plugin shape
   The file exposes a `create_{name}_agent()` factory.
2. Follow the middleware stack pattern from an existing agent (e.g., `standard/recon.py`)
3. Define the agent's skill sources in the `SkillsMiddleware` configuration
4. **Subagents only**: add a module-level `SUBAGENT_SPEC = SubAgentSpec(...)`
   declaring `parent_agents=(...)`, `bundle=...`, and `priority=...`. Register
   it under `[project.entry-points."decepticon.subagents"]` in `packages/decepticon/pyproject.toml`.
   The relevant main agent picks it up automatically via
   `load_subagents_for_parent(...)`. See `packages/decepticon-core/decepticon_core/plugin_loader.py` for the
   contract.
5. Create a skills directory at `packages/decepticon/decepticon/skills/{bundle}/{name}/` mirroring the agent
   bundle (`standard/` or `plugins/`).

### Activating plugin bundles

Decepticon defaults to the lean `standard` bundle. To activate additional
bundles (e.g. the `plugins` bundle that ships `vulnresearch`), use the
4-tier hierarchy (highest precedence wins):

1. **`DECEPTICON_PLUGINS` env var** — runtime override:
   ```bash
   DECEPTICON_PLUGINS=standard,plugins langgraph dev   # or "*" for all
   ```
2. **`.decepticon.toml` in CWD** — per-checkout opt-in:
   ```toml
   [plugins]
   enabled = ["standard", "plugins"]
   ```
3. **`pyproject.toml` in CWD** — project-default opt-in:
   ```toml
   [tool.decepticon.plugins]
   enabled = ["standard", "plugins"]
   ```
4. **Hardcoded default** — `["standard"]`.

The OSS repo itself ships with both bundles enabled via the project-level
`pyproject.toml` (so `make dev` / `make benchmark` work out of the box).
End-user installs that just `pip install decepticon` get the lean
`standard`-only default (neo4j and other heavy features are opt-in extras,
e.g. `decepticon[neo4j]`). Downstream Docker images override via
`ENV DECEPTICON_PLUGINS=standard,vendor` to activate their own bundle.

The OSS-shipped `langgraph.json` matches the lean default — it only
lists the 10 `standard` graphs. To expose plugin graphs to LangGraph
Platform, emit the manifest dynamically:

```bash
LANGSERVE_GRAPHS="$(python -m decepticon.graph_registry)" langgraph dev
```

That CLI emits the merged manifest of every active bundle plus any
external `decepticon.agents` entry-points.

---

## Adding a Skill

1. Create a directory: `packages/decepticon/decepticon/skills/{category}/{skill-name}/`
2. Write `SKILL.md` following the [skill format](skills.md#skill-format)
3. Add `references/` for content over 100 lines
4. Add `scripts/` for automation the agent should execute
5. Verify role routing, rebuild/publish any shipped graph catalog, and check actual retrieval.

Source discovery uses the agent's configured paths; graph-backed deployments also
need catalog synchronization. See [Skills](skills.md).

---

## Testing

Python tests live in `packages/decepticon/tests/`. Run inside Docker for a clean environment:

```bash
make test            # pytest in container
make test-local      # pytest locally (requires: uv sync --dev)
```

CLI tests run via `make quality-cli` (typecheck + build + vitest), or directly:

```bash
npm run test --workspace=@decepticon/cli
```

When adding a new agent or tool, add corresponding tests in `packages/decepticon/tests/`.

---

## Pull Request Process

1. Fork the repository
2. Create a feature branch from `main`: `git checkout -b feat/your-feature`
3. Make changes — keep commits focused and descriptive
4. Run `make quality` and ensure all checks pass
5. Open a Pull Request against `main`
6. In the PR description, include:
   - What changed and why
   - How to test the change
   - Any relevant MITRE ATT&CK technique IDs (for new agent capabilities or skills)

---

## Areas Where Help Is Welcome

| Area | What's needed |
|------|--------------|
| **New skills** | More OSINT, cloud attack, and post-exploitation skill coverage |
| **C2 profiles** | Havoc framework support (`c2-havoc` profile) |
| **Web dashboard** | UX improvements, new views, mobile responsiveness |
| **Documentation** | Tutorials, walkthroughs, translated READMEs |
| **Bug reports** | Open an issue with reproduction steps |

---

## Community

Join the [Discord](https://discord.gg/TZUYsZgrRG) to ask questions, share engagement logs, discuss techniques, or connect with others working on the project.
