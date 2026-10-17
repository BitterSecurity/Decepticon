---
name: kill-chain-analysis
description: "Kill chain analysis and attack path decision-making — findings analysis, attack vector selection, target prioritization, causal prerequisites and evidence confidence."
allowed-tools: Read
metadata:
  skillogy:
    version: 1
  subdomain: orchestration
  kind: analytic
  when_to_use: "analyze findings, select attack vector, prioritize targets, which technique, attack path, next attack, choose approach, alternative vector, blocked what next"
  tags: kill-chain, decision-making, attack-path, target-prioritization, technique-selection
  upstream_ref: "Lockheed Martin Cyber Kill Chain + Decepticon attack-path planner — analytic decision support, no direct attack technique"
---

# Causal attack-path analysis

An attack path is a hypothesis about compatible security primitives, not a list of
technique names. Each link needs an input/precondition, resulting capability,
identity, target, state and evidence. A prior primitive must actually supply what
the next consumes in the same relevant environment. Different tenants, expired
tokens, incompatible privileges or unproven reachability can break an apparent chain.

## Evidence and counterexamples

Mark links observed, independently reproduced, inferred or contradicted. Keep the
supporting artifact and a competing benign explanation. Scanner hits, topology
edges, apparent secrets and service versions are leads rather than proven transitions.
An observed access boundary does not establish a bypass. A plausible destructive
tail may explain risk but cannot be reported as executed or increase confirmed
impact without sufficient supporting evidence. Path reasoning does not authorize
new target interactions.

## Comparative analysis

Compare candidate explanations by prerequisite confidence, information gained by
a bounded test, expected impact, uncertainty, operational cost and permitted noise.
Do not assign a universal order such as credentials before misconfiguration, or
assume every mission targets Domain Admin/crown jewels. The task's actual success
criterion determines relevance. Numeric probabilities without data are assumptions,
not measurements. Detection and execution failure need attributed evidence; a
blocked request does not by itself prove either target safety or a need for evasion.

## Defensive interpretation

Identify the controls that interrupt each demonstrated link. Distinguish a missing
control from unavailable telemetry; MTTD needs correlated timestamps on a common
clock. A remediation recommendation should name the failed boundary and an observable
retest condition. The selected workflow owns which objectives to execute, allowed
expansion, verification ownership and final synthesis.
