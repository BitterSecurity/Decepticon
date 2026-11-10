# Trusted planning document source qualification

Source change, not a package release or production deployment. Started from
freshly fetched main `b1de9a8bf0928c64cf441a43a3f386ebecbe12e4` in its own worktree.

The shared SaaS Agent Server cannot use local host paths as the tenant's plan
storage. Replacing `complete_engagement_planning` through `build_tools` correctly
raises `SafetyOverrideViolation`; that failed integration was retained rather
than enabling `DECEPTICON_ALLOW_SAFETY_OVERRIDES`. This change lets the native
Soundwave factory accept only a trusted read-only document-source provider.
The native tool continues to own schema/target/authorization validation and
the draft-review event. Caller-supplied graph configuration cannot select a
Python callable. Explicit full tool replacement cannot be combined with it.

The adapter reads exactly eight fixed filenames, at most 512 KiB each, into a
private per-call temporary directory for the existing Path-based validator.
It removes the directory on every exit. Invalid UTF-8, missing revisions,
storage errors and oversized documents fail with redacted responses. Successful
validation rereads all bytes and revisions before emitting the existing
`planning_draft_ready` event with an additional bundle digest. It does not write
a local ready marker for a remote source. No-source local behavior retains its
existing marker. This recheck is not an atomic eight-object transaction, and
the notification never authorizes execution or switches from Plan to Red.

Local qualification used the already-installed private OSS cohort environment,
Python 3.13, LangGraph 1.2.18 and DeepAgents 0.7.32, with this task's Decepticon
source explicitly selected using PYTHONPATH; dependencies and installed packages
were not changed. The targeted native tools, Soundwave planning restrictions and
planning workflow suites passed **35 cases**, zero failures/errors/skips, exit 0.
The new tests cover successful remote completion, all eight fixed documents,
authorization/target refusal, malformed/missing/oversized/non-UTF-8 bytes,
revision drift, redacted failures without host fallback, local compatibility,
factory isolation and the still-enforced safety override guard. Scoped Ruff
0.17.3 check passed after formatting. Logs are retained under
`/home/catow/GIT/decepticon-pr-review-20261101/oss-planning-document-source-r2-20261110.log`.

The companion SaaS source review separately exercises a loopback HTTP source,
actual LangGraph tool-runtime injection, generation-CAS behavior and current
PostgreSQL authority. These are source-level integration checks, not an
installed candidate image or E2B/provider/prod pass. SaaS adoption requires a new
immutable private source cohort, rebuilt candidate, native/runtime acceptance
and normal release/rollback gates. No public package, version, tag, secret,
provider allocation, image or production state changed in this OSS task.
Hosted Actions may be unavailable due to account spending limits; none is
claimed as passed by this local evidence.
