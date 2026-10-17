---
name: deconfliction-template
description: "Deconfliction plan generator: distinguish authorized engagement activity from real incidents."
metadata:
  skillogy:
    version: 1
  subdomain: planning
  when_to_use: "create deconfliction plan, red-team identifiers, SOC notification, distinguish engagement activity"
  tags: deconfliction, planning, soc
  upstream_ref: "Soundwave deconfliction template — DeconflictionPlan schema"
---

# Deconfliction Plan Generator

## When to Use

When designing or revising deconfliction, use the system-provided `plan/roe.json` and any current CONOPS and contact plan as context.

## Workflow

1. Preserve confirmed decisions from an existing plan. Use the same `engagement_name` as RoE.
2. Record approved source IPs, user-agent markers, tool hashes, or testing windows in `identifiers`. Each entry needs `type` and `value`; put context in `description`. Do not invent infrastructure or claim a marker is configured when it is only proposed.
3. Set `notification_procedure` for the active phases authorized by RoE. Use the confirmed SOC contact from the contact plan when one exists. An absent contact or endpoint does not authorize contacting a third party.
4. Populate `deconfliction_code` only with an operator-confirmed code; otherwise retain its empty default. Identify missing material coordination decisions.
5. Validate `plan/deconfliction.json` against `DeconflictionPlan`. Deconfliction identifiers never expand scope or override abort triggers. Bundle publication follows the selected planning workflow.

## Document schema (derived from the validator's model)

`plan/deconfliction.json` must validate against `DeconflictionPlan`. Fields not listed here are not part of the model and are ignored by the validator.

| field | type | required | allowed values / nested keys |
| --- | --- | --- | --- |
| `engagement_name` | str | yes |  |
| `identifiers` | list[DeconflictionEntry] | no | each object: `type`, `value`, `description` |
| `notification_procedure` | str | no |  |
| `soc_contact` | str | no |  |
| `deconfliction_code` | str | no |  |

`DeconflictionEntry` object:

| field | type | required | allowed values |
| --- | --- | --- | --- |
| `type` | str | yes |  |
| `value` | str | yes |  |
| `description` | str | no |  |
