# Contributing to Decepticon

Thank you for your interest in contributing to Decepticon! Whether you're a security researcher, AI engineer, or documentation enthusiast, we welcome your contributions.

## First-Time Contributors

We use a vouch system. This exists because AI makes it trivial to generate plausible-looking but low-quality contributions, and we can no longer trust by default.

1. Open a [Vouch Request](https://github.com/BitterSecurity/Decepticon/discussions/new?category=vouch-request) discussion.
2. Describe what you want to change and why.
3. Write in your own words. AI-generated vouch requests will be denied.
4. A maintainer will comment `/vouch` if approved, and the request discussion
   will close automatically.
5. **Get vouched before opening a PR.** Once vouched, you can submit pull requests.

**If you are not vouched, any pull request you open will be automatically closed.** The PR and its discussion are preserved. Once vouched, open a new PR (preferred) or reopen the closed one. It cannot be held open or converted to a draft to bypass the check. Org members and collaborators with push access bypass this check. AI-assisted contributions are still welcome — see [Working with AI assistants](#working-with-ai-assistants) — but the vouch request itself must be written by you, the human.

## Getting Started

### Prerequisites

- Python 3.13+
- Docker & Docker Compose v2
- Node.js 22+ (for CLI client)
- [uv](https://docs.astral.sh/uv/) (Python package manager)

### Development Setup

```bash
# The benchmark suite uses git submodules (xbow-validation-benchmarks,
# MHBench). Clone with --recursive to fetch them; they are optional —
# the main tool builds and runs without them.
git clone --recursive https://github.com/BitterSecurity/Decepticon.git
cd Decepticon

# Start with hot-reload (builds Docker images + watches for source changes)
make dev

# In a separate terminal — open the interactive CLI
make cli-dev
```

### Running Tests & Linting

```bash
make test          # Run pytest inside container
make test-local    # Run pytest locally (requires: uv sync --dev)
make lint          # Lint + typecheck locally
make lint-fix      # Auto-fix lint issues
```

## How to Contribute

### Reporting Bugs

Use the [Bug Report](https://github.com/BitterSecurity/Decepticon/issues/new?template=bug_report.yml) issue template. Include:
- Steps to reproduce
- Expected vs actual behavior
- Docker and Python version info

### Suggesting Features

Use the [Feature Request](https://github.com/BitterSecurity/Decepticon/issues/new?template=feature_request.yml) issue template.

### Submitting Pull Requests

1. **Fork** the repository and create your branch from `main`.
2. **Write code** following the conventions below.
3. **Test** your changes — ensure `make lint` and `make test-local` pass.
4. **Commit** with clear, descriptive messages using [Conventional Commits](https://www.conventionalcommits.org/) format:
   - `feat(scope):` — new feature
   - `fix(scope):` — bug fix
   - `docs:` — documentation only
   - `chore:` — maintenance
   - `refactor:` — code restructuring
5. **Record every PR**, including docs/maintenance, under `[Unreleased]` in
   single root [CHAGELOG.md](CHAGELOG.md), preserving full history and including
   the PR link. Maintainers use the
   actual publication date when moving released changes to a version section.
6. **Open a PR** against `main` with a clear description of what and why.

## Code Conventions

- **Python**: Pydantic v2, Ruff for formatting/linting, basedpyright for type checking
- **Line length**: 100 characters
- **Imports**: Absolute imports, public API re-exported through `__init__.py`
- **Logging**: `from decepticon_core.utils.logging import get_logger; log = get_logger("module.sub")`
- **Runtime knowledge skills**: `packages/decepticon/decepticon/skills/**/SKILL.md`
- **External CLI wrapper skills**: see [external-agent integration](docs/integrations/external-agents.md)
- **CLI (TypeScript)**: Ink.js components in `clients/cli/src/`

## Project Structure

```
packages/            Python core, framework, and SDK packages
clients/launcher/    Go launcher (installed decepticon command)
clients/cli/         Ink.js terminal UI (TypeScript)
clients/shared/      Shared TypeScript streaming support
clients/web/         Optional Next.js dashboard
benchmark/           Benchmark harnesses and adapters
containers/          Dockerfiles
config/              Runtime configuration
scripts/             Installer and utilities
docs/                Current guides and historical records (see docs/README.md)
```

See [the documentation index](docs/README.md) for maintained entry points and
the distinction between runtime knowledge and external-agent CLI wrappers.

## Authoring Skills

Every `packages/decepticon/decepticon/skills/**/SKILL.md` file must
conform to the schema in [docs/skill-schema.md](docs/skill-schema.md).
Before opening a PR, run `make audit-skills` locally and fix any
reported violations. CI runs the same check on every PR.

For the historical cleanup of skills that pre-dated the schema, see
[archived Phase 0 process](docs/archive/skill-cleanup/process.md). New
authors writing skills against the schema do not need to read the
cleanup process — only the schema doc.

## Working with AI assistants

If any material part of your contribution was produced with an AI
coding agent (Claude, Codex, Copilot, Cursor, etc.), read
[CONTRIBUTING_AGENT.md](CONTRIBUTING_AGENT.md) before opening a PR. It
restates a few [docs/COWORK.md](docs/COWORK.md) rules in their
AI-contributor framing and adds a short self-review checklist. The
checklist is not enforced by CI; it is the bar a maintainer reviews
against.

## Architecture decisions

Non-obvious architectural decisions — middleware composition order,
sandbox transport mechanism, C2 framework selection, network-isolation
invariants — are recorded as numbered, append-only ADRs under
[docs/adr/](docs/adr/). See [docs/adr/README.md](docs/adr/README.md)
for when to write one and the format. If your PR depends on or reverses
an architectural decision, link the ADR from the PR description.

## Releases

Maintainers: see [RELEASE.md](RELEASE.md) for the versioning model and the release process.

## Security

If you discover a security vulnerability, please follow our [Security Policy](SECURITY.md) instead of opening a public issue.

## License

By contributing, you agree that your contributions will be licensed under the [Apache License 2.0](LICENSE).
