---
name: wireless-operator-specialist-methods
description: Evidence-led wireless operator technical methods and evidence interpretation for the relevant environment.
metadata:
  skillogy:
    version: 1
  subdomain: wireless
  upstream_ref: "Decepticon standard wireless_operator core prompt and skills/standard/wireless technique references"
  when_to_use: "wireless_operator investigation methodology technical evidence handoff"
---

# Technical methods and evidence

Use the relevant existing technique skills for implementation details. These notes
explain technical prerequisites and evidentiary limits; they do not define an
engagement stage order, dispatch policy, or report lifecycle. Tool availability and
argument names come from the live schema. Authorization comes from the RoE.

## Capture context

Monitor-mode capability, adapter/channel, regulatory domain, band and location
bound what a capture can establish. Preserve SSID/BSSID, encryption, PMF settings,
client identifiers and capture timestamps with the actual packet artifact. Nearby
BSSIDs are observations, not authorized targets. Sandbox versus remote-radio modes
change where commands execute; use the configured hardware path.

## Protocol-specific evidence

WPA2 handshakes/PMKID artifacts, WPA3 SAE traces, enterprise EAP exchanges, BLE GATT
and Zigbee frames have different prerequisites and replay properties. Use the
existing matching wireless skills. A captured handshake is not a cracked PSK;
a cracked key is not proof of access to every network sharing a name. Deauth and
evil-twin traffic are active transmissions requiring explicit authorization. Compare
observed authentication or association effects with baseline traffic and account
for client roaming, retries and unrelated RF interference.
