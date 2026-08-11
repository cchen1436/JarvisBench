from __future__ import annotations

import json

import pytest

from jarvisbench.core.providers import Completion
from jarvisbench.reference.luna import LunaUser


class _SequenceProvider:
    def __init__(self, responses: list[object]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[object, ...]] = []

    def complete(self, messages, *, model: str, reasoning: str | None = None):
        self.calls.append(tuple(messages))
        value = self.responses.pop(0)
        text = value if isinstance(value, str) else json.dumps(value)
        return Completion(text, 1.0, 2.0, model)


class _EchoProvider:
    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []

    def complete(self, messages, *, model: str, reasoning: str | None = None):
        self.calls.append(tuple(messages))
        index = len(self.calls)
        return Completion(
            json.dumps(
                {
                    "answerability": "answered",
                    "user_text": f"Requester answer {index}.",
                    "disclosed_memory_ids": ["user.choice"],
                }
            ),
            1.0,
            2.0,
            model,
        )


CONTEXT = json.dumps(
    {
        "choice": "Use the safer reversible option.",
        "preferences": {"risk": "Avoid an irreversible commitment."},
    }
)


def _answered(memory_id: str = "user.choice") -> dict[str, object]:
    return {
        "answerability": "answered",
        "user_text": "Please use the safer reversible option.",
        "disclosed_memory_ids": [memory_id],
    }


def test_luna_answer_record_resolves_only_cited_exact_memory_ids() -> None:
    provider = _SequenceProvider([_answered()])
    luna = LunaUser(provider, model="provider/luna")

    record = luna.answer_with_record("Which option do you prefer?", CONTEXT)

    assert record.user_text == "Please use the safer reversible option."
    assert record.answerability == "answered"
    assert record.disclosed_memory_ids == ("user.choice",)
    assert record.disclosed_memories == (
        {
            "memory_id": "user.choice",
            "field": "choice",
            "value": "Use the safer reversible option.",
        },
    )
    packet = json.loads(provider.calls[0][-1].content)
    system_prompt = " ".join(provider.calls[0][0].content.split())
    assert "materially changes the named outcome" in system_prompt
    assert "Never turn a related priority or rationale into a yes/no answer" in (
        system_prompt
    )
    assert {item["memory_id"] for item in packet["requester_memories"]} == {
        "user.choice",
        "user.preferences.risk",
    }


def test_luna_preserves_an_explicit_exact_user_memory_schema() -> None:
    context = json.dumps(
        {
            "memories": [
                {
                    "memory_id": "user.release_posture",
                    "field": "release_posture",
                    "value": "Keep the change reversible.",
                }
            ]
        }
    )
    provider = _SequenceProvider([_answered("user.release_posture")])

    record = LunaUser(provider, model="provider/luna").answer_with_record(
        "What is your release posture?", context
    )

    assert record.disclosed_memory_ids == ("user.release_posture",)
    assert record.disclosed_memories[0]["value"] == "Keep the change reversible."


def test_luna_accepts_bounded_partial_context_without_guessing_the_rest() -> None:
    provider = _SequenceProvider(
        [
            {
                "answerability": "partially_answered",
                "user_text": (
                    "I have not specified the owner. Separately, use the safer "
                    "reversible option."
                ),
                "disclosed_memory_ids": ["user.choice"],
            }
        ]
    )

    record = LunaUser(provider, model="provider/luna").answer_with_record(
        "Who owns the selected option?", CONTEXT
    )

    assert record.answerability == "partially_answered"
    assert record.disclosed_memory_ids == ("user.choice",)


@pytest.mark.parametrize(
    "response",
    [
        {
            **_answered(),
            "disclosed_memories": ["model-authored text is forbidden"],
        },
        {**_answered(), "disclosed_memory_ids": []},
        _answered("user.unknown"),
        {
            "answerability": "insufficient_information",
            "user_text": "I do not know, but I would guess the safer option.",
            "disclosed_memory_ids": ["user.choice"],
        },
        {
            "answerability": "insufficient_information",
            "user_text": "I do not know, but I would guess the safer option.",
            "disclosed_memory_ids": [],
        },
    ],
)
def test_luna_malformed_or_unverifiable_answers_fail_closed_after_two_attempts(
    response: dict[str, object],
) -> None:
    provider = _SequenceProvider([response, response])
    luna = LunaUser(provider, model="provider/luna")

    with pytest.raises(ValueError, match="failed its bounded contract"):
        luna.answer_with_record("Which option do you prefer?", CONTEXT)

    assert len(provider.calls) == 2
    first_packet = json.loads(provider.calls[0][-1].content)
    retry_packet = json.loads(provider.calls[1][-1].content)
    assert "retry_instruction" not in first_packet
    assert "retry_instruction" in retry_packet


def test_luna_accepts_a_valid_second_schema_attempt() -> None:
    provider = _SequenceProvider(
        [
            _answered("user.not_supplied"),
            _answered("user.preferences.risk"),
        ]
    )

    record = LunaUser(provider, model="provider/luna").answer_with_record(
        "How much irreversible risk should I take?", CONTEXT
    )

    assert len(provider.calls) == 2
    assert record.disclosed_memory_ids == ("user.preferences.risk",)
    assert record.disclosed_memories[0]["field"] == "preferences.risk"


def test_luna_prior_exchange_packet_is_bounded_to_four_successful_turns() -> None:
    provider = _EchoProvider()
    luna = LunaUser(provider, model="provider/luna")

    for index in range(6):
        assert luna.answer(f"Requester question {index}?", CONTEXT).startswith(
            "Requester answer"
        )

    final_packet = json.loads(provider.calls[-1][-1].content)
    prior = final_packet["prior_exchanges"]
    assert len(prior) == 4
    assert [item["jarvis_question"] for item in prior] == [
        "Requester question 1?",
        "Requester question 2?",
        "Requester question 3?",
        "Requester question 4?",
    ]
    assert all(set(item) == {"jarvis_question", "user_text", "answerability"} for item in prior)
