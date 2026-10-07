# Objective Templates — Recon Phase

Copy and customize these objective rows for a single `commit_opplan` DAG submission.
Replace `<TARGET>` with the actual target. Omit `id`; the server issues each UUID.
Add `blocked_by` using submitted row positions where a prerequisite is required.

## Passive Recon Objectives (priority 1-5)

### Passive Subdomain Enumeration

```json
{
  "attack_tactic_id": "TA0043",
  "phase": "recon",
  "title": "Passive subdomain enumeration for <TARGET>",
  "description": "Enumerate all subdomains of <TARGET> using passive sources (subfinder, amass passive, crt.sh) without directly touching target infrastructure.",
  "acceptance_criteria": [
    "subfinder -d <TARGET> results saved to <engagement>/recon/subfinder.txt",
    "amass enum -passive -d <TARGET> results saved to <engagement>/recon/amass.txt",
    "crt.sh query results saved to <engagement>/recon/crtsh.txt",
    "All sources merged and deduplicated into <engagement>/recon/subdomains.txt",
    "All discovered targets verified against plan/roe.json in-scope list",
    "OPSEC: No direct DNS queries sent to target nameservers — public resolvers only"
  ],
  "priority": 1,
  "blocked_by": [],
  "status": "pending",
  "mitre": ["T1596.001"],
  "opsec": "standard",
  "opsec_notes": "Passive only — no packets to target. Use 8.8.8.8 and 1.1.1.1 as resolvers."
}
```

### DNS Record Mapping

```json
{
  "attack_tactic_id": "TA0043",
  "phase": "recon",
  "title": "DNS record mapping for <TARGET>",
  "description": "Query all DNS record types (A, AAAA, MX, NS, TXT, SOA, CAA, CNAME) for <TARGET> and discovered subdomains.",
  "acceptance_criteria": [
    "dig queries for A, AAAA, MX, NS, TXT, SOA, CAA completed",
    "Results saved to <engagement>/recon/dns_records.txt",
    "Dangling CNAMEs identified and flagged",
    "All queried domains verified against plan/roe.json in-scope list",
    "OPSEC: Queries routed through public resolvers, not target nameservers"
  ],
  "priority": 2,
  "blocked_by": [0],
  "status": "pending",
  "mitre": ["T1596.001"],
  "opsec": "standard",
  "opsec_notes": "DNS queries to public resolvers are passive. Zone transfer attempts require active authorization."
}
```

### WHOIS & ASN Intelligence

```json
{
  "attack_tactic_id": "TA0043",
  "phase": "recon",
  "title": "WHOIS and ASN intelligence for <TARGET>",
  "description": "Gather WHOIS registration data, ASN ownership, and IP range allocation for <TARGET>.",
  "acceptance_criteria": [
    "WHOIS data for primary domain saved to <engagement>/recon/whois.txt",
    "ASN and IP ranges identified",
    "Infrastructure relationships documented",
    "All discovered IP ranges cross-referenced with roe.json scope",
    "OPSEC: Public database queries only"
  ],
  "priority": 3,
  "blocked_by": [],
  "status": "pending",
  "mitre": ["T1596.002"],
  "opsec": "standard",
  "opsec_notes": "WHOIS is fully passive — public registry data."
}
```

### Web Fingerprinting (httpx)

```json
{
  "attack_tactic_id": "TA0043",
  "phase": "recon",
  "title": "Web fingerprinting and live host probing",
  "description": "Probe all discovered subdomains with httpx for status codes, technology detection, and content analysis.",
  "acceptance_criteria": [
    "httpx probe results saved to <engagement>/recon/httpx_results.txt",
    "JSON output saved to <engagement>/recon/httpx.json for parsing",
    "Technology stack identified per live host",
    "All probed targets verified against plan/roe.json in-scope list",
    "OPSEC: Request rate ≤ 10 req/sec, custom User-Agent set"
  ],
  "priority": 4,
  "blocked_by": [0, 1],
  "status": "pending",
  "mitre": ["T1592.002"],
  "opsec": "standard",
  "opsec_notes": "httpx makes HTTP requests to targets — this is active recon. Rate limit appropriately."
}
```

### OSINT Gathering

```json
{
  "attack_tactic_id": "TA0043",
  "phase": "recon",
  "title": "OSINT gathering for <TARGET>",
  "description": "Search Google dorks, GitHub/GitLab repos, and Wayback Machine for leaked credentials, exposed config files, and historical infrastructure data.",
  "acceptance_criteria": [
    "Google dork results documented in <engagement>/recon/osint_dorks.txt",
    "GitHub search for org/domain completed, results saved to <engagement>/recon/osint_github.txt",
    "Wayback Machine historical URLs saved to <engagement>/recon/osint_wayback.txt",
    "Any discovered credentials flagged but NOT stored in plaintext",
    "All findings scoped to roe.json authorized targets",
    "OPSEC: No direct interaction with target systems"
  ],
  "priority": 5,
  "blocked_by": [],
  "status": "pending",
  "mitre": ["T1593.002"],
  "opsec": "standard",
  "opsec_notes": "Search engine and public repo queries — fully passive."
}
```

## Active Recon Objectives (priority 6-10)

### TCP Port Scan

```json
{
  "attack_tactic_id": "TA0043",
  "phase": "recon",
  "title": "TCP SYN scan top 1000 ports on in-scope IPs",
  "description": "Perform TCP SYN scan of top 1000 ports on all in-scope IP addresses identified during passive recon.",
  "acceptance_criteria": [
    "nmap SYN scan completed on all in-scope IPs",
    "Results saved to <engagement>/recon/nmap_syn.txt (text) and nmap_syn.xml (XML)",
    "Open ports summarized in table format",
    "All scanned IPs verified against plan/roe.json in-scope list BEFORE scan",
    "OPSEC: Scan rate ≤ 100 packets/sec (-T3), within authorized testing window"
  ],
  "priority": 6,
  "blocked_by": [1, 2],
  "status": "pending",
  "mitre": ["T1595.001"],
  "opsec": "standard",
  "opsec_notes": "Active scanning — generates network traffic visible to IDS/IPS. Use -T2 for high-security targets."
}
```

### Service Version Detection

```json
{
  "attack_tactic_id": "TA0043",
  "phase": "recon",
  "title": "Service version detection on open ports",
  "description": "Perform service version detection (-sV) on all open ports discovered by the port-scan objective.",
  "acceptance_criteria": [
    "nmap -sV completed on all open ports",
    "Results saved to <engagement>/recon/nmap_versions.txt and nmap_versions.xml",
    "Service versions mapped for CVE research",
    "All targets verified against plan/roe.json",
    "OPSEC: Version probes limited to previously discovered open ports only"
  ],
  "priority": 7,
  "blocked_by": [5],
  "status": "pending",
  "mitre": ["T1592.002"],
  "opsec": "standard",
  "opsec_notes": "Version detection sends service-specific probes — more noisy than SYN scan."
}
```

### Web Directory Fuzzing

```json
{
  "attack_tactic_id": "TA0043",
  "phase": "recon",
  "title": "Web directory fuzzing on live HTTP services",
  "description": "Run ffuf directory fuzzing against live web services identified by httpx, using common wordlist.",
  "acceptance_criteria": [
    "ffuf run against top 5 highest-value web targets",
    "Results saved to <engagement>/recon/ffuf_<host>.json per target",
    "Interesting paths (admin panels, API docs, config files) flagged",
    "All targets verified against plan/roe.json",
    "OPSEC: Request rate ≤ 10 req/sec (-rate 10), custom User-Agent"
  ],
  "priority": 8,
  "blocked_by": [3],
  "status": "pending",
  "mitre": ["T1595.003"],
  "opsec": "standard",
  "opsec_notes": "Directory fuzzing generates many HTTP requests — highly visible to WAFs. Rate limit aggressively."
}
```

### Vulnerability Scan (nuclei)

```json
{
  "attack_tactic_id": "TA0043",
  "phase": "recon",
  "title": "Vulnerability scan on live web targets",
  "description": "Run nuclei with default templates against all live web targets for known vulnerabilities.",
  "acceptance_criteria": [
    "nuclei scan completed on all live web hosts",
    "Results saved to <engagement>/recon/nuclei_results.txt",
    "Findings categorized by severity (critical, high, medium, low)",
    "All targets verified against plan/roe.json",
    "OPSEC: Rate limited (-rl 5 -c 2), within authorized testing window"
  ],
  "priority": 9,
  "blocked_by": [6, 7],
  "status": "pending",
  "mitre": ["T1595.002"],
  "opsec": "standard",
  "opsec_notes": "Nuclei sends detection payloads — IDS/WAF may flag template signatures."
}
```

### Synthesis Report

```json
{
  "attack_tactic_id": "TA0043",
  "phase": "recon",
  "title": "Merge findings into prioritized attack surface report",
  "description": "Consolidate all recon findings into a single prioritized report with CVSS scoring and MITRE ATT&CK mapping.",
  "acceptance_criteria": [
    "All passive and active findings merged",
    "Each finding scored with CVSS 3.1",
    "Each finding mapped to MITRE ATT&CK technique",
    "Findings prioritized: critical → high → medium → low",
    "Final report saved to <engagement>/recon/report_<target>_recon.md",
    "JSON export saved to <engagement>/recon/report_<target>_recon.json"
  ],
  "priority": 10,
  "blocked_by": [3, 4, 8],
  "status": "pending",
  "mitre": ["T1596"],
  "opsec": "standard",
  "opsec_notes": "Report generation — no target interaction."
}
```
