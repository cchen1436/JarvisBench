#!/bin/sh
set -eu

repo="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
platform="${JARVISBENCH_PLATFORM:-linux/amd64}"
tag="${JARVISBENCH_IMAGE:-jarvisbench:dev}"
docker_cli="${JARVISBENCH_DOCKER_CLI:-docker}"

command -v "$docker_cli" >/dev/null 2>&1 || {
    echo "docker CLI is required" >&2
    exit 2
}
"$docker_cli" buildx version >/dev/null 2>&1 || {
    echo "docker buildx is required" >&2
    exit 2
}

"$docker_cli" buildx build \
    --load \
    --platform "$platform" \
    --tag "$tag" \
    --file "$repo/runtime/docker/Dockerfile" \
    "$repo"

if [ "${JARVISBENCH_SKIP_SMOKE:-0}" != "1" ]; then
    "$repo/scripts/container-smoke.sh" "$tag" "$platform"
fi

"$docker_cli" image inspect "$tag" \
    --format 'image={{index .RepoTags 0}} id={{.Id}} os={{.Os}} architecture={{.Architecture}}'
