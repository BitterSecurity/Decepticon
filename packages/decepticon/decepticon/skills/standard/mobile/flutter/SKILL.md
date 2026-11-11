---
name: flutter
description: Flutter app reversing and SSL pinning bypass — reFlutter Dart-AOT patching, BoringSSL bypass, libapp.so static analysis in Ghidra/radare2, Dart snapshot dump, and iOS Flutter.framework notes.
allowed-tools: Bash Read Write
metadata:
  subdomain: mobile
  when_to_use: "flutter dart aot libapp.so libflutter reflutter boringssl ssl pinning bypass snapshot dump cross-platform jadx-useless tunproxy uber-apk-signer"
  tags: flutter, dart, reflutter, ssl-pinning, reverse-engineering, libapp, boringssl
  mitre_attack: T1635, T1521.003, T1406
---

# Flutter App Reversing Playbook

> Flutter compiles Dart AOT to a native shared library (`libapp.so` /
> `Flutter.framework`). `jadx` / `apktool` show only the thin Java/ObjC
> shell and are useless for app logic. Use this playbook when static
> Android or iOS skills yield nothing but an empty Java wrapper.

## Prerequisites

- APK / IPA obtained (see `mobile/android/SKILL.md` Path 1 for APK pull).
- Tools: `reflutter` (`pip install reflutter`), `uber-apk-signer`
  (download JAR from GitHub releases), `adb` + device or emulator,
  `jadx` (identification only), Burp Suite proxy.
- For iOS: `reFlutter` also patches `Flutter.framework`; use
  `objection patchipa` or direct framework patching workflow.

## Step 1: Identify a Flutter App

```bash
# Unzip APK, look for the telltale Flutter artifacts
unzip -o base.apk -d /tmp/apk-out/

ls /tmp/apk-out/lib/arm64-v8a/
# Flutter app: libflutter.so  libapp.so
# Present:     flutter_assets/   kernel_blob.bin (optional, debug)

# jadx shows only the Java bootstrap:
jadx -d /tmp/apk-java /tmp/base.apk
# Expect: ~1 class, LoadLibrary("app"), nothing useful

# Confirm with strings:
strings /tmp/apk-out/lib/arm64-v8a/libflutter.so | grep -i "flutter"
strings /tmp/apk-out/lib/arm64-v8a/libapp.so | grep -E "https?://"
```

Key tell: `libapp.so` size > 5 MB with no dex business logic; the
`kernel_blob.bin` in `flutter_assets/` indicates a debug/JIT build
(rare in production; can extract Dart directly with `dart_vm`).

## Step 2: reFlutter Workflow — Patch BoringSSL Pinning

reFlutter patches the `ssl_crypto_x509_session_verify_cert_chain`
function in `libflutter.so` to always return `true`, bypassing all
BoringSSL-based certificate validation (covers both custom pinning
and system trust).

### Flutter <= 3.23.x (hardcoded proxy IP)

```bash
# Patch: supply your Burp listener IP
reflutter base.apk

# reFlutter prompts for IP — enter your Burp machine IP (e.g. 192.168.1.100)
# Output: release.RE.apk (patched, unsigned)

# Sign with uber-apk-signer
java -jar uber-apk-signer.jar --allowResign -a release.RE.apk -o /tmp/

# Install on device
adb install /tmp/release.RE-aligned-signed.apk

# Configure Burp proxy listener on 0.0.0.0:8083 (reFlutter default port is 8083)
# Launch app — HTTPS traffic appears in Burp
```

### Flutter >= 3.24.0 (no hardcoded proxy IP — breaking change)

Starting Flutter 3.24.0 (August 2024), the hardcoded proxy IP was
removed from the patched BoringSSL stub. Traffic is no longer
redirected to a fixed IP. Two options:

**Option A: Device-level proxy**
```bash
# Patch as above (no IP prompt in 3.24+)
reflutter base.apk
java -jar uber-apk-signer.jar --allowResign -a release.RE.apk -o /tmp/

# Install and configure device proxy manually:
# Android Settings → Wi-Fi → [SSID] → Proxy → Manual
# Host: <Burp-IP>  Port: 8080

# Or use TunProxy for non-proxy-aware processes:
adb install TunProxy.apk
# Launch TunProxy, set server to <Burp-IP>:8080, toggle VPN
# Then launch patched app
```

**Option B: Per-app proxy injection via Frida**
```bash
# Run patched APK + Frida script to redirect all socket connections
frida -U -f com.target.app -l proxy-redirect.js --no-pause
# proxy-redirect.js: hooks connect() syscall, redirects to Burp IP
```

### iOS Flutter.framework patching

```bash
# Extract IPA
unzip target.ipa -d /tmp/ipa-out/
# Locate Flutter.framework
ls /tmp/ipa-out/Payload/TargetApp.app/Frameworks/Flutter.framework/Flutter

# reFlutter iOS (experimental — check reFlutter README for current support)
reflutter target.ipa
# Sign with codesign + adhoc or with valid mobileprovision
codesign --force --sign - /tmp/ipa-out/Payload/TargetApp.app/Frameworks/Flutter.framework/Flutter
# Repack + install with ios-deploy or Sideloadly
```

## Step 3: BoringSSL Bypass Mechanism

reFlutter patches `ssl_crypto_x509_session_verify_cert_chain` to
unconditionally return `1` (success). This function is the central
chain verification entry point in BoringSSL (the TLS library embedded
in `libflutter.so`) — no root CA, no pinning config, and no custom
validator override this patch because the chain evaluation never runs.

**Manual binary patch (if reFlutter fails on a specific version):**
```bash
# Find the function offset in libflutter.so
r2 -A /tmp/apk-out/lib/arm64-v8a/libflutter.so
# In r2: afl~ssl_crypto_x509
# Or: /c ret  in data section near ssl_crypto_x509_session_verify_cert_chain

# Patch: overwrite function prologue with MOV W0, #1 / RET (AArch64)
# MOV W0, 1 = 20 00 80 52
# RET        = C0 03 5F D6
python3 -c "
import struct
with open('libflutter.so', 'r+b') as f:
    f.seek(<offset>)
    f.write(b'\x20\x00\x80\x52\xC0\x03\x5F\xD6')
"
```

## Step 4: Static Native RE — libapp.so in Ghidra / radare2

```bash
# Load libapp.so in radare2 for quick triage
r2 -A /tmp/apk-out/lib/arm64-v8a/libapp.so

# Strings — API endpoints, Firebase config, hardcoded keys
r2 -qc 'iz~https' /tmp/apk-out/lib/arm64-v8a/libapp.so
r2 -qc 'iz~firebase' /tmp/apk-out/lib/arm64-v8a/libapp.so
r2 -qc 'iz~AIza' /tmp/apk-out/lib/arm64-v8a/libapp.so   # Firebase API key prefix

# Imports — identify Flutter plugins with native bridges
r2 -qc 'ii' /tmp/apk-out/lib/arm64-v8a/libapp.so | head -40
```

In Ghidra (MCP-connected via `ghidra` server):
```
# connect_instance first, then batch analyze
# Load libapp.so → auto-analyze
# Search defined strings for URL patterns, credential patterns
# Dart AOT functions are not named — use string xrefs to find handlers
```

Dart AOT functions lack names (no symbol table in release builds) but
the Dart snapshot contains type metadata that `Il2CppDumper`-style
tools and `dart_vm` snapshot parsers can partially recover (see
snapshot dump section below).

## Step 5: Dart Snapshot Dump (Hit-or-Miss)

```bash
# Option 1: runtime adb pull from memory-mapped snapshot
adb shell "run-as com.target.app cat /data/data/com.target.app/app_flutter/snapshot_blob.bin" \
  > /tmp/snapshot_blob.bin 2>/dev/null

# Option 2: reFlutter's snapshot output
# After running reFlutter and launching the patched app once,
# reFlutter dumps snapshot_hash.txt which can seed symbol recovery tools
```

**Caveats**: Dart snapshot format changed in Dart 2.15, 3.0, and 3.4.
Use tools documented by their publisher for the exact Flutter/Dart version;
if no compatible tool is available, skip snapshot decoding and use static
string analysis. Do not guess package names or suppress installation errors.

## Step 6: Flutter Plugin Identification

Flutter plugins use platform channels with predictable naming:

```bash
# Find channel names in libapp.so strings
strings /tmp/apk-out/lib/arm64-v8a/libapp.so | grep -E "plugins\.|flutter\." | sort -u

# Channel names like:
#   com.google.firebase.messaging       → push tokens
#   plugins.flutter.io/path_provider   → storage paths
#   flutter.baseflow.com/geolocator    → GPS
#   plugins.flutter.io/local_auth      → biometric

# Cross-reference with flutter_assets/AssetManifest.json
cat /tmp/apk-out/flutter_assets/AssetManifest.json | python3 -m json.tool | head
```

## Evidence

```python
kg_add_node(
    kind="finding",
    label="Flutter BoringSSL pinning bypassed",
    props={
        "key": f"flutter-boringssl-bypass::{package_id}",
        "severity": "high",
        "cvss": 7.4,
        "package": package_id,
        "flutter_version": "<version-from-libflutter-strings>",
        "bypass_method": "reFlutter",
        "proxy_verified": True,
    },
)
```

## ZFP

1. Burp HTTP history showing decrypted HTTPS from patched Flutter app.
2. `strings libapp.so | grep https` output showing extracted endpoints.
3. Screenshot of reFlutter build output + uber-apk-signer signing
   confirming the patched APK was installed.

## OPSEC Notes

- reFlutter requires re-signing the APK; app integrity checks (Google
  Play Integrity API, SafetyNet) will flag the modified signature.
  Use emulator or device without Play Store for testing.
- The patched APK has a different certificate than the original; side-
  load via `adb install` or `adb install --bypass-low-target-sdk-block`.
- For production devices, TunProxy routes traffic at the VPN layer
  without modifying the APK signature.
- libflutter.so binary patching is version-specific; wrong offset
  crashes the app. Always test on a throwaway device/emulator first.

## Severity Table

| Bug | Severity |
|---|---|
| No certificate validation (BoringSSL patched trivially) | High 7.4 |
| Hardcoded API key / Firebase config in libapp.so strings | Critical 9.0 |
| Sensitive data in Dart snapshot / kernel_blob | High 7.5 |
| Flutter plugin channel unauthenticated method call | Medium-High |

## References

- reFlutter: https://github.com/ptswarm/reFlutter
- Flutter 3.24.0 proxy change: https://github.com/ptswarm/reFlutter/issues/107
- TunProxy (Android): https://github.com/raise-isayan/TunProxy
- uber-apk-signer: https://github.com/patrickfav/uber-apk-signer
- kayssel "Breaking Flutter" guide: https://blog.nviso.eu/2022/08/18/intercept-flutter-traffic-on-ios-and-android-http-https-edition/
- Cross-ref: `mobile/android/SKILL.md` (APK pull), `mobile/ios/dynamic/SKILL.md` (iOS BoringSSL path)
