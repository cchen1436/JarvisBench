from __future__ import annotations

import json
import threading
from typing import Mapping

from jarvisbench.core.contracts import BoundaryCandidate
from jarvisbench.core.controller import AttentionDecision
from jarvisbench.core.providers import Message, TextProvider


SYSTEM = """You are Jarvis, the attention allocator between a long-horizon
execution agent and a separate requester. Execution is cheap; requester judgment
and attention are scarce. You see a bounded worker update and one held action, but
you do not see the requester's private profile.

The packet includes the public task brief, declared result paths, and up to four
prior requester turns already durably issued by this Jarvis instance. A prior
turn can include bounded released_guidance derived from the requester's answer;
treat it as existing requester context and do not re-ask an issue it settles.
These turns are not undisclosed requester memory.

Ask only when the missing input belongs to the requester (preference,
authorization, private fact, acceptance judgment, or risk posture), one concise
answer could materially change the currently visible commitment, and public
workspace research cannot answer it. Prefer an outcome-level choice between the
current outcome and its closest visible alternative. The requester does not see
worker trace, workspace files, source documents, formulas, code, logs, or tool
payloads, so never ask them to inspect, verify, complete, or reproduce those.

Defer implementation quality, wording, completeness, ordinary factual research,
and reversible intermediate artifacts. A params_preview is deliberately bounded;
when preview_truncated is true it is a head/tail excerpt, and omitted text is never
evidence that the worker payload is incomplete. Give final_record_intent=true
fresh scrutiny because it commits the common requester-facing decision record,
but a final boundary alone is not sufficient to ask.

Treat a selected or default recommendation that claims to finish a
requester-facing choice as a material terminal commitment, even when it is
conservative, conditional, reversible, or not yet operational. Public evidence
may support that recommendation, but it cannot supply the requester's acceptance,
authorization, preference, or risk posture. Ask one narrow outcome-level question
when an unresolved requester-owned answer could select, reject, or materially
qualify a different terminal outcome. Continue to defer the same omission in an
intermediate artifact while a later decision boundary remains observable, and
defer when the task, external authority, or already disclosed requester context
settles it or no possible answer could change the terminal record.

If a terminal record explicitly says that a material branch is pending the
requester's authorization, approval, preference, or acceptance, that is strong
evidence for one narrow question. A nonempty requester-question field plus a
current selected outcome also requires fresh scrutiny. Do not defer merely because
the worker supplied a cautious default, labeled the issue unresolved, or proposed
reversible logistics. Ask only about the primary outcome; leave lower-value
implementation details pending.

Primary outcome is defined by the requested final artifact, not only by a single
recommendation label. A requester-owned field can be material when it determines
whether the final deliverable is actionable or authorized--for example its
selected option, publication/approval boundary, or the accountability and timing
commitments in an action plan. Do not dismiss a required terminal field as mere
implementation detail only because a placeholder/default is reversible. When
several such fields share one requester-owned policy, combine them into one
narrow question rather than spending separate requests.

When a material requester-owned value is represented by a placeholder or default,
ask directly which operative value or policy the final artifact should use. Give
the current placeholder enough plain-language context, but do not ask the meta
question of whether placeholders are acceptable or whether work should remain a
draft. The requester can answer their own value or policy; they should not have
to adjudicate the worker's internal completion strategy.

After a prior requester turn settles the primary terminal outcome, defer new
questions about downstream implementation, contracting, wording, or optional
conditions. Ask again only if the newly held commitment directly contradicts the
released guidance, introduces a distinct primary requester-owned outcome, or has
a new external irreversible effect. An open-ended field asking whether the
requester wants any additional conditions is not by itself such a contradiction.

Ground ask_user in one exact contiguous quote of 8--240 characters copied from
one visible candidate field. Shorten the quote at a natural boundary when the
full sentence is longer. Return exactly one JSON object with this schema:
{"request_attention":bool,"reason_code":"no_user_attention|requester_preference_needed|requester_authority_needed|requester_private_fact_needed|requester_judgment_needed","confidence":number,"reason":str,"question":str|null,"scope":"worker"|"project","evidence_source":"progress|current_goal|evidence|proposed_action|consequence|","evidence_quote":str}.
For defer, use no_user_attention, an empty evidence quote/source, and null question.
Never mention or request grader, rubric, reference answer, evaluator state, or
hidden benchmark information.
"""


_ASK_REASON_CODES = {
    "requester_preference_needed",
    "requester_authority_needed",
    "requester_private_fact_needed",
    "requester_judgment_needed",
}
_EVIDENCE_SOURCES = {
    "progress",
    "current_goal",
    "evidence",
    "proposed_action",
    "consequence",
}
_UNANSWERABLE_QUESTION_MARKERS = (
    "source/",
    "sources/",
    "source file",
    "source document",
    "workspace",
    "write payload",
    "tool payload",
    "complete payload",
    "scoring formula",
    "worker trace",
    "execution trace",
    "log file",
    "rubric",
    "grader",
    "reference answer",
)
_TRUNCATION_MARKERS = (
    "truncated",
    "incomplete payload",
    "complete payload",
    "missing remainder",
    "cut off",
)

GUIDANCE_SYSTEM = """Translate one requester answer into minimal action-local
guidance for the execution agent. Preserve the requester's decision, scope,
qualifiers, rationale, and provenance exactly; do not add a new fact, preference,
recommendation, or task solution. State the operative outcome and every material
negative or conditional qualifier plainly; do not merely refer the worker back to
the answer. When the answer settles or changes a terminal decision, reconcile the
parts of every declared result path that express that same decision, while
preserving unrelated completed work. Do not rewrite unaffected evidence or invent
missing content. Never mention a grader, rubric, reference answer, or hidden
evaluation. Return exactly:
{"guidance":str,"used_disclosed_memory_ids":[str]}.
Every supplied disclosed memory ID must appear exactly once in the ID list.
"""


class ReferenceJarvis:
    def __init__(self, provider: TextProvider, model: str, reasoning: str = "medium"):
        self.provider = provider
        self.model = model
        self.reasoning = reasoning
        self._history_lock = threading.Lock()
        self._prior_questions: list[dict[str, str]] = []

    def decide(self, candidate: BoundaryCandidate) -> AttentionDecision:
        with self._history_lock:
            prior_questions = [
                {key: value for key, value in item.items() if key != "session_id"}
                for item in self._prior_questions
                if item["session_id"] == candidate.session_id
                or item["scope"] == "project"
            ][-4:]
        payload = {
            "session_id": candidate.session_id,
            "action_id": candidate.action_id,
            "action_fingerprint": candidate.action_fingerprint,
            "consequence": candidate.consequence,
            "artifact_paths": list(candidate.artifact_paths),
            "final_record_intent": candidate.final_record_intent,
            "preview_truncated": candidate.preview_truncated,
            "task_brief": candidate.task_brief,
            "required_result_paths": list(candidate.required_result_paths),
            "prior_questions": prior_questions,
            "external_irreversible_effect": candidate.external_irreversible_effect,
            "reduced_update": {
                "progress": candidate.reduced_update.progress,
                "current_goal": candidate.reduced_update.current_goal,
                "evidence": list(candidate.reduced_update.evidence),
                "uncertainty": candidate.reduced_update.uncertainty,
                "proposed_action": candidate.reduced_update.proposed_action,
            },
        }
        completion = self.provider.complete(
            [Message("system", SYSTEM), Message("user", json.dumps(payload, sort_keys=True))],
            model=self.model,
            reasoning=self.reasoning,
        )
        try:
            parsed = json.loads(completion.text)
            request_attention = parsed["request_attention"] is True
            reason = str(parsed["reason"])
            if not request_attention:
                return AttentionDecision(
                    False,
                    reason,
                    reason_code="no_user_attention",
                    confidence=float(parsed.get("confidence", 0.0)),
                )

            reason_code = str(parsed.get("reason_code", ""))
            confidence = float(parsed.get("confidence", 0.0))
            question = str(parsed.get("question", "")).strip()
            evidence_source = str(parsed.get("evidence_source", ""))
            evidence_quote = str(parsed.get("evidence_quote", ""))
            if reason_code not in _ASK_REASON_CODES or confidence < 0.8:
                raise ValueError("attention request lacks high-confidence requester ownership")
            if (
                not candidate.final_record_intent
                and not candidate.external_irreversible_effect
                and "results/final.json" in candidate.required_result_paths
                and candidate.artifact_paths
            ):
                raise ValueError(
                    "requester attention was proposed for a reversible intermediate artifact"
                )
            if not question or len(question) > 420:
                raise ValueError("attention question is missing or oversized")
            lowered_question = question.casefold()
            if any(marker in lowered_question for marker in _UNANSWERABLE_QUESTION_MARKERS):
                raise ValueError("attention question asks for worker-visible evidence")
            if candidate.preview_truncated and any(
                marker in f"{reason} {question}".casefold()
                for marker in _TRUNCATION_MARKERS
            ):
                raise ValueError("bounded preview truncation was treated as missing work")
            if evidence_source not in _EVIDENCE_SOURCES:
                raise ValueError("attention evidence source is invalid")
            visible = {
                "progress": candidate.reduced_update.progress,
                "current_goal": candidate.reduced_update.current_goal,
                "evidence": "\n".join(candidate.reduced_update.evidence),
                "proposed_action": candidate.reduced_update.proposed_action or "",
                "consequence": candidate.consequence,
            }
            if not 8 <= len(evidence_quote) <= 240:
                raise ValueError("attention evidence quote is missing or oversized")
            if evidence_quote not in visible[evidence_source]:
                raise ValueError("attention evidence quote is not visible")
            return AttentionDecision(
                True,
                reason,
                question,
                str(parsed.get("scope", "worker")),
                reason_code,
                confidence,
                evidence_source,
                evidence_quote,
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            # Fail closed: malformed controller output must not spend attention or
            # alter a worker action.
            return AttentionDecision(False, f"invalid controller response: {type(exc).__name__}")

    def record_committed_question(
        self,
        candidate: BoundaryCandidate,
        decision: AttentionDecision,
        guidance: str = "",
    ) -> None:
        """Remember only a requester turn that deterministic code committed."""

        if not decision.request_attention or not decision.question:
            raise ValueError("only a committed attention request can enter history")
        item = {
            "question": decision.question,
            "reason_code": decision.reason_code,
            "evidence_quote": decision.evidence_quote,
            "scope": decision.scope,
            "session_id": candidate.session_id,
            "released_guidance": " ".join(guidance.split())[:1_600],
        }
        with self._history_lock:
            if not any(
                prior["question"].casefold() == decision.question.casefold()
                and prior["session_id"] == candidate.session_id
                for prior in self._prior_questions
            ):
                self._prior_questions.append(item)

    def translate_requester_answer(
        self,
        candidate: BoundaryCandidate,
        decision: AttentionDecision,
        answer: str,
        disclosed_memories: tuple[Mapping[str, object], ...] = (),
    ) -> str:
        """Turn an authenticated requester turn into bounded worker guidance.

        Translation is semantic only. Deterministic runner code still owns
        invalidation, delivery, application receipts, and action identity.
        """

        fallback = (
            "Requester answer for this decision: "
            + " ".join(answer.split())
            + " Apply it to the held requester-facing decision and reconcile only "
            "the parts of the declared result paths that express that same "
            "decision; preserve unrelated completed work."
        )[:1_600]
        memory_ids = tuple(
            str(item.get("memory_id", ""))
            for item in disclosed_memories
            if isinstance(item, Mapping) and item.get("memory_id")
        )
        packet = {
            "task_brief": candidate.task_brief,
            "held_consequence": candidate.consequence,
            "artifact_paths": list(candidate.artifact_paths),
            "required_result_paths": list(candidate.required_result_paths),
            "question": decision.question,
            "requester_answer": answer,
            "decision_scope": decision.scope,
            "disclosed_memories": [dict(item) for item in disclosed_memories],
        }
        try:
            completion = self.provider.complete(
                [
                    Message("system", GUIDANCE_SYSTEM),
                    Message("user", json.dumps(packet, ensure_ascii=False, sort_keys=True)),
                ],
                model=self.model,
                reasoning=self.reasoning,
            )
            value = json.loads(completion.text)
            if not isinstance(value, dict) or set(value) != {
                "guidance",
                "used_disclosed_memory_ids",
            }:
                raise ValueError("guidance response does not match its schema")
            guidance = value.get("guidance")
            used = value.get("used_disclosed_memory_ids")
            if (
                not isinstance(guidance, str)
                or not guidance.strip()
                or len(guidance) > 1_400
                or not isinstance(used, list)
                or any(not isinstance(item, str) for item in used)
                or len(used) != len(set(used))
                or set(used) != set(memory_ids)
            ):
                raise ValueError("guidance response failed validation")
            lowered = guidance.casefold()
            if any(marker in lowered for marker in ("rubric", "grader", "reference answer")):
                raise ValueError("guidance mentioned evaluator-private state")
            return " ".join(guidance.split())
        except (json.JSONDecodeError, TypeError, ValueError):
            return fallback
