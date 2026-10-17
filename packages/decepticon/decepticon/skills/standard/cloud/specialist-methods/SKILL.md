---
name: cloud-hunter-specialist-methods
description: Evidence-led cloud hunter technical methods and evidence interpretation for the relevant environment.
metadata:
  skillogy:
    version: 1
  subdomain: cloud
  upstream_ref: "Decepticon standard cloud_hunter core prompt and skills/standard/cloud technique references"
  when_to_use: "cloud_hunter investigation methodology technical evidence handoff"
---

# Technical methods and evidence

Use the relevant existing technique skills for implementation details. These notes
explain technical prerequisites and evidentiary limits; they do not define an
engagement stage order, dispatch policy, or report lifecycle. Tool availability and
argument names come from the live schema. Authorization comes from the RoE.

## Policy and resource interpretation

`iam_policy_audit`, `tfstate_audit` and `k8s_audit` surface candidate capabilities.
Compare policy conditions, explicit denies, trust policies, permission boundaries,
organization controls and resource policies before claiming effective access.
A Terraform value may be historical, redacted, revoked or valid only in another
environment; record state version and resource provenance. Credential exposure and
credential validity are different claims.

## Kubernetes boundaries

Interpret role/cluster-role bindings with namespace, subject and effective verbs.
Privileged pods, hostPath mounts and exposed dashboards are context, not automatic
host compromise. Admission controls, mount permissions, runtime identity and node
isolation may prevent the inferred transition. Use existing cloud/k8s technique
skills for the relevant authorization and configuration tests.

## Metadata requests and SSRF evidence

Identify the provider before selecting metadata behavior with `metadata_endpoints`.
Token requirements, hop limits, network policy and application URL handling affect
reachability. Correlate a request with an owned canary or returned resource evidence;
a timeout alone does not establish internal access. Keep response attribution,
request identity and credential scope separate. The assigned objective determines
whether any further use of a captured credential is needed and authorized.
