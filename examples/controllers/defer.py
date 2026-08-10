"""Smallest valid third-party attention controller: always defer."""

from jarvisbench.core.controller import AttentionDecision


class Controller:
    def decide(self, candidate):
        return AttentionDecision(False, "no user judgment is required")

