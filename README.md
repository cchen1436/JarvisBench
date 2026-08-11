# JarvisBench

JarvisBench evaluates how a control layer allocates scarce user attention
during long-horizon agent work. The benchmark is usable without Jarvis; the
included Jarvis controller is an optional reference implementation.

> **Research preview:** redistribution is not yet licensed, and release review
> is still in progress. See the [release inventory](docs/RELEASE_INVENTORY.md).

## What it evaluates

| Setting | Agent collaboration (Track 1) | User interaction (Track 2) |
|---|---|---|
| `single_agent` | one working agent with optional attention control | text replay of one agent's bounded updates |
| `multi_agent` | Parent plus dynamic children and a separate global attention controller | text replay of bounded project updates |

- **Track 1** measures whether a controller requests user judgment at useful
  moments and routes the answer back to the right execution node.
- **Track 2** measures whether Jarvis can answer user questions from a bounded,
  progressively revealed replay without changing Track 1 execution.

The execution topology and evaluation track are independent. In MAS, Parent is
the execution manager and Jarvis is the attention layer; they are never the same
logical agent.

Controller choices are `none` (baseline), a participant's own controller, or
the optional `reference` Jarvis implementation. Official grading remains a
separate sealed-evaluator operation.

## Run it with your coding agent

Ask your agent:

> Read `AGENTS.md`, install JarvisBench locally, run the no-model validation,
> and report the results. Stop before any API call unless I explicitly approve
> a model-backed run.

[`AGENTS.md`](AGENTS.md) points to the complete machine-readable runbook and
defines safe stopping points, credential handling, Docker setup, and validation
criteria.

## Quickstart

Validate the source tree without an API key or model call:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[test]'
./scripts/validate.sh
```

Build the pinned `linux/amd64` runtime and run its no-model container smoke:

```sh
./scripts/build-image.sh
```

Inspect one configuration or run the isolated Track 2 replay:

```sh
jarvisbench dry-run \
  --setting multi_agent \
  --track agent_collaboration \
  --controller none
./scripts/run-track2-smoke.sh
```

These commands do not produce a benchmark score. Model-backed baseline and
reference-controller instructions are in the [full quickstart](docs/QUICKSTART.md).

## Documentation

- [Documentation index](docs/README.md)
- [Architecture and the two independent axes](docs/ARCHITECTURE.md)
- [Installation, Docker, controllers, and real episodes](docs/QUICKSTART.md)
- [Privacy and evaluation boundary](docs/PRIVACY.md)
- [Runtime reproducibility](docs/RUNTIME_MIGRATION.md)
- [Security policy](.github/SECURITY.md)

The main implementation is under `src/jarvisbench/`; public tasks are under
`tasks/`; the optional OpenClaw integration is under `plugins/` and `runtime/`.

