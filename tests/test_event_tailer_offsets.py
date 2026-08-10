from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from jarvisbench.core.decision_ledger import DecisionLedger
from jarvisbench.reference.dynamic_mas.service import DynamicMasService
from jarvisbench.reference.single_agent.control import SingleAgentControlService


def _write_events(path: Path, *names: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps({"type": name}) + "\n" for name in names),
        encoding="utf-8",
    )


class _FailOnceScheduler:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.failed = False

    def sync_registry(self, **_kwargs: Any) -> None:
        return None

    def process_event(self, event: dict[str, Any]) -> None:
        name = str(event["type"])
        self.calls.append(name)
        if name == "first" and not self.failed:
            self.failed = True
            raise RuntimeError("transient scheduler failure")

    def diagnostics(self) -> dict[str, Any]:
        return {"project_id": "project"}


def test_dynamic_tailer_retries_failed_line_without_losing_later_events(
    tmp_path: Path,
) -> None:
    events = tmp_path / "events.jsonl"
    _write_events(events, "before", "first", "second")
    committed_prefix = len((json.dumps({"type": "before"}) + "\n").encode("utf-8"))
    scheduler = _FailOnceScheduler()
    service = DynamicMasService(
        scheduler=scheduler,  # type: ignore[arg-type]
        events_path=events,
        sessions_root=tmp_path / "sessions",
        workspace=tmp_path / "workspace",
        registry_path=tmp_path / "registry.json",
        diagnostics_path=tmp_path / "diagnostics.json",
    )

    with pytest.raises(RuntimeError, match="transient scheduler failure"):
        service.poll_once()
    assert service._offset == committed_prefix
    assert scheduler.calls == ["before", "first"]

    assert service.poll_once() == 2
    assert service._offset == events.stat().st_size
    assert scheduler.calls == ["before", "first", "first", "second"]


def test_single_tailer_retries_failed_line_without_losing_later_events(
    tmp_path: Path,
) -> None:
    events = tmp_path / "events.jsonl"
    _write_events(events, "before", "first", "second")
    committed_prefix = len((json.dumps({"type": "before"}) + "\n").encode("utf-8"))
    service = SingleAgentControlService(
        project_id="project",
        session_id="worker-0",
        session_key="agent:main:worker-0",
        control_root=tmp_path / "control",
        events_path=events,
        registry_path=tmp_path / "registry.json",
        diagnostics_path=tmp_path / "diagnostics.json",
        decision_ledger=DecisionLedger(tmp_path / "ledger.jsonl"),
        review=lambda _candidate: None,  # type: ignore[arg-type,return-value]
    )
    calls: list[str] = []
    failed = False

    def process(event: dict[str, Any]) -> None:
        nonlocal failed
        name = str(event["type"])
        calls.append(name)
        if name == "first" and not failed:
            failed = True
            raise RuntimeError("transient control failure")

    service.process_event = process  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="transient control failure"):
        service.poll_once()
    assert service._offset == committed_prefix
    assert service._event_count == 1
    assert calls == ["before", "first"]

    assert service.poll_once() == 2
    assert service._offset == events.stat().st_size
    assert service._event_count == 3
    assert calls == ["before", "first", "first", "second"]
