---
name: bounty-report-formatter
description: Bug bounty report presentation, reproduction evidence and destination field conventions for HackerOne, Bugcrowd, Immunefi and GitHub Security Advisories.
metadata:
  skillogy:
    version: 1
  subdomain: reporting
  when_to_use: "bug bounty report hackerone h1 bugcrowd immunefi github security advisory ghsa submission triage cvss writeup"
  upstream_ref: "HackerOne / Bugcrowd / Immunefi / GitHub Security Advisory submission templates"
---

# Bug Bounty Report Formatter

Format validated findings as platform-ready bug bounty reports that
survive triage. Optimized for acceptance rate, not word count. The
standard this targets: 7.00 HackerOne signal — precision over volume.

## Title Convention

Format: `[Component]: [Vulnerability Class] via [Mechanism]`

Good titles (from real accepted advisories):
- `zrok: WebDAV drive backend follows symlinks outside DriveRoot, enabling host filesystem read/write`
- `Parse Server: cloud function validator bypass via prototype chain traversal`
- `Directus: TUS Upload Authorization Bypass Allows Arbitrary File Overwrite`
- `lodash: Code Injection via _.template imports key names`
- `AVideo: Unauthenticated SSRF via HTTP redirect bypass in LiveLinks proxy`
- `OpenFGA: Unauthenticated playground endpoint discloses preshared API key`

Rules:
- Component name first (the project/package name)
- Vulnerability class in plain English (not CWE numbers in the title)
- Mechanism must be specific (not "via user input" — say "via prototype chain traversal")
- No adjectives like "critical" or "severe" in the title
- Under 120 characters

## Report Template

The workflow selects the canonical record, requested destination schema and
disclosure action. This optional presentation example does not create a second
finding file or authorize submission. Destination-specific limits below are
examples; the selected program's current requirements take precedence.

```markdown
# {Title}

## Summary

{One paragraph. State what the bug is, where it lives (file:line or endpoint),
and what impact it has. Three sentences maximum.}

## Severity

**CVSS 3.1**: {full vector string} ({score} {severity_label})

| Metric | Value | Justification |
|--------|-------|---------------|
| AV | Network | Exploitable over HTTP |
| AC | Low | No special conditions |
| PR | None | No authentication required |
| UI | None | No user interaction |
| S | Unchanged | Impact limited to vulnerable component |
| C | High | Full read access to filesystem |
| I | None | No write capability demonstrated |
| A | None | No availability impact |

## Affected Version

- Package: {name}
- Version: {exact version or range}
- Commit: {commit hash if applicable}

## Steps to Reproduce

1. {Exact setup step — e.g., "Clone the repository: `git clone ...`"}
2. {Exact action — e.g., "Start the server: `npm start`"}
3. {Exact exploit — e.g., "Send the following request:"}

## Proof of Concept

\```bash
{Exact command that demonstrates the vulnerability}
\```

**Expected response (vulnerable):**
\```
{Exact output showing exploitation}
\```

**Baseline response (not vulnerable):**
\```
{Output from the same request without the payload}
\```

## Impact

{What an attacker can actually do with this bug. Only state impacts you
demonstrated in the PoC. Do NOT extrapolate to theoretical scenarios.}

## Remediation

{Specific code fix. Show the diff or the corrected code.}

\```diff
- const user = db.query(`SELECT * FROM users WHERE id=${req.params.id}`)
+ const user = db.query(`SELECT * FROM users WHERE id=$1`, [req.params.id])
\```
```

## Quality Checklist

Evidence and presentation questions for the requested destination:

- [ ] Title follows `Component: VulnClass via Mechanism` format
- [ ] Summary is ≤3 sentences
- [ ] CVSS vector string is complete (not just a number)
- [ ] Every CVSS metric has a justification (not default values)
- [ ] Steps to reproduce are exact commands, not descriptions
- [ ] PoC is a single copy-pasteable command
- [ ] Baseline/negative response is included (proves the PoC is meaningful)
- [ ] Impact describes only demonstrated effects
- [ ] Remediation is a specific code fix, not generic advice
- [ ] Affected version is exact (not "all versions" unless verified)
- [ ] Total report is under 500 words (excluding code blocks)
- [ ] No filler phrases: "an attacker could potentially", "this might allow",
      "it is recommended to", "in certain conditions"
- [ ] No severity inflation beyond what the PoC proves
Program scope and duplicate disposition belong to the workflow and remain
separate from technical validity and document quality.

## Platform-Specific Notes

### HackerOne
- Use their severity rating (maps from CVSS)
- Reference CWE numbers in the weakness field
- Attach PoC scripts as files if longer than 10 lines
- Include the CVSS calculator link: `https://www.first.org/cvss/calculator/3.1#CVSS:3.1/...`

### Bugcrowd
- Map to their Vulnerability Rating Taxonomy (VRT)
- P1 = Critical (9.0-10.0), P2 = High (7.0-8.9), P3 = Medium (4.0-6.9), P4 = Low (0.1-3.9)

### GitHub Security Advisories
- Use the GHSA template: affected package, affected versions, patched versions
- Include the CWE in the advisory metadata
- Credit field: your username or team name

### Immunefi
- Web3/DeFi focus: quantify financial impact when possible
- Include affected contract addresses if applicable
- Reference the specific bounty program's scope document
