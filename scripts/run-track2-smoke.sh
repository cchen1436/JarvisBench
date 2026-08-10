#!/bin/sh
set -eu

repo="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
python="${PYTHON:-python3}"
output="${1:-$repo/results/track2-smoke/conversation.json}"
PYTHONPATH="$repo/src" exec "$python" -m jarvisbench.cli replay \
  --trace "$repo/tests/fixtures/replay/bounded_episode.jsonl" \
  --output "$output"

