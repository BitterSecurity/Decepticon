---
name: dfir-overview
description: >
  Use to close the Offensive Vaccine loop on the defender side. The Detector
  agent produces Sigma / YARA rules from offensive operations; this catalog
  validates those rules against real memory dumps, event logs, and forensic
  artifacts using Volatility 3, plaso, and sigma-cli. Without this catalog,
  detection rules are theoretical.
metadata:
  skillogy:
    version: 1
  subdomain: dfir
  when_to_use: "dfir validation sigma yara volatility plaso memory dump event log forensic artifact detection rule blue team"
  tags: dfir, memory, volatility, plaso, sigma, validation, blue-team
  upstream_ref: "Sigma rules + YARA + Volatility 3 + plaso — detection-rule validation harness for the Offensive Vaccine loop"
---

# Forensicator / DFIR Skill Catalog

Decepticon emits attacks AND detection rules. This catalog feeds the
detection rules back through real forensic artifacts to confirm they fire
— closing the Offensive Vaccine loop on the operations side.

## Playbooks

> **Inline technique reference — not separately loadable skills.** The entries below
> are summarized here for direct use; there is no separate `SKILL.md` to open for
> each. Do NOT call the skill loader on them — apply the technique with your tools
> using this summary and the validation guidance in this file.


| Technique | Use for |
|---|---|
| **volatility-windows** | Volatility 3 Windows plugins: pslist, malfind, cmdline, netscan, dlllist, handles |
| **volatility-linux** | Volatility 3 Linux: linux.pslist, linux.bash, linux.malfind |
| **plaso-timeline** | psort + log2timeline; super-timeline construction; Sigma matchers on the timeline |
| **sigma-cli-validation** | sigma-cli convert + match against captured event logs |
| **yara-scan** | yara-x scan against memory dumps and disk images |
| **event-log-mining** | Windows Event Log (.evtx) extraction + key event ID reference |
| **etw-trace** | ETW provider triage; .etl file extraction |
| **edr-validation** | Replay an attack against a target with Velociraptor / OSQuery active; capture artifacts |

## Detection validation

- Collect the event log from the relevant host at attack time, along with
  the detection rule and expected event pattern (for example, event 4662
  with the relevant `ControlAccessRights`).
- Run `sigma-cli convert --target sqlite` and match against the log file.
  A zero match count requires checking the rule and artifact coverage;
  matches provide evidence that the rule detects the captured activity.
- Record the match count and supporting forensic artifacts in the
  engagement knowledge graph.
- For patch-regression validation, verify whether the rule still fires
  on attempted exploitation of the patched build.

## Tools sandbox

- Volatility 3 (`vol`, `volshell`) — already in operator's AGENTS.md tooling.
- plaso (`log2timeline`, `psort`).
- sigma-cli (`sigma convert`, `sigma check`).
- yara-x (`yr`) — operator already has it installed at `C:\Tools\yara-x\yr.exe`.
- Velociraptor + OSQuery (for live-system validation paths).

## Why this is differentiating

Strix doesn't have this. XBOW doesn't have this. The "Offensive Vaccine"
promise (every attack becomes a defense improvement) is only deliverable
when someone validates the detection. Without Forensicator, the loop
stops at "rule written". With it, the loop completes at "rule verified to
fire on this attack class against this client's stack".
