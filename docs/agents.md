# Agents

Decepticon ships a **multi-agent stack** organized by kill chain phase: three orchestrators (Decepticon, Vulnresearch, Soundwave), a roster of specialist sub-agents under Decepticon (recon → exploit → post-exploit → domain operators), the five-stage Vulnresearch pipeline (`scanner → detector → verifier → patcher → exploiter`), and the `blue_cell` defense agent for the Offensive Vaccine loop. Each agent starts with a **fresh context window** per objective — no accumulated noise, no context degradation. Findings persist to disk (`workspace/`) and the knowledge graph, not agent memory.

---

## Agent Roster

### Orchestrators

| Agent | Role |
|-------|------|
| **Decepticon** | Main red-team orchestrator. Reads the OPPLAN, dispatches objectives to specialist sub-agents, and tracks status transitions. Sub-agents: `recon`, `exploit`, `postexploit`, `analyst`, `reverser`, `contract_auditor`, `cloud_hunter`, `ad_operator`, `phisher`, `mobile_operator`, `wireless_operator`, `osint_operator`, `iot_operator`, `ics_operator`, `forensicator`, `supply_chain_operator`. |
| **Vulnresearch** | Vulnerability research orchestrator — runs the five-stage pipeline (`scanner → detector → verifier → patcher → exploiter`) with state passed between stages exclusively through the knowledge graph. |
| **Soundwave** | Engagement planner. Standalone graph (not a sub-agent of Decepticon). Interviews the operator and writes the eight-document engagement bundle — RoE, Threat Profile, CONOPS, Deconfliction, Contact, Data Handling, Abort, Cleanup. The orchestrator builds the OPPLAN. |

### Reconnaissance

| Agent | Role |
|-------|------|
| **Recon** | Port scanning, service enumeration, DNS, subdomain discovery, OSINT. Populates the knowledge graph with hosts and services. |

### Vulnerability Research Pipeline

Sub-agents of the **Vulnresearch** orchestrator. State flows between stages via the knowledge graph; each stage runs with fresh context.

| Stage | Agent | Output |
|-------|-------|--------|
| Discovery | **Scanner** | Vulnerability candidates with CVE/CVSS |
| Analysis | **Detector** | Confidence-rated findings, detection rules |
| Confirmation | **Verifier** | Verified findings (2+ methods for CRITICAL/HIGH) |
| Exploitation | **Exploiter** | Working proof-of-concept |
| Remediation | **Patcher** | Patch code or configuration fix |

### Exploitation & Post-Exploitation

| Agent | Role |
|-------|------|
| **Exploit** | Initial access and exploitation tactics. Web/AD attacks (SQLi, SSTI, Kerberoasting, ADCS abuse, credential attacks). |
| **Post-Exploit** | Privilege escalation, lateral movement, credential harvesting, persistence. Operates via C2 sessions once initial access is established. |

### Domain Specialists

| Agent | Role |
|-------|------|
| **AD Operator** | Active Directory attacks — Kerberoasting, AS-REP roasting, ADCS ESC1-ESC15, DCSync, BloodHound path analysis. |
| **Cloud Hunter** | Cloud infrastructure attacks — IAM privilege escalation, S3 bucket exposure, Kubernetes RBAC escapes, metadata service abuse. |
| **Contract Auditor** | Solidity / EVM smart contract audits — reentrancy, oracle manipulation, flash loan abuse, access control. |
| **Reverser** | Binary analysis and reverse engineering — ELF/PE/Mach-O triage, packer detection, ROP gadget inventories, and REA/Ghidra static analysis when its optional workload is configured. [REA reversing](features/rea-reversing.md). |
| **Analyst** | Vulnerability research and reporting — source code review, static analysis (semgrep/bandit/gitleaks), dependency CVE sweeps, multi-hop exploit chain construction. |
| **Phisher** | Initial-access via phishing / social engineering (MITRE T1566.\*) — GoPhish campaigns, evilginx2 MFA-bypass token capture, M365/O365 OAuth device-code harvest, lookalike domains, pretext engineering. Mandatory lure deconfliction with the blue team before sending. |
| **Mobile Operator** | Android / iOS application attacks — static analysis (apktool/jadx/class-dump), dynamic instrumentation (frida/objection), SSL pinning + root/jailbreak bypass, exported-component abuse, WebView JS bridge exploitation, MobSF. |
| **Wireless Operator** | Wi-Fi / BLE / Zigbee / sub-GHz attacks — WPA2 handshake / PMKID capture, WPA3-SAE downgrade, WPA-Enterprise evil-twin, KARMA / Mana, deauth, WPS Pixie Dust, BLE GATT, Zigbee Touchlink, sub-GHz replay. Requires hardware passthrough or an SSH dropbox. |
| **OSINT Operator** | Passive open-source intelligence — domain/email/employee/breach/code-leak/infra footprinting. Read-only; feeds Recon and Exploit. |
| **IoT Operator** | IoT / embedded device attacks — firmware acquisition + binwalk extraction, hardcoded credentials, U-Boot / `/dev/mem`, and BLE/Zigbee/Z-Wave/sub-GHz/LoRaWAN radios. |
| **ICS Operator** | ICS / OT / SCADA attacks (Modbus, DNP3, S7comm, BACnet, OPC-UA). RoE-gated; read-only enumeration first, lab/canary-only for writes. |
| **Forensicator** | DFIR / purple-team validation — disk/memory/log/network timeline analysis, IOC extraction, attack→detection mapping. |
| **Supply Chain Operator** | Software supply-chain attacks — dependency confusion, typosquatting, malicious packages (npm/PyPI/crates), CI/CD + build-step compromise. |

---

## Fresh Context Model

Every specialist agent runs with a **clean context window** for each objective:

- The orchestrator picks the next pending objective from the OPPLAN
- A new agent instance is spawned with only what it needs: the objective, RoE guard rails, and relevant findings from disk
- The agent executes, writes findings to `workspace/`, and returns a `PASSED` or `BLOCKED` signal
- The orchestrator updates the OPPLAN and moves to the next objective

This prevents context window bloat and token accumulation across a long engagement.

---

## Middleware Stack

Each agent assembles its middleware stack from a fixed set of named **slots** (`MiddlewareSlot`). The enum declaration order is the canonical assembly order — every factory walks the enum top-to-bottom and instantiates only the slots its role opts into via `SLOTS_PER_ROLE`. Plugins replace or disable slots by name (`PluginBundle.replaced_middleware` / `disabled_middleware`); slot definitions and the per-role mapping live in `decepticon_core.contracts.slots`, the langchain-bound factories in `decepticon.agents.middleware_slots`.

### Safety stack (every agent)

These slots are part of the base set every role receives — they are the engagement guard rails:

| Middleware | Slot | Purpose |
|------------|------|---------|
| `RoEGuardrailMiddleware` | `roe-guardrail` | Legal/safety gate on tool calls — extracts the target, evaluates it against the Rules of Engagement, and appends to a chained audit log (`<workspace>/audit/roe-decisions.jsonl`, or `DECEPTICON_ROE_AUDIT_PATH`). A fast-fail command-parse gate; the authoritative scope boundary is the egress layer (sandbox nftables, on by default for `enforce` mode, opt-out via `DECEPTICON_EGRESS_DISABLE`). The pre-rename name `RoEEnforcementMiddleware` / slot `roe-enforcement` stays as a compat alias until 2.0.0. |
| `UntrustedOutputMiddleware` | `untrusted-output` | Quarantines attacker-influenceable tool output (bash stdout, file reads, KG queries) inside a `<UNTRUSTED_TOOL_OUTPUT>` envelope so hostile content can't re-author the agent's instructions. |
| `PromptInjectionShieldMiddleware` | `prompt-injection-shield` | Deny-list wrap of attacker-controlled tool output (HTTP bodies, banners, file reads); dedups against `UNTRUSTED_OUTPUT` and does not re-inject the system policy. |
| `EventLogMiddleware` | `event-log` | Structured event logging for every model and tool call. |
| `BudgetEnforcementMiddleware` | `budget` | Enforces per-engagement spend caps (no-op when caps ≤ 0). |

Bash-executing agents and the orchestrator additionally get:

| Middleware | Slot | Purpose |
|------------|------|---------|
| `EngagementContextMiddleware` | `engagement-context` | Injects engagement metadata (slug, workspace) and per-challenge state (target URL, tags, flag format, mission brief) into every model call. Carries RoE scope into tool calls — it is **safety-critical**. |
| `SandboxNotificationMiddleware` | `sandbox-notification` | Tracks background-job completion and emits the CLI's `Background command` event. Bash agents only. |
| `HITLApprovalMiddleware` | `hitl-approval` | Operator-approval gate for high-impact actions (credential dumping, destructive ops). **Opt-in** — skipped unless `DECEPTICON_HITL__ENABLED` is set to a truthy value (off by default so engagements don't freeze waiting on a human). |

### Framework middleware

| Middleware | Slot | Purpose |
|------------|------|---------|
| `SkillsMiddleware` | `skills` | Loads `SKILL.md` frontmatter, filters by agent role, and injects matching skill descriptions into the system prompt. Full skill content is fetched on demand via the `load_skill` tool — `read_file` / `bash cat` **do not** resolve `/skills/*` (that tree is served in-process by a local `FilesystemBackend`, not the sandbox). |
| `FilesystemMiddleware` | `filesystem` | Provides `read_file`, `write_file`, `edit_file`, `ls`, `glob`, `grep` tools backed by the sandbox filesystem. Execute is intentionally stripped — agents use the dedicated `bash` tool for command execution. |
| `SubAgentMiddleware` | `subagent` | Allows orchestrators (Decepticon, Vulnresearch) to delegate objectives to specialist sub-agents via the `task()` tool. |
| `OPPLANMiddleware` | `opplan` | Injects the current OPPLAN progress table into every LLM call and provides CRUD tools for objective management. |
| `ModelOverrideMiddleware` | `model-override` | Per-objective model selection. Orchestrator only. |
| `ModelFallbackMiddleware` | `model-fallback` | Switches to a fallback model on provider outage, rate limit, or context overflow. Conditional — skipped when no fallback chain is configured. |
| `SummarizationMiddleware` | `summarization` | Auto-compacts conversation history when the context window approaches capacity. |
| `AnthropicPromptCachingMiddleware` | `prompt-caching` | Caches static system prompt content for Anthropic models to reduce token costs. Silently no-ops on non-Anthropic providers. |
| `PatchToolCallsMiddleware` | `patch-tool-calls` | Sanitizes and normalizes tool call formats for compatibility across model providers (e.g. repairs dangling tool calls). |

### Safety-critical slots

`SAFETY_CRITICAL_SLOTS` — `engagement-context`, `roe-guardrail`, `untrusted-output`, `prompt-injection-shield`, `sandbox-notification`, `hitl-approval` — can only be replaced or disabled by a plugin when `DECEPTICON_ALLOW_SAFETY_OVERRIDES=1` is set in the environment. The gate is enforced by `build_middleware` in `decepticon.agents.build`; without it, an override raises `SafetyOverrideViolation`. Replacement is fine if the new middleware honours the same contract — the gate exists so an accidentally-installed plugin can't silently subvert the safety story.

### Stack per Agent Role

Each diagram lists the slots a role instantiates, in `MiddlewareSlot` enum order (the order they wrap each call). `HITLApproval` appears in the bash-agent and orchestrator stacks but is opt-in — skipped at runtime unless `DECEPTICON_HITL__ENABLED` is truthy.

**Decepticon (Orchestrator)** — full stack with engagement context, sub-agent dispatch, and per-objective model override. No `SandboxNotification`.

```
EngagementContext → RoEEnforcement → HITLApproval → UntrustedOutput → PromptInjectionShield
                  → Skills → Filesystem → SubAgent → OPPLAN → EventLog → Budget → ModelOverride
                  → ModelFallback → Summarization → AnthropicPromptCaching → PatchToolCalls
```

**Vulnresearch (Orchestrator)** — base + sub-agent dispatch. No `EngagementContext` (it consumes its parent's context), no `ModelOverride`, no `HITLApproval`, no `SandboxNotification`.

```
RoEEnforcement → UntrustedOutput → PromptInjectionShield → Skills → Filesystem → SubAgent → OPPLAN
              → EventLog → Budget → ModelFallback → Summarization → AnthropicPromptCaching → PatchToolCalls
```

**Bash-executing specialists** (Recon, Exploit, Post-Exploit, Analyst, Reverser, Contract Auditor, Cloud Hunter, AD Operator, Phisher, Mobile Operator, Wireless Operator — and the plugin specialists Verifier, Patcher, Scanner, Exploiter) — the full bash-agent stack including `EngagementContext` and `SandboxNotification`.

```
EngagementContext → RoEEnforcement → HITLApproval → UntrustedOutput → PromptInjectionShield
                  → Skills → Filesystem → EventLog → SandboxNotification → Budget
                  → ModelFallback → Summarization → AnthropicPromptCaching → PatchToolCalls
```

These specialists also have the `bash` tool for command execution inside the sandbox; `FilesystemMiddleware` covers all file I/O.

**Detector (read-only plugin specialist)** — base set only. No `bash`, no `EngagementContext`, no `SandboxNotification`, no `HITLApproval`.

```
RoEEnforcement → UntrustedOutput → PromptInjectionShield → Skills → Filesystem → EventLog → Budget
              → ModelFallback → Summarization → AnthropicPromptCaching → PatchToolCalls
```

**Soundwave (Planner)** — base set + `EngagementContext`. No `bash`, no sub-agents, no `SandboxNotification`, no `HITLApproval` (document generation only).

```
EngagementContext → RoEEnforcement → UntrustedOutput → PromptInjectionShield → Skills → Filesystem
                  → EventLog → Budget → ModelFallback → Summarization → AnthropicPromptCaching → PatchToolCalls
```
