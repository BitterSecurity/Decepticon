# Web Dashboard

The web dashboard is an open-source (Apache 2.0) browser-based control plane for managing engagements. It runs alongside the terminal CLI — use whichever interface fits your workflow.

**Stack**: Next.js 16 · React 19 · PostgreSQL (Prisma) · Neo4j · React Flow · Tailwind CSS v4

---

## Running the Dashboard

**End users:**
```bash
decepticon                    # starts the core stack + drops into the terminal CLI
```

The dashboard is **dynamic-spawn** (v1.1.8+): it does NOT come up on `decepticon start`. Bring it up from inside the CLI with the `/web` slash command, then open <http://localhost:3000>:

| CLI command | Effect |
|---|---|
| `/web` (or `/web up`) | `docker compose --profile web up -d web` against the host daemon |
| `/web down` (or `/web stop`) | stops the container; preserves it so the next `/web up` is fast |
| `/web url` | prints `http://localhost:${WEB_PORT}` without touching docker |
| `/dashboard` | alias for `/web` |

**Desktop app (contributors):**
```bash
npm run desktop
```

The desktop app is an Electron shell for the product UI:

| Mode | When | Target |
|---|---|---|
| `auto` (default) | Local install present → local; otherwise cloud | `http://localhost:${WEB_PORT:-3000}` or `https://app.decepticon.red` |
| `cloud` | `DECEPTICON_DESKTOP_MODE=cloud` | Hosted app (website login, Google/GitHub OAuth in-window) |
| `local` | `DECEPTICON_DESKTOP_MODE=local` | Local dashboard; auto-starts the `web` Docker Compose profile from `~/.decepticon` when offline |

Sessions use a persistent Electron partition (`persist:decepticon-desktop`). Optional one-shot cookie import: drop a Cookie-Editor JSON or Netscape export at `~/.decepticon/desktop-cookies.txt` (Windows: `%USERPROFILE%\.decepticon\desktop-cookies.txt`) or set `DECEPTICON_DESKTOP_COOKIES`. Prefer interactive sign-in; never commit session tokens.

Cross-platform notes (Linux / macOS / Windows):

| Concern | Behavior |
|---|---|
| Install copy-button | `install.sh` on Unix; `install.ps1` + winget Docker hint on Windows |
| Local stack start | `docker compose …` via argv spawn (`shell: false`); `windowsHide` on Windows |
| Login form edit keys | Application **Edit** menu roles (required for Cmd/Ctrl+C/V on all three) |
| Window lifecycle | macOS stays running after last window close (dock re-activate); others quit |
| CI | `Desktop (ubuntu/macOS/windows)` runs unit + syntax checks on every desktop change |

External links stay in the system browser; status UI matches the product branding when bootstrap is blocked.

Headless operators (no CLI, e.g. CI) can drive the same lifecycle from the host shell:
```bash
docker compose -p decepticon --profile web up -d --no-build web
```

**Contributors (full stack with hot-reload):**
```bash
make dev
```
Builds and starts every service with source-sync hot-reload. The web service still requires the `web` profile to be active — e.g. `COMPOSE_PROFILES=web make dev` — or bring it up after the fact with `/web up`. Open <http://localhost:3000>.

**Contributors (local Next.js dev server):**
```bash
make web-dev
```
Runs the Next.js dev server locally on the host while infra (postgres, neo4j, litellm, langgraph, sandbox) stays in Docker. Faster web iteration than `make dev`.

---

## Features

### Engagement Setup

Create an engagement with one target and authorization reference. Choose Red
for direct execution, Plan for optional Soundwave planning, or Blue for local
sensor investigation. The current direct-scope form accepts:

| Type | Example |
|------|---------|
| IP range | `10.0.0.0/24` |
| Web URL | `https://app.example.com` |

### Soundwave Interview

Plan mode runs Soundwave's interview in the terminal and builds the eight-document
bundle. Red mode can start without this interview from the confirmed direct RoE.
To move from Plan to Red, review the documents and approve the validated bundle
with the mode selector. Blue mode checks the local sensor before opening its
defensive agent. Each mode keeps its own terminal session and LangGraph thread.

### Execution Streaming

Once an engagement is running, the dashboard streams all agent events via Server-Sent Events (SSE) from LangGraph. You see tool calls, agent outputs, and objective status updates as they happen.

### Findings Viewer

Parses `FIND-NNN.md` reports from `workspace/findings/` and presents them in a structured view:
- Severity filter (CRITICAL / HIGH / MEDIUM / LOW / INFO)
- Per-finding detail: description, evidence, CVSS, CWE, MITRE technique
- Remediation recommendation

### Attack Graph Canvas

Interactive visualization of the Neo4j knowledge graph:
- Pan and zoom
- Click any node for full property detail
- Color-coded by node type (Host, Service, Vulnerability, Credential)
- Live — updates as the agent adds nodes and edges

Powered by [React Flow](https://reactflow.dev/) with `d3-force` for graph layout.

### OPPLAN Tracker

Per-objective progress board:
- Status badges: `pending` / `in-progress` / `completed` / `blocked` / `cancelled`
- MITRE ATT&CK technique IDs per objective
- OPSEC level indicator
- Dependency graph (which objectives must complete before this one starts)

---

## Database

The dashboard uses PostgreSQL with Prisma ORM.

**Run migrations** (after `git pull` with schema changes):
```bash
make web-migrate
```

**Regenerate Prisma client** (after editing `prisma/schema.prisma`):
```bash
cd clients/web && npx prisma generate
```
Or run `make web-build` to regenerate the client and build the dashboard in one step.

Schema is at `clients/web/prisma/schema.prisma`. Key model: `Engagement`.

---

## API Routes

The dashboard exposes Next.js App Router API routes under `clients/web/src/app/api/`. These proxy requests to LangGraph and serve as the backend for the React frontend. They are not a public API — the surface area may change between versions.
