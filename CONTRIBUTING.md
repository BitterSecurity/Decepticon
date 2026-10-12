# Contributing to Decepticon

Thank you for your interest in contributing to Decepticon! Whether you're a security researcher, AI engineer, or documentation enthusiast, we welcome your contributions.

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
git clone --recursive https://github.com/PurpleAILAB/Decepticon.git
cd Decepticon

# Start with hot-reload (builds Docker images + watches for source changes)
make dev

# In a separate terminal — open the interactive CLI
make cli
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

Use the [Bug Report](https://github.com/PurpleAILAB/Decepticon/issues/new?template=bug_report.yml) issue template. Include:
- Steps to reproduce
- Expected vs actual behavior
- Docker and Python version info

### Suggesting Features

Use the [Feature Request](https://github.com/PurpleAILAB/Decepticon/issues/new?template=feature_request.yml) issue template.

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
5. **Record user-visible changes** under `[Unreleased]` in
   [CHANGELOG.md](CHANGELOG.md), with a link to the PR. Maintainers move these
   entries to a dated version when cutting the release. Internal-only changes
   can omit a changelog entry.
6. **Open a PR** against `main` with a clear description of what and why.

## Code Conventions

- **Python**: Pydantic v2, Ruff for formatting/linting, basedpyright for type checking
- **Line length**: 100 characters
- **Imports**: Absolute imports, public API re-exported through `__init__.py`
- **Logging**: `from decepticon.core.logging import get_logger; log = get_logger("module.sub")`
- **Skills**: Markdown files in `decepticon/skills/` with YAML frontmatter
- **CLI (TypeScript)**: Ink.js components in `clients/cli/src/`

## Project Structure

```
decepticon/          Python agents, core logic, backends
clients/cli/         Ink.js terminal UI (TypeScript)
decepticon/skills/   Markdown knowledge base for agents
containers/          Dockerfiles
config/              Runtime configuration
scripts/             Installer and utilities
docs/                Documentation
```

## Authoring Skills

Every `packages/decepticon/decepticon/skills/**/SKILL.md` file must
conform to the schema in [docs/skill-schema.md](docs/skill-schema.md).
Before opening a PR, run `make audit-skills` locally and fix any
reported violations. CI runs the same check on every PR.

For the cleanup of legacy skills that pre-date the schema, see
[docs/skill-cleanup-process.md](docs/skill-cleanup-process.md). New
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
