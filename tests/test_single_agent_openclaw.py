from __future__ import annotations

import json
from pathlib import Path

import pytest

from jarvisbench.core.controller import AttentionDecision
from jarvisbench.reference.single_agent import ReferenceSingleAgentControllerAdapter
from jarvisbench.settings.single_agent_openclaw import (
    OpenClawSingleAgentConfig,
    OpenClawSingleAgentWorker,
)
from jarvisbench.settings.single_agent_runtime import SingleAgentRunner


def _task(root: Path) -> Path:
    task = root / "single_fixture"
    public = task / "public"
    public.mkdir(parents=True)
    (public / "output_contract.json").write_text("{}\n", encoding="utf-8")
    (public / "result_schema.json").write_text("{}\n", encoding="utf-8")
    (task / "task.public.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "task_id": "single_fixture",
                "title": "Fixture",
                "summary": "Fixture",
                "domain": "test",
                "episode": {
                    "brief": "Write the fixture result.",
                    "result_paths": ["results/final.json"],
                },
                "runtime": {
                    "worker_timeout_seconds": 30,
                    "family": "openclaw_workspace_v1",
                },
                "baseline": {"user_availability": "unavailable_after_brief"},
                "assets": {
                    "public": [
                        "public/output_contract.json",
                        "public/result_schema.json",
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    return task


def _fake_openclaw(path: Path) -> None:
    path.write_text(
        r'''#!/usr/bin/env python3
import hashlib, json, os, signal, sys, time
from pathlib import Path

calls = Path(os.environ["FAKE_OPENCLAW_CALLS"])
with calls.open("a", encoding="utf-8") as stream:
    stream.write(json.dumps(sys.argv[1:]) + "\n")
args = sys.argv[1:]
if args[:2] in (["models", "set"], ["config", "set"]):
    raise SystemExit(0)
if args[:2] == ["gateway", "health"]:
    raise SystemExit(0)
if args[:2] == ["gateway", "run"]:
    ready = Path(os.environ["JARVIS_MAS_PLUGIN_READY_JSON"])
    ready.write_text(json.dumps({
        "schema_version": "1.0",
        "ready": True,
        "plugin_id": "jarvisbench-mas-supervisor",
        "control_protocol_version": "1.0-release",
        "project_id": os.environ["JARVIS_MAS_PROJECT_ID"],
        "hooks_registration_complete": True,
    }))
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    while True:
        time.sleep(0.05)
if args and args[0] == "agent":
    session_id = args[args.index("--session-id") + 1]
    assert session_id == "worker-0"
    assert "--local" not in args
    project_id = os.environ["JARVIS_MAS_PROJECT_ID"]
    control_root = Path(os.environ["JARVIS_MAS_CONTROL_ROOT"])
    namespace = hashlib.sha256(session_id.encode()).hexdigest()
    control = control_root / "sessions" / namespace / "control.json"
    state = json.loads(control.read_text())
    events = Path(os.environ["JARVIS_HOOK_EVENTS_JSONL"])
    identity = {
        "project_id": project_id,
        "agent_id": "worker-0",
        "session_id": session_id,
        "parent_id": "project-root",
        "role": "worker",
        "turn_id": "turn-1",
        "batch_id": "batch-none",
        "action_id": "llm-output",
    }
    records = [
        {
            "seq": 1,
            "event_id": "event-1",
            "ts": "2026-01-01T00:00:00+00:00",
            "type": "agent.llm.output",
            "payload": {**identity, "assistant_preview": "Need the requester choice before writing."},
        },
        {
            "seq": 2,
            "event_id": "event-2",
            "ts": "2026-01-01T00:00:01+00:00",
            "type": "jarvis.review.requested",
            "payload": {
                **identity,
                "run_id": "run-1",
                "batch_id": "batch-1",
                "action_id": "action-1",
                "review_id": "review-1",
                "control_epoch": state["control_epoch"],
                "nonce": state["nonce"],
                "expected_event_seq": 2,
                "held_actions": [{
                    "action_id": "action-1",
                    "tool_call_id": "call-1",
                    "tool_name": "write",
                    "action_fingerprint": "a" * 64,
                    "params_sha256": "b" * 64,
                    "params_preview": "write results/final.json",
                    "artifact_paths": ["results/final.json"],
                }],
            },
        },
    ]
    with events.open("a", encoding="utf-8") as stream:
        for record in records:
            stream.write(json.dumps(record) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
    response = None
    for _ in range(250):
        state = json.loads(control.read_text())
        response = next((item for item in state["review_responses"] if item["review_id"] == "review-1"), None)
        if response:
            break
        time.sleep(0.02)
    assert response and response["decision"] == "interrupt_replan"
    application = {
        "seq": 3,
        "event_id": "event-3",
        "ts": "2026-01-01T00:00:02+00:00",
        "type": "control.guidance.applied",
        "payload": {
            **identity,
            "action_id": "guidance-application",
            "delivery_receipt_id": response["delivery_receipt_id"],
            "decision_id": response["decision_id"],
            "model_boundary_id": "tool-continuation-1",
            "control_epoch": response["next_control_epoch"],
            "nonce": response["next_nonce"],
            "guidance_sha256": response["guidance_sha256"],
        },
    }
    with events.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(application) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    workspace = Path(os.environ["JB_WORKSPACE"])
    result = workspace / "results" / "final.json"
    result.write_text('{"ok":true}\n', encoding="utf-8")
    sessions = Path(os.environ["OPENCLAW_STATE_DIR"]) / "agents" / "main" / "sessions"
    sessions.mkdir(parents=True, exist_ok=True)
    (sessions / "worker-0.jsonl").write_text(
        json.dumps({"message": {"role": "assistant", "stopReason": "stop"}}) + "\n"
    )
    raise SystemExit(0)
raise SystemExit(2)
''',
        encoding="utf-8",
    )
    path.chmod(0o755)


def test_single_reference_transport_closes_delivery_and_application_receipts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = tmp_path / "fake-openclaw"
    _fake_openclaw(fake)
    calls = tmp_path / "calls.jsonl"
    monkeypatch.setenv("FAKE_OPENCLAW_CALLS", str(calls))
    monkeypatch.setenv("FAKE_WORKER_KEY", "private-test-key")

    class Controller:
        def decide(self, _candidate):
            return AttentionDecision(
                True,
                "requester-owned output choice",
                "Which output option should be used?",
                "worker",
            )

    class Requester:
        calls = 0

        def answer(self, question: str, context: str) -> str:
            self.calls += 1
            assert "output option" in question
            assert context == '{"choice":"B"}'
            return "Use option B."

    requester = Requester()
    worker = OpenClawSingleAgentWorker(
        OpenClawSingleAgentConfig(
            worker_model="provider/model",
            provider_base_url="https://provider.invalid/v1",
            api_key_env="FAKE_WORKER_KEY",
            openclaw_executable=str(fake),
            runtime_root=tmp_path / "runtime",
            environment_passthrough=("FAKE_OPENCLAW_CALLS",),
            poll_seconds=0.025,
            project_id="single-project",
        )
    )
    result = SingleAgentRunner(
        worker,
        ReferenceSingleAgentControllerAdapter(Controller()),
        requester=requester,
        requester_context_loader=lambda: '{"choice":"B"}',
        max_attention_requests=2,
    ).run(_task(tmp_path), tmp_path / "runs", run_id="single-reference")
    assert result.status == "completed"
    assert result.attention_request_count == 1
    assert requester.calls == 1
    episode = result.episode_root
    records = [
        json.loads(line)
        for line in (episode / "private" / "requester" / "decision_ledger.jsonl")
        .read_text()
        .splitlines()
    ]
    decision_ids = {record["decision_id"] for record in records}
    assert len(decision_ids) == 1
    assert {record.get("kind", "decision") for record in records} == {
        "decision",
        "guidance_delivery",
        "guidance_application",
    }
    archived = episode / "private" / "runtime"
    assert (archived / "sessions" / "worker-0.jsonl").is_file()
    registry = json.loads((archived / "control" / "registry.json").read_text())
    assert set(registry["sessions"]) == {"worker-0"}
    assert registry["parent_session_id"] == "no-parent"
    control_path = next((archived / "control" / "sessions").glob("*/control.json"))
    control = json.loads(control_path.read_text())
    assert len(control["delivery_receipts"]) == 1
    assert len(control["application_receipts"]) == 1
    assert control["guidance_queue"] == []
    assert (episode / "bounded_control_trace.jsonl").is_file()
    assert b"private-test-key" not in b"".join(
        path.read_bytes() for path in archived.rglob("*") if path.is_file()
    )
    recorded = [json.loads(line) for line in calls.read_text().splitlines()]
    assert any(call[:2] == ["gateway", "run"] for call in recorded)
    assert not any("--local" in call for call in recorded)
