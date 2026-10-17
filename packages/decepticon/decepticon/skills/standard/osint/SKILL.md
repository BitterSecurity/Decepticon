---
name: osint-overview
description: >
  Use when the engagement requires passive reconnaissance only — no
  packets to the target's authoritative infrastructure. Splits off from
  the Recon agent so bug-bounty and pre-engagement work can run with
  outbound-only network policy. Maltego, Shodan, Censys, Hunter.io,
  breach-data lookups, GitHub code search, Wayback Machine archives,
  certificate transparency, BGP/ASN mapping.
metadata:
  skillogy:
    version: 1
  subdomain: osint
  when_to_use: "osint passive reconnaissance maltego shodan censys hunter breach data github code search wayback machine certificate transparency bgp asn outbound only bug bounty"
  subdomain: osint
  tags: osint, passive-recon, shodan, censys, breach-data, ct-logs, bgp
  mitre_attack: T1589, T1590, T1591, T1593, T1594, T1596
  network_policy: outbound-only
---

# OSINT-Only Operator Skill Catalog

This catalog is **passive**. No packets reach the target. Sandbox network
policy must restrict outbound to known-OSINT endpoints only (Shodan,
Censys, Hunter, GitHub API, crt.sh, Wayback, etc.).

## Playbooks

> **Inline technique reference — not separately loadable skills.** The entries below
> are summarized here for direct use; there is no separate `SKILL.md` to open for
> each. Do NOT call the skill loader on them — apply the technique with your tools
> using this summary and the passive analysis guidance in this file.


| Technique | Use for |
|---|---|
| **domain-pivots** | Whois history, reverse-IP, related-domain enumeration |
| **ct-logs** | crt.sh / Censys cert search for subdomain enumeration |
| **shodan-fingerprint** | Shodan host search; service / banner / ssl.cn pivots |
| **censys-pivots** | Censys cert/host/services pivots |
| **github-code-search** | GitHub code search for org's leaked secrets / config |
| **wayback-archives** | Wayback Machine API; retired endpoints, deleted docs |
| **breach-data** | HIBP / DeHashed (RoE-permitted only); credential reuse paths |
| **employee-profiling** | LinkedIn search (Sales Nav / manual), email-format inference |
| **asn-bgp** | ASN ownership, BGP table snapshots, RIR records |
| **maltego** | Maltego CLI graph projection; transform chain |
| **cryptocurrency** | Chain analysis (Etherscan / Mempool.space / Arkham) for crypto-adjacent targets |
| **geospatial** | Image geolocation, EXIF mining, satellite/streetview cross-reference |

## Passive analysis

1. **Seed**: from the engagement target (domain, company name, brand).
2. **Domain layer**: whois, reverse-IP, CT logs → enumerate every
   subdomain and adjacent domain.
3. **Service layer**: Shodan + Censys against discovered IPs → service
   inventory (NO probing; just consume cached scan data).
4. **Code layer**: GitHub code search for the target's org name, domain
   names, internal package names, AWS account IDs.
5. **People layer**: employees via LinkedIn; email format inference;
   HaveIBeenPwned for credential reuse.
6. **Infrastructure layer**: BGP + ASN ownership; Wayback retired
   endpoints; SSL/TLS cert history.
7. **Synthesis**: project the graph into Neo4j as a pre-engagement map.
   Active confirmation requires RoE permission and is outside this
   passive-only catalog.

## Network policy

```
[osint-operator container] → outbound to: shodan.io, api.censys.io,
                              api.hunter.io, api.github.com,
                              crt.sh, archive.org, hibp/api/v3,
                              maltego.com, etherscan.io, ...
                              NO outbound to the engagement target.
```

The sandbox-net policy for OSINT engagements pins this allowlist. Any
attempted egress to the actual target IP/domain triggers a SafeCommand
refusal.

## Why split from Recon

Recon is active by default — port scans, version probing, directory
brute-forcing. Bug-bounty programs and pre-engagement scoping work
explicitly forbid touching production. OSINT-only enforces the
no-touch contract structurally rather than relying on the agent prompt
to remember.
