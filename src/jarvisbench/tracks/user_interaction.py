from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol

from jarvisbench.core.replay import ReplayFrame, choose_early_late, load_bounded_replay


class ReplayResponder(Protocol):
    def answer(self, question: str, visible_frames: tuple[ReplayFrame, ...]) -> tuple[str, float | None]:
        ...


class ReplayQuestioner(Protocol):
    """User-side question generator that never receives worker trace frames."""

    def general(self, checkpoint: str, opening_brief: str) -> str:
        ...

    def follow_up(self, checkpoint: str, question: str, answer: str, opening_brief: str) -> str:
        ...


class DeterministicReplayQuestioner:
    def general(self, checkpoint: str, opening_brief: str) -> str:
        return "How is the work going right now?"

    def follow_up(self, checkpoint: str, question: str, answer: str, opening_brief: str) -> str:
        return f"You said: {answer} What is the most important concrete detail behind that?"


class DeterministicReplayResponder:
    """No-model responder used only for contract and isolation smoke tests."""

    def answer(self, question: str, visible_frames: tuple[ReplayFrame, ...]) -> tuple[str, float]:
        frame = visible_frames[-1]
        goal = frame.current_goal.strip().rstrip(".!?") or "the next declared step"
        return (
            f"The work is {frame.status}. {frame.progress} The current focus is {goal}.",
            0.0,
        )


@dataclass(frozen=True)
class ConversationTurn:
    checkpoint: str
    kind: str
    question: str
    answer: str
    first_token_ms: float | None
    visible_frame_index: int


class UserInteractionTrack:
    """Post-hoc text replay.

    It receives immutable bounded frames and intentionally has no ControlStore,
    worker process, prompt mutation, or artifact write handle.
    """

    name = "user_interaction"

    def __init__(self, responder: ReplayResponder, questioner: ReplayQuestioner | None = None):
        self.responder = responder
        self.questioner = questioner or DeterministicReplayQuestioner()

    def run(
        self,
        replay_path: Path,
        output_path: Path,
        *,
        opening_brief: str = "",
    ) -> list[ConversationTurn]:
        frames = load_bounded_replay(replay_path)
        early, late = choose_early_late(frames)
        turns: list[ConversationTurn] = []
        for label, point in (("early", early), ("late", late)):
            visible = tuple(frame for frame in frames if frame.index <= point.index)
            # The questioner sees the opening conversation and prior reply, but
            # never the private worker trace or the bounded replay frames.
            general = self.questioner.general(label, opening_brief)
            answer, latency = self.responder.answer(general, visible)
            turns.append(ConversationTurn(label, "general", general, answer, latency, point.index))
            followup = self.questioner.follow_up(label, general, answer, opening_brief)
            follow_answer, follow_latency = self.responder.answer(followup, visible)
            turns.append(
                ConversationTurn(label, "follow_up", followup, follow_answer, follow_latency, point.index)
            )
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": "jarvisbench.track2.conversation.v1",
            "source_replay": Path(replay_path).name,
            "turns": [asdict(turn) for turn in turns],
        }
        output_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return turns
