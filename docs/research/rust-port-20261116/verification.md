# Verification of the research baseline

Date: 2026-11-16. Source: `1c0df39549c82f7c76e7d0b1667557f3778438f7`.
Environment: WSL2 Linux, CPython 3.13.7, uv 0.8.15, pytest 9.0.3,
pytest-xdist 3.8.0, Node 24.13.0. `go version` and `go env GOVERSION`
inside the launcher module both reported Go 1.25.8.

No product source was changed. No production Docker image was built, no live
engagement was started, and no user workspace or user volume was migrated.
These results verify the existing baseline and research reproducibility, not
the proposed engine, DB schema, sandbox port or deployment.

## Python boundaries

Installed the repository's frozen dependency set in the isolated worktree:

```bash
uv sync --frozen --extra mcp --extra neo4j --extra openshell
uv run --frozen pytest -q \
  packages/decepticon-core/tests \
  packages/decepticon-sdk/tests \
  packages/decepticon/tests/unit/middleware \
  packages/decepticon/tests/unit/backends \
  packages/decepticon/tests/unit/sandbox_kernel \
  packages/decepticon/tests/unit/sandbox_server \
  packages/decepticon/tests/unit/workflows \
  packages/decepticon/tests/unit/runtime \
  packages/decepticon/tests/unit/contracts \
  packages/decepticon/tests/unit/agents \
  packages/decepticon/tests/unit/llm \
  packages/decepticon/tests/unit/mcp_server \
  -n 4 -m 'not slow'
```

Result: **3,139 passed, 10 skipped**, 35 warnings, 85.68 seconds, exit 0.
Seven skips require Linux root and UID1500; three exercise Windows-only locking.
Those platform/UID paths were not qualified by this run.

## Data, search, browser and deployment contracts

```bash
uv run --frozen pytest -q \
  packages/decepticon/tests/unit/search_v2 \
  packages/decepticon/tests/unit/artifacts \
  packages/decepticon/tests/unit/reporting \
  packages/decepticon/tests/unit/sandbox_browser \
  packages/decepticon/tests/unit/sandbox_web \
  tests model-catalog/test_catalog.py \
  -n 4 -m 'not slow'
```

First run: **459 passed, 1 failed, 1 skipped**, 24.32 seconds, exit 1.
`test_cli_exposes_host_install_path_separately_from_container_path` failed
because Compose rendering found no `.env`. The isolation test module's autouse
fixture creates and removes this shared file, which can race across xdist
workers. This fixture race is the source-based explanation; it was not repaired
as part of the research task.

Created a persistent empty, ignored `.env` in this new worktree, containing no
credentials. Re-ran the exact selected command with that prerequisite stable:
**460 passed, 1 skipped**, 91 warnings, 46.56 seconds, exit 0. The skip requires
`curl_cffi`, which is a sandbox-image dependency absent from this environment.
No live WAF/browser acquisition parity is claimed.

The two successful selected batches contain **3,599 passing cases and 11 skips**.
This is a subset of the repository's Python tests, without coverage collection.

## Go launcher

From `clients/launcher`:

```bash
go test -p 4 ./...
```

Exit 0: 16 packages reported `ok`; the module root has no test files.
Examples of reported package durations are `cmd` 27.625 seconds, `compose`
80.949 seconds and `updater` 57.521 seconds. Package times are not an end-to-end
startup or installation benchmark. This run does not certify every optional
live test or every OS backend.

## CLI

```bash
npm ci --no-audit --no-fund
bash /home/catow/.local/bin/with-build-slot \
  npm run build --workspace=@decepticon/streaming
npm run test --workspace=@decepticon/cli -- --maxWorkers=4
```

Install and shared-workspace TypeScript build succeeded. The first CLI suite
reported **293 passed, 1 failed** in 31.49 seconds. The transcript pager test
expected `newest` after fixed 60 ms waits, but the rendered page had not advanced.
The failure is retained in this record; its root cause was not conclusively
established. Scheduling sensitivity is an inference, not a demonstrated fix.

```bash
npm run test --workspace=@decepticon/cli -- \
  src/components/TranscriptPager.test.tsx --maxWorkers=1
npm run test --workspace=@decepticon/cli -- --maxWorkers=1
```

The focused run passed both cases in 951 ms. The full serial run passed
**294 cases in 40 files**, 39.03 seconds, exit 0. No source or test was changed
between these CLI runs. The serial result does not erase the parallel failure.
No interactive desktop terminal session or GUI was exercised for this
documentation-only change.

## Research artifacts and external evidence

The inventory script reads an immutable `git archive`, never imports the
agent runtime, and uses only the Python standard library. Generate twice and
compare byte-for-byte:

```bash
python3 docs/research/rust-port-20261116/inventory.py \
  1c0df39549c82f7c76e7d0b1667557f3778438f7 > /tmp/rust-audit-a.json
python3 docs/research/rust-port-20261116/inventory.py \
  1c0df39549c82f7c76e7d0b1667557f3778438f7 > /tmp/rust-audit-b.json
cmp /tmp/rust-audit-a.json /tmp/rust-audit-b.json
cmp /tmp/rust-audit-a.json docs/research/rust-port-20261116/inventory.json
```

Both comparisons passed after making equal-frequency import ordering
deterministic. Relevant external source was read at these released Git trees:

| Project | Tag | Git tree object returned by GitHub |
| --- | --- | --- |
| Rig | `v0.46.0` | `6e039cd24994bc1e7d963af6f6278df73a81b130` |
| Goose | `v1.59.2` | `9da692fde8ac69ec385b5e7d68b92cc7101a509a` |
| ADK-Rust | `v2.3.0` | `7dd31e2571e0e50ee5039b3edcec1150c106bcc6` |
| AutoAgents | `v0.4.0` | `83e58ded37152028a572d99677882aecf33c27aa` |

GitHub `git/trees/<tag>` identifiers name tree objects; they are not reported
here as commit identities. The research document links tagged source files.
Goose's former `block/goose` repository redirects to `aaif-goose/goose`;
Pi's former `badlogic/pi-mono` redirects to `earendil-works/pi`.
Official unversioned documentation is a dated observation, not a frozen API
specification. Upstream framework tests and external memory benchmarks were
not run. No candidate has passed Decepticon's proposed parity gates.

Ruff check and format check passed for the inventory script. Markdownlint CLI2
0.24.1 (markdownlint 0.42.1) reported zero issues for the four changed/new guide
documents. Relative file links resolved, `git diff --check` passed, and the
repository skill audit scanned 329 files with zero violations. The skill audit
was run in warn mode, as directed by the contributor guide.

GitHub Actions is not claimed as successful; verification
for this task was local. `make quality`, the full Python suite, production
builds, real-model runs and actual replacement deployment were not performed.
