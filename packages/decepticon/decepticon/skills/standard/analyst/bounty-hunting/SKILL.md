---
name: bounty-hunting-methodology
description: Bug bounty white-box hunting methodology. Load when the target is an open-source project with a security advisory program, bug bounty, or responsible disclosure policy.
metadata:
  skillogy:
    version: 1
  subdomain: analyst
  when_to_use: "bug bounty white-box methodology open source security advisory program responsible disclosure policy h1 bugcrowd"
---

# Bug Bounty Hunting Methodology

You are not scanning. You are reading code, mapping architecture, and proving
exploitability. Volume is the enemy — signal is the metric. Every report must
survive triage by an experienced security engineer.

## Target Assessment

The selected program and workflow determine effort allocation. Useful context
for interpreting a supplied target includes:

1. **Exposure context**: known deployments and dependency reach. Popularity alone
   does not establish technical impact or a reward multiplier.
2. **Trust boundary complexity**: Does the app load config from untrusted sources?
   Handle plugins? Parse user-controlled serialized data? Multi-tenant auth?
   Complex trust boundaries = more attack surface.
3. **Security advisory history**: Check `github.com/advisories?query=<package>`.
   Projects that accept and credit researchers will work with you. Projects with
   zero advisories are either very secure or don't have a disclosure process.
4. **Reward program**: HackerOne, Bugcrowd, Immunefi, GitHub Security Advisories,
   Google VRP. Check scope, excluded vuln classes, and reward tiers.

Record the assessment as a node:
```
kg_add_node("repo", "<name>", props={"stars": N, "downloads_weekly": N,
  "has_security_policy": true, "advisory_count": N, "bounty_program": "hackerone"})
```

## White-Box Methodology

This is the core loop. Fork. Read. Trace. Prove.

### Step 1 — Map the project
```bash
find /workspace/target -name 'package.json' -o -name 'pyproject.toml' \
  -o -name 'go.mod' -o -name 'Cargo.toml' -o -name 'composer.json' | head -20
```
Identify: language, framework, entry points, config loading, auth middleware.

### Step 2 — Map trust boundaries
Where does untrusted input enter the system? Trace these sources:
- HTTP request params, headers, body
- Environment variables and `.env` files
- Config files from current directory (`.gemini/settings.json`, `.vscode/settings.json`)
- Plugin/extension loading paths
- Deserialization of user-controlled data (pickle, YAML, JSON with class hints)
- IPC channels, WebSocket messages, MCP tool inputs

For each source, add an ENTRYPOINT node:
```
kg_add_node("entrypoint", "POST /api/upload body", props={"type": "http",
  "file": "routes/upload.py", "line": 42})
```

### Step 3 — Identify crown jewels
What is the most valuable thing an attacker could reach?
- Admin access / account takeover
- RCE / command execution
- Database contents / PII
- Secret keys / credentials
- File system read/write

Add CROWN_JEWEL nodes for each.

### Step 4 — Trace source → sink
For each entrypoint, trace the data flow to dangerous sinks:
- `exec()`, `eval()`, `spawn()`, `subprocess.run()`, `os.system()`
- `db.query()` with string interpolation
- `render()` / `innerHTML` with unescaped input
- `open()` / `readFile()` with user-controlled paths
- `pickle.loads()`, `yaml.unsafe_load()`, `JSON.parse()` with reviver
- `redirect()` with unvalidated URLs

Use `semgrep`, `grep`, or `bash` to find sinks, then manually trace backwards
to see if untrusted input reaches them without sanitization.

### Step 5 — Prove or discard
Every finding MUST go through `validate_finding` with:
- A working `poc_command`
- `success_patterns` that uniquely match the exploit signal
- A `negative_command` (same request without payload)
- `negative_patterns` matching the baseline response
- Full `cvss_vector` string

An unreproduced claim remains unverified. Distinguish an unavailable prerequisite
from a control that refutes the hypothesis; preserve the evidence and limitations.

## Mechanism families

These are investigation categories, not a universal payout or priority ranking:

1. **Remote Code Execution** — deserialization, template injection, command injection,
   unsafe `eval`/`exec`, `shell: true` with user input.
2. **Access Control Bypass** — IDOR, BOLA, missing ownership validation, broken
   function-level authorization.
3. **Authentication Bypass** — token leakage, session fixation, OAuth state confusion,
   JWT `alg=none`, password reset flaws.
4. **Path Traversal / File Write** — directory escape in upload/download handlers,
   session file storage with crafted IDs, zip slip.
5. **Server-Side Request Forgery** — redirect bypass, DNS rebinding, cloud metadata
   access via internal URL parameters.
6. **Injection** — SQLi, XSS (stored > reflected), LDAP injection, GraphQL injection.
7. **Information Disclosure** — API key leakage, user enumeration via error codes,
   schema/debug endpoint exposure, directory listing.

## Report Title Convention

Every report title MUST follow: `[Impact] via [Mechanism] in [Component]`

Good:
- "Path traversal in plugin file upload enables arbitrary directory deletion and file write"
- "Cloud function validator bypass via prototype chain traversal"
- "Unauthenticated SSRF via HTTP redirect bypass in LiveLinks proxy"

Bad:
- "XSS vulnerability found" (no mechanism, no component)
- "Possible SQL injection" (speculative)
- "Security issue in API" (says nothing)

## Interpretation and disposition

Keep hypotheses separate from reproduced effects and justify severity from the
demonstrated impact. Scope exclusions forbid unauthorized testing. Eligibility,
novelty, internal report completion and external disclosure are separate workflow
decisions; this technical reference does not authorize submission.
