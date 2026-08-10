from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Mapping

from jarvisbench.core.contracts import ReducedUpdate

from .contracts import (
    MAX_EVIDENCE_ITEMS,
    MAX_PREVIEW_CHARS,
    MAX_REDUCED_UPDATE_BYTES,
    DynamicMasContractError,
    ReviewRequest,
    SessionBinding,
    bounded_text,
)


def _compact(value: Any, maximum: int = MAX_PREVIEW_CHARS) -> str:
    if isinstance(value, str):
        text = value
    else:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return " ".join(text.split())[:maximum]


def _evidence_id(session_id: str, sequence: int, event_type: str, text: str) -> str:
    seed = f"{session_id}\0{sequence}\0{event_type}\0{text}".encode("utf-8")
    return f"evidence-{hashlib.sha256(seed).hexdigest()[:24]}"


@dataclass
class _ChildState:
    binding: SessionBinding
    sequence: int = 0
    progress: str = "Registered; no bounded worker update observed yet."
    current_goal: str = "Await the first worker model boundary."
    uncertainty: str = ""
    evidence: list[tuple[str, str]] = field(default_factory=list)
    proposed_action: str = ""
    last_review: ReviewRequest | None = None
    saw_update_before_completion: bool = False


class LiveChildReducer:
    """Reduce plugin events without ever loading a full ReAct transcript."""

    def __init__(self, project_id: str) -> None:
        self.project_id = project_id
        self._states: dict[str, _ChildState] = {}

    def register(self, binding: SessionBinding) -> None:
        if binding.project_id != self.project_id:
            raise DynamicMasContractError("reducer session crossed project namespace")
        prior = self._states.get(binding.session_id)
        if prior is None:
            self._states[binding.session_id] = _ChildState(binding=binding)
        else:
            prior.binding = binding

    def set_status(self, session_id: str, status: str) -> None:
        state = self._states[session_id]
        state.binding = SessionBinding(**{**state.binding.to_dict(), "status": status})

    def _add_evidence(self, state: _ChildState, event_type: str, text: str) -> None:
        if not text:
            return
        evidence_id = _evidence_id(
            state.binding.session_id,
            state.sequence,
            event_type,
            text,
        )
        state.evidence.append((evidence_id, text[:640]))
        state.evidence = state.evidence[-MAX_EVIDENCE_ITEMS:]

    @staticmethod
    def _fit(value: dict[str, Any]) -> dict[str, Any]:
        while len(json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")) > MAX_REDUCED_UPDATE_BYTES:
            evidence = value.get("key_evidence")
            if isinstance(evidence, list) and len(evidence) > 1:
                evidence.pop(0)
                continue
            for key in ("progress", "current_goal", "uncertainty", "proposed_consequential_action"):
                text = value.get(key)
                if isinstance(text, str) and len(text) > 120:
                    value[key] = text[: max(120, len(text) // 2)]
                    break
            else:
                raise DynamicMasContractError("reduced child card cannot fit its bound")
        return value

    def card(self, session_id: str) -> dict[str, Any]:
        state = self._states[session_id]
        return self._fit(
            {
                "schema_version": "1.0",
                "kind": "bounded_child_update",
                "project_id": self.project_id,
                "agent_id": state.binding.agent_id,
                "session_id": state.binding.session_id,
                "parent_id": state.binding.parent_id,
                "role": state.binding.role,
                "workstream_id": state.binding.workstream_id,
                "status": state.binding.status,
                "progress": state.progress,
                "current_goal": state.current_goal,
                "uncertainty": state.uncertainty,
                "proposed_consequential_action": state.proposed_action,
                "key_evidence": [
                    {"evidence_id": evidence_id, "quote": quote}
                    for evidence_id, quote in state.evidence
                ],
                "raw_trace_included": False,
                "source_event_sequence": state.sequence,
            }
        )

    def reduced_update(self, session_id: str) -> ReducedUpdate:
        card = self.card(session_id)
        return ReducedUpdate(
            progress=str(card["progress"]),
            current_goal=str(card["current_goal"]),
            evidence=tuple(str(item["quote"]) for item in card["key_evidence"]),
            uncertainty=str(card["uncertainty"]) or None,
            proposed_action=str(card["proposed_consequential_action"]) or None,
        )

    def review(self, session_id: str) -> ReviewRequest | None:
        return self._states[session_id].last_review

    def saw_live_update(self, session_id: str) -> bool:
        return self._states[session_id].saw_update_before_completion

    def observe(self, event: Mapping[str, Any]) -> dict[str, Any] | None:
        event_type = str(event.get("type", ""))
        payload = event.get("payload")
        if not isinstance(payload, Mapping):
            return None
        if str(payload.get("project_id", "")) != self.project_id:
            return None
        session_id = str(payload.get("session_id", ""))
        state = self._states.get(session_id)
        if state is None or state.binding.role != "worker":
            return None
        sequence = event.get("seq", payload.get("event_seq", state.sequence + 1))
        if type(sequence) is not int or sequence <= state.sequence:
            return None
        state.sequence = sequence

        if event_type == "agent.llm.output":
            preview = _compact(payload.get("assistant_preview", ""))
            state.progress = preview or "Worker completed a model step."
            state.current_goal = "Continue the current workstream from the latest model boundary."
            # Uncertainty is evidence, not a keyword hit on the initial task prompt.
            lowered = preview.lower()
            markers = ("uncertain", "unknown", "need the requester", "user preference", "cannot determine")
            state.uncertainty = preview if any(marker in lowered for marker in markers) else ""
            self._add_evidence(state, event_type, preview)
        elif event_type == "agent.tool.output":
            tool_name = _compact(payload.get("tool_name", "tool"), 120)
            result = _compact(payload.get("result_preview", ""))
            state.progress = f"Observed bounded result from {tool_name}: {result}"[:1_600]
            state.current_goal = "Use the tool result in the current workstream."
            self._add_evidence(state, event_type, result)
        elif event_type == "jarvis.review.requested":
            review = ReviewRequest.from_event(event)
            if review.session_id != session_id:
                raise DynamicMasContractError("review event crossed session identity")
            state.last_review = review
            summaries = [
                f"{action.tool_name}: {action.params_preview}".strip()
                for action in review.actions
            ]
            state.proposed_action = " | ".join(summaries)[:1_600]
            state.current_goal = "Resolve or release the exact held consequential action batch."
            for summary in summaries:
                self._add_evidence(state, event_type, summary)
        elif event_type == "agent.final.observed":
            # Lifecycle completion is attested by the registry/transcript, not
            # by a provider-attempt hook.  This is only a bounded progress note.
            result = _compact(payload.get("result_preview", ""))
            state.progress = result or "A worker provider attempt ended."
            self._add_evidence(state, event_type, result)
        elif event_type == "agent.model_boundary":
            state.current_goal = "Continue from the current model boundary."
        else:
            return None

        if state.binding.status not in {"completed", "failed", "cancelled"}:
            state.saw_update_before_completion = True
        return self.card(session_id)

