from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .contracts import BoundaryCandidate


@dataclass(frozen=True)
class AttentionDecision:
    request_attention: bool
    reason: str
    question: str | None = None
    scope: str = "worker"

    def __post_init__(self) -> None:
        if self.request_attention and not self.question:
            raise ValueError("an attention request requires a question")
        if self.scope not in {"worker", "project", "portfolio"}:
            raise ValueError("invalid decision scope")


class AttentionController(Protocol):
    """Minimal interface shared by baseline, third-party, and reference controllers."""

    def decide(self, candidate: BoundaryCandidate) -> AttentionDecision:
        ...


class NoController:
    """Baseline controller: never consumes user attention."""

    def decide(self, candidate: BoundaryCandidate) -> AttentionDecision:
        return AttentionDecision(False, "baseline controller disabled")

