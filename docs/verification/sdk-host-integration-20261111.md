# SDK host integration review — 2026-11-11

For [#1173](https://github.com/BitterSecurity/Decepticon/pull/1173), review all
14 changed paths from original head`280bda87` and integrate merged host-seam
#1170/main`4b24db95`. Tested combined source`592846a1` and integration
commit`5930b2c724d8e42f281c30092613e1ec60bc1a9a` have exactly the same Git tree.
Only evidence/history/consumer-guide documentation follows that source.
[Complete source paths, wheel receipts and process/log hashes](sdk-host-integration-20261111.json).

## Contract and connected consumers

All26public names re-export the existing framework objects;14TypedDict keys
describe trusted configurable inputs and do not confer authorization. Public
aliases preserve old private imports and identity. Per-run rebinding preserves
non-sandbox routes and the existing hydrated-state workspace precedence.
OpenShell type detection includes the unbound placeholder; it is not admission.
Required-provider discovery and provider-downgrade refusal survive composition
with #1170. Neither agent input nor a spoofed attribute installs a resolver.

The guide now makes two important existing boundaries explicit. Importing the
host module alone does not build Soundwave, but resolving its lazy factory
attribute—including named import—loads that module and its default graph
initialization. Clearing the resolver to `None` permits the bundled local
default to return; service hosts that prohibit local resolution must explicitly
set `DECEPTICON_OPENSHELL_LOCAL=0` and install their trusted resolver before
constructing/executing agents. Missing workspace/admission still refuses.

The lockfile changes SDK extra metadata only: no resolved package versions
change. The SDK base import remains independent of framework import; `[host]`
explicitly adds the framework dependency.

Actual SaaS consumers include agent assembly, the native session resolver,
sandbox transport classification, summarization, artifact/search filesystem
rebinding, dynamic subagents and workflow synchronization. Their current
private aliases remain compatible. Adopt the new public imports under the
existing owners when qualifying the final source cohort.

One concrete SaaS compatibility prerequisite was found:
`apps/decepticon/build_oss_cohort.py` currently permits the SDK's framework
dependency only under the `fixtures` marker. The new `host` extra must be
handled by that verifier and actual source-cohort packaging with exact sibling
version pins retained. This OSS API PR does not silently update or qualify the
SaaS serving image. Track that change separately before release.

## Actual direct gates

- Fresh locked environment with existing MCP/OpenShell/Neo4j extras: full
  fast suite **6,326passed /3explicit Windows-only skips /17warnings /process0**.
- Full Ruff/format and raw basedpyright pass:0errors/969retained warnings.
- Current SaaS private-provider suites against this combined OSS source:
  **27passed /2existing deprecation warnings /process0**, including actual
  controlled HTTP profiles. Production credentials/tracing were not inherited.
- Three actual wheels built through the shared build slot. Every RECORD hash
  and installed payload is verified; tests execute outside the source tree
  with all three package imports checked against the installed wheel directory.
- Seven separate network-refusing interpreter profiles pass: base SDK stays
  independent; host module/export/config identities; HTTP per-run isolation;
  OpenShell downgrade refusal; missing admission resolver refusal; genuine
  optional null provider; missing required provider refusal.

The first wheel harness had six passes and one wrong-message assertion: its
missing-resolver profile left the bundled default enabled, which correctly
refused the absent workspace. The retained retry explicitly disables local
resolution and passes all seven profiles with the same verified payloads.
No production code or refusal was relaxed to obtain a pass. Local wheel
version`0.0.0` is the existing QA sentinel; no release/version selection is implied.

Existing #1170 images do **not** contain this SDK change. No selected final
SaaS cohort/image, licensed model execution, real E2B or desktop acceptance is
claimed. Re-read the newly published exact-head required checks before normal
merge. No protection/status bypass, Secret/IAM/provider operation, package
publication or production deployment.

[CHAGELOG.md](../../CHAGELOG.md)
