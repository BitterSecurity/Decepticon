---
name: recon-workflow
description: "Recon agent workflow — intake, scope discipline, execute, structured handoff to exploit. Tag-conditional rules for race-condition / smuggling-desync challenges."
metadata:
  when_to_use: "recon, reconnaissance, enumeration, recon workflow, scope rules, recon handoff, SUMMARY.md, recon → exploit handoff"
  subdomain: workflow
---

# Recon Workflow

## Role

Surface the target's attack surface — domains, services, web routes, auth flows, session state — and hand a precise, structured map to exploit. Recon does NOT exploit. Recon does NOT iterate. Recon stops at "here is the gating cookie / sink / framing" and lets exploit run.

## The Loop

### Phase 1 — Intake

Before any probe:

1. Read the objective from the orchestrator's `task()` handoff.
2. Note the target, scope (RoE), challenge tags (e.g. `race_condition`, `smuggling_desync`, `insecure_deserialization`), and prior findings.
3. Decide which sub-skills apply. Available sub-skills:
   - `load_skill("/skills/standard/recon/passive-recon/SKILL.md")`
   - `load_skill("/skills/standard/recon/osint/SKILL.md")`
   - `load_skill("/skills/standard/recon/cloud-recon/SKILL.md")`
   - `load_skill("/skills/standard/recon/active-recon/SKILL.md")`
   - `load_skill("/skills/standard/recon/web-recon/SKILL.md")` (hub: discovery, api-enumeration, cms-scanning, waf-detection, auth-mapping, cookie-audit)

### Phase 2 — Scope Rules

> **Recon stays a recon — no exploit harnesses.**
> - At MOST 2 confirm probes per hypothesis. If 2 don't confirm, hand the hypothesis off, do not deepen.
> - ZERO full exploit harnesses. NO race-condition scripts. NO multi-endpoint orchestrations. NO sqlmap dumps. NO ysoserial payloads. Those belong to exploit.
> - Always set tool-level timeouts on HTTP/network calls so a slow target cannot wedge the shell. Always bound loops. Specific timeout values belong in the per-skill recon docs, not here.
> - Prefer `python3 -c '...'` or `python3 - <<'PY' ... PY` with explicit timeouts over chained bash one-liners.
> - NEVER use `&` to parallelize in bash. Parallelism that goes past sequential probing is exploit territory — hand off.
> - Always emit a **"Tried, ruled out"** list in the handoff so exploit doesn't repeat work.

### Phase 3 — Execute

General sequence: passive → OSINT → cloud → active → web.

| Order | Skill | Focus | Noise |
|-------|-------|-------|-------|
| 1 | `passive-recon` | DNS, subdomains, WHOIS, ASN, CT logs, fingerprinting | None |
| 2 | `osint` | Email harvesting, employees, GitHub secrets, breach data | None |
| 3 | `cloud-recon` | S3/Blob/GCS buckets, cloud services, CDN origins | Low |
| 4 | `active-recon` | Port scanning, service detection, banner grabbing | Medium-High |
| 5 | `web-recon` | Discovery → fingerprint → API → CMS → WAF → auth → cookie audit | Medium-High |

Web-recon order within the hub:

1. **Technology fingerprint** (passive httpx)
2. **Discovery** (`discovery.md`) — directory/file fuzzing, vhost, JS analysis
3. **API enumeration** (`api-enumeration.md`) — REST, GraphQL, params
4. **CMS scanning** (`cms-scanning.md`) — only if CMS detected
5. **WAF detection** (`waf-detection.md`) — note multi-proxy stack for smuggling handoff
6. **Auth mapping** (`auth-mapping.md`) — login flow, session, JWT/OAuth/SAML
7. **Cookie audit** (`cookie-audit.md`) — REQUIRED whenever a sink is reached, REQUIRED with session-write timeline whenever the tag includes `race_condition`/`toctou`/`concurrent`

### Phase 4 — Handoff

Every recon run MUST produce `SUMMARY.md` with this fixed structure. Exploit reads this file first; missing sections are a recon-incomplete signal.

**MANDATORY**: Writing `SUMMARY.md` is your LAST action before returning. No exceptions — even a null result run MUST write `RECON_BUDGET_EXHAUSTED` with negative findings. The orchestrator treats absent `SUMMARY.md` as a sub-agent crash (Rule 13 in decepticon.md) and will retry or block the objective. Your findings are invisible without this file.

```
# SUMMARY.md — recon handoff

Target: <url>
Tags: <comma-separated challenge tags, e.g. race_condition, deserialization>

## Confirmed sinks
- <endpoint> <method> — <sink type, e.g. blind deser, eval, render>
- ...

## Required session state
cookies=[<name>=<value>, ...]; obtained via <auth flow, e.g. POST /login (creds: user@example.com / hunter2)>

## Sink preconditions
| Sink | Endpoint | Method | Required cookies | Behavior w/o cookie | Behavior w/ cookie | Notes |
| ... |

## Session-write timeline
| Endpoint | Reads session keys | Writes session keys (pre-verdict) | Slow ops | Race window (ms) |
| ... |

## Tried, ruled out
- <hypothesis> — <2-probe evidence why ruled out>
- ...

## Open hypotheses
- <hypothesis> — <signal that pointed here, exploit should pursue>
- ...

## Frontend stack (for smuggling-tagged handoffs)
Differential parsing observed: <variants tested + status codes, e.g. "duplicate TE header → 400 from edge but 200 from origin">
Confirmed desync: <YES (with trace excerpt) | NO>
Frontend behavior: <reject malformed framing | forward malformed framing | normalize malformed framing>
Recommended exploit gate: <e.g. "run smuggling.md confirm-desync gate before iterating CL.TE / TE.CL">
```

## Discipline / Tag-conditional rules

- If `Tags` includes `race_condition` / `toctou` / `concurrent`, the **Session-write timeline** section MUST be populated. Empty timeline + race tag = exploit will flag handoff back as "recon incomplete: session-write timeline missing".
- If `Tags` includes `smuggling_desync` / `request_smuggling` / `hrs` / `desync`, the **Frontend stack** section MUST be populated. Differential parsing alone (different status codes from different framing headers) is **NOT** confirmed desync — it is a hint. Recon must distinguish:
  - "Differential parsing observed" — one parser path returns 400/501 while another returns 200. Routing signal only.
  - "Confirmed desync" — a smuggled-prefix request causes the back-end to mis-frame the next request on the connection (e.g. observable `XGET` 400 on a fresh victim request). Requires a trace excerpt as evidence.
  If recon hands off `Differential parsing observed: YES` and `Confirmed desync: NO`, exploit MUST run the smuggling.md confirm-desync gate before iterating any payload variants.
- Sandbox bash discipline applies cross-cutting (auto-injected into every sub-agent's bash tool prompt — no manual load required).

## Handoff Format (output files)

```
./
├── recon_notes.md                  # working scratch
├── SUMMARY.md                     # the handoff (fixed structure above)
└── recon_<target>_*.{json,txt}     # per-tool artifacts (ffuf, httpx, nmap, etc.)
```
