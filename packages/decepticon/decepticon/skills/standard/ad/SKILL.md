---
name: ad-overview
description: Active Directory attack lane — BloodHound ingestion, Kerberoasting, ADCS ESC scanning, DCSync, LAPS extraction.
metadata:
  subdomain: active-directory
  when_to_use: "active directory ad attack lane overview routing bloodhound kerberoast adcs dcsync laps domain compromise"
  mitre_attack:
    - T1078.002
    - T1558.003
    - T1558.004
    - T1003.006
    - T1649
    - T1555
  capability_contract:
    lane: active-directory
    scope: isolated-lab
    environment: [resettable-ad-lab, isolated-network]
    required_tools: [bloodhound-ce, certipy, netexec]
    evidence: [attack-path-query, lab-replay, remediation-check]
    verification: "replay the exact path in a freshly reset authorized lab"
    negative_control: "confirm the path fails after the remediated control is applied"
    scorecard: [verified-path-rate, noisy-action-rate, remediation-correctness]
    benchmark: dreadgoad
---

# AD Operator Skill Catalog

## Playbooks
| Skill | Use for |
|---|---|
| `/skills/standard/ad/bloodhound-query/SKILL.md` | Authorized collection upload, coverage, object and path analysis |
| `/skills/standard/ad/kerberoasting/SKILL.md`    | Roast SPN users, crack with hashcat |
| `/skills/standard/ad/asrep-roasting/SKILL.md`   | dontreqpreauth users |
| `/skills/standard/ad/adcs-esc1/SKILL.md`        | ESC1 template abuse → domain admin |
| `/skills/standard/ad/dcsync/SKILL.md`           | Replication rights → krbtgt dump |
| `/skills/standard/ad/laps/SKILL.md`             | LAPS local admin password extraction |
| `/skills/standard/ad/netexec/SKILL.md`          | NetExec (formerly CrackMapExec) cheatsheet — SMB/WinRM/LDAP/MSSQL modules |

## Workflow

1. Check the actual authorized domain, collection access, and active BloodHound runtime. In hosted engagements, delegate AD analysis to the AD operator and use the engagement-isolated `bloodhound_mcp_*` tools. Standalone OSS may instead expose `bhce_*` tools for its local instance.
2. Upload a compatible collection ZIP and verify successful completion. A discovered hostname alone does not populate BloodHound. Inspect collection coverage before interpreting absent results.
3. Use object profiles, exposure search, and bounded shortest paths to identify candidates. Treat replication rights, SPNs, and pre-authentication settings as leads until independently validated.
4. Record evidence and update executable objectives through the active OPPLAN tools. BloodHound graph results alone do not authorize target-facing work or prove compromise.
