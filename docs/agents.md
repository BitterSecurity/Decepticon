# Agents

Decepticon combines stable specialist capabilities with selected business workflows. Decepticon owns execution OPPLAN, Soundwave is an optional standalone planner, and the optional Vulnresearch plugin retains its separate legacy pipeline. Blue Cell investigates local sensor evidence; the broader Offensive Vaccine loop remains planned. See the [extension contract](design/extension-contract.md). Each agent starts with a **fresh context window** per objective — no accumulated noise, no context degradation. Findings persist to disk (`workspace/`) and the knowledge graph, not agent memory.

---

## Agent Roster

### Orchestrators

| Agent | Role |
|-------|------|
| **Decepticon** | Main red-team orchestrator. Reads the OPPLAN, dispatches objectives to specialist sub-agents, and tracks status transitions. Sub-agents: `recon`, `exploit`, `postexploit`, `analyst`, `reverser`, `contract_auditor`, `cloud_hunter`, `ad_operator`, `phisher`, `mobile_operator`, `wireless_operator`, `osint_operator`, `iot_operator`, `ics_operator`, `forensicator`, `supply_chain_operator`, `finding_verifier`, `finding_reporter`. |
| **Vulnresearch** | Vulnerability research orchestrator — runs the five-stage pipeline (`scanner → detector → verifier → patcher → exploiter`) with state passed between stages exclusively through the knowledge graph. |
| **Soundwave** | Engagement planner. Standalone graph (not a sub-agent of Decepticon). Uses an optional planning workflow to discuss and prepare seven documents alongside the operator-provisioned, read-only RoE. Publication does not switch to Red. The execution orchestrator builds OPPLAN. |

### Additional standard and optional graphs

- `autohunt` is a separately registered bootstrap planner, reusing Soundwave's
  document-only contracts. It is not a mandatory precondition for normal Red.
- `finding_verifier` independently adjudicates findings using reproduction and
  equivalent negative controls. `finding_reporter` writes the canonical report
  after verification; it has no shell tool.
- Blue mode selects the external AutoBot `autobot` graph when its package and
  sensor are configured. It is not a built-in Decepticon blue_cell factory.

The current [graph registry](../packages/decepticon/decepticon/graph_registry.py)
and package entry points define availability; a fixed historical agent count is
not a discovery API.

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
| `TimeWindowMiddleware` | `time-window` | Ends a run outside the RoE `authorized_windows` / inside `blackout_windows`: checked before the run starts and before every model call. Orchestrator and target-acting specialists only (not part of the base set). Network scope is enforced by the OpenShell sandbox policy, not by middleware. |
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
| `ModelOverrideMiddleware` | `model-override` | Session/per-objective model selection; included in the common tail slots. |
| `ModelFallbackMiddleware` | `model-fallback` | Switches to a fallback model on provider outage, rate limit, or context overflow. Conditional — skipped when no fallback chain is configured. |
| `SummarizationMiddleware` | `summarization` | Auto-compacts conversation history when the context window approaches capacity. |
| `AnthropicPromptCachingMiddleware` | `prompt-caching` | Caches static system prompt content for Anthropic models to reduce token costs. Silently no-ops on non-Anthropic providers. |
| `PatchToolCallsMiddleware` | `patch-tool-calls` | Sanitizes and normalizes tool call formats for compatibility across model providers (e.g. repairs dangling tool calls). |

### Safety-critical slots

`SAFETY_CRITICAL_SLOTS` — `engagement-context`, `time-window`, `untrusted-output`, `prompt-injection-shield`, `sandbox-notification`, `opscontrol-notification`, `hitl-approval` — can only be replaced or disabled by a plugin when `DECEPTICON_ALLOW_SAFETY_OVERRIDES=1` is set in the environment. The gate is enforced by `build_middleware` in `decepticon.agents.build`; without it, an override raises `SafetyOverrideViolation`. Replacement is fine if the new middleware honours the same contract — the gate exists so an accidentally-installed plugin can't silently subvert the safety story.

### Assembly and role differences

The [slot map](../packages/decepticon-core/decepticon_core/contracts/slots.py)
and [default factories](../packages/decepticon/decepticon/agents/middleware_slots.py)
are the current assembly contract. Older diagrams omitted KG, opscontrol
notifications, proxy-key overrides and model overrides on specialists.

| Role family | Distinct slots/behavior |
| --- | --- |
| Decepticon | Engagement context, subagent dispatch, OPPLAN, optional HITL and opscontrol notifications |
| Bash specialists | Engagement context, sandbox notifications and optional HITL |
| Analyst, AD Operator, Contract Auditor | Bash specialist stack plus conditional KG middleware |
| Soundwave and finding reporter | Engagement context without bash/HITL/sandbox notifications |
| Vulnresearch | Common stack plus subagent dispatch and OPPLAN |
| Detector | Common read-only stack without bash/engagement context |

Common tail slots include model override/fallback, per-run proxy-key override,
summarization, prompt caching and tool-call repair. Individual factories may add
middleware such as StepBudget beyond slot assembly. Skillogy normally replaces
the local SkillsMiddleware through `maybe_install_skillogy`; an explicit opt-out
retains local skill reads. See [Skillogy](skillogy.md).

The legacy `blue_cell` slot definition is not proof of an installed Blue graph;
current Blue runtime ownership is [AutoBot](features/blue-cell.md). Runtime tool
availability, workflow pins and authorization must be checked on the actual role.
