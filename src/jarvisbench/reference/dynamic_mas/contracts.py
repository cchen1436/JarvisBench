from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence


SCHEMA_VERSION = "1.0"
CONTROL_PROTOCOL_VERSION = "1.0-release"
CANONICAL_DYNAMIC_MAS_SOURCE_SHA256 = (
    "10c16921abc4c7158f76e9f1ed37adab98ff642fc0f4353e5ea65fa56f9cfd17"
)
CANONICAL_DYNAMIC_SUBMITTER_SHA256 = (
    "b6576cf7cd711277aa6f77f05905c8ecf8ef7acbcf9ce7c9814465c2cbdd1ea7"
)
CANONICAL_LEGACY_CONVERGENCE_SHA256 = (
    "8eeea63bbfaeac6ba7fa195a6e095a4f1d9bf53e86167d1a6cec5252e6847aad"
)
CANONICAL_MAS_UPSTREAM_SNAPSHOT_SHA256 = (
    "666a0301e648ab2f155eb4198e9ab3596df4d24cf173dfdacbb572d85fecb64a"
)
MAX_EVENT_LINE_BYTES = 64 * 1024
MAX_REDUCED_UPDATE_BYTES = 6 * 1024
MAX_PREVIEW_CHARS = 900
MAX_EVIDENCE_ITEMS = 4
MAX_HELD_ACTIONS = 8
MAX_GUIDANCE_CHARS = 1_600

_ID = re.compile(r"[A-Za-z0-9_.:@/-]{1,240}\Z")
_SHA256 = re.compile(r"[a-f0-9]{64}\Z")
_NONCE = re.compile(r"[a-f0-9]{32,128}\Z")


class DynamicMasContractError(ValueError):
    """A public dynamic-MAS message violated its exact schema."""


class StaleReviewError(DynamicMasContractError):
    """A control mutation did not bind the current session generation."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def exact_id(value: Any, name: str) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise DynamicMasContractError(f"{name} is not a safe identifier")
    return value


def exact_nonce(value: Any) -> str:
    if not isinstance(value, str) or not _NONCE.fullmatch(value):
        raise DynamicMasContractError("nonce is malformed")
    return value


def exact_sha256(value: Any, name: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise DynamicMasContractError(f"{name} is not a SHA-256 digest")
    return value


def bounded_text(value: Any, name: str, maximum: int, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise DynamicMasContractError(f"{name} must be text")
    clean = " ".join(value.split())
    if (not clean and not allow_empty) or len(clean) > maximum:
        raise DynamicMasContractError(f"{name} exceeds its bound")
    return clean


@dataclass(frozen=True)
class SessionBinding:
    project_id: str
    agent_id: str
    session_id: str
    session_key: str
    parent_id: str
    parent_session_id: str
    parent_session_key: str
    role: str
    workstream_id: str
    status: str = "active"

    def __post_init__(self) -> None:
        for name in (
            "project_id",
            "agent_id",
            "session_id",
            "session_key",
            "parent_id",
            "parent_session_id",
            "parent_session_key",
        ):
            exact_id(getattr(self, name), name)
        if self.role not in {"parent", "worker"}:
            raise DynamicMasContractError("role must be parent or worker")
        if self.status not in {"registered", "active", "completed", "failed", "cancelled"}:
            raise DynamicMasContractError("unsupported session status")
        if self.role == "worker":
            exact_id(self.workstream_id, "workstream_id")
        elif self.workstream_id:
            raise DynamicMasContractError("parent must not claim a workstream")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class HeldAction:
    action_id: str
    tool_call_id: str
    tool_name: str
    action_fingerprint: str
    params_sha256: str
    params_preview: str
    artifact_paths: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        exact_id(self.action_id, "action_id")
        exact_id(self.tool_call_id, "tool_call_id")
        exact_id(self.tool_name, "tool_name")
        exact_sha256(self.action_fingerprint, "action_fingerprint")
        exact_sha256(self.params_sha256, "params_sha256")
        bounded_text(self.params_preview, "params_preview", MAX_PREVIEW_CHARS, allow_empty=True)
        if len(self.artifact_paths) > 24:
            raise DynamicMasContractError("too many artifact paths")
        for path in self.artifact_paths:
            if not isinstance(path, str) or not path.startswith("results/") or ".." in path.split("/"):
                raise DynamicMasContractError("artifact path leaves results/")

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "HeldAction":
        return cls(
            action_id=str(value.get("action_id", "")),
            tool_call_id=str(value.get("tool_call_id", "")),
            tool_name=str(value.get("tool_name", "")),
            action_fingerprint=str(value.get("action_fingerprint", "")),
            params_sha256=str(value.get("params_sha256", "")),
            params_preview=bounded_text(
                value.get("params_preview", ""),
                "params_preview",
                MAX_PREVIEW_CHARS,
                allow_empty=True,
            ),
            artifact_paths=tuple(str(item) for item in value.get("artifact_paths", ())),
        )

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["artifact_paths"] = list(self.artifact_paths)
        return value


@dataclass(frozen=True)
class ReviewRequest:
    project_id: str
    run_id: str
    session_id: str
    turn_id: str
    batch_id: str
    review_id: str
    control_epoch: int
    nonce: str
    expected_event_seq: int
    actions: tuple[HeldAction, ...]

    def __post_init__(self) -> None:
        for name in (
            "project_id",
            "run_id",
            "session_id",
            "turn_id",
            "batch_id",
            "review_id",
        ):
            exact_id(getattr(self, name), name)
        if self.control_epoch < 0 or self.expected_event_seq < 1:
            raise DynamicMasContractError("review generation is malformed")
        exact_nonce(self.nonce)
        if not self.actions or len(self.actions) > MAX_HELD_ACTIONS:
            raise DynamicMasContractError("held action batch has invalid cardinality")
        action_ids = [action.action_id for action in self.actions]
        if len(action_ids) != len(set(action_ids)):
            raise DynamicMasContractError("held action id was duplicated")

    @classmethod
    def from_event(cls, value: Mapping[str, Any]) -> "ReviewRequest":
        payload = value.get("payload")
        if not isinstance(payload, Mapping):
            raise DynamicMasContractError("review event lacks payload")
        if value.get("type") != "jarvis.review.requested":
            raise DynamicMasContractError("event is not a review request")
        actions = payload.get("held_actions")
        if not isinstance(actions, Sequence) or isinstance(actions, (str, bytes)):
            raise DynamicMasContractError("held_actions must be a list")
        return cls(
            project_id=str(payload.get("project_id", "")),
            run_id=str(payload.get("run_id", "")),
            session_id=str(payload.get("session_id", "")),
            turn_id=str(payload.get("turn_id", "")),
            batch_id=str(payload.get("batch_id", "")),
            review_id=str(payload.get("review_id", "")),
            control_epoch=int(payload.get("control_epoch", -1)),
            nonce=str(payload.get("nonce", "")),
            expected_event_seq=int(payload.get("expected_event_seq", 0)),
            actions=tuple(HeldAction.from_mapping(item) for item in actions if isinstance(item, Mapping)),
        )

    @property
    def action_ids(self) -> tuple[str, ...]:
        return tuple(action.action_id for action in self.actions)

    @property
    def action_fingerprints(self) -> tuple[str, ...]:
        return tuple(action.action_fingerprint for action in self.actions)

    @property
    def sha256(self) -> str:
        return hashlib.sha256(canonical_json(self.to_dict())).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "run_id": self.run_id,
            "session_id": self.session_id,
            "turn_id": self.turn_id,
            "batch_id": self.batch_id,
            "review_id": self.review_id,
            "control_epoch": self.control_epoch,
            "nonce": self.nonce,
            "expected_event_seq": self.expected_event_seq,
            "held_actions": [action.to_dict() for action in self.actions],
        }
