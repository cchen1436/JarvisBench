from __future__ import annotations

import copy
import fcntl
import hashlib
import json
import os
import secrets
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .contracts import (
    DynamicMasContractError,
    MAX_GUIDANCE_CHARS,
    ReviewRequest,
    SessionBinding,
    StaleReviewError,
    bounded_text,
    canonical_json,
    exact_id,
    exact_nonce,
    exact_sha256,
    sha256_text,
    utc_now,
)


MAX_CONTROL_STATE_BYTES = 512 * 1024
MAX_HISTORY = 64


def session_namespace(session_id: str) -> str:
    return hashlib.sha256(exact_id(session_id, "session_id").encode("utf-8")).hexdigest()


class SessionControlPlane:
    """Deterministic mutable control state, strictly namespaced per session.

    OpenClaw reads these files through the supervisor plugin.  Only this host
    controller writes them.  Every release/interrupt binds an exact
    ``(session, epoch, nonce, review, batch, action fingerprints)`` tuple.
    """

    def __init__(self, root: Path, *, project_id: str) -> None:
        self.root = Path(root)
        self.project_id = exact_id(project_id, "project_id")

    def namespace_path(self, session_id: str) -> Path:
        return self.root / "sessions" / session_namespace(session_id)

    def control_path(self, session_id: str) -> Path:
        return self.namespace_path(session_id) / "control.json"

    def _lock_path(self, session_id: str) -> Path:
        return self.namespace_path(session_id) / ".control.lock"

    @contextmanager
    def _locked(self, session_id: str) -> Iterator[None]:
        namespace = self.namespace_path(session_id)
        namespace.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(self._lock_path(session_id), os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    @staticmethod
    def _nonce() -> str:
        return secrets.token_hex(24)

    def _empty(self, binding: SessionBinding) -> dict[str, Any]:
        return {
            "schema_version": "1.0",
            "kind": "dynamic_session_control",
            "protocol_version": "1.0-release",
            "revision": 0,
            "project_id": binding.project_id,
            "agent_id": binding.agent_id,
            "session_id": binding.session_id,
            "session_key": binding.session_key,
            "parent_id": binding.parent_id,
            "role": binding.role,
            "control_epoch": 0,
            "nonce": self._nonce(),
            "pause": {"active": False, "reason": "", "source": ""},
            "active_review": None,
            "review_responses": [],
            "invalidations": [],
            "guidance_queue": [],
            "delivery_receipts": [],
            "application_receipts": [],
            "updated_at": utc_now(),
        }

    def _read_unlocked(self, session_id: str) -> dict[str, Any]:
        path = self.control_path(session_id)
        if path.is_symlink() or not path.is_file():
            raise DynamicMasContractError("session control state is not registered")
        if path.stat().st_size > MAX_CONTROL_STATE_BYTES:
            raise DynamicMasContractError("session control state exceeds its bound")
        value = json.loads(path.read_text(encoding="utf-8"))
        self._validate_state(value, session_id)
        return value

    def _write_unlocked(self, state: dict[str, Any]) -> None:
        state["revision"] = int(state["revision"]) + 1
        state["updated_at"] = utc_now()
        self._validate_state(state, str(state["session_id"]))
        encoded = canonical_json(state) + b"\n"
        if len(encoded) > MAX_CONTROL_STATE_BYTES:
            raise DynamicMasContractError("session control state exceeds its bound")
        path = self.control_path(str(state["session_id"]))
        temporary = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(4)}")
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            os.write(fd, encoded)
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)

    def _validate_state(self, value: Any, session_id: str) -> None:
        if not isinstance(value, dict):
            raise DynamicMasContractError("control state is not an object")
        if (
            value.get("schema_version") != "1.0"
            or value.get("kind") != "dynamic_session_control"
            or value.get("protocol_version") != "1.0-release"
            or value.get("project_id") != self.project_id
            or value.get("session_id") != session_id
        ):
            raise DynamicMasContractError("control state identity is stale")
        if type(value.get("control_epoch")) is not int or value["control_epoch"] < 0:
            raise DynamicMasContractError("control epoch is malformed")
        exact_nonce(value.get("nonce"))
        pause = value.get("pause")
        if not isinstance(pause, dict) or not isinstance(pause.get("active"), bool):
            raise DynamicMasContractError("pause state is malformed")
        for name in (
            "review_responses",
            "invalidations",
            "guidance_queue",
            "delivery_receipts",
            "application_receipts",
        ):
            if not isinstance(value.get(name), list) or len(value[name]) > MAX_HISTORY:
                raise DynamicMasContractError(f"{name} exceeds its bound")

    def register(self, binding: SessionBinding) -> dict[str, Any]:
        if binding.project_id != self.project_id:
            raise DynamicMasContractError("session crossed project namespace")
        with self._locked(binding.session_id):
            path = self.control_path(binding.session_id)
            if path.exists():
                state = self._read_unlocked(binding.session_id)
                expected = {
                    "project_id": binding.project_id,
                    "agent_id": binding.agent_id,
                    "session_id": binding.session_id,
                    "session_key": binding.session_key,
                    "parent_id": binding.parent_id,
                    "role": binding.role,
                }
                if any(state.get(name) != expected_value for name, expected_value in expected.items()):
                    raise DynamicMasContractError("session id was rebound")
                return copy.deepcopy(state)
            state = self._empty(binding)
            self._write_unlocked(state)
            return copy.deepcopy(state)

    def read(self, session_id: str) -> dict[str, Any]:
        exact = exact_id(session_id, "session_id")
        with self._locked(exact):
            return copy.deepcopy(self._read_unlocked(exact))

    @staticmethod
    def _review_dict(review: ReviewRequest) -> dict[str, Any]:
        return {
            **review.to_dict(),
            "review_sha256": review.sha256,
        }

    @staticmethod
    def _assert_review(state: dict[str, Any], review: ReviewRequest) -> None:
        if (
            state["session_id"] != review.session_id
            or state["project_id"] != review.project_id
            or state["control_epoch"] != review.control_epoch
            or state["nonce"] != review.nonce
        ):
            raise StaleReviewError("review epoch, nonce, or session is stale")
        active = state.get("active_review")
        if not isinstance(active, dict) or active.get("review_sha256") != review.sha256:
            raise StaleReviewError("review is no longer the exact held action batch")

    def hold(self, review: ReviewRequest) -> dict[str, Any]:
        with self._locked(review.session_id):
            state = self._read_unlocked(review.session_id)
            if state["control_epoch"] != review.control_epoch or state["nonce"] != review.nonce:
                raise StaleReviewError("review arrived for an old generation")
            active = state.get("active_review")
            if active is not None:
                if active.get("review_sha256") == review.sha256:
                    return copy.deepcopy(state)
                raise DynamicMasContractError("session already holds another action batch")
            state["active_review"] = self._review_dict(review)
            state["pause"] = {
                "active": True,
                "reason": "consequential_action_review",
                "source": "dynamic_mas_split",
            }
            self._write_unlocked(state)
            return copy.deepcopy(state)

    @staticmethod
    def _response(
        review: ReviewRequest,
        *,
        decision: str,
        next_epoch: int,
        next_nonce: str,
        decision_id: str = "",
        guidance: str = "",
        delivery_receipt_id: str = "",
    ) -> dict[str, Any]:
        return {
            "schema_version": "1.0",
            "kind": "review_response",
            "control_id": f"control-{secrets.token_hex(12)}",
            "project_id": review.project_id,
            "run_id": review.run_id,
            "session_id": review.session_id,
            "turn_id": review.turn_id,
            "review_id": review.review_id,
            "batch_id": review.batch_id,
            "action_ids": list(review.action_ids),
            "action_fingerprints": list(review.action_fingerprints),
            "control_epoch": review.control_epoch,
            "next_control_epoch": next_epoch,
            "nonce": review.nonce,
            "next_nonce": next_nonce,
            "expected_event_seq": review.expected_event_seq,
            "decision": decision,
            "decision_id": decision_id,
            "guidance": guidance,
            "guidance_sha256": sha256_text(guidance) if guidance else "",
            "delivery_receipt_id": delivery_receipt_id,
            "created_at": utc_now(),
        }

    def allow(self, review: ReviewRequest) -> dict[str, Any]:
        with self._locked(review.session_id):
            state = self._read_unlocked(review.session_id)
            self._assert_review(state, review)
            response = self._response(
                review,
                decision="allow",
                next_epoch=review.control_epoch,
                next_nonce=review.nonce,
            )
            state["review_responses"].append(response)
            state["review_responses"] = state["review_responses"][-MAX_HISTORY:]
            state["active_review"] = None
            state["pause"] = {"active": False, "reason": "", "source": "dynamic_mas_split"}
            self._write_unlocked(state)
            return copy.deepcopy(response)

    def interrupt(self, review: ReviewRequest, *, decision_id: str, guidance: str) -> dict[str, Any]:
        exact_decision = exact_id(decision_id, "decision_id")
        clean_guidance = bounded_text(guidance, "guidance", MAX_GUIDANCE_CHARS)
        with self._locked(review.session_id):
            state = self._read_unlocked(review.session_id)
            self._assert_review(state, review)
            next_nonce = self._nonce()
            next_epoch = review.control_epoch + 1
            state["control_epoch"] = next_epoch
            state["nonce"] = next_nonce
            delivery = self._queue_delivery(
                state,
                decision_id=exact_decision,
                guidance=clean_guidance,
                route="next_model_boundary",
                scope="worker",
            )
            response = self._response(
                review,
                decision="interrupt_replan",
                next_epoch=next_epoch,
                next_nonce=next_nonce,
                decision_id=exact_decision,
                guidance=clean_guidance,
                delivery_receipt_id=str(delivery["receipt_id"]),
            )
            invalidation = {
                "schema_version": "1.0",
                "kind": "action_invalidation",
                "receipt_id": f"invalidate-{secrets.token_hex(12)}",
                "decision_id": exact_decision,
                "session_id": review.session_id,
                "review_id": review.review_id,
                "batch_id": review.batch_id,
                "action_ids": list(review.action_ids),
                "action_fingerprints": list(review.action_fingerprints),
                "prior_epoch": review.control_epoch,
                "new_epoch": next_epoch,
                "prior_nonce_sha256": sha256_text(review.nonce),
                "new_nonce": next_nonce,
                "delivery_receipt_id": delivery["receipt_id"],
                "guidance_sha256": delivery["guidance_sha256"],
                "created_at": utc_now(),
            }
            state["review_responses"].append(response)
            state["invalidations"].append(invalidation)
            state["review_responses"] = state["review_responses"][-MAX_HISTORY:]
            state["invalidations"] = state["invalidations"][-MAX_HISTORY:]
            state["active_review"] = None
            state["pause"] = {"active": False, "reason": "", "source": "dynamic_mas_split"}
            self._write_unlocked(state)
            return copy.deepcopy(invalidation)

    @staticmethod
    def _queue_delivery(
        state: dict[str, Any],
        *,
        decision_id: str,
        guidance: str,
        route: str,
        scope: str,
    ) -> dict[str, Any]:
        guidance_sha = sha256_text(guidance)
        seed = "\0".join(
            (
                str(state["project_id"]),
                str(state["session_id"]),
                decision_id,
                str(state["control_epoch"]),
                str(state["nonce"]),
                route,
                guidance_sha,
            )
        )
        receipt_id = f"delivery-{sha256_text(seed)[:24]}"
        existing = [item for item in state["delivery_receipts"] if item["receipt_id"] == receipt_id]
        if existing:
            return existing[0]
        receipt = {
            "schema_version": "1.0",
            "kind": "guidance_delivery",
            "receipt_id": receipt_id,
            "decision_id": decision_id,
            "project_id": state["project_id"],
            "target_agent_id": state["agent_id"],
            "target_session_id": state["session_id"],
            "route": route,
            "scope": scope,
            "control_epoch": state["control_epoch"],
            "nonce": state["nonce"],
            "guidance_sha256": guidance_sha,
            "status": "delivered",
            "created_at": utc_now(),
        }
        state["delivery_receipts"].append(receipt)
        state["guidance_queue"].append(
            {
                "receipt_id": receipt_id,
                "decision_id": decision_id,
                "text": guidance,
                "guidance_sha256": guidance_sha,
                "control_epoch": state["control_epoch"],
                "nonce": state["nonce"],
                "route": route,
                "scope": scope,
                "created_at": receipt["created_at"],
            }
        )
        state["delivery_receipts"] = state["delivery_receipts"][-MAX_HISTORY:]
        state["guidance_queue"] = state["guidance_queue"][-MAX_HISTORY:]
        return receipt

    def deliver(
        self,
        binding: SessionBinding,
        *,
        decision_id: str,
        guidance: str,
        route: str,
        scope: str,
    ) -> dict[str, Any]:
        exact_decision = exact_id(decision_id, "decision_id")
        clean_guidance = bounded_text(guidance, "guidance", MAX_GUIDANCE_CHARS)
        if route not in {"next_model_boundary", "parent_integration", "targeted_repair"}:
            raise DynamicMasContractError("unsupported delivery route")
        if scope not in {"worker", "project", "portfolio"}:
            raise DynamicMasContractError("unsupported delivery scope")
        with self._locked(binding.session_id):
            state = self._read_unlocked(binding.session_id)
            if state["agent_id"] != binding.agent_id or state["session_key"] != binding.session_key:
                raise DynamicMasContractError("delivery crossed session binding")
            receipt = self._queue_delivery(
                state,
                decision_id=exact_decision,
                guidance=clean_guidance,
                route=route,
                scope=scope,
            )
            self._write_unlocked(state)
            return copy.deepcopy(receipt)

    def mark_applied(
        self,
        *,
        session_id: str,
        receipt_id: str,
        model_boundary_id: str,
        control_epoch: int,
        nonce: str,
        guidance_sha256: str,
    ) -> dict[str, Any]:
        exact_receipt = exact_id(receipt_id, "receipt_id")
        exact_boundary = exact_id(model_boundary_id, "model_boundary_id")
        exact_nonce(nonce)
        exact_sha256(guidance_sha256, "guidance_sha256")
        with self._locked(session_id):
            state = self._read_unlocked(session_id)
            if state["control_epoch"] != control_epoch or state["nonce"] != nonce:
                raise StaleReviewError("application generation is stale")
            matches = [item for item in state["delivery_receipts"] if item["receipt_id"] == exact_receipt]
            if len(matches) != 1:
                raise StaleReviewError("delivery receipt is missing or ambiguous")
            delivery = matches[0]
            if delivery["guidance_sha256"] != guidance_sha256:
                raise StaleReviewError("application guidance hash is stale")
            prior = [item for item in state["application_receipts"] if item["delivery_receipt_id"] == exact_receipt]
            if prior:
                if len(prior) != 1 or prior[0]["model_boundary_id"] != exact_boundary:
                    raise StaleReviewError("delivery was applied at another boundary")
                return copy.deepcopy(prior[0])
            receipt = {
                "schema_version": "1.0",
                "kind": "guidance_application",
                "receipt_id": f"application-{secrets.token_hex(12)}",
                "delivery_receipt_id": exact_receipt,
                "decision_id": delivery["decision_id"],
                "project_id": state["project_id"],
                "target_agent_id": state["agent_id"],
                "target_session_id": state["session_id"],
                "model_boundary_id": exact_boundary,
                "control_epoch": control_epoch,
                "nonce_sha256": sha256_text(nonce),
                "guidance_sha256": guidance_sha256,
                "created_at": utc_now(),
            }
            delivery["status"] = "applied"
            state["application_receipts"].append(receipt)
            state["application_receipts"] = state["application_receipts"][-MAX_HISTORY:]
            state["guidance_queue"] = [
                item for item in state["guidance_queue"] if item["receipt_id"] != exact_receipt
            ]
            self._write_unlocked(state)
            return copy.deepcopy(receipt)

    def pause(self, session_id: str, *, expected_epoch: int, expected_nonce: str, reason: str) -> None:
        clean_reason = bounded_text(reason, "pause reason", 300)
        with self._locked(session_id):
            state = self._read_unlocked(session_id)
            if state["control_epoch"] != expected_epoch or state["nonce"] != expected_nonce:
                raise StaleReviewError("pause generation is stale")
            state["pause"] = {"active": True, "reason": clean_reason, "source": "dynamic_mas_split"}
            self._write_unlocked(state)

    def resume(self, session_id: str, *, expected_epoch: int, expected_nonce: str) -> None:
        with self._locked(session_id):
            state = self._read_unlocked(session_id)
            if state["control_epoch"] != expected_epoch or state["nonce"] != expected_nonce:
                raise StaleReviewError("resume generation is stale")
            state["control_epoch"] += 1
            state["nonce"] = self._nonce()
            state["pause"] = {"active": False, "reason": "", "source": "dynamic_mas_split"}
            state["active_review"] = None
            self._write_unlocked(state)
