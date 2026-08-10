from __future__ import annotations

import hashlib
import json
import os
import secrets
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


def action_fingerprint(action: str) -> str:
    return hashlib.sha256(action.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class HeldAction:
    session_id: str
    epoch: int
    nonce: str
    action_id: str
    fingerprint: str
    status: str = "held"
    decision_id: str | None = None


class ControlStore:
    """Deterministic per-session state.

    Each session has its own epoch and atomic state file. A state transition must
    acknowledge the exact session, epoch, nonce, action id, and fingerprint.
    """

    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, session_id: str) -> Path:
        if not session_id or "/" in session_id or ".." in session_id:
            raise ValueError("unsafe session_id")
        return self.root / f"{session_id}.json"

    def _read(self, session_id: str) -> dict[str, Any]:
        path = self._path(session_id)
        if not path.exists():
            return {"session_id": session_id, "epoch": 0, "actions": {}}
        return json.loads(path.read_text(encoding="utf-8"))

    def _write(self, session_id: str, state: dict[str, Any]) -> None:
        path = self._path(session_id)
        temp = path.with_suffix(f".tmp.{os.getpid()}.{secrets.token_hex(4)}")
        temp.write_text(json.dumps(state, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        os.chmod(temp, 0o600)
        os.replace(temp, path)

    def hold(self, session_id: str, action_id: str, action: str) -> HeldAction:
        state = self._read(session_id)
        state["epoch"] += 1
        held = HeldAction(
            session_id=session_id,
            epoch=state["epoch"],
            nonce=secrets.token_hex(16),
            action_id=action_id,
            fingerprint=action_fingerprint(action),
        )
        # New epoch invalidates all earlier held siblings in this session only.
        for existing in state["actions"].values():
            if existing.get("status") == "held":
                existing["status"] = "invalidated"
        state["actions"][action_id] = asdict(held)
        self._write(session_id, state)
        return held

    def transition(
        self,
        held: HeldAction,
        *,
        status: str,
        decision_id: str | None = None,
    ) -> HeldAction:
        if status not in {"delivered", "applied", "invalidated"}:
            raise ValueError("invalid transition")
        state = self._read(held.session_id)
        current = state["actions"].get(held.action_id)
        identity = ("session_id", "epoch", "nonce", "action_id", "fingerprint")
        if current is None or any(current.get(key) != getattr(held, key) for key in identity):
            raise RuntimeError("stale or mismatched held action")
        allowed = {
            "held": {"delivered", "invalidated"},
            "delivered": {"applied", "invalidated"},
        }
        if status not in allowed.get(current["status"], set()):
            raise RuntimeError(f"illegal transition {current['status']} -> {status}")
        current["status"] = status
        if decision_id is not None:
            current["decision_id"] = decision_id
        self._write(held.session_id, state)
        return HeldAction(**current)

    def snapshot(self, session_id: str) -> dict[str, Any]:
        return self._read(session_id)

