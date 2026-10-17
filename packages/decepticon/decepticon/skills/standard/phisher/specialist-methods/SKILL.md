---
name: phisher-specialist-methods
description: Evidence-led phisher technical methods and evidence interpretation for the relevant environment.
metadata:
  skillogy:
    version: 1
  subdomain: phishing
  upstream_ref: "Decepticon standard phisher core prompt and skills/standard/phisher technique references"
  when_to_use: "phisher investigation methodology technical evidence handoff"
---

# Technical methods and evidence

Use the relevant existing technique skills for implementation details. These notes
explain technical prerequisites and evidentiary limits; they do not define an
engagement stage order, dispatch policy, or report lifecycle. Tool availability and
argument names come from the live schema. Authorization comes from the RoE.

## Campaign artifact inspection

Existing phisher technique skills describe template rendering, landing-page forms,
domain/TLS configuration and protocol-specific token behavior. Inspect template
variables, links, redirect targets, certificate identity and event logging in an
operator-controlled test environment. A rendered lure is not a delivered message;
a delivery event is not a human interaction.

## Attribution of observed events

Correlate campaign, message, recipient and event IDs with timestamps. Link scanners,
mail gateways and preview bots can produce clicks without user action. A form
submission may contain synthetic data; a captured token can be expired or bound to
a different audience/device. Distinguish these from demonstrated authorized access.
Store sensitive material only in the protected workspace. Population selection,
deconfliction, send windows, rate policy and rollout decisions belong to the
selected engagement workflow and RoE, not this method reference.
