---
name: iot-operator-specialist-methods
description: Evidence-led iot operator technical methods and evidence interpretation for the relevant environment.
metadata:
  skillogy:
    version: 1
  subdomain: iot
  upstream_ref: "Decepticon standard iot_operator core prompt and skills/standard/iot technique references"
  when_to_use: "iot_operator investigation methodology technical evidence handoff"
---

# Technical methods and evidence

Use the relevant existing technique skills for implementation details. These notes
explain technical prerequisites and evidentiary limits; they do not define an
engagement stage order, dispatch policy, or report lifecycle. Tool availability and
argument names come from the live schema. Authorization comes from the RoE.

## Firmware and hardware evidence

Firmware acquisition provenance, model/revision, image hash, partition offsets and
filesystem formats determine what static evidence means. Existing firmware-acquisition
and binwalk-extract skills cover image handling. Strings resembling keys may be
test fixtures or inactive defaults; trace consumers and configuration before
claiming exposure. Hardware bootloader, MTD and memory access require the actual
board/interface and write permissions; an unavailable dongle is an environment limit.

## Radio and backend boundaries

BLE GATT, Zigbee, Z-Wave, sub-GHz, LoRaWAN and ROS2/DDS use distinct identities,
pairing and replay protections. Use the matching existing IoT skill rather than
assuming a generic packet replay works across protocols. Retain packet provenance,
radio parameters, device identity and freshness evidence. A firmware credential and
successful authorized authentication are separate claims; neither implies unrestricted
backend access. Compare effects against a benign control without involving neighboring
devices or production safety systems.
