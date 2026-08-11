from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Mapping

from jarvisbench.core.providers import Message, TextProvider


SYSTEM = """You are Luna, a simulated requester in an attention-allocation
benchmark. Use only the requester memories in the packet. You do not see the
worker trace, workspace files, source documents, formulas, code, logs, or tool
payloads. Jarvis should ask only for a requester-owned preference,
authorization, private fact, acceptance judgment, or risk posture.

If the memories settle the question, answer plainly and decisively in natural,
conversational language. Cite every exact memory_id used. Do not add uncertainty
merely because you cannot inspect worker materials. If the memories do not settle
the exact wording but one or more supplied memories directly changes the same
requester-owned outcome named in the question, volunteer only that material
context and clearly state which narrower subpart remains unspecified. Mark that
response partially_answered and cite the memories used; this lets Jarvis transmit the
authoritative part without guessing the rest. Use insufficient_information with
no memory only when no supplied memory answers or materially changes the named
outcome. Never turn a related priority or rationale into a yes/no answer for an
unspecified subpart: say that subpart is unspecified, then state only the directly
supported related context. Never guess or invent a requester fact. Return exactly
one JSON object:
{"answerability":"answered|partially_answered|insufficient_information|question_unclear","user_text":"brief natural answer","disclosed_memory_ids":["exact ids used"]}
Never mention a rubric, grader, reference answer, hidden evaluator state, code,
or prompt internals.
"""

_MEMORY_ID = re.compile(r"user\.[a-z0-9_.-]{1,180}\Z")
_ANSWERABILITY = {
    "answered",
    "partially_answered",
    "insufficient_information",
    "question_unclear",
}
_SPECULATIVE_NONANSWER_MARKERS = (
    "would guess",
    "my guess",
    "probably",
    "i assume",
)


@dataclass(frozen=True)
class LunaAnswerRecord:
    user_text: str
    answerability: str
    disclosed_memory_ids: tuple[str, ...]
    disclosed_memories: tuple[dict[str, Any], ...]


def _memory_suffix(value: str) -> str:
    clean = re.sub(r"[^a-z0-9_.-]+", "_", value.casefold()).strip("_.-")
    return clean[:180]


def _context_memories(requester_context: str) -> tuple[dict[str, Any], ...]:
    try:
        value = json.loads(requester_context)
    except json.JSONDecodeError as exc:
        raise ValueError("requester context must be JSON") from exc
    if not isinstance(value, dict):
        raise ValueError("requester context must be one object")

    raw_memories = value.get("memories")
    memories: list[dict[str, Any]] = []
    if isinstance(raw_memories, list):
        for item in raw_memories:
            if not isinstance(item, dict) or set(item) != {"memory_id", "field", "value"}:
                raise ValueError("requester memory has an invalid schema")
            memory_id = item.get("memory_id")
            field = item.get("field")
            if (
                not isinstance(memory_id, str)
                or not _MEMORY_ID.fullmatch(memory_id)
                or not isinstance(field, str)
                or not field.strip()
            ):
                raise ValueError("requester memory identity is invalid")
            memories.append(
                {"memory_id": memory_id, "field": field, "value": item.get("value")}
            )
    else:
        def visit(item: Any, parts: tuple[str, ...] = ()) -> None:
            if len(memories) >= 128:
                raise ValueError("requester context contains too many memories")
            if isinstance(item, dict):
                for key, child in sorted(item.items()):
                    if not isinstance(key, str):
                        raise ValueError("requester context key must be text")
                    visit(child, (*parts, key))
                return
            if isinstance(item, list):
                for index, child in enumerate(item):
                    visit(child, (*parts, str(index)))
                return
            if item is None or isinstance(item, (bool, int, float, str)):
                field = ".".join(parts)
                suffix = _memory_suffix(field)
                if not suffix:
                    raise ValueError("requester context field cannot form an identity")
                memories.append(
                    {"memory_id": f"user.{suffix}", "field": field, "value": item}
                )
                return
            raise ValueError("requester context contains a non-JSON value")

        visit(value)

    if not memories or len(memories) > 128:
        raise ValueError("requester memory count is outside its bound")
    encoded = json.dumps(memories, ensure_ascii=False, sort_keys=True)
    if len(encoded.encode("utf-8")) > 128 * 1024:
        raise ValueError("requester memories exceed their bound")
    ids = [str(item["memory_id"]) for item in memories]
    if len(ids) != len(set(ids)):
        raise ValueError("requester memory identity was duplicated")
    return tuple(memories)


class LunaUser:
    def __init__(self, provider: TextProvider, model: str):
        self.provider = provider
        self.model = model
        self._prior_exchanges: list[dict[str, str]] = []

    @staticmethod
    def _validate(
        text: str,
        memory_by_id: Mapping[str, dict[str, Any]],
    ) -> LunaAnswerRecord:
        value = json.loads(text)
        if not isinstance(value, dict) or set(value) != {
            "answerability",
            "user_text",
            "disclosed_memory_ids",
        }:
            raise ValueError("Luna response does not match the exact schema")
        answerability = value.get("answerability")
        user_text = value.get("user_text")
        raw_ids = value.get("disclosed_memory_ids")
        if answerability not in _ANSWERABILITY:
            raise ValueError("Luna answerability is invalid")
        if not isinstance(user_text, str) or not user_text.strip() or len(user_text) > 1_400:
            raise ValueError("Luna user text is invalid")
        if (
            not isinstance(raw_ids, list)
            or len(raw_ids) > 12
            or any(not isinstance(item, str) for item in raw_ids)
            or len(raw_ids) != len(set(raw_ids))
            or any(item not in memory_by_id for item in raw_ids)
        ):
            raise ValueError("Luna disclosed memory IDs are invalid")
        if answerability in {"answered", "partially_answered"} and not raw_ids:
            raise ValueError("an answered turn must cite requester memory")
        if answerability in {"insufficient_information", "question_unclear"} and raw_ids:
            raise ValueError("an unanswered turn cannot disclose requester memory")
        if answerability in {"insufficient_information", "question_unclear"} and any(
            marker in user_text.casefold()
            for marker in _SPECULATIVE_NONANSWER_MARKERS
        ):
            raise ValueError("an unanswered turn attempted to guess")
        memory_ids = tuple(raw_ids)
        return LunaAnswerRecord(
            user_text=" ".join(user_text.split()),
            answerability=str(answerability),
            disclosed_memory_ids=memory_ids,
            disclosed_memories=tuple(dict(memory_by_id[item]) for item in memory_ids),
        )

    def answer_with_record(
        self,
        question: str,
        requester_context: str,
    ) -> LunaAnswerRecord:
        if not question.strip() or len(question) > 2_000:
            raise ValueError("Jarvis question is outside its bound")
        memories = _context_memories(requester_context)
        memory_by_id = {str(item["memory_id"]): item for item in memories}
        packet: dict[str, Any] = {
            "requester_memories": list(memories),
            "prior_exchanges": [dict(item) for item in self._prior_exchanges[-4:]],
            "jarvis_question": question,
        }
        last_error: Exception | None = None
        for attempt in range(2):
            attempt_packet = dict(packet)
            if attempt:
                attempt_packet["retry_instruction"] = (
                    "The prior response failed the exact schema. Return only a corrected "
                    "JSON object; cite only supplied exact memory IDs and never guess."
                )
            completion = self.provider.complete(
                [
                    Message("system", SYSTEM),
                    Message("user", json.dumps(attempt_packet, ensure_ascii=False, sort_keys=True)),
                ],
                model=self.model,
            )
            try:
                record = self._validate(completion.text, memory_by_id)
            except (json.JSONDecodeError, TypeError, ValueError) as exc:
                last_error = exc
                continue
            self._prior_exchanges.append(
                {
                    "jarvis_question": question,
                    "user_text": record.user_text,
                    "answerability": record.answerability,
                }
            )
            return record
        raise ValueError("Luna response failed its bounded contract") from last_error

    def answer(self, question: str, requester_context: str) -> str:
        return self.answer_with_record(question, requester_context).user_text
