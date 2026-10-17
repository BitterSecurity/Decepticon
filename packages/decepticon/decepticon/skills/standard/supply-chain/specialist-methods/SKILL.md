---
name: supply-chain-operator-specialist-methods
description: Evidence-led supply chain operator technical methods and evidence interpretation for the relevant environment.
metadata:
  skillogy:
    version: 1
  subdomain: supply-chain
  upstream_ref: "Decepticon standard supply_chain_operator core prompt and skills/standard/supply-chain technique references"
  when_to_use: "supply_chain_operator investigation methodology technical evidence handoff"
---

# Technical methods and evidence

Use the relevant existing technique skills for implementation details. These notes
explain technical prerequisites and evidentiary limits; they do not define an
engagement stage order, dispatch policy, or report lifecycle. Tool availability and
argument names come from the live schema. Authorization comes from the RoE.

## Resolution and build boundaries

SBOMs, lockfiles, registry metadata and CI configuration describe different parts
of the dependency path. Compare name/version, registry precedence, integrity pins,
trusted publisher and actual resolver behavior. An unclaimed public name alone
does not prove internal dependency confusion. Similarly, an unpinned action is a
risk signal rather than proof that an attacker controls the executed revision.

## Harmless validation artifacts

Use an authorized isolated resolver/build environment and a clearly labeled benign
marker to attribute package resolution or build-step execution. Preserve resolver
configuration, exact dependency graph, build identity and logs. A local result
requires explicit deployment assumptions before claiming production exposure.
Existing supply-chain technique skills cover confusion, typosquatting and pipeline
injection mechanics. Do not publish functional malware or mutate an upstream
package to demonstrate a candidate boundary.
