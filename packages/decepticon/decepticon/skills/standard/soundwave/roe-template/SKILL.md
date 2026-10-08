---
name: roe-template
description: Read and interpret the system-provided Rules of Engagement while planning with the operator.
allowed-tools: Read
metadata:
  subdomain: planning
  when_to_use: "read RoE scope authorization boundaries plan mode"
  tags: roe, scope, engagement, authorization
---

# Rules of Engagement in Plan mode

The product creates `plan/roe.json` from engagement setup and manages changes through its own UI and authorization controls. Soundwave may read it, but must never create, edit, replace, or sign it with filesystem tools. If it is absent, ask the operator to complete system setup outside Plan mode before planning target-facing work. If the operator wants a scope or exclusion change, direct them to the system-managed engagement settings; do not silently widen planning documents.

Read the existing RoE before writing other planning documents. Use its exact in-scope and out-of-scope targets, prohibited and permitted actions, authorization source, machine-enforcement rules, and any active time constraints. Do not invent an engagement type or a testing window when the system did not supply one. Ask the operator about choices for Soundwave-owned planning documents only when those choices remain material and unconfirmed.

The RoE constrains the threat profile, CONOPS, deconfliction, contact, data handling, abort, and cleanup documents. A planning draft or `complete_engagement_planning` call does not authorize a Red run or modify the RoE.
