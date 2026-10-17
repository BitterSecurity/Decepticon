---
name: wireless-overview
description: >
  Top-level index for the Decepticon 802.11 wireless attack suite. Routes the
  WirelessOperator to the correct leaf skill based on the target AP's crypto
  column (PSK / SAE / MGT / WPS) and engagement posture. BLE, Zigbee, Z-Wave,
  LoRaWAN, and sub-GHz live under iot/ by design — link provided below to
  prevent duplication.
allowed-tools: Bash Read Write
metadata:
  skillogy:
    version: 1
  subdomain: wireless
  when_to_use: "Wi-Fi, 802.11, WPA2, WPA3, EAP, enterprise, evil-twin, deauth, WPS, PSK, SAE, wireless attack, airspace, WLAN, rogue AP"
  tags:
    - wifi
    - 802.11
    - wpa2
    - wpa3
    - eap
    - evil-twin
    - deauth
    - wps
  mitre_attack: T1040, T1557, T1110.001
---

# 802.11 Wireless Attack Suite — Operator Index

> Verify the configured hardware mode and authorized wireless scope
> before using a leaf skill. This index maps the target's crypto mode
> to the applicable techniques.

## Playbook table

| Leaf skill | Crypto column / trigger | Primary MITRE | Status |
|---|---|---|---|
| [wpa2-psk](wpa2-psk/SKILL.md) | `WPA2 PSK`, `WPA PSK` | T1040, T1110.001 | shipped |
| [wpa3-sae](wpa3-sae/SKILL.md) | `WPA3 SAE`, `WPA2 WPA3` transition mode | T1557, T1040 | shipped |
| [wpa-enterprise-eap](wpa-enterprise-eap/SKILL.md) | `MGT`, `WPA-Enterprise`, `802.1X` | T1557, T1110.001 | shipped |
| [wps-pixie-dust](wps-pixie-dust/SKILL.md) | WPS column non-empty, `WPS` flag in wash | T1110.001, T1040 | shipped |
| [evil-twin-karma](evil-twin-karma/SKILL.md) | Open / PSK, PNL probe leakage, captive portal | T1557, T1556 | shipped |
| [deauth-pmf](deauth-pmf/SKILL.md) | Any target needing client reconnect or 802.11w posture finding | T1498, T1040 | shipped |
| [krack-fragattacks](krack-fragattacks/SKILL.md) | Legacy / embedded supplicant, key-reinstallation / fragmentation test | T1557, T1040 | shipped |

> BLE GATT, Zigbee Touchlink, Z-Wave, LoRaWAN, and sub-GHz attacks
> are scoped to `standard/iot/`. Cross-reference that suite when the
> objective targets non-802.11 RF.

## Hardware mode pointer

Leaf skills require the configured mode and its actual hardware access:

```
mode = plan/roe.json:machine_enforcement.wireless.mode
  "in_sandbox"  → USB passthrough, monitor mode inside Kali
  "dropbox"     → ssh <dropbox> -- '<cmd>' for every wireless op
  "none"        → refuse, return outcome=blocked
```

## Crypto-mode decision tree

```
airodump-ng --write-interval 1 --output-format csv ...
Read the ENC/CIPHER/AUTH columns:

  ENC=WPA2, AUTH=PSK          → wpa2-psk
  ENC=WPA3, AUTH=SAE          → wpa3-sae
  ENC=WPA2+WPA3, AUTH=SAE+PSK → wpa3-sae (transition-mode downgrade path)
  AUTH=MGT / 802.1X            → wpa-enterprise-eap
  WPS column non-empty          → wps-pixie-dust (run in parallel with PSK path)
  Open / no credential needed  → evil-twin-karma (KARMA/portal capture)

After selecting the primary leaf, always check:
  - deauth-pmf: needed if Path B (four-way) is chosen OR as standalone PMF finding
  - krack-fragattacks: applicable when target is legacy/embedded/poor-patch-cadence
```

## KG node contract

All wireless leaf skills use the same node types:

| Node kind | Typical props |
|---|---|
| `Network` | ssid, bssid, channel, crypto, pmf_state |
| `Host` | mac, oui, last_seen_bssid |
| `Credential` | secret_type, ssid, bssid, psk/eap_identity/eap_challenge |
| `Finding` | title, cve_ids (if applicable), severity, remediation |

## OPSEC posture cross-reference

| posture | techniques permitted |
|---|---|
| `stealth` | PMKID (wpa2-psk Path A), passive PMF detect (deauth-pmf), Pixie-Dust only |
| `standard` | + targeted deauth (1 frame), EAP capture, WPS Pixie-Dust |
| `loud` | + broadcast deauth, evil-twin, KARMA, beacon flood, online WPS brute |

> Evil-twin always requires explicit `permitted_actions: evil_twin` in
> `plan/roe.json` regardless of posture.
