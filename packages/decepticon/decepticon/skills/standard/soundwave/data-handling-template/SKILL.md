---
name: data-handling-template
description: "Data handling plan generator — evidence retention, encryption, chain-of-custody, compliance frameworks (GDPR / HIPAA / PCI-DSS / SOC2)."
allowed-tools: Read Write Edit
metadata:
  subdomain: planning
  when_to_use: "create data handling plan, evidence retention, chain-of-custody, GDPR, HIPAA, PCI-DSS, compliance, PII handling"
  tags: data-handling, evidence, retention, encryption, chain-of-custody, compliance, gdpr, hipaa
  upstream_ref: "Soundwave data-handling template — evidence retention, encryption, chain-of-custody, compliance"
---

# Data Handling Plan Generator

The data handling plan defines **what evidence the agent collects, where it lives, how long it's kept, and who can read it**. Replaces the deprecated free-form `RoE.data_handling` string with structured per-class fields.

## When to Use

- After the system-provided RoE is available (its constraints and scope drive which data classes appear)
- User says "create data handling", "retention policy", "evidence storage", "PII handling", "compliance"

## Workflow

### Step 1: Start From the Schema Defaults

The `DataHandlingPlan` schema seeds four default classes — credentials, pii, source-code, business-data — with conservative retention. **Keep these by default**; override only when the engagement requires stricter or looser rules.

### Step 2: Add Engagement-Specific Classes

Based on the interview:

| Engagement type | Likely additional classes |
|---|---|
| Healthcare client | `health-records` (classification: secret, retention: 7 days, framework: HIPAA) |
| Financial client | `cardholder-data` (classification: secret, retention: 0 days — never store, framework: PCI-DSS) |
| EU client / data subjects | Mark existing `pii` with framework: GDPR; consider `personal-data-eu` for stricter handling |
| Defense / classified | `controlled-unclassified` (classification: secret, retention: 0 days off-network) |

### Step 3: Set Evidence Storage Path

Default `"/workspace/<engagement>/evidence/"` works for sandbox-isolated engagements. Override only when:
- Engagement requires an external-bucket destination (S3 / Azure Blob with client KMS)
- Multiple engagement workspaces share an evidence repository

### Step 4: Compliance Frameworks

Set `compliance_frameworks` from the interview. Common entries: GDPR, HIPAA, PCI-DSS, SOC2, NIST 800-53, FedRAMP, ISO 27001.

The orchestrator (Decepticon) reads this list and refuses to start objectives that violate the matching framework's evidence-handling rules.

### Step 5: Purge Hard Cap

`purge_after_days` is the GLOBAL upper bound — every artifact older than this is deleted regardless of per-class retention. Default 90 days; reduce for engagements with tighter regulatory exposure.

## Validation Checklist

Before writing `plan/data-handling.json`:

- [ ] At least one `data_class` is `credentials`-equivalent
- [ ] Every class has `retention_days >= 0`
- [ ] `purge_after_days >= max(class.retention_days)` (otherwise classes get cut short)
- [ ] If `compliance_frameworks` includes HIPAA / GDPR / PCI-DSS, matching data classes exist
- [ ] `chain_of_custody=True` for any engagement that may produce findings

## Anti-patterns

- Setting `retention_days=0` for `credentials` without explicit operator approval — agent then can't reference creds across phases
- Disabling `encryption_at_rest` for any restricted+ class — straight compliance violation
- Listing HIPAA in `compliance_frameworks` without a `health-records` class — orchestrator will refuse objectives that touch PHI

## Output

Write to `plan/data-handling.json` validating against `decepticon.core.schemas.DataHandlingPlan`.
