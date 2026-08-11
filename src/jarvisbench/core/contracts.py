from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping


class Role(str, Enum):
    WORKER = "worker"
    PARENT = "parent"
    JARVIS = "jarvis"
    USER = "user"


class EventKind(str, Enum):
    SESSION_REGISTERED = "session_registered"
    REDUCED_UPDATE = "reduced_update"
    BOUNDARY_CANDIDATE = "boundary_candidate"
    ACTION_HELD = "action_held"
    ATTENTION_REQUESTED = "attention_requested"
    DECISION_RECORDED = "decision_recorded"
    GUIDANCE_DELIVERED = "guidance_delivered"
    GUIDANCE_APPLIED = "guidance_applied"
    ACTION_INVALIDATED = "action_invalidated"
    SESSION_COMPLETED = "session_completed"


REQUIRED_ID_FIELDS = (
    "project_id",
    "agent_id",
    "session_id",
    "turn_id",
    "batch_id",
    "action_id",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class ProjectEvent:
    project_id: str
    agent_id: str
    session_id: str
    parent_id: str | None
    role: Role
    turn_id: str
    batch_id: str
    action_id: str
    kind: EventKind
    payload: Mapping[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        for name in REQUIRED_ID_FIELDS:
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string")
        if self.parent_id is not None and not str(self.parent_id).strip():
            raise ValueError("parent_id must be null or a non-empty string")

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["role"] = self.role.value
        data["kind"] = self.kind.value
        data["payload"] = dict(self.payload)
        return data


@dataclass(frozen=True)
class ReducedUpdate:
    progress: str
    current_goal: str
    evidence: tuple[str, ...] = ()
    uncertainty: str | None = None
    proposed_action: str | None = None

    def __post_init__(self) -> None:
        if len(self.evidence) > 8:
            raise ValueError("a reduced update may contain at most 8 evidence items")
        if len(self.progress) > 2_000 or len(self.current_goal) > 1_000:
            raise ValueError("reduced update exceeds the public bounded-state contract")
        if any(len(item) > 1_000 for item in self.evidence):
            raise ValueError("evidence item exceeds 1,000 characters")


@dataclass(frozen=True)
class BoundaryCandidate:
    session_id: str
    epoch: int
    nonce: str
    action_id: str
    action_fingerprint: str
    reduced_update: ReducedUpdate
    consequence: str
    artifact_paths: tuple[str, ...] = ()
    final_record_intent: bool = False
    preview_truncated: bool = False
    task_brief: str = ""
    required_result_paths: tuple[str, ...] = ()
    review_id: str = ""
    batch_id: str = ""
    external_irreversible_effect: str = ""

    def __post_init__(self) -> None:
        if self.epoch < 0:
            raise ValueError("epoch must be non-negative")
        for name in ("session_id", "nonce", "action_id", "action_fingerprint", "consequence"):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"{name} must be non-empty")
        if len(self.artifact_paths) > 24:
            raise ValueError("boundary candidate has too many artifact paths")
        if len(self.task_brief) > 4_000:
            raise ValueError("boundary candidate task brief exceeds its bound")
        if len(self.required_result_paths) > 256:
            raise ValueError("boundary candidate has too many required results")
        if len(self.review_id) > 240 or len(self.batch_id) > 240:
            raise ValueError("boundary candidate review identity exceeds its bound")
        if len(self.external_irreversible_effect) > 240:
            raise ValueError("boundary candidate external-effect label exceeds its bound")
