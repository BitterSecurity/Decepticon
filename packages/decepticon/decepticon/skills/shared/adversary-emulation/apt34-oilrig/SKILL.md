---
name: apt34-oilrig
description: "Adversary-emulation profile for APT34 / OilRig (G0049), an Iranian state-sponsored espionage group, mapping its ATT&CK TTPs to Decepticon tooling for authorized red-team emulation."
allowed-tools: Bash Read Write
metadata:
  skillogy:
    version: 1
  subdomain: adversary-emulation
  when_to_use: "APT34, OilRig, Helix Kitten, Hazel Sandstorm, Earth Simnavaz, Crambus, Cobalt Gypsy, Evasive Serpens, Iranian APT, MOIS, Middle East espionage, DNS tunneling, BONDUPDATER, POWRUNER, SideTwist, STEALHOOK, Outlook Home Page persistence, Exchange exfiltration, password filter DLL, emulate Iranian threat actor"
  tags: adversary-emulation, apt34, oilrig, iran, espionage, g0049, dns-tunneling, middle-east, mitre-attack, red-team
  mitre_attack: T1566.001, T1566.002, T1566.003, T1204.001, T1204.002, T1133, T1078, T1078.002, T1190, T1195, T1059.001, T1059.003, T1059.005, T1047, T1203, T1218.001, T1053.005, T1543.003, T1505.003, T1137.004, T1556.002, T1133, T1068, T1543.003, T1112, T1140, T1070.004, T1027.005, T1027.013, T1036, T1036.005, T1553.002, T1588.003, T1497.001, T1686.003, T1003.001, T1003.004, T1003.005, T1555, T1555.003, T1555.004, T1552.001, T1110, T1056.001, T1115, T1087.001, T1087.002, T1082, T1016, T1049, T1033, T1007, T1057, T1518, T1069.001, T1069.002, T1201, T1120, T1012, T1046, T1021.001, T1021.004, T1572, T1219, T1005, T1119, T1074.001, T1113, T1217, T1025, T1071.001, T1071.004, T1132.001, T1008, T1573.002, T1105, T1048.003, T1585.003, T1583.001, T1584.004, T1586.002, T1587.001, T1588.002, T1608.001
---

# APT34 (OilRig, Helix Kitten, Hazel Sandstorm) — Adversary Emulation Profile

APT34 — tracked as OilRig, Helix Kitten, Hazel Sandstorm, Earth Simnavaz, Crambus, Cobalt Gypsy / COBALT GYPSY, IRN2, Evasive Serpens, EUROPIUM, ITG13, and TA452 (MITRE ATT&CK **G0049**) — is a suspected Iranian state-sponsored cyber-espionage group operational since at least 2014. It conducts long-running, reconnaissance-heavy intrusions against Middle Eastern and international organizations, favoring spearphishing for initial access, heavy use of PowerShell/VBScript backdoors, custom DNS-tunneling C2, supply-chain and trust-relationship abuse, and patient credential harvesting. The group continually re-tools (BONDUPDATER → SideTwist → cloud/Exchange-based C2) and is notable for living-off-the-land tradecraft, password-filter-DLL credential capture, and exfiltrating data through victim-owned Microsoft Exchange servers to blend with legitimate mail flow.

## Attribution & motivation
- **Suspected sponsor:** Iran. FireEye/Mandiant assesses APT34 "works on behalf of the Iranian government based on infrastructure details that contain references to Iran, use of Iranian infrastructure, and targeting that aligns with nation-state interests." Multiple vendors (Trend Micro, Microsoft, CrowdStrike) link the cluster to Iranian intelligence services.
- **Motivation:** Strategic **cyber espionage** — long-term reconnaissance and credential/data theft to advance Iranian geopolitical interests in the region. Primary intent is intelligence collection, not financial gain. (A separate but related Iranian destructive operation, ZeroCleare wiper (S1151), is associated with the same cluster, indicating latent destructive capability.)
- **Confidence:** High that the activity is Iranian state-aligned espionage; the precise sponsoring agency varies by vendor naming. Attribution of any single intrusion should be treated as suspected unless backed by collected infrastructure/tooling overlap.

## Targeting
- **Sectors:** Financial/banking, government, energy/oil & gas, chemical, telecommunications, critical infrastructure, and aviation/transport.
- **Regions:** Primarily the Middle East — Gulf Cooperation Council states (UAE, Saudi Arabia, Kuwait, Qatar, Bahrain), plus Israel; with international reach into the U.S. and other regions via supply-chain and trust-relationship pivots.
- **Victim profile:** Government ministries and state-aligned enterprises in strategic sectors; the group frequently compromises one organization (e.g., HR/job portals, telecoms) and abuses that trust to reach higher-value government targets.

## Notable campaigns
- **2014–2016 — Emergence (Helminth):** Social-engineering attacks delivering the Helminth backdoor (S0170) via macro-enabled Excel spreadsheets against Middle Eastern banks; FireEye began tracking the cluster as APT34. (Source: MITRE G0049; LevelBlue/Trustwave APT34 profile.)
- **December 2017 — CVE-2017-11882 government campaign:** Within a week of Microsoft's Nov-2017 patch, APT34 spearphished a Middle East government org with a malicious .rtf exploiting CVE-2017-11882 (Equation Editor) to deploy **POWRUNER** (S0184) and **BONDUPDATER** (S0360), the latter using a custom DGA for DNS-based C2. (Source: FireEye/Mandiant, 2017-12-07.)
- **Mid-2018 onward — Outlook Home Page persistence (CVE-2017-11774):** APT34 (alongside APT33) abused the Outlook Home Page feature for code execution and persistence after obtaining valid credentials. (Source: FireEye/Mandiant; U.S. Cyber Command advisory; Dark Reading.)
- **April 2019 — Tool/source leak:** A leak via the "Lab Dookhtegan" Telegram channel exposed OilRig webshells, DNS-tunneling tooling, and victim data, confirming TTPs and infrastructure. (Source: BankInfoSecurity.)
- **October 2024 — Earth Simnavaz Gulf campaign:** Trend Micro reported APT34/Earth Simnavaz targeting UAE/Gulf government and critical-infrastructure orgs: initial access via a webshell on a vulnerable web server, privilege escalation with **CVE-2024-30088** (Windows Kernel), a registered **password filter DLL** to capture plaintext credentials, the **STEALHOOK** backdoor exfiltrating data as attachments through the victim's own **Microsoft Exchange** server, and **ngrok** for tunneling/lateral movement to the Domain Controller. (Source: Trend Micro; Industrial Cyber; Dark Reading.)

## TTPs by ATT&CK tactic
*(Every technique below is grounded in the MITRE ATT&CK G0049 entry unless otherwise noted.)*

### Initial Access
- **T1566.001 / T1566.002 / T1566.003** — Spearphishing via attachment (macro-enabled Excel/Word, .rtf), via link, and via service (LinkedIn personas/fake job sites).
- **T1190** — Exploitation of public-facing web servers to drop webshells (Earth Simnavaz initial access; complements T1505.003).
- **T1133** — External remote services: persistence/access via VPN, Citrix, and OWA.
- **T1195** — Supply-chain / trust-relationship compromise: leveraging one breached org to reach government targets.
- **T1078 / T1078.002** — Valid (incl. domain) accounts using harvested credentials.

### Execution
- **T1059.001 / .003 / .005** — PowerShell, Windows Command Shell (batch), and VBScript macros for code execution.
- **T1047** — WMI for execution.
- **T1203** — Exploitation for client execution (CVE-2017-11882, CVE-2024-30088).
- **T1218.001** — Compiled HTML (.chm) payloads to load malware.
- **T1204.001 / .002** — User execution of malicious links and macro-enabled documents.

### Persistence
- **T1053.005** — Scheduled tasks executing VBScript.
- **T1543.003** — Windows services created on remote Domain Controllers.
- **T1505.003** — Webshells (e.g., SEASHARPEE S0185, RGDoor S0258) for durable network access.
- **T1137.004** — Outlook Home Page abuse (CVE-2017-11774) for persistence.
- **T1556.002** — Password Filter DLL registered both for credential capture and as a persistence/delivery mechanism.
- **T1133** — Re-entry via VPN/Citrix/OWA.

### Privilege Escalation
- **T1068** — Exploitation for privilege escalation via Windows Kernel CVE-2024-30088.
- **T1543.003** — Service creation for elevated execution.

### Defense Evasion
- **T1140** — Decode base64 with certutil/PowerShell.
- **T1070.004** — Delete payload files post-execution.
- **T1027.005 / .013** — Modify samples to evade AV; base64-encrypted/encoded files.
- **T1036 / T1036.005** — Masquerading (e.g., `.doc`-extension executables; Plink renamed `\ProgramData\Adobe.exe`).
- **T1553.002 / T1588.003** — Code signing with stolen certificates (and acquiring those certs).
- **T1497.001** — Sandbox evasion via mouse-presence/system checks.
- **T1112** — Modify registry via reg.exe.
- **T1686.003** — Disable/modify Windows firewall for remote access.

### Credential Access
- **T1003.001 / .004 / .005** — LSASS (Mimikatz S0002), LSA Secrets and cached domain creds (LaZagne S0349).
- **T1555 / .003 / .004** — Password stores; browser creds (LaZagne, PICKPOCKET); Windows Credential Manager (VALUEVAULT).
- **T1552.001** — Credentials in files via LaZagne.
- **T1556.002** — Password Filter DLL to capture plaintext credentials at logon/change.
- **T1110** — Brute force / password spraying.
- **T1056.001** — Keylogging (KEYPUNCH, LONGWATCH).
- **T1115** — Clipboard capture via infostealers.

### Discovery
- **T1087.001 / .002** — Local/domain account enumeration (`net user`, net commands).
- **T1082 / T1016 / T1049 / T1033 / T1007** — System info (`systeminfo`, `hostname`), network config (`ipconfig /all`), connections (`netstat -an`), user (`whoami`), services (`sc query`).
- **T1057 / T1518** — Process listing (`tasklist`); installed software (incl. Chrome).
- **T1069.001 / .002** — Local and domain permission-group enumeration.
- **T1201** — Password policy discovery (`net accounts`).
- **T1120 / T1012** — Peripheral (mouse) discovery; registry queries (Terminal Server Client keys).
- **T1046** — Network service discovery (SoftPerfect Network Scanner, GOLDIRONY).

### Lateral Movement
- **T1021.001** — RDP (often through tunneling).
- **T1021.004** — SSH via PuTTY.
- **T1572** — Protocol tunneling with Plink.
- **T1219** — ngrok used as remote-access/tunneling RMM to reach the DC (Earth Simnavaz).
- PsExec (S0029) for remote execution.

### Collection
- **T1005 / T1119 / T1074.001** — Local data collection, automated collection, local staging in `%TEMP%`.
- **T1113** — Screen capture (CANDYKING).
- **T1217 / T1025** — Browser data dumpers (MKG, CDumper, EDumper); USB capture via Wireshark `usbcapcmd`.
- **T1056.001 / T1115** — Keylogging and clipboard data (also credential access).

### Command and Control
- **T1071.001** — HTTP web-protocol C2.
- **T1071.004** — DNS-tunneling C2 (signature TTP; BONDUPDATER DGA, requestbin.net).
- **T1132.001** — Base64 standard encoding of C2 data.
- **T1008** — Fallback channels (ISMAgent falls back to DNS when HTTP unavailable).
- **T1573.002** — Asymmetric encrypted channels (PowerExchange/tunneling utilities).
- **T1105** — Ingress tool transfer.

### Exfiltration
- **T1048.003** — Exfiltration over alternative protocol via the victim's own Microsoft Exchange server (STEALHOOK emails data as attachments) and FTP.

### Impact
- No routine destructive impact in espionage operations, but the cluster is associated with the **ZeroCleare** (S1151) wiper, demonstrating latent destructive capability — relevant for blue-cell worst-case planning.

### Resource Development (pre-intrusion)
- **T1583.001 / T1584.004 / T1586.002** — Acquire domains (fake VPN/job sites), compromise infrastructure (hijacked Israeli HR/job portals as C2), compromise email accounts for phishing.
- **T1585.003** — Establish M365 cloud accounts for C2.
- **T1587.001 / T1588.002 / T1608.001** — Develop malware (Solar, Mango); obtain tools (Plink, Mimikatz); host malware on fake targeting sites.

## Signature tooling & malware
**Custom:** BONDUPDATER (S0360, DGA DNS downloader), POWRUNER (S0184, PowerShell backdoor), Helminth (S0170, VBScript/PowerShell + EXE), ISMInjector (S0189), QUADAGENT (S0269), OopsIE (S0264), SideTwist (S0610), RDAT (S0495, DNS/email-steganography C2), RGDoor (S0258, IIS backdoor), SEASHARPEE (S0185, webshell), PowerExchange (S1173, Exchange-based C2), Solar / Mango (S1166 / S1169), SampleCheck5000 (S1168), ODAgent (S1170), OilCheck (S1171), OilBooster (S1172), ZeroCleare (S1151, wiper). Named (non-MITRE-software) tooling from reporting: STEALHOOK, VALUEVAULT, PICKPOCKET, KEYPUNCH, LONGWATCH, CANDYKING, GOLDIRONY, MKG/CDumper/EDumper.
**Public / dual-use:** Mimikatz (S0002), LaZagne (S0349), PsExec (S0029), ngrok (S0508), certutil (S0160), Net (S0039), Reg (S0075), Tasklist (S0057), Systeminfo (S0096), netstat (S0104), ipconfig (S0100), ftp (S0095), Plink/PuTTY, SoftPerfect Network Scanner, Wireshark (`usbcapcmd`).

## Emulation guidance (Decepticon)
**AUTHORIZED USE ONLY — execute these techniques exclusively within the documented scope, rules of engagement, and target list of a signed engagement; never against systems you are not explicitly authorized to test.**

Map APT34's signature behaviors to Decepticon capabilities to reproduce the actor's tradecraft for blue-cell exercise:
- **Initial access (T1566.x / T1204.x):** Use the phishing/social-engineering skill to craft macro-enabled Office or .rtf lures and LinkedIn-style personas (mirroring T1566.003). For web-facing entry (T1190/T1505.003), use the web-exploitation/bash tooling to drop an authorized webshell on an in-scope test server.
- **Execution (T1059.001/.003/.005, T1047, T1218.001):** Drive PowerShell, cmd, VBScript, WMI, and .chm-loader payloads via the bash/command-execution and defense-evasion skills. Stage a custom-DGA "BONDUPDATER-style" beacon to emulate the actor's loader behavior.
- **C2 (T1071.004 DNS tunneling, T1071.001 HTTP, T1008 fallback, T1573.002):** Configure your **C2/Sliver** profile for DNS-over-HTTP fallback to replicate APT34's hallmark DNS-tunneling and HTTP-with-DNS-failover beaconing. Use base64 encoding (T1132.001) on C2 traffic to match the actor.
- **Persistence (T1053.005, T1543.003, T1505.003, T1137.004, T1556.002):** Via the persistence/AD skills, plant scheduled tasks, create services on a (lab) DC, deploy a webshell, and — where the engagement explicitly authorizes endpoint persistence — emulate Outlook Home Page abuse and a benign password-filter-DLL to demonstrate the credential-capture vector.
- **Privilege escalation (T1068):** Use the local-privesc tooling to emulate kernel-exploit escalation in a patched-vs-unpatched lab; do not weaponize live CVEs outside scope.
- **Credential access (T1003.x, T1555.x, T1556.002, T1110, T1056.001):** Run Mimikatz/LaZagne-equivalent modules from the credential-access skill for LSASS/LSA/browser/Credential-Manager dumping; emulate password-spray (T1110) and a keylogger to mirror APT34 harvesting.
- **Discovery (T1087/T1082/T1016/T1049/T1069/T1201/T1046):** Execute the LOLBin recon sequence (`whoami`, `net user`/`net group`, `ipconfig /all`, `netstat -an`, `net accounts`, `sc query`) via bash and run an authorized network scan to mirror the actor's footprint.
- **Lateral movement (T1021.001/.004, T1572, T1219):** Use the lateral-movement skill with RDP/SSH plus Plink/ngrok-style tunneling to traverse to a lab DC, replicating the Earth Simnavaz path.
- **Collection & exfiltration (T1113/T1074.001/T1048.003):** Stage data in `%TEMP%`, capture screenshots, then emulate exfiltration through an in-scope Exchange/mail path (STEALHOOK pattern) or DNS tunnel to validate DLP/egress detections.
- **Cloud (T1585.003):** Where the engagement includes M365/Azure, use the cloud skill to stand up an authorized cloud-mail C2 account mirroring the actor's M365 abuse.

One illustrative combination is phish → loader → DNS/HTTP C2 → discovery → credential access → escalation → lateral access → staging → mail/DNS transfer. These links describe possible technical dependencies, not a compulsory campaign; the selected workflow and RoE determine which links are needed and authorized.

## Detection & defense
- **Phishing/macros (T1566, T1204, T1218.001):** Block macros from the internet, disable Equation Editor, strip/ sandbox .rtf and .chm attachments; user-reporting + attachment detonation.
- **DNS-tunneling C2 (T1071.004, T1008):** Monitor for high-volume/long/encoded TXT/A queries, DGA-like subdomains, and unusual resolver patterns; baseline DNS egress and alert on anomalous query length/entropy. This is the highest-signal detection for APT34.
- **Outlook Home Page persistence (T1137.004):** Audit/disable the Outlook Home Page registry keys; ensure the CVE-2017-11774 patch is applied and not rolled back; alert on creation of `Software\Microsoft\Office\*\Outlook\WebView` URL values.
- **Password Filter DLL (T1556.002):** Monitor `HKLM\SYSTEM\CurrentControlSet\Control\Lsa\Notification Packages` for new entries and unexpected DLLs loaded into LSASS.
- **Credential dumping (T1003.x):** Enable LSASS protection (RunAsPPL/Credential Guard), alert on suspicious LSASS access and on LaZagne/Mimikatz behavior.
- **Exchange exfiltration (T1048.003):** Alert on programmatic mail sends with attachments from server/service contexts, anomalous EWS/Exchange transport activity, and outbound mail to unfamiliar external addresses.
- **Tunneling/RMM (T1572, T1219, T1021):** Detect ngrok/Plink/PuTTY binaries and renamed copies (e.g., `Adobe.exe` in `ProgramData` — T1036.005), monitor for unexpected RDP/SSH tunnels, and restrict outbound to known RMM domains.
- **Webshells (T1505.003):** File-integrity monitoring on web roots, alert on web-server processes spawning cmd/PowerShell.
- **Exploitation/patching (T1203, T1068):** Prioritize patching CVE-2017-11882, CVE-2017-0199, CVE-2017-11774, and CVE-2024-30088; restrict kernel-exploit prerequisites.
- **Living-off-the-land recon (T1059.001, certutil decode T1140):** Enable PowerShell script-block/module logging and alert on `certutil -decode`, base64 blobs, and the clustered LOLBin recon sequence.
- **Identity (T1078, T1110, T1133):** Enforce MFA on VPN/Citrix/OWA/M365, monitor for password spraying and impossible-travel logons.

## Sources
- MITRE ATT&CK — OilRig (G0049): https://attack.mitre.org/groups/G0049/
- FireEye/Mandiant — "New Targeted Attack in the Middle East by APT34 … Using CVE-2017-11882 Exploit" (2017-12-07): https://cloud.google.com/blog/topics/threat-intelligence/targeted-attack-in-middle-east-by-apt34/
- Mandiant/FireEye — "Breaking the Rules: A Tough Outlook for Home Page Attacks (CVE-2017-11774)": https://cloud.google.com/blog/topics/threat-intelligence/breaking-the-rules-tough-outlook-for-home-page-attacks/
- Trend Micro — "Earth Simnavaz (aka APT34) Levies Advanced Cyberattacks Against Middle East" (2024-10): https://www.trendmicro.com/en_us/research/24/j/earth-simnavaz-cyberattacks.html
- Industrial Cyber — Trend Micro Earth Simnavaz Exchange backdoor coverage: https://industrialcyber.co/ransomware/trend-micro-reveals-earth-simnavaz-apt-targets-gulf-organizations-using-microsoft-exchange-server-backdoor/
- Dark Reading — "Iran's APT34 Abuses MS Exchange to Spy on Gulf Gov'ts": https://www.darkreading.com/cyberattacks-data-breaches/iran-apt34-ms-exchange-spy-gulf-govts
- Dark Reading — "Attackers Continue to Exploit Outlook Home Page Flaw" (CVE-2017-11774): https://www.darkreading.com/vulnerabilities-threats/attackers-continue-to-exploit-outlook-home-page-flaw
- BankInfoSecurity — "Leak Exposes OilRig APT Group's Tools": https://www.bankinfosecurity.com/leak-exposes-oilrig-apt-groups-tools-a-12397
