# Per-run proxy credentials preserve selected-model behavior

## Problem and change

Rebuilding a selected model as a plain `ChatOpenAI` silently reset streaming,
streaming usage, token limits and selected timeout/retry settings. It also
discarded vendor protocol subclasses and custom HTTP transports. In a shared
server, a credential-binding error continued with the original environment
credential instead of rejecting the request.

Revalidate the selected concrete model with its original configuration and
fresh OpenAI clients. Preserve serialization-excluded configuration such as
HTTP pools, callbacks and the streaming chunk timeout. Keep the configured
proxy destination; remove a default Authorization override and update an
existing LiteLLM authentication header. Preserve provider BYOK and tracing
headers. The established temperature compatibility rule remains in place.

An explicit per-run credential that cannot be bound now refuses dispatch in
both sync and async middleware. The generic error/log does not contain the
underlying exception or credential. Requests without a per-run credential keep
their existing single-tenant behavior. This failure behavior is intentional:
multi-tenant callers must fix their credential configuration rather than
silently charge the environment account.

## Local evidence

Baseline: immutable main `28b97543aba2e80761564fdf85e8c4ba4d9f5738`.
The final verification uses an owned environment installed with
`uv sync --locked --dev --extra mcp`, reusing the download store. The driver
asserts environment ownership and all three product imports resolve to this
worktree. A shared environment was first used for compatibility measurements;
its dependency drift and foreign editable analyzer paths were identified and
those results are not the locked-environment acceptance evidence.

| Check | Actual result |
| --- | --- |
| New transport assertions against original middleware | All6 fail;0 errors/skips |
| Transport and original proxy middleware cases |16 passed |
| Connected factory, model-selection and proxy suites |902 passed;0 skips |
| Full repository Ruff |Passed |
| Full repository format check |943 files formatted |
| Changed Python files, errors-only type check |0 errors |
| Full canonical root type check, locked environment |893 files;0 errors;953 existing warnings |
| Existing CI aggregate behavior |22 real Bash fixtures pass; historical regression exits1 |

`python3 tests/ci/verify_proxy_key_transport.py --doctor` checks import ownership.
`python3 tests/ci/verify_proxy_key_transport.py` drives the connected suite.
`python3 tests/ci/verify_proxy_key_transport.py --historical` reads the immutable
old middleware into memory and must exit1 with exactly six assertion failures.
Setup, collection, skips and incomplete historical runs do not qualify.

The HTTP fixture runs real `invoke` and `ainvoke` requests against a disposable
loopback server, including SSE aggregation, token callbacks and usage. It checks
wire credentials, streaming mode, token limits, sampling, subclass behavior,
fresh client construction, custom HTTP pool identity and original-model
immutability. Two vendor tool-bound turns retain opaque synthetic protocol
blocks. Binding-failure tests assert no handler dispatch and capture the actual
logger's output. No model provider, database, remote target or real credential
is used by these tests.

## Verification limits

The pinned personal `service-harness` runner rejects the repository's existing
benchmark submodule directories before gates execute: `Submodules need an
explicit verification contract: benchmark/MHBench`. Inspect/verify exit2 and
status has no receipt. No submodule, runner, trust configuration or repository
protection was changed to work around it. All seven declared gates and their
two historical regressions were run directly with their exact argv; direct
results are not a successful personal-harness receipt.

These tests establish local HTTP/protocol behavior. They do not establish
external provider availability, end-to-end deployment acceptance, package
publication or a production rollout. No version is bumped by this change.
