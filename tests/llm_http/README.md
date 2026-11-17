# HTTP LLM calling comparison

Compare three HTTP gateway implementations, with no agent, tools, checkpoint or
memory work: LangChain ChatOpenAI, direct Python HTTPX, and a native Rust port of
the calling flow served by Axum with reqwest. Both Python candidates use the same
minimal ASGI application and Uvicorn. The Rust server uses a single-thread Tokio
runtime. This measures the complete selected HTTP stacks, not language syntax or
an Axum routing microbenchmark alone.

The Rust path corresponds to Python's persistent client, POST, streaming SSE
line parsing, delta forwarding, completion validation and error propagation.
It uses no Python gateway and no async-openai. An exploratory async-openai 0.43.0
call received text but failed completion validation; source inspection found that
its standard event enum lacks Codex's extra `response.metadata` event. Per the user's revised scope, that is diagnostic evidence,
not a performance candidate or an excuse to modify the provider stream for one
candidate.

## Shared contract

`scenario.json` fixes the actual Responses request: model `gpt-6.1-sol`, reasoning
low, stream true, store false, identical instructions and typed user input. Each
candidate checks its actual outbound request body against the complete profile
before transmission. ChatOpenAI uses its real `astream` integration. HTTPX and
reqwest use real network clients and parse the actual provider's SSE response.

Every gateway exposes loopback POST `/invoke`, returns public text deltas as SSE,
and emits a final done record only after provider completion and the exact expected
text. Errors and EOF before completion produce error records, never success.
The task asks for `{"status":"ok","items":[1,2,3,4]}`: 44 input / 19 output tokens
in the seed capture. Each invocation is one model request. No cached answer is
returned by the live gateways.

HTTP/1.1, identity content encoding, TCP_NODELAY, no environment proxy,
120-second timeout, persistent clients and up to
four concurrent calls are shared. Python's pool is capped at four connections;
reqwest retains up to four idle connections per host and the workload permits
at most four concurrent calls. There are no application/SDK retries. Wire-attempt
counters detect accidental retries.

## Workloads

**Live:** each candidate directly connects to
`https://chatgpt.com/backend-api/codex/responses` using the same existing Codex
login. There is no common Python relay. A common unmeasured launcher reads the
login using the repository's existing adapter and supplies credentials through
child environment variables, never command arguments or result files. Three
first calls and three balanced rounds of warm calls make 12 actual model calls.
The seed capture and initial rejected discovery requests are separate diagnostics.
No claims are made about public `api.openai.com` availability of this model slug.

**Replay:** an Axum HTTP upstream serves a real captured SSE response. It validates
the complete request and records peer connections. The real gateway clients,
HTTP transport, SSE decoding and downstream streaming still execute; no new LLM
call occurs. Each candidate has three batches of 100 sequential requests and
three batches of 100 requests at concurrency four. A separate fixed-output route
checks the load generator's ceiling. Replay delivery is immediate: its latency
is HTTP/SDK overhead, not real model generation latency.

The fixture retains actual public text/event structure. Encrypted reasoning,
stable user identifiers, prompt-cache keys and account-specific access metadata
are removed from the exported recording, equally for all replay candidates.
No candidate receives a private rewritten stream. The original recording had
no encrypted reasoning content. Unknown Codex metadata is retained to preserve
the compatibility condition.

**Budgeted concurrency ramp:** `scale.py` starts fresh user-systemd services for
three repeats. Every candidate gets a verified one-CPU quota (`cpu.max=100000
100000`), 256 MiB cgroup memory cap, zero swap, FD soft/hard limits of 8,192,
one event loop pinned to the same CPU, and a connection pool setting of 1,024.
The ramp load generator uses aiohttp 3.14.5 (already pinned in uv.lock) and
permits 4,096 connections; a separate HTTPX client performs control requests.
It records driver/fixture CPU and immediate-response baselines at every point. The paced upstream serves the same
recording, with a synthetic 1,750 ms wait before first text and 5 ms after each
text delta. These delays are controlled I/O waits, not captured model timings.
Concurrency rises through 1, 4, 16, 64, 128, 256 and 512, with respectively
8, 16, 32, 128, 256, 512 and 1,024 requests per cell. All 63 planned cells remain
in the evidence, including unavailable/failed cells. Candidate order rotates
between repeats; points ascend within each fresh service's lifetime.

The predeclared experimental threshold is at least 99% success and at most
3,000 ms observed p95 completion in **every** repeat. It is not a product SLO.
This closed-loop workload bounds in-flight requests; request latency starts
after the driver's semaphore is acquired. Batch goodput includes all waves.
It does not measure open-loop arrival queueing or multicore execution. A passing
maximum point is only a tested lower bound on capacity. Cgroup CPU, throttling,
memory and OOM counters are sampled every 100 ms, outside the gateway. Memory
charges include sockets/kernel/page-cache and are not RSS: shared mapped pages
may be charged to another cgroup. Sampled peaks can miss short spikes; the
kernel's memory.peak covers the service lifetime, including earlier ramp points.

## Measurement boundaries

The gateways share one CPU, the load generator and replay upstream each have
another CPU. Each service has one event loop/worker. Affinity is recorded from
`/proc/PID/status`; this is still a shared WSL host, not a reserved physical core.
Warm clients stay alive across requests. Startup CPU/memory is recorded separately.

Measure client-observed first text time and full stream completion, process CPU
clock deltas per batch/request, peak RSS, PSS, FD/thread snapshots, IO and context
switches. Socket inode/local-port snapshots distinguish persistent provider TCP
connections from reconnects. Do not confuse TTFT with first HTTP headers or CPU
clock time with network/model wait. Process CPU snapshots bracket the metrics
requests and have a wider interval than the caller-timed batch; dividing those
CPU deltas by batch wall time is not an exact utilization measurement. TTFB is caller-to-gateway response headers,
not the upstream provider header time. Delta reception timestamps measure
inter-chunk gaps, not one-token latency; chunks can contain multiple tokens and
can arrive in the same TCP packet. Byte counts cover normalized SSE text lines,
excluding HTTP headers, TCP and TLS overhead. Replay p95/p99 are nearest-rank
observed percentiles of 300 calls per candidate/concurrency; live has only three
warm samples, so only medians and ranges support interpretation.
Fixture, driver and provider resources are
excluded from gateway resource figures. Observation checks and SSE normalization
are included; they are not subtracted as hypothetical overhead.

The Python/Rust HTTP flow is aligned, while HTTPX/Uvicorn/OpenAI SDK and
reqwest/Axum/TLS implementations differ. Framework construction, serialization,
SSL, event parsing and runtime costs are part of the result. Small live samples
cannot establish a universal latency ranking. Replay RPS is not provider quota
or achievable real-LLM throughput.

## Reproduce

The benchmark uses the installed CPython 3.13.7 and Rust 1.97.1 release toolchain.
Preserve both lockfiles and use the shared build coordinator on this machine.

```bash
uv sync --project tests/llm_http --python 3.13.7
bash /home/catow/.local/bin/with-build-slot cargo +1.97.1 build --locked --release \
  --manifest-path tests/llm_http/rust/Cargo.toml
bash /home/catow/.local/bin/with-build-slot tests/llm_http/.venv/bin/python \
  tests/llm_http/run.py --output-dir /absolute/fresh/results
```

`--local-only` runs replay and negative HTTP/stream checks without new LLM calls.
All servers are terminated in finally blocks, and failures are retained in the
output's evidence file. An HTTP 400 and a truncated real SSE stream must be rejected
by each candidate with exactly one attempt. Credential values, authentication
headers, encrypted reasoning and full process environments must not be exported.

Export the verified raw traces and distributions:

```bash
tests/llm_http/.venv/bin/python tests/llm_http/analyze.py \
  --results-dir /absolute/fresh/results \
  --diagnostics-dir /absolute/diagnostics \
  --output-dir /absolute/archive
```

The exporter requires all 1,800 primary replay calls, 12 direct live calls, six
expected rejections, current source/binary hashes, identical CPU affinity and
observed provider TCP connection reuse. Initial setup/transport observations are
kept separately from the final controlled cohort. Large contexts, long-output
backpressure, client-cancellation latency, rate-limit recovery and production
capacity are not measured by this short-response profile.

Run the additional ramp without any real model calls (requires cgroup v2,
user systemd, and delegated CPU/memory controllers):

```bash
bash /home/catow/.local/bin/with-build-slot tests/llm_http/.venv/bin/python \
  tests/llm_http/scale.py --output-dir /absolute/fresh/scale-results
tests/llm_http/.venv/bin/python tests/llm_http/analyze_scale.py \
  --results-dir /absolute/fresh/scale-results \
  --diagnostics-dir /absolute/diagnostics \
  --output-dir /absolute/archive
```

Read [RESULTS.md](RESULTS.md) for the measured results and limitations. The
compressed archives retain individual call outcomes, resource snapshots, hashes,
earlier complete cohorts and interrupted diagnostics. They contain public output
from this fixed prompt and no authentication headers or credential values.


## Integration review, 2026-11-17

The review integrated latest main `0455ebcd` without changing any measured gateway,
runner, analyzer or lockfile. All original primary/ramp source and release-binary
hashes match. Both published summaries were independently recomputed exactly from
the archived 1,800 replay calls, 12 live calls, six rejection cases and all 63 ramp
cells (17,784 requests). The two original archives and their original performance
claims remain unchanged.

A fresh `run.py --local-only` run passed all 1,800 real-HTTP replay calls and all
six HTTP rejection/truncation cases with one attempt each, exit 0, and no live
model calls. Execution used the shared build coordinator and the independently
hash-verified unchanged release binary. This confirms functional behavior; shared
host review timings do not replace the original performance cohort.

Root tests pass 43 cases with no skips. Canonical locked `make ci-lint` passes
Ruff/format (940 files) and the errors-only type gate; fixture-only type analysis
reports zero errors/warnings. Cargo fmt passes. No product build, production
capacity result, native application release or deployment is claimed.

Separate public review evidence: [`artifacts/review-20261117.json.gz`](artifacts/review-20261117.json.gz),
SHA256 `383c53e3dad2d72d69e72cc6df9052accd3a5c9b91eb4412dff513a82c6f68f5`. The original author's worktree and measurement files are preserved.
