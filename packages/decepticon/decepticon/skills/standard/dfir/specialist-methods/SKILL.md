---
name: forensicator-specialist-methods
description: Evidence-led forensicator technical methods and evidence interpretation for the relevant environment.
metadata:
  skillogy:
    version: 1
  subdomain: dfir
  upstream_ref: "Decepticon standard forensicator core prompt and skills/standard/dfir technique references"
  when_to_use: "forensicator investigation methodology technical evidence handoff"
---

# Technical methods and evidence

Use the relevant existing technique skills for implementation details. These notes
explain technical prerequisites and evidentiary limits; they do not define an
engagement stage order, dispatch policy, or report lifecycle. Tool availability and
argument names come from the live schema. Authorization comes from the RoE.

## Evidence-source selection

Memory analysis (Volatility 3), disk/super-timeline work (Plaso/log2timeline), Windows
registry/EVTX analysis and network parsing (tshark/Zeek) answer different questions.
Record acquisition source, hash, timezone, clock offset, collection gaps and parser
version. Work on copies; a parsed view must remain traceable to original bytes.

## Correlation and uncertainty

Prefer shared process/request/session identifiers over temporal proximity. A nearby
log line is a lead, not causation. Missing telemetry can reflect collection loss,
retention or parser limitations; it is not proof an operation did not occur. IOC
matches need context (path, signer, ownership, observed behavior) to distinguish
benign reuse. ATT&CK mappings require a supported technique, not merely a tool name.
Existing dfir memory, disk, log and network skills provide format-specific analysis.
