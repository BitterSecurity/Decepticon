---
name: ics-operator-specialist-methods
description: Evidence-led ics operator technical methods and evidence interpretation for the relevant environment.
metadata:
  skillogy:
    version: 1
  subdomain: ics-ot
  upstream_ref: "Decepticon standard ics_operator core prompt and skills/standard/ics technique references"
  when_to_use: "ics_operator investigation methodology technical evidence handoff"
---

# Technical methods and evidence

Use the relevant existing technique skills for implementation details. These notes
explain technical prerequisites and evidentiary limits; they do not define an
engagement stage order, dispatch policy, or report lifecycle. Tool availability and
argument names come from the live schema. Authorization comes from the RoE.

## Industrial protocol evidence

Passive captures and read-only identification can reveal vendor, firmware, unit IDs,
function codes, exposed management paths and protocol authentication. Tools such as
modbus-cli, python-snap7 and OPC UA clients differ in default behavior; consult the
existing ICS technique skills and confirm a command is read-only before use.
A writable register or PLC interface does not establish process-level impact.

## State and safety context

Interpret register values against device model, scaling, process state and the
operator's safety envelope. Capture baseline state and restoration evidence for
any separately authorized write test. An emulator result does not prove that the
same operation is safe on a live controller. Preserve network segment, device
identity and observation timestamps; loss of telemetry during a test is a blocker,
not evidence of successful control.
