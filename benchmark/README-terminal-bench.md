# Terminal-Bench 2.1

This adapter measures Decepticon's production bash harness against
[Terminal-Bench 2.1](https://www.tbench.ai/docs/run-terminal-bench-2-1).
Harbor owns task provisioning and verification. Decepticon owns the model loop,
system prompt, and terminal tool surface.

The adapter intentionally imports the same four `BASH_TOOLS` objects and
`BASH_PROMPT` used by Decepticon agents. It also builds the model through
`LLMFactory` and writes the existing runtime JSONL record. The only benchmark-
specific execution component is `HarborSandboxAdapter`, which translates the
Harbor environment API into the sandbox protocol expected by those bash tools.
It does not implement a second shell tool or bypass Harbor's task container.
Use the Make target from the repository root; it preserves that root on
`PYTHONPATH` when Harbor changes into each trial directory.
The `decepticon_tbench_agent.py` bootstrap sets Decepticon's existing
single-component import guard before Python evaluates the parent `benchmark`
package, preventing the unrelated 16-agent graph from entering the run.

## Prerequisites

- Docker
- A reachable Decepticon LiteLLM proxy, normally `http://localhost:4000`
- Provider credentials already configured through Decepticon
- Development dependencies installed with `uv sync --dev`

Harbor is a root development dependency because it is benchmark infrastructure,
not a dependency of any published Decepticon package.

## Smoke run

The Make target defaults to the Codex OAuth route `auth/gpt-5.6-luna` with
`reasoning_effort=max`. Run one task once before spending subscription quota on
a full suite:

```bash
make terminal-bench ARGS="--include-task-name terminal-bench/fix-git \
  --n-attempts 1 --n-concurrent 1"
```

Override either default with a Make variable, keeping the model and effort
visible in Harbor's job configuration:

```bash
make terminal-bench \
  TERMINAL_BENCH_MODEL=auth/gpt-5.5 \
  TERMINAL_BENCH_EFFORT=xhigh \
  ARGS="--include-task-name terminal-bench/fix-git"
```

## Larger runs

Harbor accepts `--n-attempts`, `--n-concurrent`, and `--job-name` through
`ARGS`. Start with one task and review its trajectory before expanding the
run. Follow the benchmark's current submission requirements when preparing a
leaderboard result.

Results are written under Harbor's default `jobs/` directory. Every successful
agent run includes:

```text
agent/
  trajectory.json             # ATIF-v1.7, consumed by leaderboard submission
  decepticon-runtime.jsonl    # Decepticon model/tool record
```

`trajectory.json` identifies the tool surface as
`decepticon.tools.bash.BASH_TOOLS` and preserves model messages, bash tool calls,
their observations, reasoning effort, and aggregate input/cache/output tokens.
Harbor's trial `result.json` also records environment setup, agent setup, agent
execution, verifier start/end timestamps, and the verifier reward. Inspect all
of them with:

```bash
uv run harbor view jobs
```

For a machine-readable summary of one trial:

```bash
jq '{reward: .verifier_result.rewards,
     tokens: {input: .agent_result.n_input_tokens,
              cache: .agent_result.n_cache_tokens,
              output: .agent_result.n_output_tokens},
     timing: {environment_setup, agent_setup, agent_execution, verifier}}' \
  jobs/<job-name>/<trial-name>/result.json
```

Harbor records token counts; the LiteLLM configuration may also report
API-equivalent cost estimates.

## Measurement boundary

This benchmark measures a fresh, single-purpose terminal agent. It does not run
the 16-agent Decepticon orchestrator, OPPLAN approval, knowledge graph, or Red
Team engagement middleware. Those systems would change the question from “how
well does our bash harness operate a terminal?” to “how well does the complete
red-team product solve general tasks?”

Python and other programs remain allowed when created and executed through
`bash`, matching real pentest work. There is no Python side-channel tool.
