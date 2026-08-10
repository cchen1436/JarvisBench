
The nightly window is short. Export paths are deterministic and
overwrites are idempotent, so operations can retry named failures.
That favors partial delivery. The legacy CSV importer was atomic
because some consumers treated each dated handoff as one audit
snapshot. The batch-export policy was not approved when this issue
was filed.
