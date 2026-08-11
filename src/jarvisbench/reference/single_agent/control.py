"""Topology-neutral one-worker bridge for the shared OpenClaw control protocol."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import threading
from pathlib import Path
from typing import Any, Callable, Mapping

from jarvisbench.core.contracts import BoundaryCandidate
from jarvisbench.core.decision_ledger import (
    DecisionApplicationRecord,
    DecisionDeliveryRecord,
    DecisionLedger,
)
from jarvisbench.reference.dynamic_mas.contracts import (
    MAX_EVENT_LINE_BYTES,
    DynamicMasContractError,
    ReviewRequest,
    SessionBinding,
    bounded_text,
    canonical_json,
)
from jarvisbench.reference.dynamic_mas.control_plane import SessionControlPlane
from jarvisbench.reference.dynamic_mas.reducer import LiveChildReducer
from jarvisbench.settings.single_agent_runtime import ControllerReview


class SingleAgentControlService:
    """Tail bounded events and control exactly one registered execution node.

    There is intentionally no Parent or manager session. ``parent_id`` in the
    shared event contract names the project graph root only; it is not a second
    logical agent and is never registered or executed.
    """

    def __init__(
        self,
        *,
        project_id: str,
        session_id: str,
        session_key: str,
        control_root: Path,
        events_path: Path,
        registry_path: Path,
        diagnostics_path: Path,
        decision_ledger: DecisionLedger,
        review: Callable[[BoundaryCandidate], ControllerReview],
        on_attention_committed: (
            Callable[[BoundaryCandidate, ControllerReview], None] | None
        ) = None,
        task_brief: str = "",
        required_result_paths: tuple[str, ...] = (),
        poll_seconds: float = 0.1,
    ) -> None:
        self.project_id = project_id
        self.session_id = session_id
        self.session_key = session_key
        self.control_plane = SessionControlPlane(control_root, project_id=project_id)
        self.events_path = Path(events_path)
        self.registry_path = Path(registry_path)
        self.diagnostics_path = Path(diagnostics_path)
        self.decision_ledger = decision_ledger
        self.review_callback = review
        self.on_attention_committed = on_attention_committed
        self.task_brief = bounded_text(
            task_brief, "task_brief", 4_000, allow_empty=True
        )
        self.required_result_paths = tuple(required_result_paths)
        self.poll_seconds = max(0.025, float(poll_seconds))
        self.reducer = LiveChildReducer(project_id)
        self.binding = SessionBinding(
            project_id=project_id,
            agent_id="worker-0",
            session_id=session_id,
            session_key=session_key,
            parent_id="project-root",
            parent_session_id="no-parent",
            parent_session_key="no-parent",
            role="worker",
            workstream_id="single-worker",
            status="active",
        )
        self._offset = 0
        self._remainder = b""
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._errors: list[str] = []
        self._processed_reviews: dict[str, str] = {}
        self._logged_applications: set[str] = set()
        self._event_count = 0

    def prepare(self) -> None:
        self.control_plane.register(self.binding)
        self.reducer.register(self.binding)
        snapshot = {
            "schema_version": "1.0",
            "kind": "dynamic_child_registry",
            "project_id": self.project_id,
            "parent_agent_id": "",
            "parent_session_id": "no-parent",
            "parent_session_key": "no-parent",
            "sessions": {self.session_id: self.binding.to_dict()},
            "aliases": {
                self.session_id: self.session_id,
                self.session_key: self.session_id,
            },
        }
        self.registry_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = self.registry_path.with_name(
            f".{self.registry_path.name}.{os.getpid()}.{secrets.token_hex(4)}"
        )
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            os.write(descriptor, canonical_json(snapshot) + b"\n")
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.replace(temporary, self.registry_path)
        self.events_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.events_path.touch(mode=0o600, exist_ok=True)

    def _candidate(
        self, review: ReviewRequest, reducer: LiveChildReducer
    ) -> BoundaryCandidate:
        update = reducer.reduced_update(review.session_id)
        fingerprint = hashlib.sha256(
            "\0".join(review.action_fingerprints).encode("utf-8")
        ).hexdigest()
        return BoundaryCandidate(
            session_id=review.session_id,
            epoch=review.control_epoch,
            nonce=review.nonce,
            action_id=review.actions[0].action_id,
            action_fingerprint=fingerprint,
            reduced_update=update,
            consequence=bounded_text(
                update.proposed_action
                or "Execute the exact held consequential action batch.",
                "consequence",
                1_600,
            ),
            artifact_paths=tuple(
                sorted({path for action in review.actions for path in action.artifact_paths})
            ),
            final_record_intent=any(
                action.final_record_intent for action in review.actions
            ),
            preview_truncated=any(action.params_truncated for action in review.actions),
            task_brief=self.task_brief,
            required_result_paths=self.required_result_paths,
            review_id=review.review_id,
            batch_id=review.batch_id,
            external_irreversible_effect=next(
                (
                    action.external_irreversible_effect
                    for action in review.actions
                    if action.external_irreversible_effect
                ),
                "",
            ),
        )

    @staticmethod
    def _assert_exact_review(
        candidate: BoundaryCandidate, response: ControllerReview
    ) -> None:
        exact = (
            response.session_id,
            response.epoch,
            response.nonce,
            response.action_id,
            response.action_fingerprint,
        )
        expected = (
            candidate.session_id,
            candidate.epoch,
            candidate.nonce,
            candidate.action_id,
            candidate.action_fingerprint,
        )
        if exact != expected:
            raise DynamicMasContractError("controller response changed held-action identity")

    def _mark_applied(self, payload: Mapping[str, Any]) -> None:
        receipt = self.control_plane.mark_applied(
            session_id=str(payload.get("session_id", "")),
            receipt_id=str(payload.get("delivery_receipt_id", "")),
            model_boundary_id=str(payload.get("model_boundary_id", "")),
            control_epoch=int(payload.get("control_epoch", -1)),
            nonce=str(payload.get("nonce", "")),
            guidance_sha256=str(payload.get("guidance_sha256", "")),
        )
        application_id = str(receipt["receipt_id"])
        if application_id in self._logged_applications:
            return
        self.decision_ledger.append_application(
            DecisionApplicationRecord(
                decision_id=str(receipt["decision_id"]),
                delivery_receipt_id=str(receipt["delivery_receipt_id"]),
                application_receipt_id=application_id,
                target_session_id=str(receipt["target_session_id"]),
                model_boundary_id=str(receipt["model_boundary_id"]),
                control_epoch=int(receipt["control_epoch"]),
                guidance_sha256=str(receipt["guidance_sha256"]),
            )
        )
        self._logged_applications.add(application_id)

    def process_event(self, event: Mapping[str, Any]) -> None:
        payload = event.get("payload")
        if not isinstance(payload, Mapping) or payload.get("project_id") != self.project_id:
            return
        if str(payload.get("session_id", "")) != self.session_id:
            return
        if event.get("type") == "control.guidance.applied":
            self._mark_applied(payload)
            return
        card = self.reducer.observe(event)
        if event.get("type") != "jarvis.review.requested":
            return
        review = ReviewRequest.from_event(event)
        prior = self._processed_reviews.get(review.review_id)
        if prior is not None:
            return
        if card is None:
            raise DynamicMasContractError("review lacks a bounded reduced card")
        self.control_plane.hold(review)
        candidate = self._candidate(review, self.reducer)
        try:
            response = self.review_callback(candidate)
            self._assert_exact_review(candidate, response)
            if response.decision.request_attention:
                if not response.decision_id or not response.guidance:
                    raise DynamicMasContractError(
                        "attention response lacks requester guidance"
                    )
                invalidation = self.control_plane.interrupt(
                    review,
                    decision_id=response.decision_id,
                    guidance=response.guidance,
                )
                self.decision_ledger.append_delivery(
                    DecisionDeliveryRecord(
                        decision_id=response.decision_id,
                        delivery_receipt_id=str(
                            invalidation["delivery_receipt_id"]
                        ),
                        invalidation_receipt_id=str(invalidation["receipt_id"]),
                        target_session_id=review.session_id,
                        review_id=review.review_id,
                        control_epoch=int(invalidation["new_epoch"]),
                        guidance_sha256=str(invalidation["guidance_sha256"]),
                    )
                )
                if self.on_attention_committed is not None:
                    try:
                        self.on_attention_committed(candidate, response)
                    except Exception:
                        # This is a bounded controller cache update. The exact
                        # interrupt and delivery receipt above remain authority.
                        pass
                self._processed_reviews[review.review_id] = "interrupt_replan"
            else:
                self.control_plane.allow(review)
                self._processed_reviews[review.review_id] = "allow"
        except Exception:
            # Preserve the original worker action if controller/requester logic
            # fails before a generation-changing interrupt is committed.
            state = self.control_plane.read(review.session_id)
            if (
                state["control_epoch"] == review.control_epoch
                and state["nonce"] == review.nonce
                and isinstance(state.get("active_review"), dict)
            ):
                self.control_plane.allow(review)
                self._processed_reviews[review.review_id] = "allow_error"
            raise

    def _read_new_event_lines(self) -> list[bytes]:
        """Read complete lines while leaving the committed offset unchanged.

        The offset advances in ``poll_once`` only after ``process_event`` returns
        successfully.  A transient controller, ledger, or control-plane error
        therefore cannot discard this event or later events read in the chunk.
        """

        if self.events_path.is_symlink() or not self.events_path.is_file():
            raise DynamicMasContractError("single-agent event bus is unsafe")
        size = self.events_path.stat().st_size
        if size < self._offset:
            raise DynamicMasContractError("append-only event bus was truncated")
        if size == self._offset:
            self._remainder = b""
            return []
        with self.events_path.open("rb") as stream:
            stream.seek(self._offset)
            chunk = stream.read()
        lines = chunk.split(b"\n")
        self._remainder = lines.pop()
        if len(self._remainder) > MAX_EVENT_LINE_BYTES:
            raise DynamicMasContractError("unterminated event exceeds its bound")
        return lines

    def poll_once(self) -> int:
        count = 0
        for line in self._read_new_event_lines():
            next_offset = self._offset + len(line) + 1
            if not line:
                self._offset = next_offset
                continue
            if len(line) > MAX_EVENT_LINE_BYTES:
                raise DynamicMasContractError("event exceeds its bound")
            event = json.loads(line)
            if not isinstance(event, dict):
                raise DynamicMasContractError("event is not an object")
            self.process_event(event)
            # Commit only after processing succeeds. A failure leaves this line
            # and all following lines available for a later retry.
            self._offset = next_offset
            self._event_count += 1
            count += 1
        return count

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("single-agent control service already started")
        self.prepare()
        self._thread = threading.Thread(
            target=self._run,
            name="jarvisbench-single-control",
            daemon=True,
        )
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.poll_once()
            except Exception as exc:
                self._errors.append(type(exc).__name__)
            self._stop.wait(self.poll_seconds)

    def stop(self, timeout: float = 10.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)
            if self._thread.is_alive():
                raise RuntimeError("single-agent control service did not stop")
        self.poll_once()
        diagnostics = {
            "schema_version": "1.0",
            "kind": "single_agent_control_diagnostics",
            "project_id": self.project_id,
            "execution_nodes": 1,
            "manager": None,
            "processed_reviews": len(self._processed_reviews),
            "event_count": self._event_count,
            "application_receipts": len(self._logged_applications),
            "service_errors": self._errors[-32:],
            "raw_trace_included": False,
        }
        self.diagnostics_path.parent.mkdir(parents=True, exist_ok=True)
        self.diagnostics_path.write_bytes(canonical_json(diagnostics) + b"\n")
