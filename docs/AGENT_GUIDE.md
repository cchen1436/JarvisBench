# Agent setup and runbook

This guide is for a coding agent installing JarvisBench on a user's computer.
Work from the repository root and preserve unrelated working-tree changes.

## Default goal and stopping point

Unless the user asks for more, do exactly this:

1. inspect the host;
2. validate the source tree without models;
3. build and smoke-test the pinned Docker image;
4. inspect all four setting × track configurations;
5. run the isolated deterministic Track 2 replay;
6. report the results and stop.

Do not infer permission to call a model, spend API credits, run a task sweep,
publish an artifact, or change benchmark content.

## Hard boundaries

- Never modify task prompts, public assets, schemas, checksums, graders, or
  worker-visible contracts to make validation pass.
- Never print, commit, bake into an image, or persist a credential in a run
  manifest or log.
- Never expose requester context to a worker or evaluator-only state to any
  participant runtime.
- Track 2 is a post-hoc replay path. It must not mutate Track 1 state, timing,
  prompts, artifacts, or scores.
- MAS uses the native loopback Gateway. Do not replace it with
  `openclaw agent --local`, and never merge Parent with Jarvis.
- Use a fresh, empty episode directory or Docker named volume for each run.
- One representative model-backed episode is the default maximum when the user
  approves a canary. Never start a full suite implicitly.

## 1. Inspect the host

Run:

```sh
git status --short
python3 --version
docker version
docker buildx version
```

Python 3.10 or newer is required. Docker must support `linux/amd64`; Apple
Silicon uses emulation. If Docker is unavailable, complete source validation
and report Docker as a blocker rather than installing an unrequested daemon.

## 2. Validate from source

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[test]'
./scripts/validate.sh
```

This runs the Python tests, privacy scan, public-task checksum validation, and
all four no-model configuration smokes. A checksum failure is a hard stop; do
not repair a frozen task.

## 3. Build and validate Docker

```sh
JARVISBENCH_IMAGE=jarvisbench:dev \
JARVISBENCH_PLATFORM=linux/amd64 \
  ./scripts/build-image.sh
```

The build script already runs the hardened container smoke. Do not disable it.
Optional read-only inspection:

```sh
docker run --rm --platform linux/amd64 jarvisbench:dev capabilities
docker run --rm --platform linux/amd64 jarvisbench:dev \
  validate-runtime --runtime-only
```

## 4. Inspect the four configurations

```sh
for setting in single_agent multi_agent; do
  for track in agent_collaboration user_interaction; do
    docker run --rm --platform linux/amd64 jarvisbench:dev \
      dry-run \
      --setting "$setting" \
      --track "$track" \
      --controller none
  done
done
```

Expected invariants:

- `single_agent` has one worker and no synthetic manager;
- `multi_agent` reports that a Gateway is required and Jarvis is not Parent;
- `user_interaction` reports `mutates_worker: false`;
- none of these commands initializes a model client.

## 5. Run Track 2 replay

```sh
./scripts/run-track2-smoke.sh
```

Verify that `results/track2-smoke/conversation.json` contains four turns: early
general, early follow-up, late general, and late follow-up. This is a
deterministic contract smoke with synthetic latency, not a Track 2 quality score
or real first-token latency measurement.

## 6. Optional model-backed Track 1 episode

Proceed only after the user explicitly approves the call and identifies the
task, setting, worker model, provider, and credential source. Before executing,
state which models will be called and confirm that no loop or task sweep is
present.

A baseline requires a native OpenClaw worker model ID in `provider/model` form
and that provider's credential. The reference controller additionally requires
an official OpenAI credential, explicit Jarvis and Luna model IDs, and a
bounded requester-context JSON file stored outside the repository.

Use `jarvisbench run --help` as the authoritative option list. `run` executes
only `agent_collaboration`; `user_interaction` uses `jarvisbench replay`.
Start with `single_agent` unless MAS was requested. Use `--controller none` for
a baseline and `--controller reference` for the bundled implementation.

Representative command shape from the source virtual environment:

```sh
jarvisbench run \
  --setting single_agent \
  --track agent_collaboration \
  --controller none \
  --task-dir /tasks/<task-id> \
  --episode-root /episode \
  --worker-model <provider/model> \
  --worker-api-key-env <VENDOR_API_KEY_NAME>
```

For the reference controller, replace the controller and add:

```sh
--controller reference \
--jarvis-model <openai-model> \
--jarvis-reasoning medium \
--user-model <openai-model> \
--requester-context /run/requester/profile.json
```

Pass only the name of an existing credential environment variable, or mount a
private credential file read-only. Never place a literal secret in a command.
When using Docker, pass the same arguments through the image entrypoint as
`docker run ... jarvisbench:dev run ...`; the image does not install a second
`jarvisbench` console-script wrapper. Mount one selected task read-only and use
a fresh named volume for `/episode`.
See [`QUICKSTART.md`](QUICKSTART.md) for the complete runtime boundary.

## 7. Report and stop

Report:

- source validation status;
- image tag, platform, and digest;
- container smoke status;
- the four dry-run invariants;
- Track 2 replay output path;
- any blocker or compromise.

For an approved model-backed episode, additionally report only the setting,
task ID, controller, model IDs, runtime status, attention-request count, and
exported result path. Never include secret values, requester content, raw
private traces, or an unearned benchmark score.
