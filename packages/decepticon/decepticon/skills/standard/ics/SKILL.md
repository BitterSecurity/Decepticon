---
name: ics-overview
description: >
  Use when the target is an industrial control system or operational technology
  network running Modbus, BACnet, S7Comm/S7Comm Plus, DNP3, OPC-UA, or any
  PLC/HMI/SCADA stack. Engagements MUST set RoE flag
  industrial_safety_critical=true; this catalog gates every write-scope
  operation behind explicit operator confirmation regardless of HITL middleware.
metadata:
  skillogy:
    version: 1
  subdomain: ics
  when_to_use: "ics ot industrial control modbus bacnet s7comm dnp3 opcua plc hmi scada safety-critical operational technology"
  tags: ics, ot, scada, plc, hmi, modbus, bacnet, s7, dnp3, opcua
  mitre_attack: T0800, T0801, T0859, T0830, T0814, T0846
  safety_critical: true
---

# ICS / OT Operator Skill Catalog

Industrial engagements are not application security with longer rules of
engagement — they are a different discipline. A miswritten Modbus coil
on a real plant kills people. This catalog is **read-mostly by default**;
every write-scope skill carries an explicit safety gate.

## Hard rules

1. **No writes without OPPLAN.safety_critical confirmation**. The
   middleware refuses writes when the active OPPLAN objective does not
   carry `safety_critical_confirmed=true`. Bypass requires operator
   signature in `/workspace/safety-attestation.txt`.
2. **Read-only protocol discovery first**. Identify what's on the wire
   before any active probing. Many ICS protocols are unauthenticated;
   a single malformed read can crash an old PLC.
3. **Out-of-band physical safety**. The blue team includes plant ops.
   A ConOps with `blue_team.plant_ops_phone` is mandatory for engagements
   on active production lines.

## Playbooks

> **Inline technique reference — not separately loadable skills.** The entries below
> are summarized here for direct use; there is no separate `SKILL.md` to open for
> each. Do NOT call the skill loader on them — apply the technique with your tools
> using this summary and the protocol assessment guidance in this file.


| Technique | Use for |
|---|---|
| **modbus-discovery** | Read-only Modbus TCP/RTU enumeration, function code 3/4 polling |
| **modbus-write** | **GATED** Write coils / registers; safety gate enforced |
| **bacnet-discovery** | BACnet/IP Who-Is, object enumeration, device profile |
| **s7comm** | Siemens S7 / S7Comm Plus enumeration via Snap7 / python-snap7 |
| **dnp3** | DNP3 outstation / master discovery; integrity poll |
| **opcua** | OPC-UA browse, anonymous auth check, certificate analysis |
| **hmi-web** | HMI web stacks (Wonderware, Iconics, Schneider) — known CVEs |
| **engineering-software** | TIA Portal / Studio 5000 / Unity Pro project extraction |

## Protocol assessment

1. **Passive observation**: tap a SPAN port if available. Identify protocols
   on the wire (`tshark -Y modbus || tshark -Y bacnet || ...`).
2. **Network-layer discovery**: nmap with `-sV --script modbus-discover`,
   `bacnet-info`, `s7-info`, `dnp3-info` (NSE scripts ship in Kali by
   default; some are slow — set `-T2` for production networks).
3. **Function-code-3 polling**: read holding registers from every Modbus
   device discovered. Log register maps to the knowledge graph as
   `:Service` nodes with `protocol=modbus`.
4. **Identify the safety integrity level (SIL)** of any device touched.
   SIL 3+ devices NEVER get write probes without plant-ops sign-off.
5. **Engineering software attack path**: if you can reach the engineering
   workstation, extract the project archive (.s7p, .acd, .stp). The
   project file is the crown jewel — it reveals the entire process model.

## Detection gap

ICS networks rarely have host-based detection on PLCs/RTUs themselves —
the detection stack lives on the engineering workstation, the historian,
and any IT/OT gateway. Relevant Sigma detection patterns include:

- Function-code anomalies (write to coils outside normal ranges).
- Connection sources outside the documented MES/SCADA IP set.
- TIA Portal / Studio 5000 project download events.

## Out of scope by default

Active glitching of PLC firmware; firmware upload to PLCs; safety
controller writes — all of these require an explicit RoE annex signed
by the asset owner. The default ICS RoE template in `soundwave/` includes
this annex skeleton.
