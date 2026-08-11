# Release inventory

This inventory describes the current staging tree and the material that must
remain outside a participant release. It does not authorize redistribution.

## Release status

- Public task projection: present for 20 single-agent and 10 multi-agent tasks.
- Public source/runtime contracts: present.
- Optional reference controller: present as a replaceable implementation.
- No-model tests, privacy scan, task checksum validation, and topology dry runs:
  available through `scripts/validate.sh`.
- Model-backed end-to-end canary: representative single-agent and dynamic-MAS
  reference episodes passed before the final native OpenClaw/official OpenAI
  API migration. The migrated path is covered by no-model contract and
  container smokes, not a fresh score or full-suite result.
- Full benchmark rerun: intentionally not performed for release engineering.
- Public license: **missing and blocking redistribution**.
- Per-task attribution review: **not final and blocking redistribution**.
- One frozen public provenance field for `jbv1_tax_donation_audit` points to an
  upstream path named `all_tasks_with_grading.json`. No grader content is in the
  local release tree, but retaining or replacing that URL requires an explicit
  task-boundary decision because changing it would change the task checksum.
- Runtime dependency audit: **10 npm advisories (7 high, 3 critical), including
  the locked Baileys message-spoofing advisory, block public release pending
  removal, isolation, or a separately validated lock update**.
- Python packaging/runtime floor: aligned at Python 3.10 or newer; the audited
  Ubuntu 22.04 staging image uses Python 3.10.12.

## Classification

### Public participant release

The public class contains:

- sanitized `task.public.json` projections and declared public task assets;
- task, event, replay, controller, and result-handoff schemas;
- setting adapters for one-worker and Parent-plus-dynamic-children execution;
- Track 1 live attention-control and Track 2 immutable text-replay contracts;
- provider-neutral controller interfaces and the optional OpenAI-backed
  reference Jarvis/Luna code;
- the OpenClaw supervisor integration and deterministic control primitives;
- the generic evaluator handoff harness, without sealed evaluation contents;
- pinned Docker/runtime build inputs, validation scripts, examples, tests,
  checksums, and public provenance receipts.

The benchmark remains runnable with no reference controller. A custom controller
uses the same minimum interface.

### Evaluator-only

This class is absent from the participant tree and image. It includes official
task-specific graders, rubrics, evaluator prompts, reference outputs, partial
solutions, hidden validity checks, and sealed scoring data. The public
`evaluator/` directory defines only the read-only handoff contract.

Evaluator-only material is supplied by the operator after participant execution
and cannot enter a worker, Parent, child, Jarvis, Luna, replay, or participant
artifact context.

### Requester-private

This class is absent from task bundles and participant images. It includes user
profiles, memories, private requester facts, and private user-channel state. In
the separated-user reference condition, only the user service reads this class.
Jarvis receives only a bounded answer to a specific evidence-grounded question;
workers receive only the minimum scoped instruction and provenance needed for
the current decision.

Requester-private data must not be copied into prompts, traces, task manifests,
container images, controller diagnostics, or public decision receipts.

### Research archive

This class is retained outside the participant release. It includes historical
runs and reports, raw Worker/Jarvis/Luna traces, session stores, operator logs,
full research diagnostics, superseded mechanisms, source snapshots, old
pre-integration convergence-gate experiments, runtime migration archives, and
server execution artifacts.

Only explicitly reviewed, bounded, sanitized replay fixtures or aggregate public
results may cross from this class into the release. The legacy convergence gate
is an ablation and must not be described as formal dynamic MAS control.

## Task projection audit

The current task export contains:

| Setting | Tasks | Task-tree files | Approximate bytes | Symlinks |
|---|---:|---:|---:|---:|
| `single_agent` | 20 | 178 | 3,573,569 | 0 |
| `multi_agent` | 10 | 141 | 640,317 | 0 |

`TASKS_SHA256SUMS` has 320 unique entries: one provenance receipt plus all 319
task-tree files. Its path set exactly matches the exported files.

For all 20 single-agent tasks, `assets.public` exactly enumerates every public
file. Multi-agent manifests use a mix of file and directory declarations; all
10 declarations cover every actual public file, and the file-level checksum
manifest remains authoritative. No exported task path uses an evaluator-only,
requester-private, credential, or mutable-runtime path category.

The 20 single-agent public projections reproduce every frozen historical
worker-contract hash. See `docs/EQUIVALENCE.md` for the bound source identities
and limits of that equivalence claim.

The source metadata is not a completed legal attribution manifest. The current
single-agent labels are 9 MIT, 4 Apache-2.0, 1 Apache-2.0 plus paper metadata,
and 6 original-fixture/metadata-reference records. The 10 multi-agent labels
are heterogeneous original-fixture, metadata-reference, MIT, Apache-2.0, and
research-metadata combinations. Publication requires reviewing each underlying
asset and replacing these descriptive labels with approved notices where needed.

## Portability and dependency audit

The executable release surface contains no fixed server path, cluster account,
scheduler invocation, SQSH dependency, worker endpoint, or credential. The
optional reference Jarvis/Luna client intentionally targets OpenAI's official
public API. The Docker build uses pinned base-image digests and an npm lockfile;
worker model and native OpenClaw provider choices are explicit configuration.

One intentional metadata exception needs precise wording: every public task
records the provider-qualified model identity used for its frozen historical
baseline. That string is provenance, not a runtime default, endpoint, credential,
or requirement. Participants may configure another supported worker model for a
new comparison, subject to the benchmark reporting protocol.

Ignore rules mention runtime migration archive suffixes only to prevent accidental
inclusion; no such image or archive is part of this tree.

The private migration audit records the original SQSH, sanitized bootstrap
archive, capability manifests, and an audited staging OCI digest. These values
are documented in `docs/RUNTIME_MIGRATION.md` for traceability, but none of the
private artifacts is a release input or participant artifact. A final
public-release OCI manifest digest has not been issued.

## Runtime-produced material

The following is not source-release content and must remain ignored or explicitly
exported after an episode:

- workspaces, results, session stores, Gateway state, and OpenClaw homes;
- per-session control files, event buses, decision ledgers, and receipts;
- provider responses, model traces, latency records, and diagnostics;
- populated environment files and any credential-bearing configuration.

Each episode owns independent mutable state. Public task assets are read-only,
and participant results are the only normal handoff to the sealed evaluator.

## Publication blockers and non-claims

Before publication, the project must add an approved top-level license, finish
per-task attribution review, resolve or formally isolate the high/critical npm
findings, and rebuild and record the final signed OCI manifest digest.
The current same-uid file mount protects against accidental persistence, not a
malicious worker process that deliberately reads its mounted credential; a
credential broker or stronger process/uid boundary is required if that is part
of the public threat model.
The grading-bearing upstream provenance URL must likewise be reviewed; this
staging pass deliberately did not rewrite a frozen public manifest to hide it.
The release includes both the deterministic `DryRunWorker` and a model-backed
loopback-Gateway `SingleAgentWorkerPort` implementation.

This inventory does not claim that a no-model smoke reproduces an OpenClaw
trajectory, that a full batch was rerun, or that historical scores can be pooled
with the clean runtime without parity validation.
