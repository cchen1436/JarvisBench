#!/bin/sh
set -eu

repo="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
python="${PYTHON:-python3}"
PYTHONPATH="$repo/src" "$python" -m pytest "$repo/tests"
PYTHONPATH="$repo/src" "$python" -m jarvisbench.cli validate --root "$repo"
"$python" "$repo/scripts/verify_checksums.py" \
  --root "$repo" --manifest "$repo/TASKS_SHA256SUMS"
for setting in single_agent multi_agent; do
  for track in agent_collaboration user_interaction; do
    PYTHONPATH="$repo/src" "$python" -m jarvisbench.cli dry-run --setting "$setting" --track "$track" --controller none >/dev/null
  done
done
