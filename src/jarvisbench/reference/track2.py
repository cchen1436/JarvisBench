from __future__ import annotations

import json

from jarvisbench.core.providers import Message, TextProvider
from jarvisbench.core.replay import ReplayFrame


QUESTION_SYSTEM = """You are the requester checking in on long-running work through
conversational text. Ask one short, natural-language question. You cannot see the agent trace.
Do not ask for code, formulas, logs, prompts, or evaluator information."""

ANSWER_SYSTEM = """You are Jarvis, reporting on ongoing work. Answer the requester's
question accurately from only the bounded status frames supplied to you. Be brief,
natural, and candid about uncertainty. Do not expose raw trace, code, prompts,
private controller state, or evaluator information."""


class ReferenceReplayQuestioner:
    def __init__(self, provider: TextProvider, model: str):
        self.provider = provider
        self.model = model

    def general(self, checkpoint: str, opening_brief: str) -> str:
        response = self.provider.complete(
            [
                Message("system", QUESTION_SYSTEM),
                Message(
                    "user",
                    f"Opening project request:\n{opening_brief}\n\nThis is the {checkpoint} check-in. Ask a general status question.",
                ),
            ],
            model=self.model,
        )
        return response.text.strip()

    def follow_up(self, checkpoint: str, question: str, answer: str, opening_brief: str) -> str:
        response = self.provider.complete(
            [
                Message("system", QUESTION_SYSTEM),
                Message("user", f"You asked: {question}\nJarvis answered: {answer}\nAsk one specific follow-up."),
            ],
            model=self.model,
        )
        return response.text.strip()


class ReferenceReplayResponder:
    def __init__(self, provider: TextProvider, model: str):
        self.provider = provider
        self.model = model

    def answer(self, question: str, visible_frames: tuple[ReplayFrame, ...]) -> tuple[str, float | None]:
        payload = [
            {
                "index": frame.index,
                "elapsed_ms": frame.elapsed_ms,
                "progress": frame.progress,
                "current_goal": frame.current_goal,
                "evidence": list(frame.evidence),
                "status": frame.status,
            }
            for frame in visible_frames
        ]
        response = self.provider.complete(
            [
                Message("system", ANSWER_SYSTEM),
                Message("user", f"Visible bounded updates:\n{json.dumps(payload)}\n\nRequester asks: {question}"),
            ],
            model=self.model,
            reasoning="low",
        )
        return response.text.strip(), response.first_token_ms
