from __future__ import annotations

import json
from dataclasses import replace

from jarvisbench.core.contracts import BoundaryCandidate, ReducedUpdate
from jarvisbench.core.controller import AttentionDecision
from jarvisbench.core.providers import Completion
from jarvisbench.reference.dynamic_mas.contracts import HeldAction, ReviewRequest
from jarvisbench.reference.dynamic_mas.reducer import LiveChildReducer
from jarvisbench.reference.dynamic_mas.contracts import SessionBinding
from jarvisbench.reference.jarvis import ReferenceJarvis


class _StaticProvider:
    def __init__(self, value: dict[str, object]) -> None:
        self.value = value
        self.messages = ()

    def complete(self, messages, *, model: str, reasoning: str | None = None):
        self.messages = tuple(messages)
        return Completion(json.dumps(self.value), 1.0, 2.0, model)


class _SequenceProvider:
    def __init__(self, values: list[dict[str, object]]) -> None:
        self.values = list(values)
        self.calls: list[tuple[object, ...]] = []

    def complete(self, messages, *, model: str, reasoning: str | None = None):
        self.calls.append(tuple(messages))
        return Completion(json.dumps(self.values.pop(0)), 1.0, 2.0, model)


def _candidate(*, truncated: bool = False, session_id: str = "worker-0") -> BoundaryCandidate:
    proposed = (
        "tool=write; boundary=final requester-facing result; "
        "artifacts=results/final.json; preview_status="
        + (
            "bounded head/tail preview; omitted text is not evidence of an "
            "incomplete worker payload"
            if truncated
            else "complete bounded preview"
        )
        + "; decision_salient=choice: provisional option"
    )
    return BoundaryCandidate(
        session_id=session_id,
        epoch=3,
        nonce="a" * 48,
        action_id="action-final",
        action_fingerprint="b" * 64,
        reduced_update=ReducedUpdate(
            progress="The public evidence has been analyzed.",
            current_goal="Commit the requester-facing decision record.",
            evidence=("choice: provisional option",),
            uncertainty="Requester acceptance is not public evidence.",
            proposed_action=proposed,
        ),
        consequence=proposed,
        artifact_paths=("results/final.json",),
        final_record_intent=True,
        preview_truncated=truncated,
        task_brief="Compare the public alternatives and record the selected outcome.",
        required_result_paths=("results/final.json", "results/recommendation.md"),
    )


def _ask_response(
    *,
    evidence_quote: str,
    question: str,
    reason: str,
    scope: str = "worker",
) -> dict[str, object]:
    return {
        "request_attention": True,
        "reason_code": "requester_judgment_needed",
        "confidence": 0.97,
        "reason": reason,
        "question": question,
        "scope": scope,
        "evidence_source": "evidence",
        "evidence_quote": evidence_quote,
    }


def test_reference_jarvis_accepts_only_an_exact_visible_evidence_quote() -> None:
    provider = _StaticProvider(
        _ask_response(
            evidence_quote="choice: provisional option",
            question="Do you accept the provisional option, or prefer the visible alternative?",
            reason="The final record commits a requester-owned acceptance judgment.",
        )
    )
    decision = ReferenceJarvis(provider, model="provider/jarvis").decide(_candidate())

    assert decision.request_attention is True
    assert decision.evidence_source == "evidence"
    assert decision.evidence_quote == "choice: provisional option"
    packet = json.loads(provider.messages[-1].content)
    system_prompt = " ".join(provider.messages[0].content.split())
    assert "even when it is conservative, conditional, reversible" in system_prompt
    assert "pending the requester's authorization" in system_prompt
    assert "nonempty requester-question field" in system_prompt
    assert "8--240 characters" in system_prompt
    assert "defer new questions about downstream implementation" in system_prompt
    assert "accountability and timing commitments" in system_prompt
    assert "do not ask the meta question of whether placeholders are acceptable" in (
        system_prompt
    )
    assert packet["final_record_intent"] is True
    assert packet["artifact_paths"] == ["results/final.json"]
    assert packet["task_brief"].startswith("Compare the public alternatives")
    assert packet["required_result_paths"] == [
        "results/final.json",
        "results/recommendation.md",
    ]


def test_reference_jarvis_rejects_an_evidence_quote_not_in_the_named_field() -> None:
    provider = _StaticProvider(
        _ask_response(
            evidence_quote="a quote invented by the model",
            question="Which outcome do you accept?",
            reason="The answer could change the final decision.",
        )
    )

    decision = ReferenceJarvis(provider, model="provider/jarvis").decide(_candidate())

    assert decision.request_attention is False
    assert decision.reason.startswith("invalid controller response:")


def test_truncated_preview_cannot_be_used_as_the_reason_to_ask() -> None:
    provider = _StaticProvider(
        _ask_response(
            evidence_quote="choice: provisional option",
            question="Can you provide the missing remainder of the truncated payload?",
            reason="The preview is truncated, so the complete payload is needed.",
        )
    )

    decision = ReferenceJarvis(provider, model="provider/jarvis").decide(
        _candidate(truncated=True)
    )

    assert decision.request_attention is False
    assert decision.reason.startswith("invalid controller response:")


def test_external_irreversible_effect_is_not_rejected_as_local_intermediate() -> None:
    provider = _StaticProvider(
        _ask_response(
            evidence_quote="choice: provisional option",
            question="Do you authorize publishing this requester-facing outcome?",
            reason="The external write requires requester authorization.",
        )
    )
    candidate = replace(
        _candidate(),
        artifact_paths=("results/recommendation.md",),
        final_record_intent=False,
        external_irreversible_effect="http_write",
    )

    decision = ReferenceJarvis(provider, model="provider/jarvis").decide(candidate)

    assert decision.request_attention is True


def test_held_action_new_fields_round_trip_through_review_event() -> None:
    action = HeldAction(
        action_id="action-final",
        tool_call_id="call-final",
        tool_name="write",
        action_fingerprint="c" * 64,
        params_sha256="d" * 64,
        params_preview="head ... [bounded preview of 2400 chars] ... tail",
        artifact_paths=("results/final.json",),
        params_chars=2_400,
        params_truncated=True,
        params_salient_preview="choice: provisional option | rationale: public evidence",
        final_record_intent=True,
    )
    review = ReviewRequest(
        project_id="project-parity",
        run_id="run-parity",
        session_id="worker-0",
        turn_id="turn-1",
        batch_id="batch-1",
        review_id="review-1",
        control_epoch=0,
        nonce="e" * 48,
        expected_event_seq=17,
        actions=(action,),
    )

    restored = ReviewRequest.from_event(
        {"seq": 17, "type": "jarvis.review.requested", "payload": review.to_dict()}
    )

    assert restored == review
    assert restored.actions[0].params_chars == 2_400
    assert restored.actions[0].params_truncated is True
    assert restored.actions[0].params_salient_preview.startswith("choice:")
    assert restored.actions[0].final_record_intent is True


def test_reducer_preserves_boundary_and_truncation_semantics() -> None:
    reducer = LiveChildReducer("project-parity")
    reducer.register(
        SessionBinding(
            project_id="project-parity",
            agent_id="worker-0",
            session_id="worker-0",
            session_key="agent:main:worker-0",
            parent_id="project-root",
            parent_session_id="no-parent",
            parent_session_key="no-parent",
            role="worker",
            workstream_id="single-worker",
        )
    )
    action = HeldAction(
        action_id="action-final",
        tool_call_id="call-final",
        tool_name="write",
        action_fingerprint="f" * 64,
        params_sha256="1" * 64,
        params_preview="head ... [bounded preview] ... tail",
        artifact_paths=("results/final.json",),
        params_chars=2_400,
        params_truncated=True,
        params_salient_preview="choice: provisional option",
        final_record_intent=True,
    )
    review = ReviewRequest(
        project_id="project-parity",
        run_id="run-parity",
        session_id="worker-0",
        turn_id="turn-1",
        batch_id="batch-1",
        review_id="review-1",
        control_epoch=0,
        nonce="2" * 48,
        expected_event_seq=1,
        actions=(action,),
    )

    reducer.observe(
        {"seq": 1, "type": "jarvis.review.requested", "payload": review.to_dict()}
    )
    proposed = reducer.reduced_update("worker-0").proposed_action or ""

    assert "boundary=final requester-facing result" in proposed
    assert "omitted text is not evidence" in proposed
    assert "decision_salient=choice: provisional option" in proposed


def test_reducer_prioritizes_late_decision_salience_over_long_generic_preview() -> None:
    reducer = LiveChildReducer("project-parity")
    reducer.register(
        SessionBinding(
            project_id="project-parity",
            agent_id="worker-0",
            session_id="worker-0",
            session_key="agent:main:worker-0",
            parent_id="project-root",
            parent_session_id="no-parent",
            parent_session_key="no-parent",
            role="worker",
            workstream_id="single-worker",
        )
    )
    requester_question = (
        "question_for_requester: Should the final record select option B "
        "or retain option A?"
    )
    action = HeldAction(
        action_id="action-final",
        tool_call_id="call-final",
        tool_name="write",
        action_fingerprint="f" * 64,
        params_sha256="1" * 64,
        params_preview="generic " * 112,
        artifact_paths=("results/final.json",),
        params_chars=6_000,
        params_truncated=True,
        params_salient_preview=("decision context " * 47) + requester_question,
        final_record_intent=True,
    )
    review = ReviewRequest(
        project_id="project-parity",
        run_id="run-parity",
        session_id="worker-0",
        turn_id="turn-1",
        batch_id="batch-1",
        review_id="review-1",
        control_epoch=0,
        nonce="2" * 48,
        expected_event_seq=1,
        actions=(action,),
    )

    reducer.observe(
        {"seq": 1, "type": "jarvis.review.requested", "payload": review.to_dict()}
    )
    proposed = reducer.reduced_update("worker-0").proposed_action or ""

    assert requester_question in proposed
    assert proposed.index("decision_salient=") < proposed.index("params_preview=")


def test_prior_questions_follow_their_declared_scope() -> None:
    worker_question = "Does worker A's provisional option match your intended outcome?"
    project_question = "Should every workstream use the same requester risk posture?"
    defer = {
        "request_attention": False,
        "reason_code": "no_user_attention",
        "confidence": 0.99,
        "reason": "No additional requester judgment is needed.",
        "question": None,
        "scope": "worker",
        "evidence_source": "",
        "evidence_quote": "",
    }
    provider = _SequenceProvider(
        [
            _ask_response(
                evidence_quote="choice: provisional option",
                question=worker_question,
                reason="Worker A is committing a requester-owned judgment.",
            ),
            defer,
            defer,
            _ask_response(
                evidence_quote="choice: provisional option",
                question=project_question,
                reason="This requester-owned posture affects the project.",
                scope="project",
            ),
            defer,
        ]
    )
    jarvis = ReferenceJarvis(provider, model="provider/jarvis")

    worker_candidate = _candidate(session_id="worker-a")
    worker_decision = jarvis.decide(worker_candidate)
    assert worker_decision.request_attention
    jarvis.record_committed_question(worker_candidate, worker_decision)
    jarvis.decide(_candidate(session_id="worker-b"))
    jarvis.decide(_candidate(session_id="worker-a"))
    project_candidate = _candidate(session_id="worker-a")
    project_decision = jarvis.decide(project_candidate)
    assert project_decision.request_attention
    jarvis.record_committed_question(project_candidate, project_decision)
    jarvis.decide(_candidate(session_id="worker-b"))

    worker_b_before_project = json.loads(provider.calls[1][-1].content)
    worker_a_follow_up = json.loads(provider.calls[2][-1].content)
    worker_b_after_project = json.loads(provider.calls[4][-1].content)

    # Worker-scoped requester dialog is private to that execution node. Project
    # scope is the explicit opt-in that makes a prior exchange visible to a sibling.
    assert worker_b_before_project["prior_questions"] == []
    assert [item["question"] for item in worker_a_follow_up["prior_questions"]] == [
        worker_question
    ]
    assert [item["question"] for item in worker_b_after_project["prior_questions"]] == [
        project_question
    ]


def test_uncommitted_attention_proposal_does_not_enter_question_history() -> None:
    proposed_question = "Do you accept this provisional requester-facing outcome?"
    provider = _SequenceProvider(
        [
            _ask_response(
                evidence_quote="choice: provisional option",
                question=proposed_question,
                reason="The final outcome needs requester acceptance.",
            ),
            {
                "request_attention": False,
                "reason_code": "no_user_attention",
                "confidence": 0.99,
                "reason": "No additional requester judgment is needed.",
                "question": None,
                "scope": "worker",
                "evidence_source": "",
                "evidence_quote": "",
            },
        ]
    )
    jarvis = ReferenceJarvis(provider, model="provider/jarvis")

    assert jarvis.decide(_candidate(session_id="worker-a")).request_attention
    jarvis.decide(_candidate(session_id="worker-a"))

    second_packet = json.loads(provider.calls[1][-1].content)
    assert second_packet["prior_questions"] == []


def test_committed_requester_guidance_is_visible_only_in_its_scope() -> None:
    defer = {
        "request_attention": False,
        "reason_code": "no_user_attention",
        "confidence": 0.99,
        "reason": "No additional requester judgment is needed.",
        "question": None,
        "scope": "worker",
        "evidence_source": "",
        "evidence_quote": "",
    }
    provider = _SequenceProvider(
        [
            _ask_response(
                evidence_quote="choice: provisional option",
                question="Do you accept option B?",
                reason="The final outcome needs requester acceptance.",
            ),
            defer,
            defer,
        ]
    )
    jarvis = ReferenceJarvis(provider, model="provider/jarvis")
    candidate = _candidate(session_id="worker-a")
    decision = jarvis.decide(candidate)
    jarvis.record_committed_question(
        candidate,
        decision,
        "Use option B and do not add optional conditions.",
    )

    jarvis.decide(_candidate(session_id="worker-a"))
    jarvis.decide(_candidate(session_id="worker-b"))

    same_session = json.loads(provider.calls[1][-1].content)
    sibling = json.loads(provider.calls[2][-1].content)
    assert same_session["prior_questions"][0]["released_guidance"] == (
        "Use option B and do not add optional conditions."
    )
    assert sibling["prior_questions"] == []


def test_guidance_translation_reconciles_declared_result_paths() -> None:
    provider = _StaticProvider(
        {
            "guidance": (
                "Use the requester's selected outcome in the final record and "
                "recommendation; preserve unrelated analysis."
            ),
            "used_disclosed_memory_ids": ["user.preference.choice"],
        }
    )
    jarvis = ReferenceJarvis(provider, model="provider/jarvis")
    candidate = _candidate()
    decision = AttentionDecision(
        True,
        "The terminal outcome needs requester judgment.",
        "Which visible outcome should the final record use?",
        "worker",
        "requester_judgment_needed",
        0.99,
        "evidence",
        "choice: provisional option",
    )

    guidance = jarvis.translate_requester_answer(
        candidate,
        decision,
        "Use option B, but do not authorize the exception.",
        (
            {
                "memory_id": "user.preference.choice",
                "field": "preference.choice",
                "value": "B without the exception",
            },
        ),
    )

    assert "selected outcome" in guidance
    packet = json.loads(provider.messages[-1].content)
    system_prompt = " ".join(provider.messages[0].content.split())
    assert packet["required_result_paths"] == [
        "results/final.json",
        "results/recommendation.md",
    ]
    assert "every declared result path" in system_prompt
    assert "negative or conditional qualifier" in system_prompt
