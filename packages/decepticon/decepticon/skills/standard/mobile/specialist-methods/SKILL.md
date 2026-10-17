---
name: mobile-operator-specialist-methods
description: Evidence-led mobile operator technical methods and evidence interpretation for the relevant environment.
metadata:
  skillogy:
    version: 1
  subdomain: mobile
  upstream_ref: "Decepticon standard mobile_operator core prompt and skills/standard/mobile technique references"
  when_to_use: "mobile_operator investigation methodology technical evidence handoff"
---

# Technical methods and evidence

Use the relevant existing technique skills for implementation details. These notes
explain technical prerequisites and evidentiary limits; they do not define an
engagement stage order, dispatch policy, or report lifecycle. Tool availability and
argument names come from the live schema. Authorization comes from the RoE.

## Static and runtime analysis

APK/IPA hashes, package/bundle IDs, signatures and build versions tie evidence to
an app. apktool/jadx expose Android resources and bytecode; class-dump and platform
analysis expose iOS interfaces. Exported components, intent filters, WebView bridges,
embedded URLs and apparent secrets are candidates whose reachability depends on
permissions, calling identity and configuration. Reuse existing Android/iOS skills.

## Dynamic evidence interpretation

Emulator/device OS, instrumentation state, trust store and root/jailbreak changes
can alter the observed behavior. Frida hooks or TLS-pinning bypasses enable analysis;
they are not themselves proof of a remote app vulnerability. Attribute captured
traffic to the app and session. A key embedded in a bundle may be public by design;
claimed access needs authorized backend evidence and a baseline showing what the
key grants. Separate local device effects from backend or cross-user impact.
