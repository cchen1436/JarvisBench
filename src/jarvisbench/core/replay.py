from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


MAX_REPLAY_FILE_BYTES = 8 * 1024 * 1024
MAX_REPLAY_LINE_BYTES = 64 * 1024
MAX_REPLAY_FRAMES = 4_096
MAX_PROGRESS_CHARS = 2_000
MAX_GOAL_CHARS = 1_000
MAX_EVIDENCE_ITEMS = 8
MAX_EVIDENCE_CHARS = 1_000
MAX_STATUS_CHARS = 200
REPLAY_FIELDS = frozenset(
    {"index", "elapsed_ms", "progress", "current_goal", "evidence", "status"}
)


@dataclass(frozen=True)
class ReplayFrame:
    index: int
    elapsed_ms: int
    progress: str
    current_goal: str
    evidence: tuple[str, ...]
    status: str


def load_bounded_replay(path: Path) -> list[ReplayFrame]:
    source = Path(path)
    if source.is_symlink() or not source.is_file():
        raise ValueError("replay must be a regular file")
    size = source.stat().st_size
    if size > MAX_REPLAY_FILE_BYTES:
        raise ValueError("replay exceeds its file-size bound")
    raw = source.read_bytes()
    if len(raw) != size or len(raw) > MAX_REPLAY_FILE_BYTES:
        raise ValueError("replay changed while it was read")
    try:
        lines = raw.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise ValueError("replay must be strict UTF-8 JSONL") from exc

    frames: list[ReplayFrame] = []
    for line_no, line in enumerate(lines, 1):
        if not line.strip():
            continue
        if len(line.encode("utf-8")) > MAX_REPLAY_LINE_BYTES:
            raise ValueError(f"replay line {line_no} exceeds its byte bound")
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"replay line {line_no} is not valid JSON") from exc
        if not isinstance(value, dict) or set(value) != REPLAY_FIELDS:
            raise ValueError(f"replay line {line_no} violates the exact frame schema")

        index = value["index"]
        elapsed_ms = value["elapsed_ms"]
        progress = value["progress"]
        current_goal = value["current_goal"]
        evidence = value["evidence"]
        status = value["status"]
        if type(index) is not int or index < 0:
            raise ValueError(f"replay line {line_no} has an invalid frame index")
        if type(elapsed_ms) is not int or elapsed_ms < 0:
            raise ValueError(f"replay line {line_no} has an invalid elapsed time")
        if not isinstance(progress, str) or len(progress) > MAX_PROGRESS_CHARS:
            raise ValueError(f"replay line {line_no} has unbounded progress")
        if not isinstance(current_goal, str) or len(current_goal) > MAX_GOAL_CHARS:
            raise ValueError(f"replay line {line_no} has an unbounded current goal")
        if (
            not isinstance(evidence, list)
            or len(evidence) > MAX_EVIDENCE_ITEMS
            or any(
                not isinstance(item, str) or len(item) > MAX_EVIDENCE_CHARS
                for item in evidence
            )
        ):
            raise ValueError(f"replay line {line_no} has unbounded evidence")
        if not isinstance(status, str) or len(status) > MAX_STATUS_CHARS:
            raise ValueError(f"replay line {line_no} has an invalid status")

        frames.append(
            ReplayFrame(
                index=index,
                elapsed_ms=elapsed_ms,
                progress=progress,
                current_goal=current_goal,
                evidence=tuple(evidence),
                status=status,
            )
        )
        if len(frames) > MAX_REPLAY_FRAMES:
            raise ValueError("replay exceeds its frame-count bound")

    if len(frames) < 4:
        raise ValueError("a Track 2 replay needs at least four bounded frames")
    if any(b.index <= a.index for a, b in zip(frames, frames[1:])):
        raise ValueError("replay frame indexes must be strictly increasing")
    if any(b.elapsed_ms <= a.elapsed_ms for a, b in zip(frames, frames[1:])):
        raise ValueError("replay time must be strictly increasing")
    return frames


def choose_early_late(frames: Iterable[ReplayFrame]) -> tuple[ReplayFrame, ReplayFrame]:
    values = list(frames)
    # Quantiles are deterministic and independent of task identity.
    early = values[max(1, round((len(values) - 1) * 0.25))]
    late = values[min(len(values) - 2, round((len(values) - 1) * 0.75))]
    if early.index >= late.index:
        raise ValueError("replay is too short to separate early and late checkpoints")
    return early, late
