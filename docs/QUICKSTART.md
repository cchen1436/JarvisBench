# Quickstart

Sections 1--4 cover release validation and no-model configuration smokes. The
later opt-in command shape covers one model-backed episode; it is not an
official score or full-suite claim.

## Requirements

- Python 3.10 or newer for source validation.
- Docker with Buildx and `linux/amd64` support for the staging image that targets
  the eventual canonical runtime.
- No API key, provider account, Jarvis model, or user model for Sections 1--4.

The staging tree is not redistributable until a public license and per-task
attribution review are complete.

## 1. Validate from source

From the repository root:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[test]'
./scripts/validate.sh
```

The validation command runs the Python tests, privacy scan, public task checksum
verification, and all four `setting × track` baseline dry runs. It makes no
network or model call.

## 2. Inspect all four configurations

```sh
for setting in single_agent multi_agent; do
  for track in agent_collaboration user_interaction; do
    jarvisbench dry-run \
      --setting "$setting" \
      --track "$track" \
      --controller none
  done
done
```

Expected topology facts:

- `single_agent` contains exactly one execution node and no fake manager.
- `multi_agent` starts with Parent; native children are discovered dynamically
  after delegation, and Jarvis is never the Parent execution manager.
- `user_interaction` is non-mutating regardless of setting.
- `agent_collaboration` mutates worker control state only when an enabled
  controller actually intervenes.

The CLI `dry-run` command resolves these contracts; it is not an agent launcher.

## 3. Run Track 2 independently

```sh
./scripts/run-track2-smoke.sh
```

This consumes the bounded replay fixture and writes a conversation record below
the ignored local `results/` directory. The record contains four text turns:

1. early general question;
2. early follow-up grounded in Jarvis's reply;
3. late general question;
4. late follow-up grounded in Jarvis's reply.

The deterministic smoke reports synthetic zero latency and tests only the replay
contract. A model-backed responder records first-token latency, but that workflow
is intentionally excluded from the no-model quickstart.

Track 2 receives only incrementally visible bounded frames. Its questioner sees
the opening request and prior conversation, never the worker trace. It has no
`ControlStore`, worker-process, task-workspace, artifact, or grader handle, so it
cannot alter Track 1.

## 4. Build and inspect the staging Docker image

```sh
JARVISBENCH_IMAGE=jarvisbench:dev \
JARVISBENCH_PLATFORM=linux/amd64 \
  ./scripts/build-image.sh

docker run --rm --platform linux/amd64 jarvisbench:dev \
  dry-run --setting single_agent --track agent_collaboration --controller none
```

The image contains the runtime and Python package, not public task bundles,
sealed evaluator assets, populated environment files, or research runs. It is a
staging image, not the final signed release image. A real episode mounts one
selected public task read-only and exports results explicitly. Correctness-critical
mutable state must use an episode-local Docker named volume, not a macOS bind
mount.

The standalone runtime check does not require a task mount; the complete check
bind-mounts the public bundle read-only and verifies every file against the
checksum contract embedded in the image:

```sh
docker run --rm --platform linux/amd64 jarvisbench:dev \
  validate-runtime --runtime-only
docker run --rm --platform linux/amd64 \
  --mount "type=bind,src=$(pwd)/tasks,dst=/opt/jarvisbench/tasks,readonly" \
  jarvisbench:dev validate-runtime
```

On Apple Silicon, `--platform linux/amd64` uses emulation. This is expected for
parity with the canonical server architecture.

## 5. Choose a controller

### Baseline

Use `controller=none`. The worker continues with its best reversible judgment
when requester-owned information is unavailable. No reference code or provider
configuration is required.

### Custom controller

Implement:

```python
from jarvisbench.core.controller import AttentionDecision


class Controller:
    name = "my-controller"

    def decide(self, candidate):
        return AttentionDecision(False, "no user judgment is required")
```

The complete minimal example is in `examples/controllers/defer.py`. For the
single-agent setting, pass the controller through the public
`SingleAgentWorkerPort`/`SingleAgentRunner` boundary. For multi-agent execution,
keep the project-level scheduler separate from Parent and preserve per-session
held-action identity.

The staging release also includes `OpenClawSingleAgentWorker`, which executes
one worker through a loopback Gateway with no fake Parent. External runtimes can
still replace it through the same `SingleAgentWorkerPort` boundary.

### Reference controller

The reference implementation uses the same controller boundary and has no
provider or model default. Copy `.env.example` to an ignored local environment
file and populate it only when performing an explicitly approved model-backed
run. Never bake credentials into the image, task manifests, run manifests, or
logs.

The executable command surface is provider-neutral. A launcher supplies an
episode-local `/workspace` Docker volume, a read-only public task, an export
root, and out-of-tree secret/requester files, then invokes this shape:

```sh
jarvisbench run \
  --setting single_agent --track agent_collaboration \
  --controller reference \
  --task-dir /tasks/<task-id> --episode-root /episode/runs \
  --worker-model <provider/model> --provider-base-url <openai-compatible-url> \
  --worker-api-key-file /run/provider/key \
  --jarvis-model <provider/model> --jarvis-reasoning medium \
  --user-model <provider/model> \
  --requester-context /run/requester/profile.json
```

Credentials are supplied only through ignored environment/file mounts; the
public image has no endpoint, model, or key default. Authorized representative
single- and multi-agent reference canaries completed with exact receipt closure,
valid artifacts, and sealed-grader handoff. This does not validate the full
suite. File-backed `SecretRef` prevents accidental credential persistence, but
same-UID worker code can still deliberately read mounted files; use a broker or
stronger process/UID boundary when that threat is in scope. See
`docs/RUNTIME_MIGRATION.md` before enabling a provider.

## 6. Official evaluation boundary

Participant execution ends before official grading begins. The operator gives a
sealed evaluator a read-only task-evaluation bundle and read-only participant
results. The participant runtime never receives evaluator rubrics, graders, or
reference material. The reference requester component lazily reads sanitized,
out-of-tree requester context; it is not placed in the worker prompt or child
environment, while same-UID mount access remains the limitation stated above.

See `evaluator/README.md` for the public handoff and
`docs/RELEASE_INVENTORY.md` for the data classification.
