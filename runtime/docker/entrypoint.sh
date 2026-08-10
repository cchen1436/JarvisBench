#!/bin/sh
set -eu

umask 077
: "${OPENCLAW_HOME:=/var/lib/jarvisbench}"
: "${OPENCLAW_STATE_DIR:=$OPENCLAW_HOME/openclaw}"
: "${OPENCLAW_CONFIG_PATH:=$OPENCLAW_STATE_DIR/openclaw.json}"
export OPENCLAW_HOME OPENCLAW_STATE_DIR OPENCLAW_CONFIG_PATH
mkdir -p "$OPENCLAW_STATE_DIR" /var/lib/jarvisbench/episodes

case "${1:-}" in
    capabilities)
        shift
        exec /usr/local/bin/jarvisbench-capabilities "$@"
        ;;
    validate-runtime)
        shift
        exec /usr/local/bin/jarvisbench-runtime-validate "$@"
        ;;
esac

exec python3 -m jarvisbench.cli "$@"
