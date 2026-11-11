# Neo4j Hardening

> Why the `neo4j` service runs with an explicit APOC allowlist and how to
> safely extend it.

## TL;DR

The Decepticon `neo4j` container hosts the engagement knowledge graph
and is **dual-homed across `sandbox-net` and `decepticon-net`** so the
orchestrator on the management plane can read findings written by
agents from inside the sandbox. That bridge is by design - but it also
means Neo4j is the **shortest path** from a compromised sandbox into
the management plane.

This document explains the procedure-level controls that close that
path and the defense-in-depth checks layered on top of them.

## Threat model

| Asset | Why it matters | Reachable from sandbox via Neo4j? |
|-------|----------------|-----------------------------------|
| Neo4j container filesystem (`/var/lib/neo4j/conf/neo4j.conf`, `/proc/self/environ`) | Reveals Neo4j auth, container env, mounted secrets | Yes, via `apoc.cypher.runFile` / `apoc.export.csv.query(..., 'file:///...')` if unrestricted |
| LiteLLM container (holds Anthropic / OpenAI / Codex tokens) | Full LLM-provider compromise | Indirect - via Neo4j filesystem -> env leak -> credentials lateral move |
| PostgreSQL container | Engagement state, LiteLLM spend, web users | Same indirect path |
| The orchestrator's own LangGraph process | Tool calls, OPPLAN edits | Same indirect path |

A prompt-injection-driven payload that influences the agent's Cypher
strings is the realistic delivery vector. The agent reads hostile bytes
on every iteration (HTTP responses, banner grabs, file dumps), and
some of that content can convince the agent to add or query graph nodes
with attacker-influenced parameters.

## Server-level controls (`docker-compose.yml`)

The `neo4j` service in `docker-compose.yml` carries these settings:

```yaml
NEO4J_PLUGINS: '["apoc"]'

# Allowlist-only. File-I/O, runFile, system-db, and trigger procedures
# are NOT in this list and are therefore unreachable from Cypher.
NEO4J_dbms_security_procedures_allowlist: >-
  apoc.coll.*,apoc.text.*,apoc.path.*,apoc.create.*,
  apoc.merge.*,apoc.refactor.*,apoc.periodic.iterate,
  apoc.lock.*,apoc.case,apoc.do.case,apoc.do.when,apoc.when,
  apoc.help,apoc.version,apoc.diff.*,apoc.label.*,
  apoc.node.*,apoc.nodes.*,apoc.rel.*,apoc.util.*

# Refuse to boot on unknown config keys so a typo can't silently
# re-introduce `_unrestricted`.
NEO4J_server_config_strict__validation_enabled: "true"

# Lock the HTTP API to no cross-origin access. The orchestrator talks to
# Neo4j over Bolt; HTTP browser access is operator-only on loopback.
NEO4J_dbms_security_http__access__control__allow__origin: ""
```

### What was removed

Prior versions of `docker-compose.yml` shipped three settings that
together opened the sandbox->management bridge:

```yaml
# REMOVED - do NOT re-add.
NEO4J_dbms_security_procedures_unrestricted: apoc.*
NEO4J_apoc_export_file_enabled: "true"
NEO4J_apoc_import_file_enabled: "true"
```

`_unrestricted: apoc.*` told Neo4j to bypass the procedure security
sandbox for **every** APOC procedure - including `apoc.cypher.runFile`,
`apoc.load.json` (URL fetch), `apoc.export.csv.query` (arbitrary file
write), `apoc.systemdb.execute` (cross-database reach), and
`apoc.trigger.add` (persistent stored-procedure backdoor).

`apoc_export_file_enabled` and `apoc_import_file_enabled` further
explicitly enabled the file-I/O family.

The allowlist replaces both. The procedures that remain are the
read-only / collection-helper / refactor / batch-write family used by
`kg_add_node`, `kg_add_edge`, and the orchestrator's internal MERGE
patterns.

### What's still allowed

Each entry in the allowlist below was kept because it's used by at
least one tool in `packages/decepticon/decepticon/tools/research/`:

| Procedure family | Why we keep it |
|------------------|----------------|
| `apoc.coll.*`, `apoc.text.*`, `apoc.util.*` | List / string / hash helpers in query-builder paths |
| `apoc.path.*` | Path expansion used by the chain planner |
| `apoc.create.*`, `apoc.merge.*` | MERGE shortcuts used by upserts |
| `apoc.refactor.*` | Label / property rename during engagement migration |
| `apoc.periodic.iterate` | Batched MERGE for >10k node ingest (nmap mass scans) |
| `apoc.lock.*` | Optimistic concurrency control for parallel sub-agent writes |
| `apoc.case`, `apoc.do.*`, `apoc.when` | Cypher control flow |
| `apoc.label.*`, `apoc.node.*`, `apoc.nodes.*`, `apoc.rel.*` | Read-only graph introspection |
| `apoc.diff.*` | Engagement-to-engagement comparison reports |
| `apoc.help`, `apoc.version` | Operator introspection |

### How to add a new procedure to the allowlist

1. Open a PR that updates `docker-compose.yml`.
2. In the PR description, list **specifically** which tool or
   middleware needs the procedure and link to the line where it's
   called.
3. Confirm the procedure is **not** in any of these families:
   - `apoc.cypher.run*` (sub-cypher)
   - `apoc.load.*` (HTTP / URL fetch)
   - `apoc.import.*`, `apoc.export.*` (file I/O)
   - `apoc.systemdb.*` (cross-database)
   - `apoc.trigger.*` (persistent procedures)
   - `apoc.dbms.exec` (OS exec)
   - `apoc.spatial.geocode` (HTTP egress)
   - `apoc.metrics.*` (host-info disclosure)
4. Update the client-side allowlist in
   `packages/decepticon/decepticon/tools/research/_apoc_safety.py` to
   match - the safety check rejects any procedure not in **both**
   server config and client allowlist.
5. Extend the test in
   `packages/decepticon/tests/unit/research/test_apoc_safety.py` to
   verify the procedure now passes.

## Client-level controls

The server-side allowlist is the actual enforcement boundary. The
client-side `_apoc_safety.py` module is **defense in depth** that
catches obviously dangerous Cypher strings before they leave the
Python process, so the failure mode is loud (Python exception with a
useful message) instead of silent (driver error deep inside Neo4j).

Two functions:

```python
from decepticon.tools.research._apoc_safety import (
    find_violations,
    ensure_safe,
    CypherSafetyError,
)

# Returns a list of banned procedures referenced by `cypher`.
violations = find_violations(cypher_string)

# Raises CypherSafetyError if any banned procedure is referenced.
ensure_safe(cypher_string)
```

The check uses a deliberately conservative regex that flags **any**
`apoc.X.Y` token, even inside string literals. False positives on a
legitimate query string that happens to mention a banned procedure are
preferred to false negatives.

## Future hardening

These are **NOT** in this commit but are documented here so future PRs
have a target:

### Per-engagement Cypher user (next)

Today, every agent uses the single `neo4j` user with `${NEO4J_PASSWORD}`
on both `decepticon-net` and `sandbox-net`. A sandbox compromise
extracts the same credentials the orchestrator uses.

Plan:
- Create a `decepticon-mgmt` user with full read/write access for the
  orchestrator.
- Create a `decepticon-sandbox-<engagement>` user on engagement open
  with write access to only that engagement's nodes (via label predicate
  or - on Enterprise - a dedicated database).
- The `Neo4jConfig.from_env()` factory at
  `packages/decepticon/decepticon/tools/research/neo4j_store.py` already
  takes a configurable user, so the wiring is in place.
- Rotate the sandbox token on engagement close.

### Per-engagement database (Enterprise)

Neo4j Community Edition 5.x supports a system DB and one user DB. For
true tenant isolation we need Neo4j Enterprise's multi-database
feature (`CREATE DATABASE eng_<slug>`). The `Neo4jConfig.database`
field is wired through the driver already so the migration is config
only.

### Per-engagement label scoping (Community-compatible interim)

While we wait for Enterprise, every node should carry an `engagement`
property and every read should filter by `WHERE n.engagement =
$engagement`. The `kg_add_node` and `kg_add_edge` paths in
`tools/research/tools.py` should auto-inject this property from the
agent state's `engagement_name`.

This is the work tracked in the same change-set as the per-engagement
sandbox lifecycle (`docs/security/sandbox-isolation.md`).

## Verifying the hardening

Run inside the running stack (`make dev` or `decepticon`):

```bash
# Should succeed - allowlisted procedure
docker compose exec neo4j cypher-shell -u neo4j -p "$NEO4J_PASSWORD" \
  "CALL apoc.help('coll') YIELD name RETURN count(name)"

# Should fail with `There is no procedure with the name `apoc.cypher.runFile` registered`
docker compose exec neo4j cypher-shell -u neo4j -p "$NEO4J_PASSWORD" \
  "CALL apoc.cypher.runFile('file:///etc/passwd')"

# Should fail with `apoc.import.file procedure has not been imported`
docker compose exec neo4j cypher-shell -u neo4j -p "$NEO4J_PASSWORD" \
  "CALL apoc.import.csv([{fileName: 'file:///etc/passwd'}], [], {})"
```

Boot-time strict validation will also catch any future config typo:

```
Configuration setting 'dbms.security.procedures.unrestriced' not recognised.
```

## References

- [Neo4j 5.x APOC security model](https://neo4j.com/labs/apoc/5/installation/#restricted)
- CVE-2021-34371 (Neo4j APOC RCE via `apoc.cypher.runFile`)
- [Neo4j strict config validation](https://neo4j.com/docs/operations-manual/current/configuration/configuration-settings/#config_server.config.strict_validation.enabled)
- [OWASP LLM Top 10 - LLM01 Prompt Injection](https://owasp.org/www-project-top-10-for-large-language-model-applications/)
