from .base import DryRunResult


class SingleAgentSetting:
    name = "single_agent"

    def dry_run(self) -> DryRunResult:
        # A single worker is a one-node project graph; it has no fake manager.
        return DryRunResult(self.name, ("worker-0",), False, False)

