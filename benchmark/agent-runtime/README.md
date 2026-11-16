# Minimal agent runtime experiment

Compare a handwritten Rust loop, Rig's real `AgentBuilder`, and the real
`deepagents.create_deep_agent` using live `gpt-6.1-sol` calls. This is a small
runtime experiment for the proposed Decepticon Rust port, not a framework
maturity assessment or a measurement of the full Decepticon application.

Each fresh process receives the same authored system prompt, marker and `echo`
schema. The model selects the tool, the implementation executes it once, and a
second model call must return `DONE:<marker>`. The runner refuses extra tools,
streaming, repeated execution, incorrect output or other than two successful
model requests. No model response is synthesized or replayed in the comparison.
The three cases rotate through each execution position over three trials.

Rig uses `rig-agent` and `rig-core` 0.46.0 directly, without the facade's optional
database integrations. Deep Agents 0.7.32, LangChain 1.4.7, LangChain Core 1.7.0,
LangChain OpenAI 1.2.2 and LangGraph 1.2.17 match the primary OSS lock versions;
the experiment has its own complete transitive locks. Deep Agents' beta harness
profile hides built-in tools and disables the default general-purpose subagent;
required middleware remains installed. This measures that minimal profile,
not the default full harness. Rig uses a single-thread Tokio runtime; the native
loop uses blocking reqwest. HTTP client/runtime choices are part of these
implementations' measured cost, not isolated framework-only overhead.
Both Rust variants enable the same reqwest/rustls TLS support; the common relay
handles upstream TLS for all cases. The native variant is a minimal baseline,
not a delivered Rust agent framework.

## Run

Linux `/proc`, Python 3.13, uv and Rust 1.97.1 are required. Authentication uses
the existing repository `config/codex_chatgpt_handler.py` and the operator's
Codex credential store. Only the common parent relay reads credentials; measured
children receive a dummy key and a loopback URL. Source credentials and provider
response bodies are never written into measurement artifacts. The remote model
is real; its inference resources are outside this machine's process measurements.

From the repository root:

```bash
uv sync --frozen --project benchmark/agent-runtime
export CARGO_TARGET_DIR="${TMPDIR:-/tmp}/decepticon-agent-runtime-target"
bash /home/catow/.local/bin/with-build-slot cargo +1.97.1 build --locked --release \
  --manifest-path benchmark/agent-runtime/Cargo.toml --no-default-features --features native-case
cp "$CARGO_TARGET_DIR/release/agent-case" "$CARGO_TARGET_DIR/native-case"
bash /home/catow/.local/bin/with-build-slot cargo +1.97.1 build --locked --release \
  --manifest-path benchmark/agent-runtime/Cargo.toml --no-default-features --features rig-case
cp "$CARGO_TARGET_DIR/release/agent-case" "$CARGO_TARGET_DIR/rig-case"
bash /home/catow/.local/bin/with-build-slot benchmark/agent-runtime/.venv/bin/python \
  tests/agent_runtime_measure.py --bin-dir "$CARGO_TARGET_DIR" --trials 3 \
  --output /tmp/agent-runtime-results.json
```

Outside this workstation, replace the build-slot wrapper with the local
coordinator or run the wrapped command directly. The three-trial comparison
normally makes 18 live model requests and consumes the operator's subscription
quota. `--trials` accepts 1..5. A failed run exits nonzero and preserves partial
evidence with `completed: false`; it is not a successful comparison.

## Measurement definitions

- Startup is spawn to the library-ready handshake. Work includes agent/client
  construction, both real model requests, tool execution and output parsing.
  CPU time covers the whole process, including initialization and teardown.
- Peak RSS is the process's post-exec `/proc/PID/status` `VmHWM`, sampled again
  while the completed child waits at the final gate. `wait4` maximum RSS is also
  retained, but is not the headline value: it can include the large parent
  address space inherited before exec. A known 32 MiB allocation calibration
  increased RSS by 32,916 KiB; post-exec HWM was 47,232 KiB while `wait4` reported
  185,412 KiB. The same calibration observed two threads, four added descriptors
  and 264.401 ms CPU for a task requiring at least 150 ms CPU.
- RSS/PSS and `/proc/PID/io` snapshots are taken after imports and after work.
  Peak thread/descriptor counts use approximately 10 ms polling and can miss
  shorter spikes. `wait4` supplies user/kernel CPU, context switches, page
  faults and block I/O. Read/write bytes differ from character counts and are
  sensitive to the OS page cache. PSS snapshots are not peak PSS.
- Relay request duration includes credentials, conversion, remote service,
  networking and response parsing. It is not pure inference time. Subtracting
  it from work time is only a residual, not an exact framework CPU cost.
  Parent relay/sampler RSS/PSS and CPU are reported separately; build resources
  are excluded. The shared build slot is held during measurement to prevent
  concurrent coordinated builds, but this is not an otherwise isolated machine.
- Raw per-request schemas, prompt lengths, token counts and fingerprints expose
  wire differences rather than rewriting requests to conceal them. The model
  alias is not an immutable provider revision. Three trials establish neither
  statistical significance nor p95/p99 latency. No OS cache flush is performed.
- Checkpointing, crash recovery, streaming, cancellation, concurrency, database
  versus filesystem persistence, Docker deployment and production security
  policy equivalence are outside this experiment. A smaller handwritten loop
  does not establish feature or maturity equivalence.

## Local results and verification

The [raw measurement](results.json) records all nine successful process runs,
18 successful live model requests, source/binary hashes and resource counters.
Measured on 2026-11-16 UTC, Intel Core Ultra 7 155H, 18 logical CPUs, WSL2 Linux
6.18.58.1, Python 3.13.7 and rustc 1.97.1. The table reports the median of three
fresh processes per implementation; threads/descriptors report the maximum
observed across those processes.

| Metric | Native Rust loop | Rig | Deep Agents |
| --- | ---: | ---: | ---: |
| Peak RSS, MiB | 5.375 | 16.672 | 123.953 |
| Finished PSS snapshot, MiB | 3.673 | 14.856 | 112.521 |
| Whole-process CPU, ms | 7.861 | 27.960 | 2,883.047 |
| Library-ready startup, ms | 1.783 | 2.652 | 1,681.023 |
| Work wall time, ms | 4,823.587 | 4,185.909 | 5,062.795 |
| Summed relay duration, ms | 4,812.570 | 4,152.269 | 4,410.800 |
| Sampled peak threads | 2 | 1 | 2 |
| Sampled peak file descriptors | 7 | 7 | 5 |
| Input / output tokens, all three trials | 552 / 84 | 552 / 84 | 552 / 84 |
| Correct cases / live requests | 3 / 6 | 3 / 6 | 3 / 6 |

The same normalized 120-character system prompt and identical tool schema were
verified on every request. Every run consumed 184 input and 28 output tokens.
The local CPU/RSS difference is clear in this narrow, fresh-process workload;
the full time to answer is dominated by remote request duration and does not
establish a framework latency ranking. Startup/import costs would be amortized
in a persistent service, so the CPU figures are not steady-state per-request
costs. Finished state/object lifetime is also not identical across runtimes.

The common Python relay/sampler itself used 182.19 MiB RSS before measurement
and 195.93 MiB afterward, plus 2,906.148 ms user and 4,890.012 ms kernel CPU
during the batch. Those values include sampling and transport conversion and
are excluded from the agent columns. These are not whole-system RAM totals
or measurements of an entirely Rust provider/authentication stack.

The connection probe and the initial Deep Agents preflight used
three additional real requests. An exploratory nine-case run used another 18
requests before the native Rust TLS features were aligned with Rig; it is
excluded from the final comparison. Its raw evidence is preserved locally at
`/home/catow/research/decepticon-agent-comparison-20261116/preliminary-http-only-native.json`.
Including the final comparison, this work made 39 live model requests.

The measurement harness also rejected an invalid tool surface with HTTP 502
before calling the provider, and enforced a short deadline on a real sleeping
child. These checks do not replace the live-model comparison.
All three actual executables also failed nonzero against a closed loopback
endpoint, after the ready handshake and before any tool or final output.

Both feature-specific release builds and both `cargo +1.97.1 clippy --locked
--no-default-features --features <case> --manifest-path
benchmark/agent-runtime/Cargo.toml -- -D warnings` commands passed under the
shared build coordinator. The final invocation was
`benchmark/agent-runtime/.venv/bin/python tests/agent_runtime_measure.py
--bin-dir benchmark/agent-runtime/target --trials 3
--output benchmark/agent-runtime/results.json`, also under the coordinator.
All recorded source and binary hashes match the measured files at
commit `5a85adda9971271e20ccf31d0fbb270d6952bdcf`. Post-measurement review
adds only an explanatory comment to the sampler exception handler; its parsed
Python AST is identical. Raw measurement hashes are preserved without rewriting
the original evidence. Ruff check and
format, Rust fmt, the frozen uv lock check, focused Basedpyright (zero errors
and warnings), Markdownlint (zero issues), `git diff --check` and the contributor
skill audit (329 files, zero violations) passed locally. Existing production
manifests, tests and deployment paths are unchanged; no full production suite,
Docker build, `make quality`, `make smoke` or CI success is claimed. GitHub
Actions remains subject to the user's account billing constraint.

Reference APIs: [Rig source](https://github.com/0xPlaygrounds/rig),
[Rig Agent 0.46.0](https://docs.rs/rig-agent/0.46.0/rig_agent/),
[Deep Agents overview](https://docs.langchain.com/oss/python/deepagents/overview),
[harness profiles](https://docs.langchain.com/oss/python/deepagents/profiles).
