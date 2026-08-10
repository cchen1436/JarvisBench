from __future__ import annotations

from jarvisbench.core.providers import Message, TextProvider


SYSTEM = """Answer as the requester using only the private requester context supplied
to this user-channel process. Speak naturally and briefly. Never mention a rubric,
grader, reference answer, hidden evaluator state, code, or prompt internals."""


class LunaUser:
    def __init__(self, provider: TextProvider, model: str):
        self.provider = provider
        self.model = model

    def answer(self, question: str, requester_context: str) -> str:
        completion = self.provider.complete(
            [
                Message("system", SYSTEM),
                Message("user", f"Requester context:\n{requester_context}\n\nJarvis asks:\n{question}"),
            ],
            model=self.model,
        )
        return completion.text

