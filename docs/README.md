# Documentation index

Start with the maintained guides below. Design notes, proposals, and dated
verification records describe their own revision; they are not installation
instructions or evidence that a change has shipped. Released behavior is recorded
in the root [CHAGELOG.md](../CHAGELOG.md).

## Use Decepticon

| Task | Guide |
| --- | --- |
| Install and start | [Getting started](getting-started.md), [setup](setup-guide.md) |
| Use the terminal | [CLI reference](cli-reference.md), [runtime controls](runtime-controls.md) |
| Operate an engagement | [Engagement workflow](engagement-workflow.md), [RoE provisioning](roe-provisioning.md) |
| Observe Blue Cell | [Blue Cell](features/blue-cell.md) |
| Use a coding agent | [External agents and CLI wrapper skill](integrations/external-agents.md) |
| Integrate Python packages | [Library usage](library-usage.md), [consumer guide](library-consumer-guide.md) |
| Configure models | [Models](models.md) |

## Develop and extend

| Task | Guide |
| --- | --- |
| Find the source | [Repository layout](#repository-layout), [contributor architecture](contributor-architecture.md) |
| Contribute and verify | [Contributing](../CONTRIBUTING.md), [testing](testing.md), [Make targets](makefile-reference.md) |
| Extend agents, workflows, or clients | [Extension contract](design/extension-contract.md) |
| Author runtime knowledge | [Skill schema](skill-schema.md), [Skillogy](skillogy.md) |
| Author business workflows | [Workflow authoring](design/workflow-authoring.md) |
| Build plugins | [Plugin author guide](plugin-author-guide.md) |
| Understand runtime design | [Architecture](architecture.md), [architecture decisions](adr/README.md) |
| Qualify a release | [Release policy](../RELEASE.md), [release evidence](releases/) |

Runtime knowledge lives under
`packages/decepticon/decepticon/skills/**/SKILL.md` and is indexed by Skillogy.
The external-agent skill teaches a host coding agent to operate the installed
CLI; it is a separate distribution surface, documented in the integration guide.
Business processes use `WORKFLOW.md` under the framework workflow catalog.

## Repository layout

| Directory | Responsibility |
| --- | --- |
| `packages/decepticon-core/` | Shared types and contracts |
| `packages/decepticon/` | Agent framework, tools, runtime knowledge, workflow catalog |
| `packages/decepticon-sdk/` | Client SDK |
| `clients/launcher/` | Installed Go launcher |
| `clients/cli/` | Interactive Ink terminal UI |
| `clients/shared/` | Shared client libraries |
| `skills/` | External coding-agent wrapper skills; separate from runtime Skillogy knowledge |
| `benchmark/` | Benchmarks, including the canonical `benchmark.terminal_bench` adapter |
| `containers/`, `config/` | Container builds and runtime configuration |
| `scripts/` | Installer and development utilities |
| `docs/` | Guides, decisions, research, and verification records |

The deprecated Electron shell has been retired. Start the terminal with
`decepticon start`.
Host Desktop integration contracts in the launcher are separate from that
retired Electron package and remain supported.

## Historical and supporting records

- [Archive](archive/README.md): completed migration notes retained for provenance.
- [Design](design/): contracts and design investigations; the extension contract
  is the current extension entry point.
- [Proposals](proposals/): proposed approaches, not promises of current behavior.
- [Research](research/): investigations and observations tied to their dates.
- [Verification](verification/), [validation](validation/), and [releases](releases/):
  evidence for specific source revisions and environments.
- [Superpowers](superpowers/): historical planning artifacts, not the current
  contributor workflow. Follow the extension contract for current development.

Retain ADRs and release/security evidence. Move or remove historical material
only after checking its callers and updating maintained links.

The [2026-10-27 main-content audit](verification/2026-10-27-main-content-audit.md)
records source-backed corrections and its verification scope. It is not a claim
that every historical artifact or technical skill was requalified.
