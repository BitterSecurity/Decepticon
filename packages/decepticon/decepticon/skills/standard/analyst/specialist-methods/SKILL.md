---
name: analyst-specialist-methods
description: Evidence-led analyst technical methods and evidence interpretation for the relevant environment.
metadata:
  skillogy:
    version: 1
  subdomain: analyst
  upstream_ref: "Decepticon standard analyst core prompt and skills/standard/analyst technique references"
  when_to_use: "analyst investigation methodology technical evidence handoff"
---

# Technical methods and evidence

Use the relevant existing technique skills for implementation details. These notes
explain technical prerequisites and evidentiary limits; they do not define an
engagement stage order, dispatch policy, or report lifecycle. Tool availability and
argument names come from the live schema. Authorization comes from the RoE.

## Source and dependency evidence

Source taint analysis connects an attacker-controlled entry to a dangerous sink,
including guards, transformations, authentication and deployment configuration.
A semgrep/SARIF hit is a candidate, not runtime reachability. `kg_ingest("sarif",
path)` retains scanner observations; manually confirmed paths need their own
provenance. Use the existing analyst vulnerability-class skills for sink semantics.
A dependency name/version must match the installed artifact and advisory range;
feature flags, patches and reachable call sites can invalidate a version-only match.
KEV status or upstream CVSS does not establish the local target's severity.

## Patch comparisons and fuzzing

Compare security-sensitive changes with callers and surrounding unchanged code.
A new guard is evidence of changed behavior, not proof of an undisclosed bug.
Record exact revisions, build flags, runtime versions and configuration. Fuzz
harnesses should preserve the real parser boundary and relevant invariants:
libFuzzer/AFL++ for native code, Atheris for Python, Jazzer for JVM targets, and
cargo-fuzz for Rust. Minimize a reproducible crash, retain sanitizer output and
seed, and distinguish harness defects from target defects. A crash is not by itself
arbitrary code execution or production reachability.

## Mechanisms across instances

For developer tools, trace untrusted project configuration through workspace trust,
environment overrides, plugin discovery and command execution. Use the existing
trust-boundary and pattern-exhaustion skills for precise data-flow checks. A shared
root cause supports a search for related instances; each candidate still needs its
own reachability and evidence. Graph paths encode hypotheses until their actual
preconditions and connecting effects are established.
