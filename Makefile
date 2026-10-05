# Decepticon — Pre-release Verification & Development
#
# Purpose: dogfood and validate the OSS release flow before tagging. Every
# Docker target builds from the local checkout (never pulls from GHCR), so
# what you test is exactly what ships.
#
# Common workflows:
#   make dogfood      Full OSS UX (launcher → onboard → CLI) on local code
#   make smoke        Compose-only smoke (no launcher) — fast pre-release check
#   make quality      PR gate (Python + CLI + Web)
#   make dev          Backend hot-reload (compose watch) — daily dev loop
#   make help         List all targets

COMPOSE       := docker compose
# Dev override is now opt-in via an explicit ``-f`` chain — the file was
# renamed from ``docker-compose.override.yml`` to ``docker-compose.dev.yml``
# so it no longer auto-merges into every ``docker compose`` invocation.
# The launcher-driven OSS stack uses ``$(COMPOSE)`` (base only); local-dev
# targets that need the skills bind mount + workspace overlays chain
# ``$(COMPOSE_DEV)``. Closes #214.
COMPOSE_DEV   := docker compose -f docker-compose.yml -f docker-compose.dev.yml
COMPOSE_WATCH := docker compose -f docker-compose.yml -f docker-compose.dev.yml -f docker-compose.watch.yml
PROFILES_ALL  := --profile cli --profile c2-sliver
WEB_DIR       := clients/web

# Dogfood: isolated $DECEPTICON_HOME so the launcher can onboard, write .env,
# and stand up the stack without touching the user's real ~/.decepticon. The
# launcher resolves all relative paths against the compose file's directory,
# so docker-compose.yml + config/ + containers/ + .env.example are symlinked
# back to the repo. workspace/ stays a real directory (engagement bind mount).
DOGFOOD_HOME  := $(CURDIR)/.dogfood
LAUNCHER_BIN  := clients/launcher/bin/decepticon

# Match the launcher's explicit Compose project without interpolating the
# environment-controlled stack/project name into Make's shell source.
define CLEAN_LAUNCHER_STACK
	@stack="$${DECEPTICON_STACK_NAME:-}"; project="$${DECEPTICON_COMPOSE_PROJECT:-}"; \
	case "$$stack" in *[!a-z0-9-]* ) echo "invalid DECEPTICON_STACK_NAME: $$stack" >&2; exit 2;; esac; \
	if [ -z "$$project" ]; then project="decepticon$${stack:+-$$stack}"; fi; \
	case "$$project" in ""|[!a-z0-9]*|*[!a-z0-9_-]* ) echo "invalid DECEPTICON_COMPOSE_PROJECT: $$project" >&2; exit 2;; esac; \
	docker compose -p "$$project" $(PROFILES_ALL) down --volumes --remove-orphans 2>/dev/null; true
endef

# docker compose cannot expand ~ inside compose-file defaults, so resolve it
# here before any subprocess inherits the env.
export DECEPTICON_HOME ?= $(HOME)/.decepticon

# Mirror the launcher's start.go credential mount logic so `make dev` (and any
# other target that calls `docker compose`) populates the litellm container's
# Claude Code + Codex CLI OAuth tokens without requiring users to run
# `decepticon onboard`. Existence check at make-time: real file when host has
# tokens, /dev/null otherwise so docker doesn't synthesize a bind directory.
export CLAUDE_CREDENTIALS_VOLUME ?= $(shell test -f $(HOME)/.claude/.credentials.json && echo $(HOME)/.claude/.credentials.json || echo /dev/null)
export CODEX_AUTH_VOLUME ?= $(shell test -f $(HOME)/.codex/auth.json && echo $(HOME)/.codex/auth.json || echo /dev/null)

.PHONY: help \
        dogfood launcher smoke \
        dev cli-dev web-dev infra \
        quality quality-strict quality-cli test test-local lint lint-fix \
        ci-lint ci-test ci-test-coverage ci-test-coverage-report \
        web-build web-hotswap web-lint web-migrate \
        status logs health clean \
        node-install web-db-ensure \
		benchmark terminal-bench recreate-litellm

# ── Help (default target) ────────────────────────────────────────

help:
	@echo "Decepticon — Pre-release Verification & Development"
	@echo ""
	@echo "Pre-release verification (release readiness):"
	@echo "  make dogfood      Full OSS UX (launcher → onboard → CLI) on local code"
	@echo "  make smoke        Compose-only smoke (no launcher) — fast OSS-shape check"
	@echo "  make launcher     Build the Go launcher binary ($(LAUNCHER_BIN))"
	@echo ""
	@echo "Development (build the thing being released):"
	@echo "  make dev          Backend hot-reload (compose watch)"
	@echo "  make cli-dev      CLI locally + backend hot-reload"
	@echo "  make web-dev      Web (Next.js) locally + backend hot-reload"
	@echo ""
	@echo "Quality gates (PR readiness):"
	@echo "  make quality        PR gate — mirrors CI PR lane (fast, no slow tests, errors-only typecheck)"
	@echo "  make quality-strict Release gate — mirrors CI main-push lane + full basedpyright warning audit"
	@echo "  make ci-lint        Lint + format + basedpyright errors-only (CI mirror)"
	@echo "  make ci-test        pytest fast lane (-n auto -m \"not slow\", no coverage)"
	@echo "  make ci-test-coverage  pytest with coverage gate (--cov-fail-under=60)"
	@echo "  make ci-test-coverage-report  pytest fast lane with coverage report (no gate, PR-informational)"
	@echo "  make test           pytest in container"
	@echo "  make test-local     pytest locally (uv sync --dev; takes ARGS=)"
	@echo "  make lint           Python lint + format check + basedpyright (all levels, local exploratory)"
	@echo "  make lint-fix       Auto-fix Python lint + format"
	@echo ""
	@echo "Web dashboard (single checks):"
	@echo "  make web-build    Prisma generate + Next build"
	@echo "  make web-hotswap  Build + inject into running container (~15s)"
	@echo "  make web-lint     ESLint"
	@echo "  make web-migrate [NAME=]   Prisma migrate dev"
	@echo ""
	@echo "Ops:"
	@echo "  make status       docker compose ps"
	@echo "  make logs [SVC=]  Follow logs (default: langgraph)"
	@echo "  make health       KG + Neo4j; Web when running"
	@echo "  make clean        Full teardown (compose volumes + .dogfood/)"
	@echo ""
	@echo "Other:"
	@echo "  make benchmark [ARGS=\"--level 1\"]"
	@echo "  make terminal-bench [ARGS=\"--include-task-name <task>\"]  # GPT-5.6 Luna/max"

# ── Pre-release Verification (PRIMARY) ───────────────────────────

## End-to-end OSS dogfood: launcher onboard → engagement picker → compose up
## → CLI. Identical UX to a real `curl | bash` install, but every image and
## the launcher itself come from the current checkout (tag :dev). The
## launcher version is "dev" so auto-update + config-sync are skipped — the
## symlinked .dogfood/ tree is the source of truth.
dogfood: launcher
	@echo "[dogfood] Stopping any prior launcher and repo-root stacks..."
	$(CLEAN_LAUNCHER_STACK)
	@$(COMPOSE) $(PROFILES_ALL) down --remove-orphans 2>/dev/null; true
	@mkdir -p $(DOGFOOD_HOME)/workspace
	@ln -sfn $(CURDIR)/docker-compose.yml $(DOGFOOD_HOME)/docker-compose.yml
	@ln -sfn $(CURDIR)/config              $(DOGFOOD_HOME)/config
	@ln -sfn $(CURDIR)/containers          $(DOGFOOD_HOME)/containers
	@ln -sfn $(CURDIR)/.env.example        $(DOGFOOD_HOME)/.env.example
	@echo ""
	@echo "[dogfood] Building images from local code (tag :dev)..."
	DECEPTICON_VERSION=dev $(COMPOSE) --profile cli build
	@echo ""
	@echo "[dogfood] Launching ./$(LAUNCHER_BIN) (DECEPTICON_HOME=$(DOGFOOD_HOME))"
	@echo ""
	DECEPTICON_VERSION=dev DECEPTICON_HOME=$(DOGFOOD_HOME) ./$(LAUNCHER_BIN)

## Build the Go launcher binary (version=dev, gates auto-update + config sync).
launcher:
	cd clients/launcher && go build \
		-ldflags '-X github.com/PurpleAILAB/Decepticon/clients/launcher/cmd.version=dev' \
		-o bin/decepticon

## Compose-only smoke (no launcher, no onboard wizard) — fastest possible
## release-shape check. Replicates only the launcher's compose Up step:
##   1. Down + purge volumes (parity with `decepticon remove`)
##   2. Build all images from local code (replaces `compose pull` from GHCR)
##   3. up -d --no-build --wait --wait-timeout  (identical to launcher Up)
##   4. Health checks (identical to `decepticon health`)
## Use dogfood for the full release flow; use smoke when the compose stack
## is the only thing you've changed.
smoke:
	@echo "=== Decepticon pre-release smoke (compose-only, no launcher) ==="
	@echo ""
	@echo "[1/4] Clean state (purging containers + volumes)..."
	$(CLEAN_LAUNCHER_STACK)
	@$(COMPOSE) $(PROFILES_ALL) down --volumes --remove-orphans 2>/dev/null; true
	@echo ""
	@echo "[2/4] Building images from local code..."
	$(COMPOSE) --profile cli build
	@echo ""
	@echo "[3/4] Starting services (--no-build --wait, OSS launcher flow)..."
	$(COMPOSE) --profile cli up -d --no-build --wait \
		--wait-timeout $${DECEPTICON_STARTUP_TIMEOUT_SECONDS:-600}
	@echo ""
	@echo "[4/4] Health checks..."
	@$(MAKE) -s health
	@echo ""
	@echo "=== smoke OK — stack mirrors OSS user state ==="
	@echo "  Web:          optional (/web from CLI)"
	@echo "  LangGraph:    http://localhost:$${LANGGRAPH_PORT:-2024}"
	@echo "  Run dogfood:  make dogfood"
	@echo "  Teardown:     make clean"

# ── Development (build the thing being released) ─────────────────

## Build images and start with hot-reload (source changes auto-sync).
dev:
	$(COMPOSE_WATCH) watch

## CLI locally (Node) — backend stays in Docker with hot-reload sync.
##
## Build chain: shared/streaming dist → cli dist → run.
## `npm run dev` only watches tsc; `node --watch` re-execs on dist changes.
## Both watchers run as background jobs; `wait` keeps the target alive so
## Ctrl-C tears both down cleanly.
cli-dev: infra
	@$(COMPOSE_WATCH) watch --no-up --quiet langgraph &
	npm run build --workspace=@decepticon/streaming
	cd clients/cli && npm run build
	cd clients/cli && (npm run dev & DECEPTICON_API_URL=$${DECEPTICON_API_URL:-http://localhost:2024} node --watch dist/index.js & wait)

## Next.js dev server locally — infra stays in Docker with hot-reload.
web-dev: infra web-db-ensure node-install
	# Build streaming first: both the Next dev server and the PTY-spawned CLI
	# import @decepticon/streaming, which resolves to its dist/ build.
	npm run build --workspace=@decepticon/streaming
	@$(COMPOSE_WATCH) watch --no-up --quiet langgraph &
	@echo "[web-dev] Starting terminal server (ws://localhost:3003)..."
	@cd $(WEB_DIR) && npx tsx server/terminal-server.ts &
	@echo "[web-dev] Starting Next.js dev server (http://localhost:3000)..."
	cd $(WEB_DIR) && npm run dev

# Internal: bring up backend infra (built from local code).
# Chains the dev override so local-dev workflows (cli-dev, web-dev) pick up
# the skills bind mount alongside the base stack. The OSS launcher path
# (smoke + dogfood) keeps ``$(COMPOSE)`` to mirror what end users run.
infra:
	@echo "[infra] Ensuring backend services are running..."
	@$(COMPOSE_DEV) up -d --build postgres neo4j litellm langgraph sandbox

# ── Quality gates ────────────────────────────────────────────────

test:
	$(COMPOSE) exec langgraph python -m pytest $(ARGS)

test-local:
	uv run pytest $(ARGS)

lint:
	uv run ruff check .
	uv run ruff format --check .
	uv run basedpyright

lint-fix:
	uv run ruff check --fix .
	uv run ruff format .

# ── CI mirror targets ─────────────────────────────────────────────
# These are the exact commands CI runs. .github/workflows/ci.yml
# dispatches via `make ci-lint` / `make ci-test{,-coverage}` so this
# Makefile stays the single source of truth — no drift between local
# and CI is possible by construction.

ci-lint:
	@echo "==> ruff check"
	uv run ruff check .
	@echo "==> ruff format --check"
	uv run ruff format --check .
	@echo "==> basedpyright (errors only — pre-existing warnings tracked but non-blocking)"
	uv run basedpyright --outputjson > bp.json || true
	uv run python scripts/check_basedpyright_errors.py bp.json

## PR lane: fast, no coverage, slow tests excluded.
ci-test:
	uv run --extra mcp pytest -n auto -q -m "not slow"

## SKILL.md schema validator (warn mode — Phase 0). Exit 0 even if violations found.
.PHONY: audit-skills
audit-skills:
	uv run python -m decepticon.skill_audit --mode warn

## SKILL.md schema validator (strict mode — post-Phase-0 CI gate). Exit 1 on any violation.
.PHONY: audit-skills-strict
audit-skills-strict:
	uv run python -m decepticon.skill_audit --mode strict

## Download and verify the immutable MITRE STIX bundle required by the
## Skillogy graph builder. The builder itself remains offline-only.
.PHONY: bootstrap-skill-graph
bootstrap-skill-graph:
	uv run python scripts/bootstrap_skillogy_stix.py

## Skill graph builder (Phase 1a) — compile SKILL.md + seeds + MITRE STIX
## into packages/decepticon/decepticon/skills/.graph/skills.cypher.
.PHONY: build-skill-graph
build-skill-graph: bootstrap-skill-graph
	uv run python -m decepticon.skillogy.builder --frozen-built-at

## CI gate — assert the checked-in skills.cypher matches what the
## builder produces from the current SKILL.md + seed YAML + pinned STIX.
.PHONY: check-skill-graph
check-skill-graph: bootstrap-skill-graph
	uv run python -m decepticon.skillogy.builder --frozen-built-at --check

## main-push lane: slow included, coverage 60% gate (ratcheted from 35% in #380).
ci-test-coverage:
	uv run --extra mcp pytest -n auto --cov --cov-report=xml --cov-report=term --cov-fail-under=60

## PR-informational lane: coverage without any threshold, fast subset
## (``-m "not slow"`` — matches the blocking PR ``ci-test`` lane so the
## reported coverage % is for the same test set authors actually run on a
## PR). Used by the non-blocking ``coverage-report`` job in ci.yml so PR
## authors see a coverage delta without blocking merge (the documented
## decision is to keep the blocking PR pytest step coverage-free — see
## ci.yml comment).
ci-test-coverage-report:
	uv run --extra mcp pytest -n auto --cov --cov-report=xml --cov-report=term --cov-fail-under=0 -m "not slow"

quality-cli: node-install
	# streaming workspace must be built first — its package.json main
	# resolves to dist/, which cli's typecheck + build consume.
	npm run build --workspace=@decepticon/streaming
	npm run typecheck --workspace=@decepticon/cli
	npm run build --workspace=@decepticon/cli
	npm run test --workspace=@decepticon/cli

## PR gate — mirrors CI PR lane (errors-only typecheck + fast pytest + CLI + Web).
## Use before opening a PR; passing this guarantees CI will pass.
quality: ci-lint ci-test quality-cli web-lint web-build
	@echo ""
	@echo "OK — PR gates passed (mirrors CI PR lane)"

## Release gate — mirrors CI main-push lane (coverage 60%) + full basedpyright
## audit (warnings + info). Run before tagging a release.
quality-strict: ci-lint ci-test-coverage quality-cli web-lint web-build
	@echo ""
	@echo "==> basedpyright (full — warning + info audit, non-blocking)"
	uv run basedpyright
	@echo ""
	@echo "OK — release gate passed (mirrors CI main lane + warning audit)"

# ── Web Dashboard (single checks) ────────────────────────────────
# All web targets share the root `node-install` so workspace deps hoist
# into the root node_modules — no separate clients/web/node_modules tree.

web-build: node-install
	# streaming workspace first — the web resolves @decepticon/streaming to its
	# dist/ via package exports (no src path alias), so next build needs it.
	npm run build --workspace=@decepticon/streaming
	cd $(WEB_DIR) && npx prisma generate && npm run build

## Hot-swap web dashboard into running container (~15s vs ~5min docker build).
## Builds Next.js on host, injects via tar pipe, restarts container.
web-hotswap: node-install web-build
	./scripts/web-hotswap.sh --skip-build

web-lint: node-install
	cd $(WEB_DIR) && npx eslint src/ --max-warnings 0

web-migrate: node-install
	cd $(WEB_DIR) && npx prisma migrate dev --name $(or $(NAME),init)

# ── Status / Logs / Health / Clean ───────────────────────────────

status:
	$(COMPOSE) ps

## Follow logs (usage: make logs or make logs SVC=langgraph)
logs:
	$(COMPOSE) logs -f $(or $(SVC),langgraph)

## Health checks: KG backend + Neo4j + Web (parity with `decepticon health`).
health:
	@$(COMPOSE) exec -T langgraph python -m decepticon.tools.research.health >/dev/null 2>&1 \
		&& echo "kg:    OK" || (echo "kg:    FAIL" && exit 1)
	@$(COMPOSE) exec -T neo4j cypher-shell -u neo4j -p "$${NEO4J_PASSWORD:-decepticon-graph}" "RETURN 1 AS ok;" >/dev/null 2>&1 \
		&& echo "neo4j: OK" || (echo "neo4j: FAIL" && exit 1)
	@if $(COMPOSE) ps --status running --services | grep -qx web; then \
		curl -sf http://localhost:$${WEB_PORT:-3000} >/dev/null 2>&1 \
			&& echo "web:   OK (http://localhost:$${WEB_PORT:-3000})" \
			|| (echo "web:   FAIL — not reachable on port $${WEB_PORT:-3000}" && exit 1); \
	else \
		echo "web:   SKIP (optional profile not running)"; \
	fi

## Full teardown: containers + volumes + .dogfood/. Destructive — also
## wipes the dogfood .env (API keys) so the next `make dogfood` starts
## with a fresh onboard wizard.
clean:
	$(CLEAN_LAUNCHER_STACK)
	$(COMPOSE) $(PROFILES_ALL) down --volumes --remove-orphans
	@rm -rf $(DOGFOOD_HOME)
	@echo "OK — compose volumes purged, .dogfood/ removed"

# ── Internal idempotent helpers ──────────────────────────────────

node-install:
	@test -d node_modules || npm install

# postgres-init/01-create-web-db.sql auto-creates decepticon_web on fresh
# volumes. This target only waits for postgres readiness and applies
# Prisma migrations.
web-db-ensure:
	@echo "[web-db-ensure] Waiting for PostgreSQL..."
	@for i in 1 2 3 4 5 6 7 8 9 10; do \
		docker exec decepticon$${DECEPTICON_STACK_NAME:+-$${DECEPTICON_STACK_NAME}}-postgres pg_isready -U decepticon -q 2>/dev/null && break; \
		sleep 1; \
	done
	@cd $(WEB_DIR) && npx prisma migrate deploy 2>&1 | tail -1

# ── Benchmark ────────────────────────────────────────────────────

## Force-recreate the litellm container so it captures the host's CURRENT
## ~/.claude/.credentials.json inode. Workaround for the Docker single-file
## bind-mount limitation: the container's view is pinned to the inode at
## mount time; Claude Code CLI rotates credentials via atomic-rename which
## allocates a new inode that the container never sees. Force-recreate
## remounts and picks up the freshly written file. Intended to run as a
## per-cycle preamble in the benchmark loop so each cycle starts with a
## valid OAuth access token (token TTL ~8h ≫ cycle duration ~30m).
recreate-litellm:
	@$(COMPOSE) up -d --no-build --force-recreate litellm
	@docker exec decepticon$${DECEPTICON_STACK_NAME:+-$${DECEPTICON_STACK_NAME}}-litellm sh -c 'test -s /root/.claude/.credentials.json' \
		&& echo "recreate-litellm: creds mount OK" \
		|| (echo "recreate-litellm: creds mount EMPTY — onboard first" && exit 1)

## Run benchmark suite (usage: make benchmark ARGS="--level 1")
benchmark:
	uv run python -m benchmark.runner $(ARGS)

TERMINAL_BENCH_MODEL ?= auth/gpt-5.6-luna
TERMINAL_BENCH_EFFORT ?= max

terminal-bench:
	DECEPTICON_SKIP_BOOT=1 PYTHONPATH="$(CURDIR)$${PYTHONPATH:+:$${PYTHONPATH}}" \
	uv run harbor run --dataset terminal-bench/terminal-bench-2-1 \
		--agent decepticon_tbench_agent:DecepticonTerminalBenchAgent \
		--model "$(TERMINAL_BENCH_MODEL)" \
		--agent-kwarg "reasoning_effort=$(TERMINAL_BENCH_EFFORT)" $(ARGS)

## CVE-Bench offline dry run (mocked LLM + sandbox, 3 fixtures, seed=0).
## Output: benchmark/results/cve-bench/dry-run-<YYYY-MM-DD>.jsonl
cve-bench-dry:
	CVE_BENCH_DRY_RUN_SEED=0 PYTHONHASHSEED=0 \
		uv run python -m benchmark.cve_bench.dry_run $(ARGS)
