from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from jarvisbench.core.contracts import BoundaryCandidate, ReducedUpdate
from jarvisbench.core.controller import AttentionDecision
from jarvisbench.reference.single_agent import ReferenceSingleAgentControllerAdapter
from jarvisbench.settings.single_agent_runtime import (
    FROZEN_SINGLE_AGENT_PROVENANCE,
    ControllerReview,
    DryRunWorker,
    PromptKind,
    SingleAgentRunner,
    SingleAgentWorkerRequest,
    WorkerExecution,
    build_worker_prompt,
    load_single_agent_task,
    prepare_episode_layout,
    worker_contract_sha256,
)


TASKS = Path(__file__).resolve().parents[1] / "tasks" / "single_agent"

# Historical release-package values from the canonical frozen Single-Agent V1
# manifest.  Both merge and split record this same mapping.
FROZEN_WORKER_CONTRACTS = {
    "jbv1_batch_export": "780f1c192a789aff9e45a2a98d1cc0c5efc4a119bf9f52f7d3a9dd1f233aaad5",
    "jbv1_calendar_optimization": "83ca03b2d134567d2e8956beacde5c74a97ead2c6f612e02b0c110a3f36c0151",
    "jbv1_caption_field_retest": "eba599f7878cd7d4f698925d2a665e7098f8abdac211137671d8ddbd20389fb6",
    "jbv1_client_update": "b838bb30482e642f566f95cace47a638917c4c2333835196863753e58cf4ee17",
    "jbv1_customer_case_study": "cba7d264a878a829d4a87a7026660b6ae12fa7081833a8baf7c6249bedf9a3bf",
    "jbv1_customer_migration": "975d682b48511ab4f060427ae23ab2b00755a1fa09a944cf39e2d62a8ad946c0",
    "jbv1_experiment_escalation": "d07c3113b6617c44d431f3cfa31e3fbb96d4e4e181659823c39a5a25bcf8bb14",
    "jbv1_injection_triage": "bb6139458aab212b18a5482dde176ddbff181f82da25ad217ca8c690d6ba3d62",
    "jbv1_marketing_artifact_acceptance": "e8124ba0064d24fd632625dc75702f1e0bcf541f448cfbdc2f5724c00bf33768",
    "jbv1_medication_reconciliation": "d8865bb2823fdc8c1b6d86f03d57689d5b64dd9f1eb11b58b30a81674ed470d0",
    "jbv1_meeting_minutes": "4a68b93d34c3fa3c8ef0eee7f7689f9d587263415a19576f346c0ba1e7aeb70d",
    "jbv1_midride_security_key": "b0cdf909097436e06d88e2f25f7f7fe7e7de761ee821cdd0e51184565ea50a11",
    "jbv1_onboarding_handoff": "85df82e9603100ead8b9b0c10956c951d0085b9811c639baac9d825c290731ec",
    "jbv1_postmortem_actions": "87151e8e2fe35129bc0fa360e0922bc7e3052c8be9af6a9b21ca36dd3cdf84f8",
    "jbv1_product_launch_site": "a9269ca2e171304cd12b0f4c88cb51acde72cca1b559dccf52e0174ee49461f0",
    "jbv1_research_agenda_review": "fdf7df5aa3423d0b60ff2afffb9c1f9cb25d129d46f81539b24f3efa08c50842",
    "jbv1_saas_contract": "038b7d0379a27fbe0fbed19c76b74a64a09f4c3c2b95d50d9db71422caf12c61",
    "jbv1_tax_donation_audit": "5d9e0fbf5dc33d6095ce677090c1e992829e8cbc9dc259ca796a3cc27fc265a3",
    "jbv1_tender_selection": "c777181fa6cd9a58ecf842be61a709871852b6b95d60f7479a2f808b6d1b8ea9",
    "jbv1_var_model_review": "27e0940dedb8af4bacdbefe434b22352349cede1e83ac50c43cf3df0891ebe2a",
}


def candidate(session_id: str = "worker-0") -> BoundaryCandidate:
    return BoundaryCandidate(
        session_id=session_id,
        epoch=2,
        nonce="nonce-2",
        action_id="action-2",
        action_fingerprint="f" * 64,
        reduced_update=ReducedUpdate(
            progress="Checked public evidence.",
            current_goal="Choose a reversible next action.",
            evidence=("public record A conflicts with public record B",),
            uncertainty="Requester preference is unknown.",
            proposed_action="write the provisional artifact",
        ),
        consequence="The choice changes the exported artifact.",
    )


def test_public_projection_reproduces_all_frozen_worker_contracts() -> None:
    observed = {}
    for task_dir in sorted(TASKS.iterdir()):
        task = load_single_agent_task(task_dir)
        observed[task.task_id] = worker_contract_sha256(task.root, task.manifest)
    assert observed == FROZEN_WORKER_CONTRACTS


def test_frozen_source_provenance_is_release_bound() -> None:
    assert FROZEN_SINGLE_AGENT_PROVENANCE["baseline_runner_sha256"] == (
        "6f645b4283d3f8cfc244e5f25d5e8ffd59950e4a30d0214102eebf6b3102cbbc"
    )
    modes = FROZEN_SINGLE_AGENT_PROVENANCE["modes"]
    assert modes["merge"]["sha256_manifest_sha256"] == (
        "f988217df4ae19ae2498b36b2f4a2f0e4a8380b61bef410bb946f173da75ade6"
    )
    assert modes["split"]["sha256_manifest_sha256"] == (
        "6e82f3c1913275265b469d988f9803add1362ed180c54a3e30a9c431a51f4e61"
    )


def test_controlled_prompt_is_exact_frozen_boundary_replacement() -> None:
    task = load_single_agent_task(TASKS / "jbv1_batch_export")
    baseline = build_worker_prompt(task.manifest, kind=PromptKind.BASELINE)
    controlled = build_worker_prompt(task.manifest, kind=PromptKind.CONTROLLED)
    assert task.brief in baseline and task.brief in controlled
    assert baseline.count("All task materials are under /workspace.") == 1
    assert "operator-authorized Jarvis may release" not in baseline
    assert "operator-authorized Jarvis may release" in controlled
    assert hashlib.sha256(controlled.encode("utf-8")).hexdigest() == (
        "d02df0bb297c91be1f1f65d97ee64abaf5a75aeb4cda8e8339e24288310df563"
    )


def test_docker_layout_keeps_mutable_state_off_host_bind_mounts(tmp_path: Path) -> None:
    task = load_single_agent_task(TASKS / "jbv1_batch_export")
    layout = prepare_episode_layout(task, tmp_path / "runs", "layout-smoke")
    mounts = layout.docker_mounts()
    assert [(mount.kind, mount.target, mount.read_only) for mount in mounts] == [
        ("bind", "/task_public", True),
        ("volume", "/workspace", False),
        ("bind", "/host_output", False),
    ]
    assert layout.state.startswith("/workspace/")
    assert layout.control_root.startswith("/workspace/")
    assert layout.event_root.startswith("/workspace/")
    assert layout.openclaw_home.startswith("/workspace/")
    assert not (layout.episode_root / "workspace").exists()
    assert not (layout.episode_root / "state").exists()
    assert layout.episode_root.stat().st_mode & 0o777 == 0o700
    assert layout.export_root.stat().st_mode & 0o777 == 0o700


def test_baseline_none_is_first_class_and_has_no_fake_manager(tmp_path: Path) -> None:
    class CandidateDryRun:
        def __init__(self) -> None:
            self.review: ControllerReview | None = None

        def execute(self, request: SingleAgentWorkerRequest, review):
            assert request.prompt_kind is PromptKind.BASELINE
            self.review = review(candidate(request.session_id))
            return WorkerExecution("dry_run")

    worker = CandidateDryRun()
    runner = SingleAgentRunner(worker)
    assert runner.execution_nodes == ("worker-0",)
    assert runner.manager is None
    result = runner.run(
        TASKS / "jbv1_batch_export", tmp_path / "runs", run_id="baseline-none"
    )
    assert result.status == "dry_run"
    assert result.controller == "none"
    assert result.candidate_count == 1
    assert result.attention_request_count == 0
    assert worker.review is not None
    assert worker.review.decision.request_attention is False
    plan = json.loads((result.episode_root / "plan.json").read_text())
    assert plan["manager"] is None
    assert plan["execution_nodes"] == ["worker-0"]
    assert all("source" not in mount for mount in plan["container"]["mounts"])


def test_optional_reference_adapter_cannot_change_held_identity(tmp_path: Path) -> None:
    class Ask:
        def decide(self, value: BoundaryCandidate) -> AttentionDecision:
            return AttentionDecision(
                True,
                "requester choice changes the held action",
                "Which reversible option do you prefer?",
                "worker",
            )

    class CandidateDryRun:
        bound: ControllerReview | None = None

        def execute(self, request: SingleAgentWorkerRequest, review):
            assert request.prompt_kind is PromptKind.CONTROLLED
            self.bound = review(candidate(request.session_id))
            return WorkerExecution("dry_run")

    worker = CandidateDryRun()
    adapter = ReferenceSingleAgentControllerAdapter(Ask())
    result = SingleAgentRunner(worker, adapter).run(
        TASKS / "jbv1_batch_export", tmp_path / "runs", run_id="reference"
    )
    assert result.attention_request_count == 1
    assert worker.bound is not None
    assert (
        worker.bound.session_id,
        worker.bound.epoch,
        worker.bound.nonce,
        worker.bound.action_id,
        worker.bound.action_fingerprint,
    ) == ("worker-0", 2, "nonce-2", "action-2", "f" * 64)


def test_reference_adapter_fails_closed_without_logging_exception_text(tmp_path: Path) -> None:
    class Broken:
        def decide(self, value: BoundaryCandidate) -> AttentionDecision:
            del value
            raise RuntimeError("sensitive provider response")

    class CandidateDryRun:
        def execute(self, request: SingleAgentWorkerRequest, review):
            bound = review(candidate(request.session_id))
            assert bound.decision.request_attention is False
            assert "sensitive provider response" not in bound.decision.reason
            return WorkerExecution("dry_run")

    result = SingleAgentRunner(
        CandidateDryRun(), ReferenceSingleAgentControllerAdapter(Broken())
    ).run(TASKS / "jbv1_batch_export", tmp_path / "runs", run_id="fail-closed")
    events = (result.episode_root / "review_receipts.jsonl").read_text()
    assert "sensitive provider response" not in events
    assert result.attention_request_count == 0


def test_completed_worker_must_export_every_required_artifact(tmp_path: Path) -> None:
    class PartialWorker:
        def execute(self, request: SingleAgentWorkerRequest, review):
            del review
            path = request.layout.export_root / "results" / "final.json"
            path.parent.mkdir(parents=True)
            path.write_text("{}\n", encoding="utf-8")
            return WorkerExecution("completed", ("results/final.json",))

    result = SingleAgentRunner(PartialWorker()).run(
        TASKS / "jbv1_batch_export", tmp_path / "runs", run_id="partial"
    )
    assert result.status == "incomplete"
    assert "results/final.json" not in result.missing_result_paths
    assert "results/test_report.md" in result.missing_result_paths
    manifest = json.loads(
        (result.episode_root / "results_manifest.json").read_text()
    )
    assert manifest["files"][0]["path"] == "results/final.json"


def test_cross_session_candidate_is_rejected(tmp_path: Path) -> None:
    class CrossSessionWorker:
        def execute(self, request: SingleAgentWorkerRequest, review):
            review(candidate("another-worker"))
            return WorkerExecution("dry_run")

    with pytest.raises(RuntimeError, match="another session"):
        SingleAgentRunner(CrossSessionWorker()).run(
            TASKS / "jbv1_batch_export", tmp_path / "runs", run_id="cross-session"
        )


def test_dry_run_worker_needs_no_model_or_controller(tmp_path: Path) -> None:
    result = SingleAgentRunner(DryRunWorker()).run(
        TASKS / "jbv1_batch_export", tmp_path / "runs", run_id="dry-run"
    )
    assert result.status == "dry_run"
    assert result.controller == "none"
    assert result.candidate_count == 0
    assert not (result.episode_root / "review_receipts.jsonl").exists()


def test_export_symlink_is_rejected_before_hashing(tmp_path: Path) -> None:
    class SymlinkWorker:
        def execute(self, request: SingleAgentWorkerRequest, review):
            del review
            target = request.layout.export_root / "results"
            target.mkdir()
            (target / "final.json").symlink_to("/etc/passwd")
            return WorkerExecution("completed", ("results/final.json",))

    with pytest.raises(RuntimeError, match="symlinks"):
        SingleAgentRunner(SymlinkWorker()).run(
            TASKS / "jbv1_batch_export", tmp_path / "runs", run_id="symlink"
        )


def test_worker_diagnostics_are_bounded_scalars() -> None:
    with pytest.raises(ValueError, match="scalar"):
        WorkerExecution("dry_run", diagnostics={"trace": ["full", "trace"]})


def test_single_attention_budget_and_duplicate_question_are_deterministic(
    tmp_path: Path,
) -> None:
    class AlwaysAsk:
        def decide(self, _value: BoundaryCandidate) -> AttentionDecision:
            return AttentionDecision(
                True,
                "requester-owned choice",
                "Which option should I use?",
                "worker",
            )

    class TwoReviews:
        reviews: list[ControllerReview]

        def __init__(self) -> None:
            self.reviews = []

        def execute(self, request: SingleAgentWorkerRequest, review):
            self.reviews.append(review(candidate(request.session_id)))
            second = candidate(request.session_id)
            second = BoundaryCandidate(
                session_id=second.session_id,
                epoch=3,
                nonce="nonce-3",
                action_id="action-3",
                action_fingerprint="e" * 64,
                reduced_update=second.reduced_update,
                consequence=second.consequence,
            )
            self.reviews.append(review(second))
            return WorkerExecution("dry_run")

    worker = TwoReviews()
    result = SingleAgentRunner(
        worker,
        ReferenceSingleAgentControllerAdapter(AlwaysAsk()),
        max_attention_requests=2,
    ).run(TASKS / "jbv1_batch_export", tmp_path / "runs", run_id="duplicate")
    assert [item.decision.request_attention for item in worker.reviews] == [True, False]
    assert result.attention_request_count == 1
    assert result.attention_budget_used == 1
    assert result.duplicate_attention_suppressed == 1


def test_single_attention_budget_zero_never_spends_attention(tmp_path: Path) -> None:
    class AlwaysAsk:
        def decide(self, _value: BoundaryCandidate) -> AttentionDecision:
            return AttentionDecision(True, "choice", "Which option?", "worker")

    class CandidateWorker:
        bound: ControllerReview | None = None

        def execute(self, request: SingleAgentWorkerRequest, review):
            self.bound = review(candidate(request.session_id))
            return WorkerExecution("dry_run")

    worker = CandidateWorker()
    result = SingleAgentRunner(
        worker,
        ReferenceSingleAgentControllerAdapter(AlwaysAsk()),
        max_attention_requests=0,
    ).run(TASKS / "jbv1_batch_export", tmp_path / "runs", run_id="zero-budget")
    assert worker.bound is not None
    assert worker.bound.decision.request_attention is False
    assert result.attention_budget_used == 0


def test_declared_intermediate_artifact_is_prefiltered_and_terminal_slot_is_reserved(
    tmp_path: Path,
) -> None:
    class AlwaysAsk:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def decide(self, value: BoundaryCandidate) -> AttentionDecision:
            self.calls.append(value.action_id)
            return AttentionDecision(
                True,
                "requester-owned outcome choice",
                f"Which outcome should govern {value.action_id}?",
                "worker",
            )

    class IntermediateThenFinal:
        def __init__(self) -> None:
            self.reviews: list[ControllerReview] = []

        def execute(self, request: SingleAgentWorkerRequest, review):
            common = candidate(request.session_id)
            declared = (
                "results/final.json",
                "results/recommendation.md",
            )
            intermediate = BoundaryCandidate(
                session_id=common.session_id,
                epoch=2,
                nonce="a" * 48,
                action_id="action-intermediate",
                action_fingerprint="c" * 64,
                reduced_update=common.reduced_update,
                consequence="Write a reversible intermediate recommendation.",
                artifact_paths=("results/recommendation.md",),
                final_record_intent=False,
                required_result_paths=declared,
            )
            terminal = BoundaryCandidate(
                session_id=common.session_id,
                epoch=2,
                nonce="a" * 48,
                action_id="action-terminal",
                action_fingerprint="d" * 64,
                reduced_update=common.reduced_update,
                consequence="Commit the requester-facing decision record.",
                artifact_paths=("results/final.json",),
                final_record_intent=True,
                required_result_paths=declared,
            )
            self.reviews.append(review(intermediate))
            self.reviews.append(review(terminal))
            return WorkerExecution("dry_run")

    controller = AlwaysAsk()
    worker = IntermediateThenFinal()
    result = SingleAgentRunner(
        worker,
        ReferenceSingleAgentControllerAdapter(controller),
        max_attention_requests=1,
    ).run(
        TASKS / "jbv1_batch_export",
        tmp_path / "runs",
        run_id="terminal-reserve",
    )

    # Deterministic prefiltering, rather than the LLM, releases declared local
    # intermediate artifacts. The only provider call and attention slot remain
    # available for the final requester-facing decision boundary.
    assert controller.calls == ["action-terminal"]
    assert [item.decision.request_attention for item in worker.reviews] == [
        False,
        True,
    ]
    assert "intermediate" in worker.reviews[0].decision.reason
    assert result.candidate_count == 2
    assert result.attention_request_count == 1
    assert result.attention_budget_used == 1


def test_terminal_reserve_never_suppresses_an_external_irreversible_boundary(
    tmp_path: Path,
) -> None:
    class AlwaysAsk:
        def decide(self, _value: BoundaryCandidate) -> AttentionDecision:
            return AttentionDecision(
                True,
                "requester authorization is required for an external write",
                "Do you authorize this external action?",
                "worker",
            )

    class ExternalWorker:
        bound: ControllerReview | None = None

        def execute(self, request: SingleAgentWorkerRequest, review):
            common = candidate(request.session_id)
            external = BoundaryCandidate(
                session_id=common.session_id,
                epoch=common.epoch,
                nonce=common.nonce,
                action_id="action-external",
                action_fingerprint="f" * 64,
                reduced_update=common.reduced_update,
                consequence="Publish the current result to an external service.",
                required_result_paths=("results/final.json",),
                external_irreversible_effect="http_write",
            )
            self.bound = review(external)
            return WorkerExecution("dry_run")

    worker = ExternalWorker()
    result = SingleAgentRunner(
        worker,
        ReferenceSingleAgentControllerAdapter(AlwaysAsk()),
        max_attention_requests=1,
    ).run(
        TASKS / "jbv1_batch_export",
        tmp_path / "runs",
        run_id="external-boundary",
    )

    assert worker.bound is not None
    assert worker.bound.decision.request_attention is True
    assert result.attention_budget_used == 1


def test_terminal_attention_reserve_survives_a_nonprefiltered_early_ask(
    tmp_path: Path,
) -> None:
    class AlwaysAsk:
        def __init__(self) -> None:
            self.calls = 0

        def decide(self, value: BoundaryCandidate) -> AttentionDecision:
            self.calls += 1
            return AttentionDecision(
                True,
                "requester-owned outcome choice",
                f"Which outcome should govern {value.action_id}?",
                "worker",
            )

    class UnknownThenFinal:
        def __init__(self) -> None:
            self.reviews: list[ControllerReview] = []

        def execute(self, request: SingleAgentWorkerRequest, review):
            common = candidate(request.session_id)
            required = ("results/final.json", "results/recommendation.md")
            early = BoundaryCandidate(
                session_id=common.session_id,
                epoch=2,
                nonce="a" * 48,
                action_id="action-unlisted",
                action_fingerprint="e" * 64,
                reduced_update=common.reduced_update,
                consequence="Write an unlisted, non-final local artifact.",
                artifact_paths=("results/unlisted.txt",),
                final_record_intent=False,
                required_result_paths=required,
            )
            terminal = BoundaryCandidate(
                session_id=common.session_id,
                epoch=2,
                nonce="a" * 48,
                action_id="action-final",
                action_fingerprint="f" * 64,
                reduced_update=common.reduced_update,
                consequence="Commit the final requester-facing decision.",
                artifact_paths=("results/final.json",),
                final_record_intent=True,
                required_result_paths=required,
            )
            self.reviews.extend((review(early), review(terminal)))
            return WorkerExecution("dry_run")

    controller = AlwaysAsk()
    worker = UnknownThenFinal()
    result = SingleAgentRunner(
        worker,
        ReferenceSingleAgentControllerAdapter(controller),
        max_attention_requests=1,
    ).run(
        TASKS / "jbv1_batch_export",
        tmp_path / "runs",
        run_id="terminal-reserve-nonprefiltered",
    )

    assert controller.calls == 2
    assert [item.decision.request_attention for item in worker.reviews] == [
        False,
        True,
    ]
    assert "terminal requester-attention slot reserved" in (
        worker.reviews[0].decision.reason
    )
    assert result.attention_request_count == 1
    assert result.attention_budget_used == 1
