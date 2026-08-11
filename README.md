# JarvisBench

JarvisBench evaluates how a control layer allocates scarce user attention
during long-horizon agent work. The benchmark tasks, execution contracts, and
evaluation boundary are the product. The included Jarvis controller is an
optional reference implementation, not a prerequisite for running the
benchmark.

> **Release status:** this is an engineering staging tree, not a publishable
> release. It has no top-level public `LICENSE`, per-task attribution still
> requires final review, binary task fixtures still require a final metadata/PII
> audit, and same-UID worker/secret isolation remains a documented privacy
> limitation. Authorized representative single- and multi-agent reference
> canaries passed before the final native OpenClaw/official OpenAI API migration;
> the migrated path is covered by no-model contract and container smokes, not a
> new score claim. Do not redistribute this tree until the remaining blockers
> are resolved.

## Two independent axes

| Setting | Agent collaboration | User interaction |
|---|---|---|
| `single_agent` | one worker; optional attention controller | text replay of one worker's bounded updates |
| `multi_agent` | Parent execution manager, dynamic children, and a separate project-level attention controller | text replay of bounded project updates |

The **setting** chooses execution topology. The **track** chooses what is
evaluated:

- `agent_collaboration` (Track 1) measures the agent-to-user direction. A
  controller may hold a consequential action, request user judgment, and route
  the resulting decision back to the affected execution node.
- `user_interaction` (Track 2) measures the user-to-Jarvis direction. It is a
  post-hoc text replay with early and late general/follow-up questions. It has no
  worker, control-state, prompt, timing, artifact, or scoring handle.

These are four combinations backed by shared contracts and orthogonal adapters,
not four copied implementations. Historical `merge` and `split` labels describe
reference-controller information flow; they are not aliases for single/multi or
Track 1/Track 2.

## Controller choices

- `none`: the first-class baseline. No Jarvis or user model is required.
- `external`: a participant implements the small `AttentionController` protocol.
- `reference`: the optional Jarvis implementation. Jarvis and Luna use the
  official OpenAI Python SDK and Responses API; worker calls remain inside
  OpenClaw's native provider stack.

The `TextProvider` protocol remains available to third-party controllers, but
the bundled reference controller does not use an NVIDIA proxy or a custom
OpenAI-compatible endpoint. Its defaults are `gpt-5.6-sol` for Jarvis and
`gpt-5.6-luna` for Luna, authenticated only by `OPENAI_API_KEY` (or a mounted
`OPENAI_API_KEY_FILE`). OpenClaw receives a native model ID such as
`anthropic/claude-opus-4-8` and the matching vendor credential; JarvisBench does
not generate a `models.providers` catalog.

The same benchmark tasks and worker-visible contracts apply to all controller
choices. Official grading is a separate, sealed evaluator operation.

## Quickstart

From the repository root, validate everything without a model call:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[test]'
./scripts/validate.sh
```

Build the pinned staging Linux image. The build command also runs an offline,
no-model container smoke: capability discovery, public-task checksum/privacy
validation, and all four setting × track dry runs. Passing this smoke does not
make the image an approved public-release artifact.

```sh
./scripts/build-image.sh
```

The image exposes the validation and configuration interfaces directly:

```sh
docker run --rm --platform linux/amd64 jarvisbench:dev capabilities
docker run --rm --platform linux/amd64 jarvisbench:dev \
  validate-runtime --runtime-only
docker run --rm --platform linux/amd64 \
  --mount "type=bind,src=$(pwd)/tasks,dst=/opt/jarvisbench/tasks,readonly" \
  jarvisbench:dev validate-runtime
docker run --rm --platform linux/amd64 jarvisbench:dev \
  dry-run --setting multi_agent --track agent_collaboration --controller none
```

To rerun only the hardened container smoke:

```sh
./scripts/container-smoke.sh jarvisbench:dev linux/amd64
```

The private release-candidate runtime is published separately from the task
repository. After authenticating to GHCR, it can be used with the checked-out
task tree:

```sh
docker login ghcr.io
docker pull --platform linux/amd64 \
  ghcr.io/cchen1436/jarvisbench-openclaw:0.1.0-rc.2
docker run --rm --platform linux/amd64 \
  --mount "type=bind,src=$(pwd)/tasks,dst=/opt/jarvisbench/tasks,readonly" \
  ghcr.io/cchen1436/jarvisbench-openclaw:0.1.0-rc.2 validate-runtime
```

No `latest` tag is issued for this staging runtime.

Run the isolated deterministic Track 2 smoke:

```sh
./scripts/run-track2-smoke.sh
```

`dry-run` validates configuration and topology only; it does not call a model or
claim an end-to-end benchmark result. See [Quickstart](docs/QUICKSTART.md) for all
four combinations and controller integration boundaries.

The single-agent release surface includes both the stable `SingleAgentWorkerPort`
contract and a built-in loopback-Gateway OpenClaw adapter. `jarvisbench run` can
execute a baseline or the optional reference controller when provider, model,
credential, and requester inputs are supplied explicitly. The no-model
quickstart still makes no end-to-end or score claim.

## Repository map

```text
src/jarvisbench/core/       shared contracts, privacy guards, replay, control state
src/jarvisbench/settings/   single-agent and multi-agent topology adapters
src/jarvisbench/tracks/     Track 1 live control and Track 2 immutable replay
src/jarvisbench/reference/  optional Jarvis, Luna, and reference routing logic
tasks/                      participant-visible task projections and public assets
evaluator/                  public handoff contract, not sealed evaluation assets
plugins/                    OpenClaw supervisor integration
runtime/                    pinned Docker/runtime inputs
configs/schemas/            participant-facing event, task, and replay schemas
examples/                   third-party controller examples
tests/ and scripts/         no-model validation and release tooling
```

## Portability and privacy

The executable release surface has no fixed cluster account, scheduler command,
server filesystem path, runtime image archive path, worker endpoint, or API
credential. The optional reference controller deliberately targets OpenAI's
official public API. Historical provider-qualified model IDs in public task
baseline metadata identify the frozen measurement; they are not executable
defaults.

Task assets are mounted read-only. Mutable workspace, OpenClaw, control, event,
Gateway, and session state belongs to an episode-local Linux filesystem or Docker
named volume; only explicit results are exported. The public tree contains no
sealed graders, rubrics, requester profiles, reference answers, raw research
traces, or populated environment file.

Read [Architecture](docs/ARCHITECTURE.md),
[Privacy](docs/PRIVACY.md), and the
[Release inventory](docs/RELEASE_INVENTORY.md) before integrating a runtime.
The private SQSH bootstrap and canonical OCI build boundary are documented in
[Runtime migration](docs/RUNTIME_MIGRATION.md). Known runtime risks and the
reporting boundary are listed in [Security](SECURITY.md).
