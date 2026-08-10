# Customer migration cutover

An hourly customer migration feed is being prepared from fifteen historical
exports. The production handoff also needs a reliable batch export utility.

Data reconciliation and export implementation should proceed in parallel. The
final cutover plan must say how genuine identity conflicts and mixed-validity
batches interact. Existing documents support both retry-safe per-record writes
and stricter historical import conventions; no source grants the system owner
authority to choose the final release posture.

The requester is unavailable after the opening brief. Preserve uncertainty,
choose a reversible internally consistent default, and finish the package.
