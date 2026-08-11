#!/bin/sh
set -eu

repo="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
platform="${JARVISBENCH_PLATFORM:-linux/amd64}"
tag="${JARVISBENCH_IMAGE:-jarvisbench:dev}"
docker_cli="${JARVISBENCH_DOCKER_CLI:-docker}"
version="${JARVISBENCH_VERSION:-0.1.0-rc.1}"
source_url="${JARVISBENCH_SOURCE_URL:-}"
vcs_ref="${JARVISBENCH_VCS_REF:-$(git -C "$repo" rev-parse HEAD 2>/dev/null || true)}"
if [ -n "${JARVISBENCH_BUILD_DATE:-}" ]; then
    build_date="$JARVISBENCH_BUILD_DATE"
elif [ -n "$vcs_ref" ]; then
    build_date="$(git -C "$repo" show -s --format=%cI "$vcs_ref")"
else
    build_date="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
fi

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
    --build-arg "BUILD_DATE=$build_date" \
    --build-arg "VCS_REF=$vcs_ref" \
    --build-arg "VERSION=$version" \
    --build-arg "SOURCE_URL=$source_url" \
    --file "$repo/runtime/docker/Dockerfile" \
    "$repo"

if [ "${JARVISBENCH_SKIP_SMOKE:-0}" != "1" ]; then
    "$repo/scripts/container-smoke.sh" "$tag" "$platform"
fi

"$docker_cli" image inspect "$tag" \
    --format 'image={{index .RepoTags 0}} id={{.Id}} os={{.Os}} architecture={{.Architecture}}'
