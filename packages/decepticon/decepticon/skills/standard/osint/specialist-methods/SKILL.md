---
name: osint-operator-specialist-methods
description: Evidence-led osint operator technical methods and evidence interpretation for the relevant environment.
metadata:
  skillogy:
    version: 1
  subdomain: osint
  upstream_ref: "Decepticon standard osint_operator core prompt and skills/standard/osint technique references"
  when_to_use: "osint_operator investigation methodology technical evidence handoff"
---

# Technical methods and evidence

Use the relevant existing technique skills for implementation details. These notes
explain technical prerequisites and evidentiary limits; they do not define an
engagement stage order, dispatch policy, or report lifecycle. Tool availability and
argument names come from the live schema. Authorization comes from the RoE.

## Public-source provenance

DNS/CT records, search results, repositories, breach references and internet-exposure
indexes have different freshness and ownership guarantees. Record source URL,
retrieval time, record time and what was directly observed. A certificate SAN or
search snippet is a candidate asset reference; it is not proof of current ownership,
reachability or inclusion in scope. NXDOMAIN for a guessed label is a failed lookup,
not an observed service.

## Correlation and sensitive material

Correlate organization/domain/identity claims using independent public evidence;
shared hosting or a matching brand string alone is insufficient. Redact secret
values in general notes and retain protected provenance separately. Never submit a
candidate credential to a third-party checker. Existing OSINT domain, identity,
code-leak and infrastructure skills cover collector-specific methods. Passive
collection does not establish current credential validity or target impact.
