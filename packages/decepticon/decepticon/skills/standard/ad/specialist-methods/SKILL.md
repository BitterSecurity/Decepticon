---
name: ad-operator-specialist-methods
description: Evidence-led ad operator technical methods and evidence interpretation for the relevant environment.
metadata:
  skillogy:
    version: 1
  subdomain: active-directory
  upstream_ref: "Decepticon standard ad_operator core prompt and skills/standard/ad technique references"
  when_to_use: "ad_operator investigation methodology technical evidence handoff"
---

# Technical methods and evidence

Use the relevant existing technique skills for implementation details. These notes
explain technical prerequisites and evidentiary limits; they do not define an
engagement stage order, dispatch policy, or report lifecycle. Tool availability and
argument names come from the live schema. Authorization comes from the RoE.

## Directory topology and effective rights

BloodHound collector exports describe topology and potential permissions; they do
not prove that a session, credential, or multi-hop path is usable. `bh_ingest_zip`
loads its own topology backend; confirmed engagement observations use `kg_record`.
Distinguish replication rights (GetChanges/GetChangesAll), group membership, nested
ACL inheritance, and effective access on the actual object. DCSync capability does
not require Domain Admin membership; replication testing still requires its specific
authorization. Kerberos SPN and preauthentication settings identify different ticket
exposure hypotheses; record encryption type and account policy rather than assuming
any captured hash is crackable.

## Certificates, delegation and managed secrets

ADCS assessment compares template enrollment rights, subject/SAN control, EKUs,
manager approval and CA settings; an ESC label alone is not a proven escalation.
Use the existing ADCS and Kerberos technique skills for version-specific checks.
Constrained, unconstrained and resource-based constrained delegation have different
principal, SPN and ticket prerequisites. Distinguish ReadLAPSPassword and
ReadGMSAPassword rights from actual retrievable and usable credentials. GPO edits,
shadow credentials and computer-object writes are state changes, not read-only
permission checks. Capture the effective principal, object, permission source and
before/after state for any separately authorized validation.
