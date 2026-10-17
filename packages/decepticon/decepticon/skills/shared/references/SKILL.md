---
name: references
description: External knowledge integration — HackerOne reports, PayloadsAllTheThings, Book of Secret Knowledge, CVE PoC corpora, bug bounty methodologies, and reference pentest agent architectures. Use these to calibrate, look up payloads, and accelerate research.
metadata:
  skillogy:
    version: 1
  subdomain: orchestration
  when_to_use: "external reference hackerone payloadsallthethings bsk book of secret knowledge cve poc bug bounty methodology pentest agent calibration"
  upstream_ref: "HackerOne disclosed reports + PayloadsAllTheThings + Book of Secret Knowledge + curated CVE PoC corpora"
---

# External References Skill

Decepticon ships with a curated catalogue of high-signal third-party
resources. You can either query the metadata directly or fetch the
full repo into the sandbox for grep-level access.

## Catalogue (13 entries)

| Category        | Slug                    | What's inside                                                  |
|-----------------|-------------------------|----------------------------------------------------------------|
| report-corpus   | hackerone-reports       | Every public H1 disclosure — calibrate CVSS + reward           |
| payload-library | payloads-all-the-things | Canonical payloads for every web vuln class                    |
| cheat-sheet     | book-of-secret-knowledge| Pentester one-liners + recipes                                 |
| reference-agent | pentagi                 | vxcontrol multi-agent pentest platform                         |
| reference-agent | pentestgpt              | Academic pentest LLM agent                                     |
| reference-agent | shannon                 | Keygraph autonomous security agent                             |
| reference-agent | strix                   | Usestrix autonomous pentest agent                              |
| reference-agent | hexstrike-ai            | 150+ tools exposed via MCP                                     |
| reference-agent | neurosploit             | LLM + Metasploit driver                                        |
| tool-index      | redteam-tools           | Kill-chain-phase tool index                                    |
| cve-poc         | trickest-cve            | Continuous CVE + PoC corpus                                    |
| cve-poc         | penetration-testing-poc | Second-opinion PoC mirror                                      |
| methodology     | all-about-bug-bounty    | Per-class bug bounty methodology                               |

## When to use each

### When a reference could clarify the assigned question
- `ref_suggest(vuln_class="...", goal="recon")` to see which references
  could inform the current lane. Reference retrieval is conditional on a
  knowledge gap, not a prerequisite of every engagement's first iteration.

### When you need a payload RIGHT NOW
- `payload_search(vuln_class="ssrf", keyword="imds")` — no network
  needed, bundled offline. Covers sqli, ssrf, xss, ssti, deser, rce,
  xxe, idor, jwt, oauth, lfi, cmdi, graphql, prompt-injection,
  proto-pollution.

### When you need the FULL library
- `ref_fetch("payloads-all-the-things")` — one-time clone into
  `/workspace/.references/payloads-all-the-things/` (~30s).
- `ref_grep("payloads-all-the-things", "DNS rebinding")` to find
  specific variants.

### Report calibration
- `ref_fetch("hackerone-reports")` then `ref_grep("hackerone-reports",
  "<your bug class>")` to find similar disclosed reports.
- Copy the report structure that got paid — it's validated by triagers.

### CVE → PoC
- After `cve_lookup` returns a CVE ID, check `ref_fetch("trickest-cve")`
  then `ref_grep("trickest-cve", "CVE-2024-1234")` for a working PoC.
- Cross-reference with `penetration-testing-poc` for a second opinion.

### Tool discovery
- `ref_grep("redteam-tools", "lateral movement")` to find the right
  tool for a phase you haven't touched yet.

## Workflow

1. Identify the vuln class or phase you're working on.
2. `ref_suggest` to get the top 3-5 relevant references.
3. For each, decide: metadata only, offline bundled payload, or full
   repo clone.
4. Use `ref_grep` to surface the specific payload / writeup.
5. Record any borrowed payloads / patterns as `hypothesis` nodes in
   the graph until validated.

## Shannon, Strix, HexStrike, Pentagi, PentestGPT, NeuroSploit

These are *reference architectures*, not tools to run. Read them to
steal good ideas about:
- Multi-agent orchestration patterns
- Tool wrapping idioms
- Prompt engineering for pentest workflows
- How other projects handle memory / state / observability

Don't reinvent wheels — if one of them solved a problem elegantly,
borrow the pattern into Decepticon's next iteration.
