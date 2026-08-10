# Release extraction and equivalence

The public single-agent runtime is a clean extraction, not a copy of the
canonical research runner.

## Frozen source identity

The extraction is bound to these verified canonical identities:

| Source | SHA-256 |
|---|---|
| merge `SHA256SUMS` manifest | `f988217df4ae19ae2498b36b2f4a2f0e4a8380b61bef410bb946f173da75ade6` |
| split `SHA256SUMS` manifest | `6e82f3c1913275265b469d988f9803add1362ed180c54a3e30a9c431a51f4e61` |
| shared baseline `runner.py` | `6f645b4283d3f8cfc244e5f25d5e8ffd59950e4a30d0214102eebf6b3102cbbc` |
| merge `jarvis_runner.py` | `d9bd3ac4bbee65ed4144c084bd6f49b410b5132909b105a62e1b9b637fe484bc` |
| split `jarvis_runner.py` | `e86e908cfc32028730c3a2a932781419444e809340b1af3e62cd10c08e7c93ac` |

Merge and split contain byte-identical baseline runners. Their controlled
worker prompt also applies the same one-fragment evidence-boundary replacement;
their differences begin in requester-information ownership and control logic.

## What is behavior-equivalent

`jarvisbench.settings.single_agent_runtime` preserves:

1. The baseline worker prompt byte for byte.
2. The controlled worker prompt's exact frozen evidence-boundary replacement.
3. The frozen worker-contract hash algorithm: baseline prompt, normalized
   `runtime` and `baseline`, and the path-and-content hash of `public/`.
4. A one-node topology (`worker-0`) with no invented Parent or manager.

The contract test checks all 20 public task projections against the historical
frozen worker-contract hashes. This simultaneously detects a changed brief,
result path, baseline/runtime value, public asset, asset path, or prompt.

## Why the old scripts were not imported

The frozen `runner.py` is not a clean public library boundary. It combines the
worker contract with Slurm/Pyxis launch flags, a server filesystem image path,
OpenClaw configuration, evaluator-only grader imports, and private evaluation
hashes. The Jarvis runners additionally combine provider transport, private
requester records, control recovery, grading, and research-run reporting.
Copying or importing those monoliths would couple the public benchmark to one
cluster and would risk crossing the evaluator/requester privacy boundaries.

The public runner therefore delegates execution through a small
`SingleAgentWorkerPort`. `controller=None` is the first-class baseline. An
external or reference attention controller implements the same optional
decision port; the runner binds its output to the exact candidate session,
epoch, nonce, action ID, and fingerprint. The model never owns those fields.
The clean release also includes a model-backed loopback-Gateway OpenClaw
implementation of this port; it does not import the frozen research monolith.

## Portable state layout

For Docker execution, public task assets are a read-only bind mount at
`/task_public`, explicit exported results are a bind mount at `/host_output`,
and `/workspace` is a Docker named volume. Results, OpenClaw home, control state,
and event state are all below that Linux volume. No mutable correctness-critical
state relies on a macOS bind mount.

The host-side `review_receipts.jsonl` is a bounded, non-authoritative audit
export. It records decisions after deterministic identity binding but is never
read to drive an action, epoch, pause, or delivery transition.

The execution adapter must populate `/workspace` from `/task_public` at episode
start and explicitly export `/workspace/results` to `/host_output/results` at
episode end. Each episode receives a unique volume identity.

## Deliberate non-equivalence

The public module does not claim that a no-model port reproduces an OpenClaw
trajectory. It also does not embed a grader, private requester profile, provider
endpoint, model default, Slurm account, SQSH path, or task-specific rescue rule.
OpenClaw execution and the deterministic hold/delivery/application protocol are
covered by runtime integration tests and accepted representative single/MAS
canaries. Those canaries establish installation and control-path viability, not
full-suite score equivalence, arbitrary provider/platform parity, or permission
to pool clean-runner scores with historical results.

## Multi-agent extraction

The clean dynamic-MAS extraction is traceable to these audited canonical
identities:

| Source | SHA-256 |
|---|---|
| dynamic MAS source revision | `10c16921abc4c7158f76e9f1ed37adab98ff642fc0f4353e5ea65fa56f9cfd17` |
| dynamic submitter | `b6576cf7cd711277aa6f77f05905c8ecf8ef7acbcf9ce7c9814465c2cbdd1ea7` |
| legacy convergence-gate ablation | `8eeea63bbfaeac6ba7fa195a6e095a4f1d9bf53e86167d1a6cec5252e6847aad` |
| upstream MAS snapshot | `666a0301e648ab2f155eb4198e9ab3596df4d24cf173dfdacbb572d85fecb64a` |

Equivalence is deliberately limited to worker-visible prompts, topology,
two-phase native-Gateway protocol, and deterministic control-contract
invariants. The release extraction is not byte-identical to the research
implementation. A fixed `jbmav1_clinical_handoff` fixture guards the canonical
delegation and integration prompts at
`005534947ad97608826a51f47df28e7935265067523f02faa65341a4d44ed6ef`
and
`a29264858b8288665f93f163fe981e948be1aac5f32c7a92559b0589f18cc253`,
respectively.

Formal dynamic split means child sessions are registered and reduced while they
are running, with one separate project scheduler and session-namespaced epochs,
nonces, fingerprints, holds, invalidations, and receipts. The older one-shot
pre-integration convergence gate remains an archive-only ablation and is never
reported as continuous multi-agent attention allocation.
