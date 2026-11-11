# Host seam integration with current OSS main — 2026-11-11

For [#1170](https://github.com/BitterSecurity/Decepticon/pull/1170), source
`895f649ef40ab86e9efa5480b0700e283417b428` combines fetched main
`fcfc9281e556e4963d578ad895feb75b73c5cf7e` with reviewed PR head
`2107a90108ef7be10086b360de491c3953e7be10`. Only `CHAGELOG.md` conflicted;
both histories are preserved. This verification supersedes the old-head gate
claims for integration purposes; original receipts remain historical evidence.
[Complete 62-path hashes, commands/evidence hashes and image receipts](host-main-integration-20261111.json).

Main's #1172 removes the optional OSS web dashboard, its CLI command, image,
Compose service, launcher health/port settings and release lane. The terminal
CLI and shared streaming package remain. SaaS uses its own dashboard; its
source search revealed no consumer of the removed OSS dashboard entry points.
The seven host-seam consumers and strict discovery/refusal semantics retain
the reviewed implementation. The private provider is separately integrated by
[SaaS #1709](https://github.com/BitterSecurity/decepticon-saas/pull/1709);
consuming a seam-bearing engine still requires that provider in the actual image.

## Direct qualification

In a fresh locked workspace with MCP/OpenShell/Neo4j extras, sanitized credentials
and tracing disabled:

- Fast Python suite: **6,261 passed, three explicit Windows-only skips,
  17 warnings, process0**. Focused host/provider/model/plugin cases:92passed.
- Full Ruff check/format, strict skills, capability cases, pinned STIX,
  frozen skill graph and actionlint pass. Basedpyright:0errors/969warnings,
  raw process0 and configured error gate0. Warnings are not hidden.
- Pip-audit:191locked registry requirements,0active findings under the
  unchanged existing ignore policy. Introduced-commit Gitleaks passes.
- Shared streaming and CLI production builds pass using the shared build slot.
  Actual CLI:294tests/40files pass; configured CLI types pass. The shared
  package has no test script, so no shared-test result is claimed.
- Linux launcher `go test ./...` and `go vet ./...` pass. Base, dev and watch
  Compose configurations all validate with an empty temporary `.env`; no
  service was started. Windows/macOS launcher acceptance is not inferred.

Two harness setup failures are preserved: the npm resource wrapper initially
lacked its DBus session variables before compilation, and an attempted shared
test script does not exist. Correct session coordination and the actual CLI
workflow commands subsequently pass; neither failure is relabeled as a test pass.

## Actual images and independent readback

[Cloud Build 3ac96732](https://console.cloud.google.com/cloud-build/builds;region=us-central1/3ac96732-3d40-43b6-9331-af69b93aa43e?project=874764425604)
finished **SUCCESS**, all six steps successful. Engine and CLI build/run on
both AMD64 and ARM64. Engine smoke imports installed agent/backend consumers
and verifies the optional null provider. CLI smoke imports the actual compiled
command registry/shared streaming, checks retained commands and confirms the
retired web command/artifact is absent. These are actual installed runtime
smokes, not licensed graph/model execution or full SaaS native acceptance.

Independent readback verifies four registry manifest/config digests,
architectures and source labels, all18generation-pinned evidence objects, and
the full28,446,988byte source stream against its submitted hash. Source was
streamed directly to GCS; no local image build/pull or archive copy was retained.
The JSON receipt records immutable image digests and object generations.

Original informational Trivy policy remains unchanged. Its19SARIF results have
the same rule histogram as the prior-head image and remain recorded; this is
**not** a clean-security assertion or a blanket waiver. SaaS final serving-image
security review remains separate; the user's Agent Server Go deferral is not
extended to other findings by this source integration.

## Review threads and protection

The ten CodeQL `Statement has no effect` threads all point to the ten `...`
method declarations inside `HostIntegration(Protocol)`. They are intentional
typing stubs, matching the [official protocol specification](https://typing.python.org/en/latest/spec/protocol.html#defining-a-protocol).
They are not the executable null provider or a missing required-provider check.
The existing runtime provider validation/refusal tests and full types pass.
Record this disposition when resolving the threads; do not suppress the scanner
or rewrite the runtime merely to silence this diagnostic.

The earlier unavailable-required-check observation is obsolete: the original
PR head now has a real successful GitHub Actions `CI OK (required status)` check.
No status, protection rule or bypass was created. Re-read the checks for the
newly published exact head before normal merge. Direct verification above does
not pretend to be a hosted check.

## Release boundary

No package version/publication, Secret/IAM/E2B operation or production change.
Only documentation follows the qualified source commit. Latest coherent
OSS/SaaS source adoption, private-provider packaging, old checkpoint/tool/media
compatibility and actual native E2B/desktop/API lifecycle acceptance remain
under the SaaS release work. Review the other open OSS SDK/objective PRs in
dependency order before pinning the final SaaS cohort.

[CHAGELOG.md](../../CHAGELOG.md)
