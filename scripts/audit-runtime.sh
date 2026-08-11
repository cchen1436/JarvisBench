#!/bin/sh
set -eu

repo="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"

command -v npm >/dev/null 2>&1 || {
    echo "npm is required to audit the locked OpenClaw runtime" >&2
    exit 2
}

# npm exits nonzero only for findings at or above the configured threshold.
# Moderate and low findings remain visible in the report; they are not hidden.
npm audit \
    --prefix "$repo/runtime/npm" \
    --package-lock-only \
    --omit=dev \
    --audit-level=high
