---
name: contract-auditor-specialist-methods
description: Evidence-led contract auditor technical methods and evidence interpretation for the relevant environment.
metadata:
  skillogy:
    version: 1
  subdomain: smart-contracts
  upstream_ref: "Decepticon standard contract_auditor core prompt and skills/standard/contracts technique references"
  when_to_use: "contract_auditor investigation methodology technical evidence handoff"
---

# Technical methods and evidence

Use the relevant existing technique skills for implementation details. These notes
explain technical prerequisites and evidentiary limits; they do not define an
engagement stage order, dispatch policy, or report lifecycle. Tool availability and
argument names come from the live schema. Authorization comes from the RoE.

## Solidity/EVM analysis

Slither and `solidity_scan` produce pattern candidates. Confirm the relevant caller,
modifier, storage state and external interaction instead of inferring impact from a
pattern name. The legacy `slither_ingest` backend is separate from the engagement
KG; neither scanner ingestion nor a graph edge proves exploitability. Foundry
harnesses can isolate reentrancy, rounding, access-control and invariant failures
against a pinned chain state; preserve compiler flags, fork block and test inputs.

## Integration and upgrade context

Oracle analysis needs price source, decimal handling, update cadence, liquidity,
TWAP window and consumers. Flash-loan availability alone is not a profitable
manipulation. Compare callbacks and read-only external calls with state consistency
at each boundary. Upgrade review includes storage layout, proxy authorization,
initializer exposure and implementation initialization. Diff hunks guide attention,
but unchanged callers can carry the affected trust boundary. Use existing contracts
skills for mechanism-specific harnesses. Financial loss estimates require explicit
assumptions; they are not interchangeable with a class-based CVSS number.
