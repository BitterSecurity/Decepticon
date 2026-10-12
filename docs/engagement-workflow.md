# Engagement Workflow

An OSS engagement starts in a chosen mode. **Red** runs the authorized test directly, **Plan** adds Soundwave's deeper planning, and **Blue** investigates events from a connected local sensor. The launcher and web creation form offer all three; use `/mode plan|red|blue` in the CLI or the web live-view selector to switch.

---

## Start an engagement

### 1. Define the target

Declare one exact target and an authorization reference before Plan or Red. The direct-scope form accepts:

| Type | Example |
|------|---------|
| IP address or CIDR | `192.168.1.10`, `192.168.1.0/24` |
| Root web URL | `https://app.example.com` |
| Hostname (CLI) | `app.example.com` |

Red creates a direct RoE with machine-enforced scope and an approval marker tied
to its content. You can work with Red immediately. Plan creates the scoped RoE
without Red approval. Blue needs the local sensor and monitor; run `/blue up`
from the host CLI before sending Blue messages.

## Optional Plan mode

### Soundwave interview

Soundwave conducts a structured interview to understand the engagement:

- **Threat actor profile** — Who are we emulating? Nation-state, financially motivated, insider threat?
- **Authorized scope** — What systems, networks, and actions are in scope?
- **Exclusions** — What must not be touched (production databases, critical services)?
- **Testing window** — When is testing authorized?
- **Acceptance criteria** — What does a successful engagement look like?
- **OPSEC requirements** — Noise level, detection avoidance, logging preferences
- **Contacts** — Operator, escalation chain, and who is paged on an emergency abort
- **Data handling** — Whether PII, health, source-code, or business data is in play, and which compliance frameworks apply
- **Abort triggers** — Conditions that force an emergency halt beyond the defaults

The interview streams in real time — you respond to each question.

### Document generation

From the interview, Soundwave writes an eight-document engagement bundle to
`<engagement>/plan/` as JSON:

| Document | Contents |
|----------|----------|
| **RoE** (Rules of Engagement) | Authorized scope, exclusions, testing window, escalation contacts, legal authorization |
| **Threat Profile** | MITRE-mapped adversary persona — tier, group ID, key TTPs — that drives TTP selection |
| **CONOPS** (Concept of Operations) | Threat model and kill-chain phases, scoped to the RoE |
| **Deconfliction Plan** | Source IPs, user-agents, time windows, and a shared code for real-time deconfliction calls with the SOC |
| **Contact Plan** | Operator, escalation chain, and the recipient paged on an emergency abort |
| **Data Handling Plan** | Per-class evidence retention, encryption, and chain-of-custody |
| **Abort Plan** | Halt triggers and AI-aware safety gates (hallucination threshold, destructive-action gate) |
| **Cleanup Plan** | Expected artifact inventory with per-phase removal commands |

The **OPPLAN** (Operations Plan) is not written by Soundwave — the orchestrator
(Decepticon) builds it from the bundle once planning completes.

### OPPLAN structure

Each OPPLAN objective carries:

```
OBJ-001
  title: "Port scan and service enumeration"
  phase: recon
  opsec_level: standard
  mitre: [T1595, T1046]
  depends_on: []
  acceptance_criteria: "All open ports identified with service versions"
  status: pending
```

OPSEC levels: `loud` → `standard` → `careful` → `quiet` → `silent`

Phases: `recon` → `initial-access` → `post-exploit` → `c2` → `exfiltration`

### Operator review

Review the generated documents before switching to Red. Plan validates the eight
documents and records their digest. The CLI or web selector asks you to approve
that exact bundle; edits after validation require a new validation. If scope is
too broad or exclusions are missing, edit or regenerate the affected documents.

---

## Red mode: execution

Once the direct RoE or optional Plan bundle is approved, the autonomous loop can
begin. The orchestrator can build an OPPLAN from the approved scope during Red
execution.

### The orchestration loop

```
while objectives remain pending:
    obj = next_pending_objective_with_dependencies_met()
    agent = spawn_specialist_agent(obj.phase)
    result = agent.execute(obj, roe, findings_so_far)
    update_opplan_status(obj, result.status)
    append_findings_to_disk(result.findings)
    update_knowledge_graph(result.findings)
```

**Key properties:**
- Each agent starts with a **fresh context window** — no accumulated noise
- Agents read prior findings from disk, not from conversation history
- The orchestrator enforces RoE every iteration before dispatching an agent
- Dependencies are checked before an objective starts (e.g., exploitation waits for recon)

### Agent dispatch by phase

| Phase | Primary agents | Specialists (if applicable) |
|-------|---------------|---------------------------|
| `recon` | Recon, Scanner | — |
| `initial-access` | Exploit, Verifier, Exploiter | AD Operator, Cloud Hunter |
| `post-exploit` | Post-Exploit | AD Operator, Reverser |
| `c2` | Post-Exploit | — |
| `exfiltration` | Post-Exploit, Analyst | — |

### Objective status transitions

```
pending → in-progress → completed
                     ↘ blocked
                     ↘ cancelled
```

`blocked` means the agent could not complete the objective (patch applied, service unavailable, out of scope). The orchestrator records the reason and moves on.

### Findings format

Each finding is written to `workspace/findings/FIND-NNN.md`:

```markdown
# FIND-001: vsftpd 2.3.4 Backdoor (CVE-2011-2523)

**Severity**: CRITICAL (CVSS 10.0)
**Host**: 192.168.1.100
**Service**: FTP (port 21)
**CWE**: CWE-78 (OS Command Injection)
**MITRE**: T1190 (Exploit Public-Facing Application)

## Description
...

## Evidence
[command output, screenshots]

## Remediation
Upgrade vsftpd to 2.3.5 or later.
```

---

## Blue mode and Offensive Vaccine

Blue Cell's local sensor and resident agent observe web requests and optional
application logs, investigate incidents, and report findings. The broader
automated attack → defend → verify loop remains planned. See [Blue Cell](features/blue-cell.md)
and [Offensive Vaccine](offensive-vaccine.md).

---

## Outputs

Everything an engagement produces lands under its workspace directory:

| Path | Contents |
|------|----------|
| `plan/roe.json` | Direct or planned machine-enforced scope |
| `plan/*.json` | Optional Soundwave bundle — `threat-profile`, `conops`, `deconfliction`, `contact`, `data-handling`, `abort`, `cleanup`, plus the orchestrator's `opplan` |
| `findings/FIND-NNN.md` | Individual vulnerability findings |
| `recon/`, `exploit/`, `post-exploit/` | Per-phase artifacts, created lazily as agents write to them |
| `report/` | Executive summary and technical report |
| `.sessions/` | Per-session tmux logs |

The knowledge graph retains the full attack graph for post-engagement analysis via Neo4j Browser (`http://localhost:7474`).
