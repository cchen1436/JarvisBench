from __future__ import annotations

import json
import os
import secrets
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class DecisionRecord:
    decision_id: str
    answer: str
    scope: str
    affected_workers: tuple[str, ...]
    affected_artifacts: tuple[str, ...] = ()
    provenance: str = "requester"
    validity: str = "episode"
    reversible: bool = True
    delivery_receipts: tuple[str, ...] = field(default_factory=tuple)
    application_receipts: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class DecisionApplicationRecord:
    """Append-only proof that one delivered decision reached a model context."""

    decision_id: str
    delivery_receipt_id: str
    application_receipt_id: str
    target_session_id: str
    model_boundary_id: str
    control_epoch: int
    guidance_sha256: str
    kind: str = "guidance_application"


@dataclass(frozen=True)
class DecisionDeliveryRecord:
    """Append-only link from one decision to its exact control receipt."""

    decision_id: str
    delivery_receipt_id: str
    invalidation_receipt_id: str
    target_session_id: str
    review_id: str
    control_epoch: int
    guidance_sha256: str
    kind: str = "guidance_delivery"


class DecisionLedger:
    def __init__(self, path: Path):
        self.path = Path(path)

    def append(self, record: DecisionRecord) -> None:
        if record.scope not in {"worker", "project", "portfolio"}:
            raise ValueError("invalid scope")
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        line = json.dumps(asdict(record), sort_keys=True)
        flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT
        fd = os.open(self.path, flags, 0o600)
        try:
            os.write(fd, (line + "\n").encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)

    def append_application(self, record: DecisionApplicationRecord) -> None:
        self._append_record(record)

    def append_delivery(self, record: DecisionDeliveryRecord) -> None:
        self._append_record(record)

    def _append_record(
        self, record: DecisionApplicationRecord | DecisionDeliveryRecord
    ) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        line = json.dumps(asdict(record), sort_keys=True)
        fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.write(fd, (line + "\n").encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)

    @staticmethod
    def new_id() -> str:
        return f"decision-{secrets.token_hex(12)}"
