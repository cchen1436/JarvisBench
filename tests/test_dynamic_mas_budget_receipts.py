from __future__ import annotations

import json
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from jarvisbench.core.controller import AttentionDecision
from jarvisbench.core.decision_ledger import DecisionLedger
from jarvisbench.reference.dynamic_mas.contracts import (
    HeldAction,
    ReviewRequest,
    SessionBinding,
)
from jarvisbench.reference.dynamic_mas.control_plane import SessionControlPlane
from jarvisbench.reference.dynamic_mas.registry import DynamicChildRegistry
from jarvisbench.reference.dynamic_mas.scheduler import DynamicMasScheduler


PROJECT_ID = "project-budget-receipts"


def _binding(
    *,
    agent_id: str,
    session_id: str,
    workstream_id: str,
    role: str = "worker",
) -> SessionBinding:
    return SessionBinding(
        project_id=PROJECT_ID,
        agent_id=agent_id,
        session_id=session_id,
        session_key=(
            "agent:main:chat"
            if role == "parent"
            else f"agent:main:subagent:{session_id}"
        ),
        parent_id="parent",
        parent_session_id="chat",
        parent_session_key="agent:main:chat",
        role=role,
        workstream_id=workstream_id,
    )


def _review(
    store: SessionControlPlane,
    session_id: str,
    *,
    ordinal: int,
) -> ReviewRequest:
    state = store.read(session_id)
    suffix = f"{ordinal:02d}"
    return ReviewRequest(
        project_id=PROJECT_ID,
        run_id="run-budget-receipts",
        session_id=session_id,
        turn_id=f"turn-{suffix}",
        batch_id=f"batch-{session_id}-{suffix}",
        review_id=f"review-{session_id}-{suffix}",
        control_epoch=int(state["control_epoch"]),
        nonce=str(state["nonce"]),
        expected_event_seq=ordinal + 1,
        actions=(
            HeldAction(
                action_id=f"action-{session_id}-{suffix}",
                tool_call_id=f"call-{session_id}-{suffix}",
                tool_name="write",
                action_fingerprint=f"{ordinal + 1:064x}",
                params_sha256=f"{ordinal + 101:064x}",
                params_preview="write one consequential project artifact",
                artifact_paths=(f"results/{session_id}/result.txt",),
            ),
        ),
    )


def _event(review: ReviewRequest) -> dict[str, object]:
    return {
        "seq": review.expected_event_seq,
        "type": "jarvis.review.requested",
        "payload": review.to_dict(),
    }


def _scheduler(
    tmp_path: Path,
    *,
    controller: object,
    requester: object,
    child_count: int,
    max_attention_requests: int = 2,
) -> tuple[DynamicMasScheduler, SessionControlPlane, Path]:
    registry = DynamicChildRegistry(project_id=PROJECT_ID)
    store = SessionControlPlane(tmp_path / "control", project_id=PROJECT_ID)
    ledger_path = tmp_path / "private" / "decision-ledger.jsonl"
    scheduler = DynamicMasScheduler(
        project_id=PROJECT_ID,
        controller=controller,  # type: ignore[arg-type]
        requester=requester,  # type: ignore[arg-type]
        requester_context_loader=lambda: "private requester fixture",
        registry=registry,
        control_plane=store,
        decision_ledger=DecisionLedger(ledger_path),
        max_attention_requests=max_attention_requests,
    )
    scheduler.register(
        _binding(
            agent_id="parent",
            session_id="chat",
            workstream_id="",
            role="parent",
        )
    )
    for index in range(child_count):
        scheduler.register(
            _binding(
                agent_id=f"worker-{index + 1}",
                session_id=f"child-{index + 1}",
                workstream_id=f"workstream-{index + 1}",
            )
        )
    return scheduler, store, ledger_path


class _PerChildController:
    def __init__(self) -> None:
        self.calls = 0
        self._lock = threading.Lock()

    def decide(self, candidate) -> AttentionDecision:
        with self._lock:
            self.calls += 1
        return AttentionDecision(
            True,
            "requester-owned worker boundary",
            f"Which bounded option should {candidate.session_id} use?",
            "worker",
        )


class _CountingRequester:
    def __init__(self) -> None:
        self.calls = 0
        self.active = 0
        self.max_active = 0
        self._lock = threading.Lock()

    def answer(self, _question: str, requester_context: str) -> str:
        assert requester_context == "private requester fixture"
        with self._lock:
            self.calls += 1
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        # Widen the overlap window: the scheduler must still serialize access.
        time.sleep(0.01)
        with self._lock:
            self.active -= 1
        return "Use the reversible option."


@pytest.mark.parametrize("concurrent", [False, True], ids=["serial", "concurrent"])
def test_project_attention_budget_never_exceeds_two(
    tmp_path: Path,
    concurrent: bool,
) -> None:
    controller = _PerChildController()
    requester = _CountingRequester()
    scheduler, store, _ledger_path = _scheduler(
        tmp_path,
        controller=controller,
        requester=requester,
        child_count=4,
        max_attention_requests=2,
    )
    events = [
        _event(_review(store, f"child-{index + 1}", ordinal=index))
        for index in range(4)
    ]

    if concurrent:
        with ThreadPoolExecutor(max_workers=4) as executor:
            outcomes = list(executor.map(scheduler.process_event, events))
    else:
        outcomes = [scheduler.process_event(event) for event in events]

    dispositions = Counter(outcome.disposition for outcome in outcomes if outcome)
    assert dispositions == {
        "interrupt_replan": 2,
        "attention_budget_allow": 2,
    }
    assert controller.calls == 4
    assert requester.calls == 2
    assert requester.max_active == 1
    diagnostics = scheduler.diagnostics()
    assert diagnostics["max_attention_requests"] == 2
    assert diagnostics["attention_requests_used"] == 2
    assert diagnostics["attention_budget_enforced"] is True
    for index in range(4):
        state = store.read(f"child-{index + 1}")
        assert state["active_review"] is None
        assert state["pause"]["active"] is False


def test_normalized_duplicate_project_question_is_suppressed(tmp_path: Path) -> None:
    class DuplicateController:
        def __init__(self) -> None:
            self.questions = iter(
                (
                    "Which bounded option should the project use?",
                    "  WHICH   bounded option should the PROJECT use?  ",
                )
            )

        def decide(self, _candidate) -> AttentionDecision:
            return AttentionDecision(
                True,
                "same project-owned boundary",
                next(self.questions),
                "project",
            )

    requester = _CountingRequester()
    scheduler, store, _ledger_path = _scheduler(
        tmp_path,
        controller=DuplicateController(),
        requester=requester,
        child_count=2,
    )
    first = scheduler.process_event(_event(_review(store, "child-1", ordinal=0)))
    second = scheduler.process_event(_event(_review(store, "child-2", ordinal=1)))

    assert first is not None and first.disposition == "interrupt_replan"
    assert second is not None and second.disposition == "duplicate_attention_allow"
    assert requester.calls == 1
    diagnostics = scheduler.diagnostics()
    assert diagnostics["attention_requests_used"] == 1
    assert diagnostics["duplicate_attention_suppressed"] == 1
    assert store.read("child-2")["guidance_queue"] == []


def test_interrupt_review_response_binds_delivery_receipt(tmp_path: Path) -> None:
    store = SessionControlPlane(tmp_path / "control", project_id=PROJECT_ID)
    store.register(
        _binding(
            agent_id="worker-1",
            session_id="child-1",
            workstream_id="workstream-1",
        )
    )
    review = _review(store, "child-1", ordinal=0)
    store.hold(review)
    invalidation = store.interrupt(
        review,
        decision_id="decision-delivery-receipt",
        guidance="Use the requester-owned reversible option.",
    )

    state = store.read("child-1")
    response = state["review_responses"][-1]
    assert response["decision"] == "interrupt_replan"
    assert response["delivery_receipt_id"] == invalidation["delivery_receipt_id"]
    assert response["delivery_receipt_id"] == state["delivery_receipts"][0]["receipt_id"]
    assert response["guidance_sha256"] == state["delivery_receipts"][0]["guidance_sha256"]
    assert response["next_control_epoch"] == state["control_epoch"]
    assert response["next_nonce"] == state["nonce"]


def test_guidance_applied_writes_control_receipt_and_append_only_ledger(
    tmp_path: Path,
) -> None:
    controller = _PerChildController()
    requester = _CountingRequester()
    scheduler, store, ledger_path = _scheduler(
        tmp_path,
        controller=controller,
        requester=requester,
        child_count=1,
    )
    outcome = scheduler.process_event(
        _event(_review(store, "child-1", ordinal=0))
    )
    assert outcome is not None and outcome.disposition == "interrupt_replan"
    assert len(outcome.delivery_receipts) == 1
    before_diagnostics = scheduler.diagnostics()
    assert before_diagnostics["delivery_receipts"] == 1
    assert before_diagnostics["application_receipts"] == 0
    assert before_diagnostics["unresolved_delivery_receipts"] == 1
    assert before_diagnostics["receipt_closure_valid"] is False

    before = ledger_path.read_bytes()
    state = store.read("child-1")
    delivery = state["delivery_receipts"][0]
    applied_event = {
        "seq": 2,
        "type": "control.guidance.applied",
        "payload": {
            "project_id": PROJECT_ID,
            "session_id": "child-1",
            "delivery_receipt_id": delivery["receipt_id"],
            "model_boundary_id": "boundary-child-1",
            "control_epoch": state["control_epoch"],
            "nonce": state["nonce"],
            "guidance_sha256": delivery["guidance_sha256"],
        },
    }
    assert scheduler.process_event(applied_event) is None

    after = ledger_path.read_bytes()
    assert after.startswith(before)
    assert len(after) > len(before)
    records = [json.loads(line) for line in after.splitlines()]
    assert len(records) == 3
    assert records[-2]["kind"] == "guidance_delivery"
    application = records[-1]
    control_state = store.read("child-1")
    control_receipt = control_state["application_receipts"][0]
    assert application["kind"] == "guidance_application"
    assert application["application_receipt_id"] == control_receipt["receipt_id"]
    assert application["delivery_receipt_id"] == delivery["receipt_id"]
    assert application["target_session_id"] == "child-1"
    assert control_state["guidance_queue"] == []
    assert control_state["delivery_receipts"][0]["status"] == "applied"
    after_diagnostics = scheduler.diagnostics()
    assert after_diagnostics["delivery_receipts"] == 1
    assert after_diagnostics["application_receipts"] == 1
    assert after_diagnostics["unresolved_delivery_receipts"] == 0
    assert after_diagnostics["receipt_closure_valid"] is True

    # A duplicate plugin receipt is idempotent in both stores and cannot append
    # another ledger line.
    assert scheduler.process_event(applied_event) is None
    assert ledger_path.read_bytes() == after
    assert len(store.read("child-1")["application_receipts"]) == 1
