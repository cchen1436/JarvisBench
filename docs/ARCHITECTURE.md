# Architecture

JarvisBench has two orthogonal axes.

| | Agent collaboration | User interaction |
|---|---|---|
| Single agent | one worker plus an optional attention controller | immutable replay of one worker's bounded updates |
| Multi agent | Parent execution manager, dynamic children, and one separate project-level attention controller | immutable replay of bounded project updates |

The setting adapter owns execution topology. The track adapter owns what is
evaluated. Neither axis selects a model or reveals evaluator data.

`controller=none` is the baseline and is a first-class configuration. A third
party controller and the optional reference Jarvis implement the same minimal
`AttentionController` protocol.

In multi-agent runs, Parent remains the execution manager. Jarvis is a separate
attention allocator with its own prompt, context, state, and model call. Dynamic
children register after native Gateway delegation. Mutable control epochs are
strictly per session; only the append-only event bus and decision ledger are
project-wide.

Track 2 is post-hoc text replay. It only receives immutable, bounded, sanitized
frames and has no handle to a worker process, ControlStore, task workspace, or
artifact output. Running it therefore cannot alter Track 1 prompts, timing,
control state, scores, or artifacts.

The old pre-integration convergence gate is a research ablation. It is not the
formal multi-agent split and is not part of the active public runtime.

