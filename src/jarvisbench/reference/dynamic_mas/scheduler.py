from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

from jarvisbench.core.contracts import BoundaryCandidate, ReducedUpdate
from jarvisbench.core.controller import AttentionController, AttentionDecision
from jarvisbench.core.decision_ledger import (
    DecisionApplicationRecord,
    DecisionDeliveryRecord,
    DecisionLedger,
    DecisionRecord,
)

from .contracts import ReviewRequest, SessionBinding, bounded_text
from .control_plane import SessionControlPlane
from .reducer import LiveChildReducer
from .registry import DynamicChildRegistry, TERMINAL_STATUSES


class RequesterChannel(Protocol):
    def answer(self, question: str, requester_context: str) -> str:
        ...


@dataclass(frozen=True)
class SchedulerOutcome:
    disposition: str
    session_id: str
    review_id: str
    decision_id: str = ""
    asked_user: bool = False
    routed_sessions: tuple[str, ...] = ()
    delivery_receipts: tuple[str, ...] = ()
    error_type: str = ""


class DynamicMasScheduler:
    """One project-level attention scheduler for all dynamically found children.

    The lock protects the single requester channel: at most one unresolved
    attention request can exist.  It does not pause unrelated sessions; each
    plugin call waits only on its own session-namespaced review response.
    """

    def __init__(
        self,
        *,
        project_id: str,
        controller: AttentionController,
        requester: RequesterChannel,
        requester_context_loader: Callable[[], str],
        registry: DynamicChildRegistry,
        control_plane: SessionControlPlane,
        decision_ledger: DecisionLedger,
        max_attention_requests: int = 2,
    ) -> None:
        if type(max_attention_requests) is not int or max_attention_requests < 0:
            raise ValueError("max_attention_requests must be a non-negative integer")
        self.project_id = project_id
        self.controller = controller
        self.requester = requester
        self.requester_context_loader = requester_context_loader
        self.registry = registry
        self.control_plane = control_plane
        self.decision_ledger = decision_ledger
        self.reducer = LiveChildReducer(project_id)
        self.max_attention_requests = max_attention_requests
        self._attention_lock = threading.Lock()
        self._attention_requests_used = 0
        self._attention_fingerprints: set[str] = set()
        self._duplicate_attention_suppressed = 0
        self._processed_reviews: dict[str, SchedulerOutcome] = {}
        self._logged_applications: set[str] = set()

    def register(self, binding: SessionBinding) -> None:
        self.registry.register(binding)
        self.control_plane.register(binding)
        self.reducer.register(binding)

    def sync_registry(self, *, sessions_root: Path, workspace: Path, registry_path: Path) -> None:
        snapshot = self.registry.sync(
            sessions_root=sessions_root,
            workspace=workspace,
            output=registry_path,
        )
        sessions = snapshot.get("sessions", {})
        if not isinstance(sessions, Mapping):
            return
        for raw in sessions.values():
            if not isinstance(raw, Mapping):
                continue
            binding = SessionBinding(**{name: raw[name] for name in SessionBinding.__dataclass_fields__})
            self.control_plane.register(binding)
            self.reducer.register(binding)

    @staticmethod
    def _candidate(review: ReviewRequest, update: ReducedUpdate) -> BoundaryCandidate:
        combined_fingerprint = hashlib.sha256(
            "\0".join(review.action_fingerprints).encode("utf-8")
        ).hexdigest()
        return BoundaryCandidate(
            session_id=review.session_id,
            epoch=review.control_epoch,
            nonce=review.nonce,
            action_id=review.actions[0].action_id,
            action_fingerprint=combined_fingerprint,
            reduced_update=update,
            consequence=bounded_text(
                update.proposed_action or "Execute the exact held consequential action batch.",
                "consequence",
                1_600,
            ),
        )

    def _allow(self, review: ReviewRequest, *, error: Exception | None = None) -> SchedulerOutcome:
        self.control_plane.allow(review)
        return SchedulerOutcome(
            disposition="allow",
            session_id=review.session_id,
            review_id=review.review_id,
            error_type=type(error).__name__ if error is not None else "",
        )

    def _route_answer(
        self,
        *,
        review: ReviewRequest,
        decision: AttentionDecision,
        answer: str,
    ) -> SchedulerOutcome:
        guidance = bounded_text(
            f"Requester answer for this decision: {answer}",
            "requester guidance",
            1_600,
        )
        decision_id = DecisionLedger.new_id()
        target = self.registry.resolve(review.session_id)
        receipts: list[str] = []
        routed: list[SessionBinding] = [target]
        if decision.scope == "project":
            routed.append(self.registry.parent())
        unique = {binding.session_id: binding for binding in routed}

        # Persist the requester decision before making it visible in mutable
        # control state. Exact delivery/application links are appended after
        # each deterministic control transaction.
        self.decision_ledger.append(
            DecisionRecord(
                decision_id=decision_id,
                answer=answer,
                scope=decision.scope,
                affected_workers=tuple(binding.agent_id for binding in unique.values()),
                affected_artifacts=tuple(
                    path for action in review.actions for path in action.artifact_paths
                ),
                provenance="luna_requester_channel",
                validity="project_episode",
                reversible=True,
            )
        )

        # The held action is always invalidated before guidance is exposed.
        invalidation = self.control_plane.interrupt(
            review,
            decision_id=decision_id,
            guidance=guidance,
        )
        receipts.append(str(invalidation["delivery_receipt_id"]))
        self.decision_ledger.append_delivery(
            DecisionDeliveryRecord(
                decision_id=decision_id,
                delivery_receipt_id=str(invalidation["delivery_receipt_id"]),
                invalidation_receipt_id=str(invalidation["receipt_id"]),
                target_session_id=review.session_id,
                review_id=review.review_id,
                control_epoch=int(invalidation["new_epoch"]),
                guidance_sha256=str(invalidation["guidance_sha256"]),
            )
        )

        # Project scope reaches Parent integration as the narrow deterministic
        # cross-workstream route.  It is not broadcast to unrelated children.
        if decision.scope == "project":
            parent = unique[self.registry.parent().session_id]
            receipt = self.control_plane.deliver(
                parent,
                decision_id=decision_id,
                guidance=guidance,
                route="parent_integration",
                scope="project",
            )
            receipts.append(str(receipt["receipt_id"]))
            self.decision_ledger.append_delivery(
                DecisionDeliveryRecord(
                    decision_id=decision_id,
                    delivery_receipt_id=str(receipt["receipt_id"]),
                    invalidation_receipt_id="",
                    target_session_id=parent.session_id,
                    review_id=review.review_id,
                    control_epoch=int(receipt["control_epoch"]),
                    guidance_sha256=str(receipt["guidance_sha256"]),
                ),
            )
        return SchedulerOutcome(
            disposition="interrupt_replan",
            session_id=review.session_id,
            review_id=review.review_id,
            decision_id=decision_id,
            asked_user=True,
            routed_sessions=tuple(unique),
            delivery_receipts=tuple(receipts),
        )

    @staticmethod
    def _attention_fingerprint(
        *, session_id: str, decision: AttentionDecision
    ) -> str:
        normalized_question = " ".join(str(decision.question or "").casefold().split())
        return hashlib.sha256(
            f"{session_id}\0{decision.scope}\0{normalized_question}".encode("utf-8")
        ).hexdigest()

    def process_event(self, event: Mapping[str, Any]) -> SchedulerOutcome | None:
        if event.get("type") == "control.guidance.applied":
            payload = event.get("payload")
            if not isinstance(payload, Mapping) or payload.get("project_id") != self.project_id:
                return None
            receipt = self.control_plane.mark_applied(
                session_id=str(payload.get("session_id", "")),
                receipt_id=str(payload.get("delivery_receipt_id", "")),
                model_boundary_id=str(payload.get("model_boundary_id", "")),
                control_epoch=int(payload.get("control_epoch", -1)),
                nonce=str(payload.get("nonce", "")),
                guidance_sha256=str(payload.get("guidance_sha256", "")),
            )
            application_id = str(receipt["receipt_id"])
            if application_id not in self._logged_applications:
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
            return None
        card = self.reducer.observe(event)
        if event.get("type") != "jarvis.review.requested":
            return None
        review = ReviewRequest.from_event(event)
        if review.project_id != self.project_id:
            return None
        prior = self._processed_reviews.get(review.review_id)
        if prior is not None:
            return prior
        binding = self.registry.resolve(review.session_id)
        if binding.status in TERMINAL_STATUSES:
            # A terminal child cannot execute the held proposal.  Do not make
            # up a resume; a later scoped decision is routed through Parent or
            # an explicit repair workstream by route_terminal_decision().
            return SchedulerOutcome(
                disposition="terminal_stale",
                session_id=review.session_id,
                review_id=review.review_id,
            )
        if card is None:
            return self._allow(review, error=RuntimeError("missing reduced card"))
        self.control_plane.hold(review)
        candidate = self._candidate(review, self.reducer.reduced_update(review.session_id))

        # A controller/provider error fails open to the original worker action.
        # This preserves the baseline behavior and never fabricates user input.
        try:
            decision = self.controller.decide(candidate)
        except Exception as exc:  # provider adapters deliberately share no exception type
            outcome = self._allow(review, error=exc)
            self._processed_reviews[review.review_id] = outcome
            return outcome
        if not decision.request_attention:
            outcome = self._allow(review)
            self._processed_reviews[review.review_id] = outcome
            return outcome

        with self._attention_lock:
            attention_fingerprint = self._attention_fingerprint(
                session_id=(
                    self.project_id if decision.scope == "project" else review.session_id
                ),
                decision=decision,
            )
            if attention_fingerprint in self._attention_fingerprints:
                self._duplicate_attention_suppressed += 1
                outcome = self._allow(review)
                outcome = SchedulerOutcome(
                    disposition="duplicate_attention_allow",
                    session_id=outcome.session_id,
                    review_id=outcome.review_id,
                )
                self._processed_reviews[review.review_id] = outcome
                return outcome
            if self._attention_requests_used >= self.max_attention_requests:
                outcome = self._allow(review)
                outcome = SchedulerOutcome(
                    disposition="attention_budget_allow",
                    session_id=outcome.session_id,
                    review_id=outcome.review_id,
                )
                self._processed_reviews[review.review_id] = outcome
                return outcome
            # Reserve before entering the requester channel. A failed requester
            # call still consumed scarce attention/capacity and must not permit
            # a concurrent review to exceed the project-level cap.
            self._attention_requests_used += 1
            try:
                # Private requester state is loaded lazily and goes only to the
                # requester channel, never to Jarvis's candidate packet.
                answer = self.requester.answer(
                    str(decision.question),
                    self.requester_context_loader(),
                )
                outcome = self._route_answer(
                    review=review,
                    decision=decision,
                    answer=answer,
                )
                self._attention_fingerprints.add(attention_fingerprint)
            except Exception as exc:
                state = self.control_plane.read(review.session_id)
                if (
                    state["control_epoch"] == review.control_epoch
                    and state["nonce"] == review.nonce
                    and isinstance(state.get("active_review"), dict)
                ):
                    outcome = self._allow(review, error=exc)
                else:
                    # Guidance/control was already committed. A stale allow
                    # would both fail and obscure the receipt gap; surface the
                    # transaction error so episode admission fails visibly.
                    raise
        self._processed_reviews[review.review_id] = outcome
        return outcome

    def route_terminal_decision(
        self,
        *,
        target_session_id: str,
        decision_id: str,
        guidance: str,
        scope: str,
    ) -> SchedulerOutcome:
        target = self.registry.resolve(target_session_id)
        if target.status not in TERMINAL_STATUSES:
            raise ValueError("terminal decision route requires a completed child")
        parent = self.registry.parent()
        # Only a worker-scoped correction needs an explicit targeted-repair
        # marker on the completed child. Project/portfolio guidance goes solely
        # to Parent integration: a terminal child can never acknowledge a
        # normal parent-integration delivery, so creating one there would be an
        # intrinsically unclosable receipt.
        receipts: list[str] = []
        routed_sessions: list[str] = []
        if scope == "worker":
            child_receipt = self.control_plane.deliver(
                target,
                decision_id=decision_id,
                guidance=guidance,
                route="targeted_repair",
                scope=scope,
            )
            self.decision_ledger.append_delivery(
                DecisionDeliveryRecord(
                    decision_id=decision_id,
                    delivery_receipt_id=str(child_receipt["receipt_id"]),
                    invalidation_receipt_id="",
                    target_session_id=target.session_id,
                    review_id="terminal-route",
                    control_epoch=int(child_receipt["control_epoch"]),
                    guidance_sha256=str(child_receipt["guidance_sha256"]),
                )
            )
            receipts.append(str(child_receipt["receipt_id"]))
            routed_sessions.append(target.session_id)
        # Parent always receives the integration fallback. Running a worker
        # repair is an explicit runner phase, never an implicit team restart.
        parent_receipt = self.control_plane.deliver(
            parent,
            decision_id=decision_id,
            guidance=guidance,
            route="parent_integration",
            scope=scope,
        )
        self.decision_ledger.append_delivery(
            DecisionDeliveryRecord(
                decision_id=decision_id,
                delivery_receipt_id=str(parent_receipt["receipt_id"]),
                invalidation_receipt_id="",
                target_session_id=parent.session_id,
                review_id="terminal-route",
                control_epoch=int(parent_receipt["control_epoch"]),
                guidance_sha256=str(parent_receipt["guidance_sha256"]),
            )
        )
        return SchedulerOutcome(
            disposition="targeted_repair_or_parent_integration",
            session_id=target_session_id,
            review_id="terminal-route",
            decision_id=decision_id,
            routed_sessions=tuple((*routed_sessions, parent.session_id)),
            delivery_receipts=tuple((*receipts, str(parent_receipt["receipt_id"]))),
        )

    def parent_integration_guidance(self) -> tuple[str, ...]:
        parent = self.registry.parent()
        state = self.control_plane.read(parent.session_id)
        return tuple(
            str(item["text"])
            for item in state["guidance_queue"]
            if item.get("route") == "parent_integration"
        )

    def evaluate_parent_final_gate(
        self,
        *,
        project_summary: str,
        evidence: tuple[str, ...],
        integration_prompt_sha256: str,
    ) -> SchedulerOutcome:
        """Hold Parent integration in runner code for one final attention gate.

        This is a final defense in addition to live child-level control.  It is
        not the archived pre-integration-only convergence ablation because the
        same scheduler has already consumed child events throughout execution.
        """

        parent = self.registry.parent()
        state = self.control_plane.read(parent.session_id)
        candidate = BoundaryCandidate(
            session_id=parent.session_id,
            epoch=int(state["control_epoch"]),
            nonce=str(state["nonce"]),
            action_id="parent-integration",
            action_fingerprint=integration_prompt_sha256,
            reduced_update=ReducedUpdate(
                progress=bounded_text(project_summary, "project summary", 2_000),
                current_goal="Integrate completed child artifacts into the final project deliverables.",
                evidence=tuple(item[:1_000] for item in evidence[:8]),
                uncertainty=None,
                proposed_action="Run the same Parent session's final integration turn.",
            ),
            consequence="Commit the integrated project artifacts.",
        )
        try:
            decision = self.controller.decide(candidate)
        except Exception as exc:
            return SchedulerOutcome(
                disposition="parent_gate_allow",
                session_id=parent.session_id,
                review_id="parent-final-gate",
                error_type=type(exc).__name__,
            )
        if not decision.request_attention:
            return SchedulerOutcome(
                disposition="parent_gate_allow",
                session_id=parent.session_id,
                review_id="parent-final-gate",
            )
        with self._attention_lock:
            attention_fingerprint = self._attention_fingerprint(
                session_id=self.project_id,
                decision=decision,
            )
            if attention_fingerprint in self._attention_fingerprints:
                self._duplicate_attention_suppressed += 1
                return SchedulerOutcome(
                    disposition="parent_gate_duplicate_allow",
                    session_id=parent.session_id,
                    review_id="parent-final-gate",
                )
            if self._attention_requests_used >= self.max_attention_requests:
                return SchedulerOutcome(
                    disposition="parent_gate_budget_allow",
                    session_id=parent.session_id,
                    review_id="parent-final-gate",
                )
            self._attention_requests_used += 1
            try:
                answer = self.requester.answer(
                    str(decision.question),
                    self.requester_context_loader(),
                )
                guidance = bounded_text(
                    f"Requester answer for final project integration: {answer}",
                    "parent guidance",
                    1_600,
                )
                decision_id = DecisionLedger.new_id()
                self.decision_ledger.append(
                    DecisionRecord(
                        decision_id=decision_id,
                        answer=answer,
                        scope="project",
                        affected_workers=(parent.agent_id,),
                        provenance="luna_requester_channel",
                        validity="project_episode",
                        reversible=True,
                    )
                )
                receipt = self.control_plane.deliver(
                    parent,
                    decision_id=decision_id,
                    guidance=guidance,
                    route="parent_integration",
                    scope="project",
                )
                self.decision_ledger.append_delivery(
                    DecisionDeliveryRecord(
                        decision_id=decision_id,
                        delivery_receipt_id=str(receipt["receipt_id"]),
                        invalidation_receipt_id="",
                        target_session_id=parent.session_id,
                        review_id="parent-final-gate",
                        control_epoch=int(receipt["control_epoch"]),
                        guidance_sha256=str(receipt["guidance_sha256"]),
                    )
                )
                self._attention_fingerprints.add(attention_fingerprint)
                return SchedulerOutcome(
                    disposition="parent_gate_guidance",
                    session_id=parent.session_id,
                    review_id="parent-final-gate",
                    decision_id=decision_id,
                    asked_user=True,
                    routed_sessions=(parent.session_id,),
                    delivery_receipts=(str(receipt["receipt_id"]),),
                )
            except Exception as exc:
                state = self.control_plane.read(parent.session_id)
                if any(
                    item.get("decision_id") == locals().get("decision_id", "")
                    for item in state.get("delivery_receipts", [])
                    if isinstance(item, Mapping)
                ):
                    raise
                return SchedulerOutcome(
                    disposition="parent_gate_allow",
                    session_id=parent.session_id,
                    review_id="parent-final-gate",
                    error_type=type(exc).__name__,
                )

    def diagnostics(self) -> dict[str, Any]:
        sessions = self.registry.snapshot()["sessions"]
        workers = [
            binding for binding in sessions.values() if binding["role"] == "worker"
        ]

        # Completion is admissible only when every deterministic delivery was
        # consumed at an exact model boundary. A completed child cannot consume
        # a targeted-repair delivery itself; in that one explicit case, an
        # applied Parent integration delivery for the same decision is the
        # closure receipt. This makes the fallback inspectable without
        # pretending that the terminal child resumed.
        deliveries: dict[str, tuple[Mapping[str, Any], Mapping[str, Any]]] = {}
        applications: dict[str, tuple[Mapping[str, Any], Mapping[str, Any]]] = {}
        duplicate_delivery_receipts = 0
        duplicate_application_receipts = 0
        for binding in sessions.values():
            state = self.control_plane.read(str(binding["session_id"]))
            for receipt in state.get("delivery_receipts", []):
                if not isinstance(receipt, Mapping):
                    duplicate_delivery_receipts += 1
                    continue
                receipt_id = str(receipt.get("receipt_id", ""))
                if not receipt_id or receipt_id in deliveries:
                    duplicate_delivery_receipts += 1
                    continue
                deliveries[receipt_id] = (binding, receipt)
            for receipt in state.get("application_receipts", []):
                if not isinstance(receipt, Mapping):
                    duplicate_application_receipts += 1
                    continue
                delivery_id = str(receipt.get("delivery_receipt_id", ""))
                if not delivery_id or delivery_id in applications:
                    duplicate_application_receipts += 1
                    continue
                applications[delivery_id] = (binding, receipt)

        parent_applied_decisions = {
            str(delivery.get("decision_id", ""))
            for delivery_id, (binding, delivery) in deliveries.items()
            if binding.get("role") == "parent"
            and delivery.get("route") == "parent_integration"
            and delivery_id in applications
        }
        fallback_delivery_ids = {
            delivery_id
            for delivery_id, (binding, delivery) in deliveries.items()
            if delivery_id not in applications
            and binding.get("role") == "worker"
            and binding.get("status") in TERMINAL_STATUSES
            and delivery.get("route") == "targeted_repair"
            and str(delivery.get("decision_id", "")) in parent_applied_decisions
        }
        unresolved_delivery_ids = sorted(
            set(deliveries) - set(applications) - fallback_delivery_ids
        )
        orphan_application_ids = sorted(set(applications) - set(deliveries))
        receipt_closure_valid = not (
            unresolved_delivery_ids
            or orphan_application_ids
            or duplicate_delivery_receipts
            or duplicate_application_receipts
        )
        return {
            "schema_version": "1.0",
            "kind": "dynamic_mas_diagnostics",
            "project_id": self.project_id,
            "registered_workers": len(workers),
            "live_updates_before_completion": sum(
                self.reducer.saw_live_update(str(binding["session_id"]))
                for binding in workers
            ),
            "processed_reviews": len(self._processed_reviews),
            "max_attention_requests": self.max_attention_requests,
            "attention_requests_used": self._attention_requests_used,
            "duplicate_attention_suppressed": self._duplicate_attention_suppressed,
            "attention_budget_enforced": True,
            "attention_channel_serialized": True,
            "delivery_receipts": len(deliveries),
            "application_receipts": len(applications),
            "targeted_repair_parent_fallbacks": len(fallback_delivery_ids),
            "unresolved_delivery_receipts": len(unresolved_delivery_ids),
            "orphan_application_receipts": len(orphan_application_ids),
            "duplicate_delivery_receipts": duplicate_delivery_receipts,
            "duplicate_application_receipts": duplicate_application_receipts,
            "unresolved_delivery_receipt_ids": unresolved_delivery_ids[:16],
            "receipt_closure_valid": receipt_closure_valid,
            "raw_trace_included": False,
        }
