from __future__ import annotations

from dataclasses import dataclass

from jarvisbench.core.contracts import BoundaryCandidate
from jarvisbench.core.controller import AttentionController, AttentionDecision


@dataclass
class AgentCollaborationTrack:
    """Live agent-to-user attention path.

    This is the only public track allowed to expose a ControlPort to a runner.
    """

    controller: AttentionController
    name: str = "agent_collaboration"

    def evaluate_candidate(self, candidate: BoundaryCandidate) -> AttentionDecision:
        return self.controller.decide(candidate)

