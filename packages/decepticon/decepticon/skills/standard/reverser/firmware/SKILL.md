---
name: reverser-firmware
description: Router / IoT firmware extraction pipeline — unpack nested filesystems, locate web server, identify backdoor credentials.
metadata:
  subdomain: reverse-engineering
  when_to_use: "firmware extraction router iot binwalk nested filesystem web server backdoor credential squashfs cramfs"
  mitre_attack:
    - T1542
    - T1078.001
---

# Firmware Extraction Playbook

## 1. Extract
```bash
apt-get install -y binwalk squashfs-tools cramfsprogs
mkdir -p /workspace/fw && cd /workspace/fw
binwalk -eM /workspace/firmware.bin
find _firmware.bin.extracted -name "squashfs-root" -o -name "rootfs"
```

## 2. Map the root
```bash
ROOT=$(find _firmware.bin.extracted -type d -name "squashfs-root" | head -1)
ls -la "$ROOT/etc/"
cat "$ROOT/etc/passwd"          # look for hardcoded users
cat "$ROOT/etc/shadow"          # password hashes — feed to hashcat
cat "$ROOT/etc/init.d/S*" 2>/dev/null
cat "$ROOT/etc/rc.d/rc.local" 2>/dev/null
```

## 3. Web server binary
```bash
find "$ROOT" -name "httpd" -o -name "lighttpd" -o -name "nginx" -o -name "boa"
# For each: bin_identify + bin_strings + bin_symbols_report
```
CGI binaries under `www/cgi-bin/` are the highest-yield targets — often
one file per endpoint with direct `system()` calls from query params.

## 4. Hardcoded credentials
```
bin_strings(path=web_server_binary, category_filter="secret")
bin_strings(path=web_server_binary, category_filter="crypto")
```
Also check for base64 / hex key patterns near `strcmp` calls (manual
audit via the active Ghidra runtime. Hosted runs import the extracted binary
under `/workspace` with `ghidra_import_file`; use `ghidra_find_tools` for
native capabilities not initially listed. Standalone runs may expose local
script generation tools instead.)

## 5. Authentication bypass audit
Look for:
- Magic string comparisons (`strcmp(user, "admin")`)
- Debug backdoor paths (`/debug`, `/cgi-bin/shell`, `.sys`)
- UART/JTAG enabled in bootargs (check `bootargs.txt` in extraction)

## 6. Cross-reference CVEs
For the extracted busybox / kernel / dropbear / openssl versions:
```
cve_by_package("busybox", "1.27.2", "OSS-Fuzz")
cve_lookup("CVE-2021-28831,CVE-2023-0464")
```

## 7. Record
Add:
- `repo` node for the firmware image
- `file` nodes for each interesting binary
- `credential` nodes for any hardcoded creds
- `vulnerability` nodes for each audit finding
- `entrypoint` for each cgi-bin path
