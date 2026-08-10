from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from jarvisbench.core import replay


def _frames() -> list[dict[str, object]]:
    return [
        {
            "index": index,
            "elapsed_ms": index * 1_000,
            "progress": f"progress {index}",
            "current_goal": f"goal {index}",
            "evidence": [f"evidence {index}"],
            "status": "in progress",
        }
        for index in range(4)
    ]


def _write(path: Path, frames: list[dict[str, object]]) -> None:
    path.write_text(
        "".join(json.dumps(frame, separators=(",", ":")) + "\n" for frame in frames),
        encoding="utf-8",
    )


def test_replay_loader_accepts_the_exact_bounded_contract(tmp_path: Path) -> None:
    source = tmp_path / "replay.jsonl"
    _write(source, _frames())

    loaded = replay.load_bounded_replay(source)

    assert [frame.index for frame in loaded] == [0, 1, 2, 3]
    assert loaded[-1].status == "in progress"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("index", True),
        ("index", -1),
        ("elapsed_ms", 1.5),
        ("elapsed_ms", -1),
        ("progress", "x" * (replay.MAX_PROGRESS_CHARS + 1)),
        ("current_goal", "x" * (replay.MAX_GOAL_CHARS + 1)),
        ("evidence", ["ok"] * (replay.MAX_EVIDENCE_ITEMS + 1)),
        ("evidence", ["x" * (replay.MAX_EVIDENCE_CHARS + 1)]),
        ("evidence", [{"not": "text"}]),
        ("status", "x" * (replay.MAX_STATUS_CHARS + 1)),
        ("status", 7),
    ],
)
def test_replay_loader_rejects_wrong_types_and_unbounded_fields(
    tmp_path: Path, field: str, value: object
) -> None:
    frames = deepcopy(_frames())
    frames[1][field] = value
    source = tmp_path / "replay.jsonl"
    _write(source, frames)

    with pytest.raises(ValueError):
        replay.load_bounded_replay(source)


def test_replay_loader_requires_exact_fields_and_increasing_indexes(tmp_path: Path) -> None:
    missing = _frames()
    del missing[0]["status"]
    missing_path = tmp_path / "missing.jsonl"
    _write(missing_path, missing)
    with pytest.raises(ValueError, match="exact frame schema"):
        replay.load_bounded_replay(missing_path)

    extra = _frames()
    extra[0]["raw_trace"] = "must not enter replay"
    extra_path = tmp_path / "extra.jsonl"
    _write(extra_path, extra)
    with pytest.raises(ValueError, match="exact frame schema"):
        replay.load_bounded_replay(extra_path)

    unordered = _frames()
    unordered[2]["index"] = 1
    unordered_path = tmp_path / "unordered.jsonl"
    _write(unordered_path, unordered)
    with pytest.raises(ValueError, match="indexes must be strictly increasing"):
        replay.load_bounded_replay(unordered_path)


def test_replay_loader_rejects_oversized_files_and_symlinks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(replay, "MAX_REPLAY_FILE_BYTES", 32)
    oversized = tmp_path / "oversized.jsonl"
    oversized.write_bytes(b"x" * 33)
    with pytest.raises(ValueError, match="file-size bound"):
        replay.load_bounded_replay(oversized)

    target = tmp_path / "target.jsonl"
    _write(target, _frames())
    link = tmp_path / "linked.jsonl"
    link.symlink_to(target)
    with pytest.raises(ValueError, match="regular file"):
        replay.load_bounded_replay(link)
