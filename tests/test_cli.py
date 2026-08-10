from __future__ import annotations

from pathlib import Path

import pytest

from jarvisbench.cli import _requester_context_loader, build_parser


def test_private_requester_context_is_bounded_and_lazy(tmp_path: Path):
    source = tmp_path / "requester.json"
    source.write_text('{"preference":"private"}', encoding="utf-8")
    loader = _requester_context_loader(source)
    assert loader() == '{"preference":"private"}'

    oversized = tmp_path / "oversized.json"
    oversized.write_bytes(b"x" * (128 * 1024 + 1))
    with pytest.raises(ValueError):
        _requester_context_loader(oversized)


def test_episode_cli_has_explicit_setting_track_and_models():
    args = build_parser().parse_args(
        [
            "run",
            "--setting",
            "multi_agent",
            "--track",
            "agent_collaboration",
            "--controller",
            "reference",
            "--task-dir",
            "/task",
            "--episode-root",
            "/episode/run",
            "--worker-model",
            "provider/worker",
            "--jarvis-model",
            "provider/jarvis",
            "--user-model",
            "provider/user",
            "--requester-context",
            "/run/secrets/requester.json",
        ]
    )
    assert args.setting == "multi_agent"
    assert args.track == "agent_collaboration"
    assert args.controller == "reference"
    assert args.worker_thinking == "provider_default"
    assert args.jarvis_reasoning == "medium"
