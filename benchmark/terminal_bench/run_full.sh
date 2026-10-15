#!/usr/bin/env bash
set -Eeuo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
jobs_dir="${TERMINAL_BENCH_JOBS_DIR:-${repo_root}/jobs}"
job_prefix="${TERMINAL_BENCH_JOB_PREFIX:-decepticon-bash-luna-max-tbench-2-1-full}"
model="${TERMINAL_BENCH_MODEL:-auth/gpt-5.6-luna}"
effort="${TERMINAL_BENCH_EFFORT:-max}"
batch_size="${TERMINAL_BENCH_BATCH_SIZE:-5}"
attempts="${TERMINAL_BENCH_ATTEMPTS:-5}"
concurrency="${TERMINAL_BENCH_CONCURRENCY:-1}"
expected_tasks="${TERMINAL_BENCH_EXPECTED_TASKS:-89}"
proxy_container="${TERMINAL_BENCH_PROXY_CONTAINER:-saas-litellm}"
proxy_url="${TERMINAL_BENCH_PROXY_URL:-http://127.0.0.1:4001}"
cache_root="${HARBOR_CACHE_ROOT:-${XDG_CACHE_HOME:-${HOME}/.cache}/harbor}"
tasks_root="${TERMINAL_BENCH_TASKS_ROOT:-${cache_root}/tasks/packages/terminal-bench}"
dry_run=false

usage() {
    printf '%s\n' \
        "Usage: $0 [--dry-run]" \
        "" \
        "Runs Terminal-Bench 2.1 in resumable batches. Configuration is read" \
        "from TERMINAL_BENCH_* environment variables."
}

while (($#)); do
    case "$1" in
        --dry-run) dry_run=true ;;
        --help|-h) usage; exit 0 ;;
        *) printf 'Unknown argument: %s\n' "$1" >&2; usage >&2; exit 2 ;;
    esac
    shift
done

for value in "$batch_size" "$attempts" "$concurrency" "$expected_tasks"; do
    if [[ ! "$value" =~ ^[1-9][0-9]*$ ]]; then
        printf 'Batch size, attempts, concurrency, and expected task count must be positive integers.\n' >&2
        exit 2
    fi
done

if [[ ! -d "$tasks_root" ]]; then
    printf 'Harbor Terminal-Bench cache not found: %s\n' "$tasks_root" >&2
    exit 1
fi

mkdir -p "$jobs_dir"
exec 9>"${jobs_dir}/.${job_prefix}.lock"
if ! flock -n 9; then
    printf 'Another %s runner is already active.\n' "$job_prefix" >&2
    exit 0
fi

declare -a tasks=()
declare -a images=()
while IFS= read -r task_file; do
    task_name="$(basename "$(dirname "$(dirname "$task_file")")")"
    image="$(sed -n 's/^docker_image = "\([^"]*\)"/\1/p' "$task_file")"
    if [[ -z "$task_name" || -z "$image" || "$image" == *[[:space:]]* ]]; then
        printf 'Invalid task metadata in %s\n' "$task_file" >&2
        exit 1
    fi
    tasks+=("terminal-bench/${task_name}")
    images+=("$image")
done < <(find "$tasks_root" -name task.toml -type f | sort)

if ((${#tasks[@]} != expected_tasks)); then
    printf 'Expected %d Terminal-Bench tasks, found %d under %s\n' \
        "$expected_tasks" "${#tasks[@]}" "$tasks_root" >&2
    exit 1
fi

if [[ "$dry_run" == true ]]; then
    printf 'tasks=%d attempts=%d trials=%d batch_size=%d batches=%d concurrency=%d model=%s effort=%s\n' \
        "${#tasks[@]}" "$attempts" "$(( ${#tasks[@]} * attempts ))" "$batch_size" \
        "$(( (${#tasks[@]} + batch_size - 1) / batch_size ))" "$concurrency" "$model" "$effort"
    exit 0
fi

proxy_key="$(
    docker inspect "$proxy_container" --format '{{range .Config.Env}}{{println .}}{{end}}' \
        | sed -n 's/^LITELLM_MASTER_KEY=//p' \
        | head -n 1
)"
if [[ -z "$proxy_key" ]]; then
    printf 'Could not resolve LITELLM_MASTER_KEY from %s.\n' "$proxy_container" >&2
    exit 1
fi
if ! curl --fail --silent --show-error \
    --header "Authorization: Bearer ${proxy_key}" \
    "${proxy_url}/v1/models" >/dev/null; then
    printf 'LiteLLM proxy is not ready at %s.\n' "$proxy_url" >&2
    exit 1
fi

export DECEPTICON_LLM__PROXY_URL="$proxy_url"
export DECEPTICON_LLM__PROXY_API_KEY="$proxy_key"
export DECEPTICON_SKIP_BOOT=1
export PYTHONPATH="${repo_root}${PYTHONPATH:+:${PYTHONPATH}}"

batch_number=0
for ((start = 0; start < ${#tasks[@]}; start += batch_size)); do
    batch_number=$((batch_number + 1))
    end=$((start + batch_size))
    if ((end > ${#tasks[@]})); then
        end=${#tasks[@]}
    fi

    printf -v suffix '%03d' "$batch_number"
    job_name="${job_prefix}-batch-${suffix}"
    job_path="${jobs_dir}/${job_name}"

    if [[ -f "${job_path}/result.json" ]] \
        && jq -e '.stats.n_pending_trials == 0 and .stats.n_running_trials == 0' \
            "${job_path}/result.json" >/dev/null; then
        printf '[%s] already complete; releasing its task image tags.\n' "$job_name"
        for ((index = start; index < end; index += 1)); do
            docker image rm "${images[index]}" >/dev/null 2>&1 || true
        done
        continue
    fi

    printf '[%s] preparing %d task images.\n' "$job_name" "$((end - start))"
    for ((index = start; index < end; index += 1)); do
        docker pull "${images[index]}"
    done

    if [[ -f "${job_path}/config.json" ]]; then
        uv run harbor job resume \
            --job-path "$job_path" \
            --filter-error-type CancelledError \
            --filter-error-type EnvironmentStartTimeoutError
    else
        include_args=()
        for ((index = start; index < end; index += 1)); do
            include_args+=(--include-task-name "${tasks[index]}")
        done
        uv run harbor run \
            --dataset terminal-bench/terminal-bench-2-1 \
            --agent decepticon_tbench_agent:DecepticonTerminalBenchAgent \
            --model "$model" \
            --agent-kwarg "reasoning_effort=${effort}" \
            --n-attempts "$attempts" \
            --n-concurrent "$concurrency" \
            --job-name "$job_name" \
            --jobs-dir "$jobs_dir" \
            "${include_args[@]}"
    fi

    if ! jq -e '.stats.n_pending_trials == 0 and .stats.n_running_trials == 0' \
        "${job_path}/result.json" >/dev/null; then
        printf '[%s] stopped with unfinished trials; resume is required.\n' "$job_name" >&2
        exit 1
    fi

    printf '[%s] complete; releasing its task image tags.\n' "$job_name"
    for ((index = start; index < end; index += 1)); do
        docker image rm "${images[index]}" >/dev/null 2>&1 || true
    done
done

mapfile -t result_files < <(find "$jobs_dir" -maxdepth 2 -path "*/${job_prefix}-batch-*/result.json" -type f | sort)
summary_path="${jobs_dir}/${job_prefix}-summary.json"
jq -s --arg model "$model" --arg effort "$effort" '
    {
        model: $model,
        reasoning_effort: $effort,
        jobs: map(.id),
        n_jobs: length,
        n_total_trials: (map(.n_total_trials) | add),
        n_completed_trials: (map(.stats.n_completed_trials) | add),
        n_errored_trials: (map(.stats.n_errored_trials) | add),
        n_input_tokens: (map(.stats.n_input_tokens // 0) | add),
        n_cache_tokens: (map(.stats.n_cache_tokens // 0) | add),
        n_output_tokens: (map(.stats.n_output_tokens // 0) | add),
        started_at: (map(.started_at) | min),
        finished_at: (map(.finished_at) | max)
    }
' "${result_files[@]}" > "$summary_path"
printf 'Full Terminal-Bench run complete: %s\n' "$summary_path"
