#!/usr/bin/env bash
set -euo pipefail
fixture_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
golden=$1
diagnostics=$2
mkdir -p -- "$diagnostics"
perturbed="$diagnostics/negative-golden.json"
"$fixture_dir/.venv/bin/python" - "$golden" "$perturbed" <<'PY'
import json,sys
from pathlib import Path
source=json.loads(Path(sys.argv[1]).read_text())
if not source['qualified'] or source['mode']!='live':
    raise AssertionError('negative source must be a qualified live trace')
source['model_calls'][0]['upstream_request']['instructions']+=' INVALID-TRAJECTORY-CHECK'
with Path(sys.argv[2]).open('x') as output:
    json.dump(source,output)
PY
for framework in native python-native rig langchain deepagents; do
  args=()
  if [ "$framework" = native ] || [ "$framework" = rig ]; then
    args=(--binary "$fixture_dir/rust/target/$framework")
  fi
  if "$fixture_dir/.venv/bin/python" "$fixture_dir/run.py" --framework "$framework" \
    --replay "$perturbed" --work-dir "$diagnostics/negative-$framework" \
    --output "$diagnostics/negative-$framework.json" "${args[@]}" \
    > "$diagnostics/negative-$framework.log" 2>&1; then
    echo "Incorrectly accepted a different request: $framework" >&2
    exit 1
  fi
  "$fixture_dir/.venv/bin/python" - "$diagnostics/negative-$framework.json" <<'PY'
import sys,json
trial=json.load(open(sys.argv[1]))
assert not trial['qualified']
assert len(trial['model_calls'])==1
assert trial['model_calls'][0]['error_type']=='AssertionError'
assert not any(e['event']=='tool.start' for e in trial['events'])
print(trial['framework'],'rejected mismatched request before tools or live model call',flush=True)
PY
done
