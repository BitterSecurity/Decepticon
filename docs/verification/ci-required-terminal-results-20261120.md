# Required CI aggregate rejects incomplete outcomes

Actual main run44314841378 at be0892d5 failed when GitHub could not assign the
Launcher macOS runner. The platform reported the job cancelled and supplied
`launcher.result = abandoned` to the required aggregate. Its blacklist checked
only failure/cancelled, so CI OK incorrectly succeeded. Runner capacity is a
separate infrastructure issue; this change does not claim to repair it.

Accept only success and intentionally skipped dependencies. Refuse all other
results and empty input objects. Preserve the existing dependency set, scope
skips, matrix, permissions, pinned actions and workflow triggers. No package,
application, publishing or release-version changes are introduced.

The standalone helper executes the actual workflow Bash block with22 fixtures
in three unittest methods. Before the correction,13 bad inputs falsely passed,
including the actual abandoned result. The fixed source preserves all three
successful/scoped-out fixtures and rejects all19 bad fixtures. Historical mode
loads the real immutable pre-fix shell in memory; assertion failures exit1 and
infrastructure/parse/timeout errors exit2. No source reset or synthetic CI status
is used. The same aggregation commands run locally and in the hosted workflow.

Ruff, formatting, narrow type checking, workflow YAML parsing and the executable
source/historical assertions are local checks. Hosted runner availability,
full application tests/builds and hosted CI success are separate and are not
claimed by this scoped correction. No application server, target, provider or
production deployment is needed to verify result aggregation.

The service-harness contract and project-local verification skill describe the
required gates. Qualification by the pinned runner is still blocked: inspect
and verify exit2 before running any gate with `Submodules need an explicit
verification contract: benchmark/MHBench`. These existing benchmark gitlinks
are unrelated to this correction. The pinned Rust source rejects directory
inputs in workspace snapshotting, and its contract schema exposes no submodule
contract field. No benchmark, index entry, runner or protection was modified to
bypass this limitation. Status has no receipt to read. Manual gate results are
recorded separately and are not represented as a successful harness receipt.

The later documentation-only main run44573832461 actually succeeded, including
all three Launcher platforms. Its Python/Docker/telemetry lanes intentionally
skipped according to scope. That result does not reproduce or negate the older
runner failure, nor does it qualify this proposed correction. The Entire Brain
seed is older and returned no durable intent evidence for this defect; actual
GitHub job/aggregate logs and current source establish the reported behavior.
