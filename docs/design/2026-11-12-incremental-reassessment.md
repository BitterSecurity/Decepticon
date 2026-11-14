# Incremental re-assessment (baseline diff) — design proposal

Status: proposal — design only, needs maintainer decisions before implementation.
Baseline: `main` after scheduled engagements (#1183). No code in this document.

## Decision already taken

Periodic re-assessment runs **from scratch** every time (confirmed 2026-11-12).
`reassess` mode (#1183) starts a fresh thread per tick and re-scans the whole
target. This document does **not** change that: a full run still happens. It
proposes a layer *on top* that compares each full run to the previous one and
reports only what changed. Incremental runs that skip work are explicitly not
proposed here.

## Goal

After a periodic re-assessment, surface **what changed since last time** instead
of re-reporting the entire surface:

- **NEW** — appeared this run (new open port, new subdomain, new vulnerability).
- **RESOLVED** — a previously confirmed finding has a comparable, completed
  recheck that explicitly confirms it is closed or patched.
- **UNCHANGED** — same as last run (suppressed in the delta report).
- **REGRESSED** — was RESOLVED earlier, NEW again (optional, high-signal).

This is the continuous-monitoring / CTEM value: the operator reads the weekly
delta, not the weekly full report.

## The hard part: finding identity

A diff needs a **stable identity** for a finding across runs, independent of the
per-run `FIND-NNN.md` numbering (which restarts each run). Two findings from two
runs are "the same" when their identity key matches.

Candidate identity key (to decide): a normalized tuple such as
`(asset, port/endpoint, service or finding class, a stable discriminator)`.
Examples:

- open port: `(host, port, protocol)`
- subdomain: `(fqdn)`
- web finding: `(host, path, finding_class)` — not the raw evidence, which
  changes run to run.

Getting this wrong causes churn: too strict → everything looks NEW every week;
too loose → real changes hide. This is the main design risk and the reason it is
a separate proposal.

Identity keys must be versioned and scoped to the tenant and engagement. The
web-finding example above is only a candidate: method, affected parameter or
component, and a stable vulnerability discriminator may be necessary to avoid
collapsing distinct findings on the same path. Secrets and volatile evidence
values must not become identity fields.

## Comparison correctness

Absence from a new report is not evidence of resolution. A failed, canceled,
rate-limited, time-window-paused or incomplete run cannot resolve earlier
findings or replace a complete baseline. An untested target or changed scope,
collector capability or check coverage must remain unknown/not comparable until
an appropriate recheck completes. Record scope/policy identity and coverage
with each snapshot so the report can explain that distinction.

Baseline writes must be atomic and ordered by the accepted run sequence, so a
late-finishing older run cannot overwrite a newer baseline. Baselines and
reports retain the engagement's storage/access controls and must never compare
findings across tenants. These are correctness constraints for the proposal;
they do not settle the implementation decisions below.

## Storage: baselines

Each completed re-assessment writes a **snapshot** of its findings' identity
keys (plus severity/status) to a per-engagement baseline store. The next run
diffs its snapshot against the stored one.

Options (to decide):

- **Workspace file** — `baselines/<run-ts>.json` under the engagement workspace.
  Simple, operator-visible, versioned with the engagement. Recommended for v1.
- **Knowledge graph partition** — richer queries, but couples the feature to the
  KG/Neo4j path.

Compare-to options: compare to the *immediately previous* run (rolling), or to a
*pinned* baseline an operator froze (drift from an approved state). Recommend
both, defaulting to rolling.

## Reporting

The final report gains a **delta section**: NEW and RESOLVED (and REGRESSED)
lists, with UNCHANGED collapsed to a count. The existing full `FIND-NNN.md`
output stays — the delta is an overlay, not a replacement, so a first run (no
baseline) degrades to "everything is NEW", i.e. the normal full report.

## Open decisions (for PR review)

1. **Identity key fields** per finding type — the core decision. Start with a
   small, well-defined set (ports, subdomains, named vulns) rather than "all
   findings".
2. **Normalization** — case, trailing dots, port/service canonicalization, how
   to treat evidence that legitimately varies (timestamps, nonces).
3. **Baseline store** — workspace file vs KG; retention (keep N snapshots).
4. **Comparison target** — rolling previous vs pinned baseline vs both.
5. **Where the diff runs** — a reporting step/tool after the run, or a middleware
   at run end. Recommendation: a reporting tool/step, so it is explicit and
   testable and does not touch the hot path.
6. **Interaction with `finding_verifier`** — diff on verified findings only, to
   avoid churn from unverified noise.

## Dependencies / sequencing

- Independent of external triggers; can land before or after.
- Benefits from, but does not require, a structured findings store. If findings
  stay as `FIND-NNN.md`, the snapshot step must extract identity keys from them.

## Verification plan (when implemented)

- Unit tests: identity-key extraction and normalization; diff classification
  (NEW/RESOLVED/UNCHANGED/REGRESSED) over crafted before/after snapshots; first
  run with no baseline = all NEW.
- Real engagement: two consecutive `reassess` runs against a fixture whose
  surface changes between them; the delta report lists exactly the injected
  change.
- Failed/incomplete coverage never produces a false RESOLVED finding. Verify
  scope and identity-version changes, different parameters on the same endpoint,
  tenant isolation and out-of-order completion without baseline loss.
