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
- **RESOLVED** — present last run, absent now (closed / patched).
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
