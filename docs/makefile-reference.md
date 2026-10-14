# Makefile Reference

The Makefile is for **local development and pre-release verification**. Every Docker target builds from your local checkout. End users install via `curl | bash` and run `decepticon` — they don't use `make`.

Run `make help` for a quick summary. Full reference below.

---

## Pre-release Verification

The primary purpose of this Makefile. Run these before tagging a release.

| Target | Description |
|--------|-------------|
| `make dogfood` | Full OSS UX on local code: builds the Go launcher + every service image (`:dev` tag), wires up an isolated `$DECEPTICON_HOME` under `.dogfood/`, then runs the launcher. The onboard wizard, engagement picker, `compose up`, health checks, and CLI all execute exactly as a `curl \| bash` install. |
| `make smoke` | Compose-only smoke (no launcher, no onboard wizard) — fastest possible release-shape check. Replicates only the launcher's `compose up` step: clean → build images locally → `up -d --no-build --wait` → health checks. Use when you only changed the compose stack. |
| `make launcher` | Build the Go launcher binary at `clients/launcher/bin/decepticon`. Embedded version is `dev`, which skips release update notices and versioned config sync inside the launcher. Invoked automatically by `make dogfood`. |

`make dogfood` runs against an isolated `.dogfood/` directory so the user's real `~/.decepticon` is never touched. `make clean` purges both the compose volumes and `.dogfood/` when you want a fresh onboard.

---

## Development

| Target | Description |
|--------|-------------|
| `make dev` | Build all Docker images and start with hot-reload (`docker compose watch`) — source changes sync into containers automatically |
| `make cli-dev` | Open the interactive terminal UI locally with hot-reload (Node) — backend stays in Docker |
| `make web-dev` | Run the Next.js dev server locally with infra services in Docker |

Typical contributor workflow:

```bash
# Day-to-day iteration: hot-reload
make dev

# Before opening a PR
make quality

# Before tagging a release
make smoke      # fast check
make dogfood    # full OSS UX
```

The Web dashboard is optional. Start it from the CLI with `/web`; it is then reachable at <http://localhost:3000>.

---

## Quality Gates

| Target | Description |
|--------|-------------|
| `make quality` | Run all quality gates: Python lint + tests + CLI typecheck/build/test + Web lint/build |
| `make lint` | Python lint + type-check (`ruff check` + `ruff format --check` + `basedpyright`) |
| `make lint-fix` | Auto-fix Python lint and formatting |
| `make quality-cli` | CLI typecheck + build + test (`vitest`) |

---

## Testing

| Target | Description |
|--------|-------------|
| `make test [ARGS=...]` | Run Python tests (`pytest`) inside the Docker container |
| `make test-local [ARGS=...]` | Run Python tests locally (requires `uv sync --dev`) |

CLI tests are part of `make quality-cli`. To run them in isolation: `npm run test --workspace=@decepticon/cli`.

---

## Web Dashboard

| Target | Description |
|--------|-------------|
| `make web-dev` | Start the Next.js dev server locally; brings up infra in Docker |
| `make web-build` | Build the web dashboard (also generates the Prisma client) |
| `make web-lint` | Lint the web dashboard (ESLint) |
| `make web-migrate [NAME=name]` | Run a Prisma dev migration |

To regenerate just the Prisma client (without a full build): `cd clients/web && npx prisma generate`.

---

## Operations

| Target | Description |
|--------|-------------|
| `make status` | Show running service status (`docker compose ps`) |
| `make logs [SVC=service]` | Follow logs (default: `langgraph`). Override: `make logs SVC=litellm` |
| `make health` | KG backend + Neo4j health checks, plus Web when it is running (broader than `decepticon kg-health`, which only checks the KG) |
| `make clean` | Full teardown: stop services, remove volumes, **and remove `.dogfood/`**. Use this when you want the next `make dogfood` to start from a fresh onboard wizard. |

---

## Benchmark

| Target | Description |
|--------|-------------|
| `make benchmark [ARGS="--level 1"]` | Run the benchmark suite locally |

---

## Versioning (no manual step)

There is no `make sync-version` or pre-tag commit. Source-tree version fields carry a `"0.0.0"` sentinel in `pyproject.toml`, `clients/cli/package.json`, and `clients/web/package.json`. The release workflow stamps the real tag into the images at Docker build time via `--build-arg VERSION=<tag>`. The Go launcher is similarly stamped via GoReleaser ldflags.

Release channel policy (see [update-channels.md](./update-channels.md) for the user-facing view):

- `vX.Y.Z` Git tags are the source of truth for releases.
- GHCR version tags (`X.Y.Z`) are immutable release artifacts and should be used by installed deployments.
- Two moving channel tags (Claude-Code-style soak model — both **final-only**):
  - GHCR `latest` → moved to the newest **final** release **immediately**, by the `publish-release` job (after all version-tagged images exist and the GitHub release is undrafted).
  - GHCR `stable` → moved to the newest final release **baked ≥ the soak window** (default 7 days), by the **scheduled** `promote-stable.yml` workflow — *not* on release. An explicit opt-in; the `${DECEPTICON_VERSION:-latest}` compose fallback uses the newest final release.
- Pre-releases use SemVer suffixes such as `v1.1.0-rc.1`, stay marked as GitHub pre-releases, and move **neither** channel tag (the channels never auto-select them).
- Bug fixes ship as new patch releases. Do not rebuild or rewrite an existing version tag.

To cut a release:

```bash
make quality && make smoke && make dogfood
git tag v1.0.16 && git push origin v1.0.16
```
