# HTTP LLM calling measurements, 2026-11-17

The native Rust gateway used less process CPU and resident memory in this
short-response experiment. Actual model completion time did not establish a
Rust latency advantage. These are measurements of selected HTTP stacks, not a
universal language ranking. No product code or deployment behavior changes. This HTTP-only experiment
does not establish full agent-framework feature parity or maturity.

## Actual task and observation

Each caller requested exactly `{"status":"ok","items":[1,2,3,4]}` from
`gpt-6.1-sol`, reasoning low, using the identical system instructions, typed input
and full Responses request in [scenario.json](scenario.json). The LangChain
candidate executed `ChatOpenAI.astream`; direct Python executed persistent HTTPX;
native Rust executed persistent reqwest through Axum. Each streamed the observed
public deltas and checked provider completion, exact text and model identity.
There were no agent tools, TODOs, memory, DB, subprocesses or cached live answers.

The live launcher provisioned the same existing Codex OAuth login once, outside
measurement. Each gateway directly connected over HTTPS to
`chatgpt.com/backend-api/codex/responses`; this was neither a common Python relay
nor public OpenAI API-key access. The endpoint reported the requested model and
44 input / 19 output / 63 total tokens on every primary live call. This verifies
what the endpoint returned, not model weights or public API availability.

Each implementation completed four direct live requests: one first call and
three balanced warm calls, **12/12 qualified**. Captured per-call deltas, TTFT,
completion and resource snapshots are exported in the compressed archive. Earlier complete diagnostic cohorts add 48 live calls outside this primary
cohort; the initial seed/functional probes are separate as described below. All
three reused the same observed provider TCP socket across their warm calls.

## Direct live results

Warm samples are only **n=3 per candidate**; compare medians and observed ranges,
not statistically reliable p95/p99 or an asserted latency winner.

| Gateway | Completion median (range), seconds | First text median, seconds | CPU ms/request, median | Peak process RSS, MiB |
|---|---:|---:|---:|---:|
| LangChain | 1.937 (1.680–2.387) | 1.927 | 27.792 | 138.87 |
| Python HTTPX | 1.641 (1.613–2.013) | 1.581 | 16.939 | 40.70 |
| Rust Axum/reqwest | 2.073 (1.931–2.177) | 2.002 | 2.413 | 7.22 |

CPU time is the process clock, not elapsed time waiting for the model. CPU
snapshots include observation work and bracket a slightly wider interval than
the timed caller request/batch; do not derive exact CPU utilization from them. On this
cohort Rust had the longest median elapsed time and the lowest internal CPU
cost. Replacing HTTP calling code cannot remove provider generation or network
wait. LangChain includes the OpenAI SDK, typed event construction and integration
logic; comparing it with direct Python also shows costs unrelated to language.

## Immediate real-response replay

The common Axum upstream replayed the same recorded SSE over real HTTP. This
exercises request creation, transport, SDK/SSE decoding and public streaming,
without new model generation. It preserves unknown Codex metadata for everyone.
All **1,800/1,800** primary replay calls qualified, 300 per candidate/concurrency.
Every 100-call batch had exactly 100 outbound attempts and 100 upstream validated
requests/completions. Six additional HTTP-rejection/truncated-stream cases were
rejected after exactly one attempt each; HTTP 200 alone never counted as success.

| Gateway | Concurrency | Batch RPS, median of 3 | CPU ms/request, median of 3 | Completion p95 / p99, ms | Peak RSS, MiB |
|---|---:|---:|---:|---:|---:|
| LangChain | 1 | 89.1 | 10.524 | 14.53 / 18.31 | 138.83 |
| Python HTTPX | 1 | 366.6 | 2.061 | 4.04 / 5.34 | 40.49 |
| Rust Axum/reqwest | 1 | 570.8 | 0.412 | 2.51 / 3.21 | 5.70 |
| LangChain | 4 | 125.4 | 8.581 | 43.82 / 47.89 | 138.87 |
| Python HTTPX | 4 | 495.4 | 2.129 | 11.10 / 13.38 | 40.52 |
| Rust Axum/reqwest | 4 | 746.7 | 0.348 | 9.30 / 13.17 | 5.70 |

The concurrency-four fixed-output driver baseline was median **746.9 RPS**
(range 723.2–827.2), close to Rust's observed goodput. Therefore this replay
cannot establish Rust's maximum server throughput. Provider scheduling, network conditions and any provider prefix caching are
uncontrolled in the small live cohort. The shared WSL host also
showed substantial variation across earlier complete cohorts; all are retained
rather than selecting the fastest run. CPU/RSS reductions are better supported
here than a universal throughput multiplier.

At concurrency four, process PSS was median 127.72 / 29.58 / 2.56 MiB; maximum
threads were 2 / 1 / 1 and FDs 17 / 17 / 13, respectively. All emitted the same
496 normalized SSE line bytes per request. TTFB, TTFT, terminal/EOF times,
inter-chunk gaps, IO, context switches and connection observations are in
[summary.json](artifacts/summary.json). TTFB is downstream response headers;
inter-chunk time is not token latency. Process figures exclude fixture, driver,
provider and other host services.

## Same-budget concurrency ramp

All three repeats finished: **63/63 cells**, **17,784/17,784 qualified stress
calls**, plus 1,976 qualified immediate driver-baseline calls and nine warmups.
Every gateway had the same verified one-CPU quota, affinity CPU 17, 256 MiB
memory cap, zero swap and FD soft/hard limits of 8,192. Fresh services were used
for each repeat. The workload is described in README: the recorded SSE content
has a synthetic 1,750 ms first-text wait and 5 ms between deltas. This is **not
17,784 real model calls**, nor a replay of captured model timings.

The predeclared threshold was success at least 99% and observed p95 completion
at most 3 seconds in every repeat. All stress requests succeeded; the threshold
separates latency under load, not failures. The table shows the median of three
per-repeat p95 values, in seconds:

| Concurrent requests | LangChain | Python HTTPX | Rust Axum/reqwest |
|---:|---:|---:|---:|
| 1 | 1.856 | 1.851 | 1.848 |
| 4 | 1.872 | 1.856 | 1.851 |
| 16 | 1.966 | 1.878 | 1.852 |
| 64 | 2.651 | 2.342 | 1.868 |
| 128 | 5.064 | 2.913 | 1.877 |
| 256 | 32.873 | 28.340 | 1.905 |
| 512 | 55.478 | 114.808 | 1.995 |

| Gateway | Largest tested concurrency passing every repeat | At 512: p95 range, seconds | At 512: good RPS, median | At 512: cgroup CPU ms/attempt, median |
|---|---:|---:|---:|---:|
| LangChain | 64 | 55.314–65.365 | 16.69 | 58.944 |
| Python HTTPX | 128 | 50.371–117.412 | 7.90 | 125.167 |
| Rust Axum/reqwest | 512 | 1.991–2.123 | 261.30 | 0.566 |

Rust passed the largest tested point; **512 is a lower bound, not its measured
ceiling**. LangChain passed 64 and failed the latency threshold at 128; direct
Python passed 128 and failed at 256. No intermediate points were tested. The
low-concurrency result is essentially identical because the controlled wait
sets the pace. At higher concurrency the selected Rust stack spent less CPU
managing the same work and maintained shorter observed latencies. This supports concurrent
I/O headroom for this implementation; it does not prove multicore scalability,
a universal language multiplier or actual provider throughput at this rate.

The final aiohttp driver's 1,024-request immediate baseline at concurrency 512
took 341 ms. During the Rust 512 cells, the driver's process CPU/batch-wall ratio
was at most 17.5%, and the fixture's at most 7.3%, leaving CPU headroom in those
supporting processes. These are observations on a shared host, not a guarantee
of zero scheduling interference or unlimited driver capacity.

No OOM kills were observed. Maximum service-lifetime cgroup memory peaks at
512 were 153.14 / 56.57 / 33.69 MiB in candidate order. These are kernel memory
charges, **not RSS**; mapped shared pages may be charged elsewhere, and peaks
include earlier points. The experiment did not locate a memory-capacity limit.
Memory reductions in the live RSS table and CPU reductions here are separate
observations. Identical work still needs sockets/FDs and transfers the same text.

The high-concurrency Python results vary considerably across repeats. In
particular, direct Python's 512 p95 ranges from 50.4 to 117.4 seconds, while
Rust's ranges from 1.99 to 2.12 seconds. These data compare the complete selected
HTTPX/OpenAI-SDK/Uvicorn and reqwest/Axum stacks. They do not isolate the Python
interpreter, GIL, a specific connection-pool algorithm or TLS implementation as
the cause; no sampling-profiler attribution is claimed.

[scale-summary.json](artifacts/scale-summary.json) contains each repeated cell;
[scale-evidence.json.gz](artifacts/scale-evidence.json.gz) retains every call,
cgroup sample, attempted cell, source/binary hash and diagnostic. Earlier ramps
are retained: scale-01 setup was interrupted; scale-02 exposed default FD limits
of 1,024; scale-03 was removed from the launch queue before execution; scale-04
showed a common slowdown with the pooled HTTPX load driver. The final scale-05
changed only the load/observation harness to aiohttp and added driver-baseline
and CPU observations. Candidate gateway code/binary and primary live data stayed
fixed. Do not use the driver-limited preliminary curve as a gateway capacity
result. All final transient services and fixture processes were stopped.

## Evidence and reproduction

Host: Intel Core Ultra 7 155H, 18 available logical CPUs, WSL2 Linux
6.18.58.1. Driver/fixture/gateways used affinity 15/16/17. This is a shared
host, without reserved physical cores; [environment.json](artifacts/environment.json)
records the observation. CPU quota and process affinity are verified separately;
no cpuset-controller enforcement is claimed.

See [README.md](README.md) for shared transport settings, measurement boundaries
and commands. [evidence.json.gz](artifacts/evidence.json.gz) contains the primary
`final-07` traces, distributions, source and release-binary hashes, and retained
diagnostics. Export validation rejects incomplete cohorts, mismatched current
source/binary, missing calls, unequal affinity, retries and missing observed
provider connection reuse.

Earlier `final-02` exposed TCP buffering from unmatched TCP_NODELAY; it is not
used for the final comparison. `final-03` was an interrupted setup duplicate.
`final-04` predates extra timing/encoding observations. `final-05` and `final-06`
are earlier complete cohorts, not primary. An optional async-openai 0.43.0
functional probe received the answer but failed completion qualification. Its
standard event enum lacks Codex's extra `response.metadata`; that mismatch is a
possible explanation, not a retained exception trace proving exact causality.
The user authorized the corresponding native Rust implementation instead. No
performance conclusion is drawn from the failed SDK probe.

Local verification: locked Rust release build and release clippy with warnings
as errors; cargo fmt; root Ruff/format and errors-only type gate (0 errors,
949 reported warnings); five benchmark Python files separately type checked
(0 errors, 0 warnings); `uv run pytest tests/ -q` (37 passed, 0 skipped).
Both exporters were also observed rejecting actual interrupted evidence.
GitHub Actions, product production builds and UI smoke tests are not claimed.
The addition is an isolated benchmark under tests, with its own two lockfiles.


The integration review independently reproduced the original summaries and qualified
a fresh HTTP replay/negative cohort without new live calls. See the separate
[review evidence and scope](README.md#integration-review-2026-11-17).
