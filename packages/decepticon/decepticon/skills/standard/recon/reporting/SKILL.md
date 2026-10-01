---
name: recon-reporting
description: "Recon output formatting — report structure, CVSS v4.0 scoring (primary), MITRE ATT&CK mapping, finding prioritization, Markdown output, detection gap tracking, handoff checklists."
allowed-tools: Read Write
metadata:
  subdomain: reporting
  kind: reporting
  when_to_use: "generate report, write report, summarize findings, CVSS score, prioritize findings, recon report, final report, handoff"
  tags: report, cvss, findings, mitre-mapping, handoff, detection-gap, purple-team
  mitre_attack:
---

# Reconnaissance Reporting Knowledge Base

Effective reconnaissance is only as valuable as the intelligence it communicates. This skill defines how to structure, prioritize, and present findings for actionable handoff to the next engagement phase.

All agent-authored documents MUST be Markdown format (`.md`). Scan output files (`.txt`, `.xml`) are operational data and are not documents.

## 1. Report Structure

Every recon engagement produces a structured report with these sections:

### Executive Summary
A 2-3 sentence overview of what was found, the overall attack surface size, and the most critical findings.

### Target Overview
| Field | Value |
|-------|-------|
| Primary Domain | example.com |
| Scope | *.example.com, 10.0.0.0/24 |
| Engagement Type | External Recon |
| Recon Duration | Passive: X min, Active: Y min |

## 2. Finding Document Template

Each significant verified finding gets its own Markdown file in `findings/` named by canonical ID: `findings/FIND-{NNN}.md`. Do not create placeholder finding files. Use this template:

````markdown
---
id: FIND-001
severity: CRITICAL
cvss_score: 9.3
cvss_vector: "CVSS:4.0/AV:N/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:H/SC:N/SI:N/SA:N"
cwe: CWE-306
mitre: T1595.001
affected_target: api.example.com:3306
confidence: confirmed
objective_id: OBJ-REC-006
phase: recon
agent: recon-agent-run-42
detected: false
remediation_priority: immediate
discovered_at: 2026-04-06T14:30:00Z
---

# [CRITICAL] Exposed MySQL Database on api.example.com:3306 allows Unauthenticated Access

## Description

MySQL 5.7.42 is accessible from the internet on `api.example.com:3306` without network-level access controls. Any party that can reach this host can attempt authentication directly against the database engine.

## Steps to Reproduce

1. From an external IP, run:
   ```
   nmap -sV -p 3306 api.example.com
   ```
2. Observe that port 3306 is open and the MySQL 5.7.42 banner is returned.
3. Attempt connection with no credentials:
   ```
   mysql -h api.example.com -u root
   ```
4. Observe whether login succeeds or fails with an authentication error (both indicate the port is reachable).

## Impact

Direct internet exposure of the MySQL port enables brute-force, credential stuffing, and exploitation of any MySQL CVE applicable to version 5.7.42. Successful authentication yields full database read/write access to application data.

## Evidence

| Type | Path | Description |
|------|------|-------------|
| Scan output | findings/evidence/FIND-001_nmap.txt | nmap -sV -p 3306 full output |
| Screenshot | findings/evidence/FIND-001_mysql_banner.txt | Raw MySQL banner response |

## Detection Gap Analysis

| Control | Fired? | Notes |
|---------|--------|-------|
| Firewall / ACL | No | Port 3306 reachable from external IP |
| SIEM alert | Unknown | No alert observed during scan window |
| IDS/IPS | No | Scan completed without interruption |
| WAF | N/A | Not applicable to TCP/MySQL traffic |

Blue team detection status: **Not detected**. The reconnaissance activity produced no observable defensive response.

## Remediation

1. Add a firewall rule to block inbound TCP 3306 from all sources except application servers.
2. Upgrade MySQL to the latest 8.x release (5.7 is EOL).
3. Audit MySQL user accounts and remove anonymous/root remote logins.
4. Enable MySQL audit logging.

## References

- CVE database for MySQL 5.7: https://www.cvedetails.com/product/3300/
- CIS MySQL Benchmark: https://www.cisecurity.org/benchmark/mysql
- MITRE ATT&CK T1595.001: https://attack.mitre.org/techniques/T1595/001/
````

## 3. Finding Categories

### A. Domain & Subdomain Inventory
```markdown
| Subdomain | IP Address | Status | Notes |
|-----------|-----------|--------|-------|
| www.example.com | 93.184.216.34 | Active | Main site, Cloudflare CDN |
| api.example.com | 10.0.1.50 | Active | REST API, no WAF detected |
| dev.example.com | 10.0.1.51 | Active | Development server, potential target |
| old.example.com | — | NXDOMAIN | Decommissioned |
| staging.example.com | CNAME → *.herokuapp.com | Dangling | Subdomain takeover candidate |
```

### B. DNS & Infrastructure Map
```markdown
| Record Type | Value | Analysis |
|-------------|-------|----------|
| A | 93.184.216.34 | Primary web server |
| MX | aspmx.l.google.com (pri 10) | Google Workspace email |
| NS | ns1.cloudflare.com | Cloudflare DNS hosting |
| TXT (SPF) | v=spf1 include:_spf.google.com ~all | Soft fail SPF |
| TXT (DMARC) | v=DMARC1; p=none | DMARC not enforced |
| CAA | 0 issue "letsencrypt.org" | Only Let's Encrypt can issue certs |
```

### C. Open Ports & Services
```markdown
| IP | Port | Protocol | Service | Version | Risk Notes |
|----|------|----------|---------|---------|------------|
| 10.0.1.50 | 22 | TCP | SSH | OpenSSH 8.9p1 | Current version |
| 10.0.1.50 | 80 | TCP | HTTP | nginx 1.18.0 | Outdated (CVE potential) |
| 10.0.1.50 | 443 | TCP | HTTPS | nginx 1.18.0 | TLS 1.2, missing HSTS |
| 10.0.1.50 | 3306 | TCP | MySQL | 5.7.42 | Exposed database port |
| 10.0.1.51 | 8080 | TCP | HTTP | Apache Tomcat 9.0.65 | Dev server, default page |
```

### D. Technology Stack
```markdown
| Layer | Technology | Evidence |
|-------|-----------|----------|
| CDN | Cloudflare | CF-RAY header, NS records |
| Web Server | nginx 1.18.0 | Server header |
| Backend | PHP 8.1 | X-Powered-By header |
| CMS | WordPress 6.x | /wp-content/ paths |
| Database | MySQL 5.7 | Port 3306 open, banner |
| Email | Google Workspace | MX records |
| DNS | Cloudflare | NS records |
```

### E. Vulnerability Scan Results
```markdown
| Source | Target | Finding | Severity | Template/CVE |
|--------|--------|---------|----------|--------------|
| nuclei | api.example.com | Exposed .env file | CRITICAL | exposure-env |
| nuclei | dev.example.com | Git config disclosure | HIGH | git-config |
| nikto | www.example.com | X-Frame-Options missing | MEDIUM | — |
| nmap | 10.0.1.50:443 | TLS 1.0 supported | MEDIUM | ssl-enum-ciphers |
```

## 4. CVSS Scoring

### CVSS 4.0 (Primary)

CVSS 4.0 is the primary scoring system for all findings. Use CVSS 3.1 only for dual-reporting when client systems require it.

#### Score Ranges

| Score | Severity | Response |
|-------|----------|----------|
| 9.0 – 10.0 | Critical | Immediate remediation required |
| 7.0 – 8.9 | High | Remediate within days |
| 4.0 – 6.9 | Medium | Remediate within weeks |
| 0.1 – 3.9 | Low | Remediate within quarter |
| 0.0 | None | Informational |

#### CVSS 4.0 Metric Groups

**Base (BTE — required):**
- `AV` Attack Vector: N (Network) / A (Adjacent) / L (Local) / P (Physical)
- `AC` Attack Complexity: L (Low) / H (High)
- `AT` Attack Requirements: N (None) / P (Present) — replaces Scope concept
- `PR` Privileges Required: N / L / H
- `UI` User Interaction: N (None) / P (Passive) / A (Active)
- `VC/VI/VA` Vulnerable System: Confidentiality / Integrity / Availability — N/L/H
- `SC/SI/SA` Subsequent System: Confidentiality / Integrity / Availability — N/L/H

**Threat (T — optional, adjusts exploitability):**
- `E` Exploit Maturity: X (Not Defined) / A (Attacked) / P (POC) / U (Unreported)

**Environmental (E — optional, adjusts for deployment context):**
- `CR/IR/AR` Confidentiality/Integrity/Availability Requirements
- `MAV/MAC/MAT/MPR/MUI` Modified Base metrics

**Supplemental (S — informational only, no score impact):**
- `AU` Automatable: Y/N — can the attack be scripted at scale?
- `R` Recovery: A (Automatic) / U (User) / I (Irrecoverable)
- `V` Value Density: D (Diffuse) / C (Concentrated)
- `RE` Response Effort: L (Low) / M (Moderate) / H (High)
- `U` Provider Urgency: Clear / Green / Amber / Red

#### Common Recon Finding CVSS 4.0 Scores

| Finding | CVSS 4.0 | Vector |
|---------|----------|--------|
| Exposed database port (MySQL/Postgres) | 9.3 | AV:N/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:H/SC:N/SI:N/SA:N |
| Subdomain takeover | 8.7 | AV:N/AC:L/AT:N/PR:N/UI:N/VC:H/VI:N/VA:N/SC:H/SI:N/SA:N |
| .env file exposure | 8.7 | AV:N/AC:L/AT:N/PR:N/UI:N/VC:H/VI:N/VA:N/SC:N/SI:N/SA:N |
| Git config disclosure | 7.5 | AV:N/AC:L/AT:N/PR:N/UI:N/VC:H/VI:N/VA:N/SC:N/SI:N/SA:N |
| Directory listing enabled | 6.9 | AV:N/AC:L/AT:N/PR:N/UI:N/VC:L/VI:N/VA:N/SC:N/SI:N/SA:N |
| Missing security headers | 4.3 | AV:N/AC:L/AT:N/PR:N/UI:P/VC:N/VI:L/VA:N/SC:N/SI:N/SA:N |
| Information disclosure (version) | 6.9 | AV:N/AC:L/AT:N/PR:N/UI:N/VC:L/VI:N/VA:N/SC:N/SI:N/SA:N |
| DMARC not enforced | 4.0 | AV:N/AC:H/AT:P/PR:N/UI:N/VC:N/VI:L/VA:N/SC:N/SI:N/SA:N |

#### Dual Reporting (when client requires CVSS 3.1 as well)

Include both scores in the finding frontmatter and the body:

```markdown
- **CVSS 4.0**: 9.3 (AV:N/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:H/SC:N/SI:N/SA:N)
- **CVSS 3.1**: 9.8 (AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H)
```

Set `cvss_vector` in the YAML frontmatter to the CVSS 4.0 vector. Add `cvss_v31_score` and `cvss_v31_vector` as additional frontmatter fields if dual-reporting.

## 5. Evidence Management

### Storage Layout

Raw scan evidence is stored separately from finding documents:

```
findings/
├── FIND-001.md                       # Finding document (Markdown; id in frontmatter)
├── FIND-002.md
└── evidence/
    ├── FIND-001_nmap.txt              # Raw nmap output
    ├── FIND-001_mysql_banner.txt      # Raw banner grab
    ├── FIND-002_nuclei.txt            # Raw nuclei output
    └── FIND-002_curl_env.txt          # Raw HTTP response
recon/
├── recon_<target>_passive.txt
├── recon_<target>_subdomains.txt
├── httpx_<target>.txt
├── nmap_<target>_<scan_type>.txt
├── nmap_<target>_<scan_type>.xml
├── ffuf_<target>.txt
└── nuclei_<target>.txt
```

### Evidence File Rules

- Evidence files in `findings/evidence/` are plain text dumps of tool output, not Markdown.
- Name format: `FIND-{NNN}_{tool}.txt` where `{tool}` is the tool that produced the output (e.g., `nmap`, `nuclei`, `curl`, `httpx`).
- Multiple evidence files per finding are allowed; add one row per file to the Evidence table in the finding document.
- Do not truncate evidence files — preserve full raw output for re-analysis.

### Evidence Table Format

In the finding document, list evidence as a table:

```markdown
## Evidence

| Type | Path | Description |
|------|------|-------------|
| Scan output | findings/evidence/FIND-001_nmap.txt | nmap -sV -p 3306 full output |
| Banner grab | findings/evidence/FIND-001_mysql_banner.txt | Raw MySQL server banner |
```

## 6. Detection Gap Analysis (Purple Team / TIBER-EU)

Every finding document MUST include a Detection Gap Analysis section. This is a core red team deliverable that distinguishes operational red teaming from standard penetration testing.

### Purpose

Record whether the blue team (SOC/SIEM/IDS) detected the reconnaissance activity that led to this finding. This data feeds:
- TIBER-EU and CBEST purple team exercises
- SOC maturity assessments
- Control gap remediation roadmaps

### Detection Gap Table

```markdown
## Detection Gap Analysis

| Control | Fired? | Notes |
|---------|--------|-------|
| Firewall / ACL | No | Port 3306 reachable from external IP |
| SIEM alert | Unknown | No alert observed during scan window |
| IDS/IPS | No | Scan completed without interruption |
| WAF | N/A | Not applicable to TCP/MySQL traffic |
| EDR on host | Unknown | No endpoint visibility from external position |
```

- **Fired?** values: `Yes`, `No`, `Unknown`, `N/A`
- Use `Unknown` when detection cannot be confirmed or denied from the attacker position
- Use `N/A` when the control is not applicable to the attack vector

### Detection Status Line

After the table, state the overall detection outcome explicitly:

```markdown
Blue team detection status: **Not detected**. The reconnaissance activity produced no observable defensive response during the scan window (2026-04-06T14:00–14:45Z).
```

Or if detected:

```markdown
Blue team detection status: **Detected**. SIEM alert fired at 14:22Z, approximately 8 minutes after scanning began. Scanning was not interrupted, indicating detection-without-response gap.
```

## 7. MITRE ATT&CK Mapping

### Reconnaissance Tactics (TA0043)

| Technique ID | Name | Recon Activity |
|-------------|------|---------------|
| T1595.001 | Active Scanning: IP Blocks | nmap port scanning |
| T1595.002 | Active Scanning: Vulnerability Scanning | nuclei, nikto scans |
| T1595.003 | Active Scanning: Wordlist Scanning | ffuf, gobuster |
| T1592.001 | Gather Victim Host Info: Hardware | OS fingerprinting (-O) |
| T1592.002 | Gather Victim Host Info: Software | Service version detection (-sV) |
| T1593.001 | Search Open Websites: Social Media | OSINT gathering |
| T1593.002 | Search Open Websites: Search Engines | Google dorking |
| T1596.001 | Search Open Technical Databases: DNS/Passive DNS | dig, subfinder, amass |
| T1596.002 | Search Open Technical Databases: WHOIS | whois lookups |
| T1596.003 | Search Open Technical Databases: Digital Certificates | crt.sh CT log queries |

Use the `mitre` field in finding frontmatter for the primary technique. List secondary techniques in the References section of the finding document.

## 8. Finding Prioritization

### Priority Levels

| Priority | Criteria | Example |
|----------|----------|---------|
| **CRITICAL** | Immediate exploitation potential, CVSS 4.0 ≥ 9.0 | Exposed database, default creds, subdomain takeover |
| **HIGH** | Known CVE or significant misconfiguration, CVSS 7.0–8.9 | Outdated service with public exploit, missing auth |
| **MEDIUM** | Information disclosure or weak configuration, CVSS 4.0–6.9 | Verbose error pages, missing security headers |
| **LOW** | Informational or hardening recommendation, CVSS < 4.0 | DMARC not enforced, older TLS ciphers |

### Prioritized Findings List Format

The consolidated report's findings section groups by priority:

```markdown
## Critical Findings

### FIND-001 — [CRITICAL] Exposed MySQL on api.example.com:3306
- **CVSS 4.0**: 9.3 (AV:N/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:H/SC:N/SI:N/SA:N)
- **MITRE ATT&CK**: T1595.001
- **Evidence**: nmap -sV confirms MySQL 5.7.42 open from internet
- **Risk**: Unauthenticated database access
- **Recommendation**: Firewall TCP 3306, upgrade MySQL to 8.x
- **Full finding**: [findings/FIND-001.md](findings/FIND-001.md)

## High Findings

### FIND-002 — [HIGH] Dangling CNAME — staging.example.com
- **CVSS 4.0**: 8.7 (AV:N/AC:L/AT:N/PR:N/UI:N/VC:H/VI:N/VA:N/SC:H/SI:N/SA:N)
- **MITRE ATT&CK**: T1596.001
- **Evidence**: CNAME points to deprovisioned Heroku app
- **Risk**: Subdomain takeover enables phishing and cookie theft
- **Recommendation**: Remove DNS record or reclaim the Heroku app
- **Full finding**: [findings/FIND-002.md](findings/FIND-002.md)
```

## 9. Attack Chain Analysis

### Identifying Exploit Chains

Individual findings combine into attack chains. Document these explicitly — they represent real-world risk better than isolated scores.

```markdown
## Attack Chain: Unauthenticated Database Access

**Chain**: Subdomain discovery → Exposed MySQL → Credential extraction
**Combined Risk**: CRITICAL

1. Passive recon discovered `db.example.com` via CT logs (T1596.003)
2. Active scan confirmed MySQL 5.7.42 on port 3306, internet-facing (T1595.001)
3. No authentication required for `root` user (empty password)
4. Database contains PII for ~50,000 users

**Impact**: Full database compromise without any credentials
**Remediation**: Firewall MySQL port, set root password, audit access logs
**Detection gap**: Neither step 1 nor step 2 triggered a SOC alert
```

### Chain Severity Escalation

| Individual Findings | Severity Alone | Chained Severity |
|-------------------|---------------|-----------------|
| Directory listing + .env exposure | Medium + High | CRITICAL (credentials leaked) |
| Subdomain takeover + cookie scope | High + Medium | CRITICAL (session hijack) |
| SSRF + cloud metadata | Medium + N/A | CRITICAL (IAM credential theft) |
| Weak TLS + HSTS missing | Low + Low | Medium (downgrade attack viable) |

## 10. File Management

### Directory Layout

```
/workspace/
├── plan/
│   ├── roe.json                          # Rules of Engagement (read-only)
│   ├── conops.json                       # Threat actor profile
│   └── opplan.json                       # Objectives tracker
├── timeline.jsonl                         # Append-only activity/finding timeline
├── recon/
│   └── report_<target>.md                # Consolidated recon report
├── findings/
│   ├── FIND-001.md                       # Individual finding (Markdown; id in frontmatter)
│   ├── FIND-002.md
│   └── evidence/
│       ├── FIND-001_nmap.txt              # Raw scan output (plain text)
│       └── FIND-002_nuclei.txt
└── recon/
    ├── recon_<target>_passive.txt
    ├── recon_<target>_subdomains.txt
    ├── httpx_<target>.txt
    ├── nmap_<target>_<scan_type>.txt
    ├── nmap_<target>_<scan_type>.xml
    ├── ffuf_<target>.txt
    └── nuclei_<target>.txt
```

### Naming and Persistence Rules

- Finding documents: `findings/FIND-{NNN}.md` (e.g., `findings/FIND-001.md`)
- The file name and `id` field in YAML frontmatter (`FIND-001`, `FIND-002`, ...) are the canonical cross-reference
- Evidence artifacts: `findings/evidence/FIND-{NNN}_{tool}.txt` (keyed by finding ID)
- Raw scan data: `recon/` directory, named by tool and target
- Always save scan output with `-oN` (nmap), `-o` (subfinder/nuclei/httpx), or output flags (ffuf)
- Keep raw data — the final report synthesizes, but raw data enables re-analysis

## 11. OPPLAN Feedback Loop

After generating the report, return the objective IDs, evidence paths, and proposed follow-up work to the orchestrator. Do not edit `plan/opplan.json`: the orchestrator owns OPPLAN mutations through `get_objective`, `update_objective`, and `add_objective`.

### Update Completed Objectives

For each recon objective, report whether its acceptance criteria were met, cite the supporting evidence, and explain any blocked or partial work. The orchestrator records the appropriate status and evidence summary with `update_objective` after reviewing the handoff.

### Create Follow-Up Objectives

When the report reveals new targets or attack paths not in the original OPPLAN:
1. Propose follow-up objectives to the orchestrator; do not assign IDs or insert them into the plan yourself.
2. Include the finding ID, target, suggested priority, scope and OPSEC checks, and evidence-based acceptance criteria.
3. Identify prerequisite objective IDs separately from finding IDs. The orchestrator validates scope and adds authorized work with `add_objective(blocked_by=[...])`.

### Report → OPPLAN Mapping

```markdown
| Report Finding | Completed Objective | New Objective (if authorized) |
|----------------|--------------------|-----------------------------|
| FIND-001: Exposed MySQL (CRITICAL, CVSS 9.3) | OBJ-REC-006 (port scan) | OBJ-EXP-001 (test MySQL default credentials) |
| FIND-002: Dangling CNAME (HIGH, CVSS 8.7) | OBJ-REC-003 (subdomain enum) | OBJ-EXP-002 (attempt subdomain takeover) |
```

## 12. Handoff Checklist

Before concluding reconnaissance and handing off to the exploitation phase:

- [ ] All subdomains enumerated and resolved
- [ ] DNS infrastructure fully mapped
- [ ] All in-scope IPs port-scanned with service versions
- [ ] Technology stack identified for key assets
- [ ] Vulnerability scan (nuclei) run on all live web targets
- [ ] Each significant finding has a `findings/FIND-{NNN}.md` document
- [ ] All findings scored with CVSS 4.0 (v3.1 added if dual-reporting required)
- [ ] Findings mapped to MITRE ATT&CK techniques
- [ ] Detection gap analysis completed for each finding
- [ ] Findings prioritized by exploitability and chained risk
- [ ] Raw evidence saved to `findings/evidence/` with correct naming
- [ ] Raw scan data preserved in `recon/` directory
- [ ] Attack chains documented in consolidated report
- [ ] Consolidated recon report saved to `recon/report_<target>.md`
- [ ] Objective IDs, results, evidence paths, and follow-up proposals returned to the orchestrator
- [ ] No direct OPPLAN file edits made by the specialist
