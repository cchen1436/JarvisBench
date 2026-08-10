from __future__ import annotations

import json
import hashlib
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import pytest

from jarvisbench.core.controller import AttentionDecision
from jarvisbench.core.decision_ledger import DecisionLedger
from jarvisbench.reference.dynamic_mas.contracts import (
    CANONICAL_DYNAMIC_MAS_SOURCE_SHA256,
    HeldAction,
    ReviewRequest,
    SessionBinding,
    StaleReviewError,
)
from jarvisbench.reference.dynamic_mas.control_plane import SessionControlPlane
from jarvisbench.reference.dynamic_mas.registry import DynamicChildRegistry
from jarvisbench.reference.dynamic_mas.scheduler import DynamicMasScheduler
from jarvisbench.settings.multi_agent_runtime import (
    MultiAgentRuntime,
    MultiAgentRuntimeConfig,
    build_delegation_prompt,
    build_integration_prompt,
)


def _task(root: Path) -> Path:
    task = root / "task"
    public = task / "public"
    public.mkdir(parents=True)
    (public / "workstreams.json").write_text(
        json.dumps(
            {
                "workstreams": [
                    {"id": "analysis", "brief": "analyze"},
                    {"id": "delivery", "brief": "deliver"},
                ]
            }
        ),
        encoding="utf-8",
    )
    (public / "output_contract.json").write_text("{}\n", encoding="utf-8")
    (public / "result_schema.json").write_text("{}\n", encoding="utf-8")
    (task / "task.public.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "task_id": "fixture_mas",
                "title": "Fixture",
                "summary": "Fixture",
                "domain": "test",
                "episode": {
                    "brief": "Prepare the fixture project.",
                    "worker_count": 2,
                    "result_paths": [
                        "results/analysis/result.txt",
                        "results/final.txt",
                    ],
                },
                "runtime": {
                    "worker_timeout_seconds": 30,
                    "family": "openclaw_mas_workspace_v1",
                },
                "baseline": {"user_availability": "unavailable_after_brief"},
                "assets": {"public": ["public/workstreams.json"]},
            }
        ),
        encoding="utf-8",
    )
    return task


def _binding(
    project_id: str,
    *,
    agent: str,
    session: str,
    workstream: str,
    role: str = "worker",
    status: str = "active",
) -> SessionBinding:
    return SessionBinding(
        project_id=project_id,
        agent_id=agent,
        session_id=session,
        session_key=("agent:main:chat" if role == "parent" else f"agent:main:subagent:{session}"),
        parent_id="parent",
        parent_session_id="chat",
        parent_session_key="agent:main:chat",
        role=role,
        workstream_id=workstream,
        status=status,
    )


def _review(project_id: str, session_id: str, epoch: int, nonce: str) -> ReviewRequest:
    return ReviewRequest(
        project_id=project_id,
        run_id="run-1",
        session_id=session_id,
        turn_id="turn-1",
        batch_id=f"batch-{session_id}",
        review_id=f"review-{session_id}",
        control_epoch=epoch,
        nonce=nonce,
        expected_event_seq=1,
        actions=(
            HeldAction(
                action_id=f"action-{session_id}",
                tool_call_id=f"call-{session_id}",
                tool_name="write",
                action_fingerprint="a" * 64,
                params_sha256="b" * 64,
                params_preview="write a consequential project artifact",
                artifact_paths=(f"results/{session_id}/result.txt",),
            ),
        ),
    )


def test_runtime_plan_preserves_native_gateway_protocol(tmp_path: Path):
    runtime = MultiAgentRuntime(
        MultiAgentRuntimeConfig(
            task_dir=_task(tmp_path),
            episode_root=tmp_path / "episode",
            worker_model="provider/model",
        )
    )
    plan = runtime.plan()
    assert plan.protocol == (
        "parent_delegation",
        "children_complete",
        "parent_integration",
    )
    assert plan.parent_session_id == "chat"
    assert plan.native_children is True
    assert plan.local_agent_mode is False
    assert plan.canonical_dynamic_source_sha256 == CANONICAL_DYNAMIC_MAS_SOURCE_SHA256
    assert "sessions_spawn" in build_delegation_prompt(runtime.manifest)
    assert "same project" in build_integration_prompt(runtime.manifest)


def test_reference_storage_construction_is_side_effect_free(tmp_path: Path):
    project_id = "project-lazy-storage"
    control_root = tmp_path / "episode" / "private" / "control"
    ledger_path = tmp_path / "episode" / "private" / "requester" / "ledger.jsonl"
    SessionControlPlane(control_root, project_id=project_id)
    DecisionLedger(ledger_path)
    assert not (tmp_path / "episode").exists()


def test_prepare_keeps_episode_workspace_writable_with_read_only_public_assets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    task = _task(tmp_path)
    for path in (task / "public").rglob("*"):
        path.chmod(0o555 if path.is_dir() else 0o444)
    (task / "public").chmod(0o555)
    monkeypatch.setenv("FAKE_WORKER_KEY", "private-test-key")
    runtime = MultiAgentRuntime(
        MultiAgentRuntimeConfig(
            task_dir=task,
            episode_root=tmp_path / "episode",
            worker_model="provider/model",
            provider_base_url="https://provider.invalid/v1",
            api_key_env="FAKE_WORKER_KEY",
        )
    )
    runtime._prepare()
    assert (runtime.workspace / "AGENTS.md").is_file()
    assert runtime.workspace.stat().st_mode & 0o700 == 0o700
    assert (runtime.workspace / "workstreams.json").stat().st_mode & 0o222 == 0


def test_representative_worker_prompts_match_frozen_canonical_hashes():
    task = (
        Path(__file__).resolve().parents[1]
        / "tasks"
        / "multi_agent"
        / "jbmav1_clinical_handoff"
        / "task.public.json"
    )
    manifest = json.loads(task.read_text(encoding="utf-8"))
    assert hashlib.sha256(build_delegation_prompt(manifest).encode()).hexdigest() == (
        "005534947ad97608826a51f47df28e7935265067523f02faa65341a4d44ed6ef"
    )
    assert hashlib.sha256(build_integration_prompt(manifest).encode()).hexdigest() == (
        "a29264858b8288665f93f163fe981e948be1aac5f32c7a92559b0589f18cc253"
    )


def test_random_gateway_port_is_written_to_openclaw_config_commands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    runtime = MultiAgentRuntime(
        MultiAgentRuntimeConfig(
            task_dir=_task(tmp_path),
            episode_root=tmp_path / "episode",
            worker_model="provider/model",
        )
    )
    runtime.workspace.mkdir(parents=True)
    calls: list[list[str]] = []

    class Completed:
        returncode = 0

    def fake_run(command, **_kwargs):
        calls.append(list(command))
        return Completed()

    monkeypatch.setattr("subprocess.run", fake_run)
    runtime._configure_openclaw({}, gateway_port=43127)
    settings = {tuple(call[-2:]) for call in calls if call[1:3] == ["config", "set"]}
    assert ("gateway.mode", "local") in settings
    assert ("gateway.bind", "loopback") in settings
    assert ("gateway.port", "43127") in settings
    assert ("gateway.auth.mode", "none") in settings


def test_openclaw_state_and_config_are_exactly_episode_local(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    # A release image may define global OpenClaw paths.  The episode runner
    # must override every relevant precedence layer, not merely HOME.
    monkeypatch.setenv("HOME", "/image-global/home")
    monkeypatch.setenv("OPENCLAW_HOME", "/image-global/openclaw-home")
    monkeypatch.setenv("OPENCLAW_STATE_DIR", "/image-global/state")
    monkeypatch.setenv("OPENCLAW_CONFIG_PATH", "/image-global/openclaw.json")
    runtime_a = MultiAgentRuntime(
        MultiAgentRuntimeConfig(
            task_dir=_task(tmp_path),
            episode_root=tmp_path / "episode-a",
            worker_model="provider/model",
        )
    )
    runtime_b = MultiAgentRuntime(
        MultiAgentRuntimeConfig(
            task_dir=tmp_path / "task",
            episode_root=tmp_path / "episode-b",
            worker_model="provider/model",
        )
    )
    env_a = runtime_a._environment()
    env_b = runtime_b._environment()
    assert env_a["HOME"] == str(runtime_a.home)
    assert env_a["OPENCLAW_HOME"] == str(runtime_a.home)
    assert env_a["OPENCLAW_STATE_DIR"] == str(runtime_a.openclaw_home)
    assert env_a["OPENCLAW_CONFIG_PATH"] == str(
        runtime_a.openclaw_home / "openclaw.json"
    )
    assert runtime_a.sessions_root == (
        Path(env_a["OPENCLAW_STATE_DIR"]) / "agents" / "main" / "sessions"
    )
    assert env_a["OPENCLAW_STATE_DIR"] != env_b["OPENCLAW_STATE_DIR"]
    assert not any("/image-global" in env_a[name] for name in (
        "HOME",
        "OPENCLAW_HOME",
        "OPENCLAW_STATE_DIR",
        "OPENCLAW_CONFIG_PATH",
    ))


def test_session_control_isolation_and_stale_batch_invalidation(tmp_path: Path):
    project_id = "project-1"
    store = SessionControlPlane(tmp_path / "control", project_id=project_id)
    child_a = _binding(project_id, agent="worker-1", session="child-a", workstream="analysis")
    child_b = _binding(project_id, agent="worker-2", session="child-b", workstream="delivery")
    state_a = store.register(child_a)
    state_b = store.register(child_b)
    review_a = _review(project_id, "child-a", state_a["control_epoch"], state_a["nonce"])
    review_b = _review(project_id, "child-b", state_b["control_epoch"], state_b["nonce"])
    store.hold(review_a)
    store.hold(review_b)
    store.interrupt(review_a, decision_id="decision-a", guidance="Use the requester choice.")
    assert store.read("child-a")["control_epoch"] == 1
    assert store.read("child-b")["control_epoch"] == 0
    assert store.read("child-b")["active_review"]["review_id"] == "review-child-b"
    with pytest.raises(StaleReviewError):
        store.allow(review_a)
    store.allow(review_b)


def test_child_is_registered_and_reduced_before_completion(tmp_path: Path):
    project_id = "project-1"
    sessions = tmp_path / "sessions"
    workspace = tmp_path / "workspace"
    sessions.mkdir()
    workspace.mkdir()
    (workspace / "workstreams.json").write_text(
        json.dumps({"workstreams": [{"id": "analysis"}, {"id": "delivery"}]}),
        encoding="utf-8",
    )
    (sessions / "sessions.json").write_text(
        json.dumps(
            {
                "agent:main:subagent:a": {
                    "sessionId": "child-a",
                    "spawnedBy": "agent:main:chat",
                    "label": "analysis",
                }
            }
        ),
        encoding="utf-8",
    )
    registry = DynamicChildRegistry(project_id=project_id)
    snapshot = registry.sync(
        sessions_root=sessions,
        workspace=workspace,
        output=tmp_path / "control" / "registry.json",
    )
    assert snapshot["sessions"]["child-a"]["status"] == "active"

    class NeverController:
        def decide(self, _candidate):
            return AttentionDecision(False, "unused")

    class NeverRequester:
        def answer(self, _question: str, _context: str) -> str:
            raise AssertionError("requester should not be called")

    scheduler = DynamicMasScheduler(
        project_id=project_id,
        controller=NeverController(),
        requester=NeverRequester(),
        requester_context_loader=lambda: "",
        registry=registry,
        control_plane=SessionControlPlane(tmp_path / "state", project_id=project_id),
        decision_ledger=DecisionLedger(tmp_path / "private" / "ledger.jsonl"),
    )
    for raw in snapshot["sessions"].values():
        scheduler.register(SessionBinding(**raw))
    scheduler.process_event(
        {
            "seq": 1,
            "type": "agent.llm.output",
            "payload": {
                "project_id": project_id,
                "session_id": "child-a",
                "assistant_preview": "I found one bounded public inconsistency before writing.",
            },
        }
    )
    assert scheduler.reducer.saw_live_update("child-a") is True
    assert scheduler.reducer.card("child-a")["raw_trace_included"] is False
    (sessions / "child-a.jsonl").write_text(
        json.dumps({"message": {"role": "assistant", "stopReason": "stop"}}) + "\n",
        encoding="utf-8",
    )
    completed = registry.sync(
        sessions_root=sessions,
        workspace=workspace,
        output=tmp_path / "control" / "registry.json",
    )
    assert completed["sessions"]["child-a"]["status"] == "completed"


def test_scheduler_targets_one_child_and_routes_project_scope_to_parent(tmp_path: Path):
    project_id = "project-1"
    registry = DynamicChildRegistry(project_id=project_id)
    store = SessionControlPlane(tmp_path / "control", project_id=project_id)

    class Controller:
        def decide(self, _candidate):
            return AttentionDecision(
                True,
                "requester-owned project boundary",
                "Which bounded option should we use?",
                "project",
            )

    class Requester:
        calls = 0

        def answer(self, question: str, requester_context: str) -> str:
            self.calls += 1
            assert "bounded option" in question
            assert requester_context == "private requester context"
            return "Use option B."

    requester = Requester()
    scheduler = DynamicMasScheduler(
        project_id=project_id,
        controller=Controller(),
        requester=requester,
        requester_context_loader=lambda: "private requester context",
        registry=registry,
        control_plane=store,
        decision_ledger=DecisionLedger(tmp_path / "private" / "ledger.jsonl"),
    )
    parent = _binding(
        project_id,
        agent="parent",
        session="chat",
        workstream="",
        role="parent",
    )
    child_a = _binding(project_id, agent="worker-1", session="child-a", workstream="analysis")
    child_b = _binding(project_id, agent="worker-2", session="child-b", workstream="delivery")
    for binding in (parent, child_a, child_b):
        scheduler.register(binding)
    state_a = store.read("child-a")
    review = _review(project_id, "child-a", state_a["control_epoch"], state_a["nonce"])
    outcome = scheduler.process_event(
        {
            "seq": 1,
            "type": "jarvis.review.requested",
            "payload": review.to_dict(),
        }
    )
    assert outcome is not None
    assert outcome.disposition == "interrupt_replan"
    assert requester.calls == 1
    assert outcome.routed_sessions == ("child-a", "chat")
    assert store.read("child-a")["control_epoch"] == 1
    assert store.read("child-b")["control_epoch"] == 0
    assert store.read("child-b")["guidance_queue"] == []
    assert len(store.read("chat")["guidance_queue"]) == 1


def test_completed_child_decision_has_parent_fallback(tmp_path: Path):
    project_id = "project-1"
    registry = DynamicChildRegistry(project_id=project_id)
    store = SessionControlPlane(tmp_path / "control", project_id=project_id)

    class NeverController:
        def decide(self, _candidate):
            return AttentionDecision(False, "unused")

    class NeverRequester:
        def answer(self, _question: str, _context: str) -> str:
            raise AssertionError("requester should not be called")

    ledger_path = tmp_path / "private" / "ledger.jsonl"
    scheduler = DynamicMasScheduler(
        project_id=project_id,
        controller=NeverController(),
        requester=NeverRequester(),
        requester_context_loader=lambda: "",
        registry=registry,
        control_plane=store,
        decision_ledger=DecisionLedger(ledger_path),
    )
    parent = _binding(project_id, agent="parent", session="chat", workstream="", role="parent")
    child = _binding(
        project_id,
        agent="worker-1",
        session="child-a",
        workstream="analysis",
        status="completed",
    )
    scheduler.register(parent)
    scheduler.register(child)
    outcome = scheduler.route_terminal_decision(
        target_session_id="child-a",
        decision_id="decision-terminal",
        guidance="Apply the narrow requester correction.",
        scope="worker",
    )
    assert outcome.routed_sessions == ("child-a", "chat")
    assert store.read("child-a")["guidance_queue"][0]["route"] == "targeted_repair"
    assert store.read("chat")["guidance_queue"][0]["route"] == "parent_integration"
    assert scheduler.diagnostics()["receipt_closure_valid"] is False

    parent_state = store.read("chat")
    parent_delivery = parent_state["delivery_receipts"][0]
    scheduler.process_event(
        {
            "seq": 1,
            "type": "control.guidance.applied",
            "payload": {
                "project_id": project_id,
                "session_id": "chat",
                "delivery_receipt_id": parent_delivery["receipt_id"],
                "model_boundary_id": "parent-integration-terminal-fallback",
                "control_epoch": parent_state["control_epoch"],
                "nonce": parent_state["nonce"],
                "guidance_sha256": parent_delivery["guidance_sha256"],
            },
        }
    )
    diagnostics = scheduler.diagnostics()
    assert diagnostics["delivery_receipts"] == 2
    assert diagnostics["application_receipts"] == 1
    assert diagnostics["targeted_repair_parent_fallbacks"] == 1
    assert diagnostics["unresolved_delivery_receipts"] == 0
    assert diagnostics["receipt_closure_valid"] is True
    ledger_records = [json.loads(line) for line in ledger_path.read_text().splitlines()]
    deliveries = [item for item in ledger_records if item["kind"] == "guidance_delivery"]
    applications = [
        item for item in ledger_records if item["kind"] == "guidance_application"
    ]
    assert len(deliveries) == 2
    assert len(applications) == 1
    assert applications[0]["delivery_receipt_id"] == parent_delivery["receipt_id"]
    assert applications[0]["delivery_receipt_id"] in {
        item["delivery_receipt_id"] for item in deliveries
    }


def test_dynamic_admission_requires_exact_receipt_closure(tmp_path: Path):
    runtime = MultiAgentRuntime(
        MultiAgentRuntimeConfig(
            task_dir=_task(tmp_path),
            episode_root=tmp_path / "episode-admission",
            worker_model="provider/model",
        )
    )
    # Admission only needs to distinguish an instrumented run from baseline;
    # the diagnostics themselves remain the source of truth in this unit test.
    runtime.scheduler = object()  # type: ignore[assignment]
    runtime.output.mkdir(parents=True)
    runtime.control_root.mkdir(parents=True)
    (runtime.control_root / "plugin_ready.json").write_text(
        json.dumps(
            {
                "ready": True,
                "plugin_id": "jarvisbench-mas-supervisor",
                "control_protocol_version": "1.0-release",
                "project_id": runtime.project_id,
                "hooks_registration_complete": True,
                "ready_event_seq": 1,
            }
        ),
        encoding="utf-8",
    )
    diagnostics = {
        "project_id": runtime.project_id,
        "raw_trace_included": False,
        "attention_channel_serialized": True,
        "registered_workers": 2,
        "live_updates_before_completion": 2,
        "receipt_closure_valid": True,
        "unresolved_delivery_receipts": 0,
        "orphan_application_receipts": 0,
        "service_errors": [],
    }
    path = runtime.output / "dynamic_mas_diagnostics.json"
    path.write_text(json.dumps(diagnostics), encoding="utf-8")
    assert runtime._dynamic_admission(2) is True

    diagnostics["receipt_closure_valid"] = False
    diagnostics["unresolved_delivery_receipts"] = 1
    path.write_text(json.dumps(diagnostics), encoding="utf-8")
    assert runtime._dynamic_admission(2) is False


def test_completed_child_project_decision_routes_only_to_parent(tmp_path: Path):
    project_id = "project-terminal-project-scope"
    registry = DynamicChildRegistry(project_id=project_id)
    store = SessionControlPlane(tmp_path / "control", project_id=project_id)

    class NeverController:
        def decide(self, _candidate):
            return AttentionDecision(False, "unused")

    class NeverRequester:
        def answer(self, _question: str, _context: str) -> str:
            raise AssertionError("requester should not be called")

    scheduler = DynamicMasScheduler(
        project_id=project_id,
        controller=NeverController(),
        requester=NeverRequester(),
        requester_context_loader=lambda: "",
        registry=registry,
        control_plane=store,
        decision_ledger=DecisionLedger(tmp_path / "private" / "ledger.jsonl"),
    )
    scheduler.register(
        _binding(project_id, agent="parent", session="chat", workstream="", role="parent")
    )
    scheduler.register(
        _binding(
            project_id,
            agent="worker-1",
            session="child-a",
            workstream="analysis",
            status="completed",
        )
    )
    outcome = scheduler.route_terminal_decision(
        target_session_id="child-a",
        decision_id="decision-project-terminal",
        guidance="Apply this project-wide correction during integration.",
        scope="project",
    )
    assert outcome.routed_sessions == ("chat",)
    assert store.read("child-a")["delivery_receipts"] == []
    parent_state = store.read("chat")
    assert len(parent_state["delivery_receipts"]) == 1
    parent_delivery = parent_state["delivery_receipts"][0]
    assert parent_delivery["route"] == "parent_integration"

    scheduler.process_event(
        {
            "seq": 1,
            "type": "control.guidance.applied",
            "payload": {
                "project_id": project_id,
                "session_id": "chat",
                "delivery_receipt_id": parent_delivery["receipt_id"],
                "model_boundary_id": "parent-integration-project-scope",
                "control_epoch": parent_state["control_epoch"],
                "nonce": parent_state["nonce"],
                "guidance_sha256": parent_delivery["guidance_sha256"],
            },
        }
    )
    diagnostics = scheduler.diagnostics()
    assert diagnostics["delivery_receipts"] == 1
    assert diagnostics["application_receipts"] == 1
    assert diagnostics["targeted_repair_parent_fallbacks"] == 0
    assert diagnostics["receipt_closure_valid"] is True


def test_executable_backend_uses_gateway_and_same_parent_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    task = _task(tmp_path)
    fake = tmp_path / "fake-openclaw"
    fake.write_text(
        """#!/usr/bin/env python3
import json, os, signal, sys, time
from pathlib import Path

calls = Path(os.environ['FAKE_OPENCLAW_CALLS'])
with calls.open('a', encoding='utf-8') as stream:
    stream.write(json.dumps(sys.argv[1:]) + '\\n')
args = sys.argv[1:]
if args[:2] in (['models', 'set'], ['config', 'set']):
    raise SystemExit(0)
if args[:2] == ['gateway', 'health']:
    raise SystemExit(0)
if args[:2] == ['gateway', 'run']:
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    while True: time.sleep(0.05)
if args and args[0] == 'agent':
    message = args[args.index('--message') + 1]
    state = Path(os.environ['OPENCLAW_STATE_DIR'])
    assert os.environ['OPENCLAW_HOME'] == os.environ['HOME']
    assert Path(os.environ['OPENCLAW_CONFIG_PATH']) == state / 'openclaw.json'
    home = state / 'agents' / 'main' / 'sessions'
    home.mkdir(parents=True, exist_ok=True)
    workspace = Path(os.environ['JB_WORKSPACE'])
    if 'DELEGATION_STARTED' in message:
        sessions = {}
        for index, label in enumerate(('analysis', 'delivery'), 1):
            session_id = f'child-{index}'
            sessions[f'agent:main:subagent:{index}'] = {
                'sessionId': session_id,
                'spawnedBy': 'agent:main:chat',
                'label': label,
            }
            (home / f'{session_id}.jsonl').write_text(
                json.dumps({'message': {'role': 'assistant', 'stopReason': 'stop'}}) + '\\n'
            )
        (home / 'sessions.json').write_text(json.dumps(sessions))
    else:
        for relative in ('results/analysis/result.txt', 'results/final.txt'):
            target = workspace / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text('ok\\n')
    raise SystemExit(0)
raise SystemExit(2)
""",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    calls = tmp_path / "calls.jsonl"
    monkeypatch.setenv("FAKE_OPENCLAW_CALLS", str(calls))
    monkeypatch.setenv("FAKE_WORKER_KEY", "private-test-key")
    runtime = MultiAgentRuntime(
        MultiAgentRuntimeConfig(
            task_dir=task,
            episode_root=tmp_path / "episode",
            worker_model="provider/model",
            provider_base_url="https://provider.invalid/v1",
            api_key_env="FAKE_WORKER_KEY",
            environment_passthrough=("FAKE_OPENCLAW_CALLS",),
            openclaw_executable=str(fake),
            poll_seconds=0.025,
        )
    )
    result = runtime.run()
    assert result.status == "completed"
    recorded = [json.loads(line) for line in calls.read_text().splitlines()]
    gateway = next(call for call in recorded if call[:2] == ["gateway", "run"])
    configured_port = next(
        call[-1]
        for call in recorded
        if call[:4] == ["config", "set", "gateway.port", call[-1]]
    )
    assert gateway[gateway.index("--port") + 1] == configured_port
    assert "--local" not in {item for call in recorded for item in call}
    agents = [call for call in recorded if call and call[0] == "agent"]
    assert len(agents) == 2
    assert all(call[call.index("--session-id") + 1] == "chat" for call in agents)
    assert not any("private-test-key" in json.dumps(call) for call in recorded)
    assert not (runtime.output / "gateway.log").exists()
    assert (runtime.logs_root / "gateway.log").is_file()
    assert all(
        b"private-test-key" not in path.read_bytes()
        for path in runtime.output.rglob("*")
        if path.is_file()
    )


def test_project_attention_budget_is_atomic_across_children(tmp_path: Path):
    project_id = "project-budget"
    registry = DynamicChildRegistry(project_id=project_id)
    store = SessionControlPlane(tmp_path / "control", project_id=project_id)

    class AlwaysAsk:
        def decide(self, _candidate):
            return AttentionDecision(True, "choice", "Which option?", "worker")

    class Requester:
        calls = 0

        def answer(self, _question: str, _context: str) -> str:
            self.calls += 1
            return "Use the reversible option."

    requester = Requester()
    scheduler = DynamicMasScheduler(
        project_id=project_id,
        controller=AlwaysAsk(),
        requester=requester,
        requester_context_loader=lambda: '{"choice":"reversible"}',
        registry=registry,
        control_plane=store,
        decision_ledger=DecisionLedger(tmp_path / "private" / "ledger.jsonl"),
        max_attention_requests=1,
    )
    events = []
    for index, session_id in enumerate(("child-a", "child-b"), 1):
        scheduler.register(
            _binding(
                project_id,
                agent=f"worker-{index}",
                session=session_id,
                workstream=f"stream-{index}",
            )
        )
        state = store.read(session_id)
        review = _review(
            project_id,
            session_id,
            state["control_epoch"],
            state["nonce"],
        )
        events.append(
            {"seq": 1, "type": "jarvis.review.requested", "payload": review.to_dict()}
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(scheduler.process_event, events))
    assert sorted(outcome.disposition for outcome in outcomes if outcome is not None) == [
        "attention_budget_allow",
        "interrupt_replan",
    ]
    assert requester.calls == 1
    assert scheduler.diagnostics()["attention_requests_used"] == 1
    assert sum(store.read(session)["control_epoch"] == 1 for session in ("child-a", "child-b")) == 1


def test_duplicate_attention_is_released_and_application_is_ledgered(tmp_path: Path):
    project_id = "project-deduplicate"
    registry = DynamicChildRegistry(project_id=project_id)
    store = SessionControlPlane(tmp_path / "control", project_id=project_id)

    class AlwaysAsk:
        def decide(self, _candidate):
            return AttentionDecision(True, "same choice", "Which option?", "worker")

    class Requester:
        calls = 0

        def answer(self, _question: str, _context: str) -> str:
            self.calls += 1
            return "Use option B."

    requester = Requester()
    ledger_path = tmp_path / "private" / "ledger.jsonl"
    scheduler = DynamicMasScheduler(
        project_id=project_id,
        controller=AlwaysAsk(),
        requester=requester,
        requester_context_loader=lambda: '{"choice":"B"}',
        registry=registry,
        control_plane=store,
        decision_ledger=DecisionLedger(ledger_path),
        max_attention_requests=2,
    )
    scheduler.register(
        _binding(
            project_id,
            agent="worker-1",
            session="child-a",
            workstream="analysis",
        )
    )
    initial = store.read("child-a")
    first = _review(
        project_id,
        "child-a",
        initial["control_epoch"],
        initial["nonce"],
    )
    first_outcome = scheduler.process_event(
        {"seq": 1, "type": "jarvis.review.requested", "payload": first.to_dict()}
    )
    assert first_outcome is not None
    assert first_outcome.disposition == "interrupt_replan"

    delivered = store.read("child-a")
    receipt = delivered["delivery_receipts"][0]
    scheduler.process_event(
        {
            "seq": 2,
            "type": "control.guidance.applied",
            "payload": {
                "project_id": project_id,
                "session_id": "child-a",
                "delivery_receipt_id": receipt["receipt_id"],
                "model_boundary_id": "tool-continuation-1",
                "control_epoch": delivered["control_epoch"],
                "nonce": delivered["nonce"],
                "guidance_sha256": receipt["guidance_sha256"],
            },
        }
    )
    applied = store.read("child-a")
    assert len(applied["application_receipts"]) == 1
    assert applied["guidance_queue"] == []

    second = replace(
        first,
        turn_id="turn-2",
        batch_id="batch-child-a-2",
        review_id="review-child-a-2",
        control_epoch=applied["control_epoch"],
        nonce=applied["nonce"],
        expected_event_seq=3,
    )
    second_outcome = scheduler.process_event(
        {"seq": 3, "type": "jarvis.review.requested", "payload": second.to_dict()}
    )
    assert second_outcome is not None
    assert second_outcome.disposition == "duplicate_attention_allow"
    assert requester.calls == 1
    assert scheduler.diagnostics()["duplicate_attention_suppressed"] == 1
    records = [json.loads(line) for line in ledger_path.read_text().splitlines()]
    assert any(record.get("kind") == "guidance_application" for record in records)
