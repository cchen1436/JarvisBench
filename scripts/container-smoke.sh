#!/bin/sh
set -eu

image="${1:-${JARVISBENCH_IMAGE:-jarvisbench:dev}}"
platform="${2:-${JARVISBENCH_PLATFORM:-linux/amd64}}"
repo="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
docker_cli="${JARVISBENCH_DOCKER_CLI:-docker}"

run_image() {
    "$docker_cli" run \
        --rm \
        --platform "$platform" \
        --network none \
        --read-only \
        --cap-drop ALL \
        --security-opt no-new-privileges \
        --pids-limit 256 \
        --mount "type=bind,src=$repo/tasks,dst=/opt/jarvisbench/tasks,readonly" \
        --tmpfs /var/lib/jarvisbench:rw,nosuid,nodev,size=64m,uid=10001,gid=10001,mode=0700 \
        --tmpfs /tmp:rw,nosuid,nodev,size=64m,mode=1777 \
        "$image" "$@"
}

run_node_plugin_tests() {
    "$docker_cli" run \
        --rm \
        --platform "$platform" \
        --network none \
        --read-only \
        --cap-drop ALL \
        --security-opt no-new-privileges \
        --pids-limit 256 \
        --mount "type=bind,src=$repo/tests/typescript,dst=/opt/jarvisbench/tests/typescript,readonly" \
        --mount "type=bind,src=$repo/configs,dst=/opt/jarvisbench/configs,readonly" \
        --tmpfs /tmp:rw,nosuid,nodev,size=64m,mode=1777 \
        --entrypoint node \
        "$image" --test /opt/jarvisbench/tests/typescript/plugin_contract.test.ts
}

run_secretref_gateway_smoke() {
    "$docker_cli" run \
        --rm \
        --platform "$platform" \
        --network none \
        --read-only \
        --cap-drop ALL \
        --security-opt no-new-privileges \
        --pids-limit 256 \
        --mount "type=bind,src=$repo/tasks,dst=/opt/jarvisbench/tasks,readonly" \
        --mount "type=bind,src=$repo/tests/runtime/secretref_gateway_smoke.py,dst=/opt/jarvisbench/secretref_gateway_smoke.py,readonly" \
        --tmpfs /var/lib/jarvisbench:rw,nosuid,nodev,size=256m,uid=10001,gid=10001,mode=0700 \
        --tmpfs /tmp:rw,nosuid,nodev,size=64m,mode=1777 \
        --entrypoint python3 \
        "$image" /opt/jarvisbench/secretref_gateway_smoke.py
}

run_image capabilities
run_image validate-runtime

for setting in single_agent multi_agent; do
    for track in agent_collaboration user_interaction; do
        run_image dry-run \
            --setting "$setting" \
            --track "$track" \
            --controller none
    done
done

config_path="$(
    "$docker_cli" run \
        --rm \
        --platform "$platform" \
        --network none \
        --read-only \
        --cap-drop ALL \
        --security-opt no-new-privileges \
        --tmpfs /var/lib/jarvisbench:rw,nosuid,nodev,size=64m,uid=10001,gid=10001,mode=0700 \
        --tmpfs /tmp:rw,nosuid,nodev,size=64m,mode=1777 \
        --entrypoint openclaw \
        "$image" config file
)"
case "$config_path" in
    /var/lib/jarvisbench/openclaw/openclaw.json|'$OPENCLAW_HOME/openclaw/openclaw.json')
        ;;
    *)
        printf 'unexpected OpenClaw config path: %s\n' "$config_path" >&2
        exit 1
        ;;
esac

run_node_plugin_tests
run_secretref_gateway_smoke

printf 'container smoke passed: image=%s platform=%s\n' "$image" "$platform"
