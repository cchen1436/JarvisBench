"""Bind the optional reference Jarvis to the public single-agent port."""

from __future__ import annotations

from dataclasses import dataclass

from jarvisbench.core.contracts import BoundaryCandidate
from jarvisbench.core.controller import AttentionController, AttentionDecision
from jarvisbench.core.providers import TextProvider
from jarvisbench.reference.jarvis import ReferenceJarvis


@dataclass(frozen=True)
class ReferenceSingleAgentControllerAdapter:
    """Small, provider-neutral adapter with fail-closed controller semantics.

    Deterministic held-action identity binding remains in the setting runner;
    this adapter only asks the reference Jarvis for an attention decision.
    """

    controller: AttentionController
    name: str = "reference"
    max_reason_chars: int = 2_000
    max_question_chars: int = 2_000

    @classmethod
    def from_provider(
        cls,
        provider: TextProvider,
        *,
        model: str,
        reasoning: str = "medium",
    ) -> "ReferenceSingleAgentControllerAdapter":
        """Construct explicitly; there is no default provider, URL, model, or key."""

        if not model.strip():
            raise ValueError("reference controller model must be explicit")
        return cls(ReferenceJarvis(provider, model=model, reasoning=reasoning))

    def decide(self, candidate: BoundaryCandidate) -> AttentionDecision:
        try:
            decision = self.controller.decide(candidate)
            if not isinstance(decision, AttentionDecision):
                raise TypeError("reference controller returned the wrong type")
            if len(decision.reason) > self.max_reason_chars:
                raise ValueError("reference controller reason exceeds the bound")
            if (
                decision.question is not None
                and len(decision.question) > self.max_question_chars
            ):
                raise ValueError("reference controller question exceeds the bound")
            return decision
        except Exception as exc:
            # Do not include provider output, exception messages, or credentials.
            return AttentionDecision(
                False,
                f"reference controller failed closed: {type(exc).__name__}",
            )
