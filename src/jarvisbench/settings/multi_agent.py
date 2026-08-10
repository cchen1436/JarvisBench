from __future__ import annotations

from dataclasses import dataclass

from .base import DryRunResult


@dataclass(frozen=True)
class SessionRegistration:
    session_key: str
    agent_id: str
    parent_id: str
    role: str = "worker"


class DynamicSessionRegistry:
    def __init__(self) -> None:
        self._by_session: dict[str, SessionRegistration] = {}

    def register(self, registration: SessionRegistration) -> None:
        existing = self._by_session.get(registration.session_key)
        if existing is not None and existing != registration:
            raise RuntimeError("a child session cannot be rebound to another logical worker")
        self._by_session[registration.session_key] = registration

    def resolve(self, session_key: str) -> SessionRegistration:
        return self._by_session[session_key]


class MultiAgentSetting:
    name = "multi_agent"

    def dry_run(self) -> DryRunResult:
        # Children are dynamic, so only the parent exists before delegation.
        return DryRunResult(self.name, ("parent",), False, True)

    @staticmethod
    def protocol() -> tuple[str, ...]:
        return ("parent_delegation", "children_complete", "parent_integration")

