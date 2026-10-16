# Security Policy

## Supported Versions

Security updates are provided for the current release line only. The latest
release is **v1.4.4**, so the **v1.4.x** line is actively supported. Older
lines no longer receive security fixes.

| Version | Supported          |
|---------|--------------------|
| v1.4.x  | :white_check_mark: |
| < v1.4  | :x:                |

## Reporting a Vulnerability

**Do not open a public GitHub issue for security vulnerabilities.**

If you discover a security vulnerability in Decepticon, please report it
responsibly:

1. **GitHub Security Advisories** (preferred): Use [GitHub's private
   vulnerability reporting](https://github.com/BitterSecurity/Decepticon/security/advisories/new)
   (Security Advisories → "Report a vulnerability") to submit a report
   directly. If this link returns a 404, the feature may be pending
   enablement — use the email method below instead.

2. **Email** (fallback): Contact the maintainers at **markus@decepticon.red**
   with:
   - Description of the vulnerability
   - Steps to reproduce
   - Potential impact assessment
   - Suggested fix (if any)

## Response Timeline

- **Acknowledgment**: Within 48 hours
- **Triage / initial assessment**: Within 7 days
- **Fix or mitigation**: Dependent on severity, typically within 30 days

## Coordinated Disclosure

We follow a coordinated disclosure process:

- Reporters are asked to give maintainers a reasonable period to investigate
  and ship a fix before any public disclosure.
- Maintainers will work with the reporter to agree on a disclosure timeline
  appropriate to the severity and complexity of the issue.
- We are happy to credit reporters for responsibly disclosed vulnerabilities.
  If you prefer to remain anonymous, let us know and we will respect that.

## Scope

**In scope:**

- The Decepticon codebase in this repository (agent logic, orchestration,
  sandboxing, credential handling, insecure default configurations).
- The autonomous Decepticon agent itself, including its behavior and
  guardrails.
- Docker container security issues (privilege escalation, network isolation
  bypass).
- Dependency vulnerabilities that directly affect Decepticon.

**Out of scope:**

- Target systems that Decepticon is designed to test against — that is its
  intended purpose, not a vulnerability.
- General security best practices or hardening suggestions (open a regular
  issue instead).
- Vulnerabilities in third-party services not bundled with Decepticon.

## Safe Harbor

We support good-faith security research and will not pursue or support legal
action against researchers who, in good faith, discover and report
vulnerabilities in accordance with this policy. This safe harbor explicitly
covers testing the **autonomous Decepticon agent itself** — probing its
behavior and guardrails in good faith is authorized and will not be pursued
legally, provided you stay within the rules below.

To qualify for safe harbor, you must:

- Act in good faith and avoid privacy violations, data destruction, and any
  interruption or degradation of services.
- Not access, modify, or exfiltrate data that does not belong to you.
- Not perform social engineering, phishing, or physical attacks against the
  maintainers, contributors, or users.
- Comply with all applicable laws.
- Give maintainers reasonable time to remediate before public disclosure, as
  described in the Coordinated Disclosure section above.

Activity conducted consistently with this policy is considered authorized.
If in doubt about whether a specific test is in scope, ask first via the
reporting channels above.

## Responsible Use

Decepticon is an offensive security tool designed for **authorized** red team
engagements only. Users are responsible for ensuring they have proper
authorization before using Decepticon against any target. See the
[LICENSE](LICENSE) for terms of use.
