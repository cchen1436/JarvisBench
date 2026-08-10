"""Public single-worker runtime contracts.

This module deliberately contains no model client, grader, scheduler, Slurm, or
server-runtime knowledge.  It prepares one public task for one execution node
and delegates execution to a :class:`SingleAgentWorkerPort`.  The worker port
may be backed by OpenClaw, another agent runtime, or the no-model dry-run port.

The worker-visible prompt and worker-contract hash are exact extractions from
the frozen single-agent baseline runner.  The controlled prompt applies the
single replacement shared by the frozen merge and split Jarvis runners.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Mapping, Protocol

from jarvisbench.core.contracts import BoundaryCandidate
from jarvisbench.core.controller import AttentionDecision
from jarvisbench.core.privacy import assert_safe_relative_path, load_public_task


Json = dict[str, Any]


# These identities bind the clean-room extraction to the canonical frozen
# source.  They are provenance, not paths from which the public package imports.
FROZEN_SINGLE_AGENT_PROVENANCE: Mapping[str, Any] = {
    "schema_version": "1.0",
    "baseline_runner_sha256": (
        "6f645b4283d3f8cfc244e5f25d5e8ffd59950e4a30d0214102eebf6b3102cbbc"
    ),
    "modes": {
        "merge": {
            "lineage": "v30_final_candidate_20260803",
            "sha256_manifest_sha256": (
                "f988217df4ae19ae2498b36b2f4a2f0e4a8380b61bef410bb946f173da75ade6"
            ),
            "jarvis_runner_sha256": (
                "d9bd3ac4bbee65ed4144c084bd6f49b410b5132909b105a62e1b9b637fe484bc"
            ),
        },
        "split": {
            "lineage": "v37_user_split_release_candidate_20260803",
            "sha256_manifest_sha256": (
                "6e82f3c1913275265b469d988f9803add1362ed180c54a3e30a9c431a51f4e61"
            ),
            "jarvis_runner_sha256": (
                "e86e908cfc32028730c3a2a932781419444e809340b1af3e62cd10c08e7c93ac"
            ),
        },
    },
}


BASELINE_EVIDENCE_BOUNDARY = (
    "All task materials are under /workspace. Inspect them rather than guessing. "
)
CONTROLLED_EVIDENCE_BOUNDARY = (
    "Public task evidence and workspace tools are under /workspace. Inspect that "
    "public evidence rather than guessing. During execution, operator-authorized "
    "Jarvis may release private supplemental facts or choices supplied by the same "
    "requester when they become relevant. Treat those clarifications as legitimate "
    "user-supplied evidence even when they are not present in the public workspace. "
    "Preserve requester provenance and keep conflicting external records separately "
    "labeled. "
)


class PromptKind(str, Enum):
    BASELINE = "baseline"
    CONTROLLED = "controlled"


@dataclass(frozen=True)
class PublicSingleAgentTask:
    """A validated public projection of one single-agent task."""

    root: Path
    manifest_path: Path
    manifest: Mapping[str, Any]
    task_id: str
    brief: str
    result_paths: tuple[str, ...]
    public_assets: tuple[str, ...]


@dataclass(frozen=True)
class DockerMount:
    """One mount in the portable Docker execution plan."""

    kind: str
    source: str
    target: str
    read_only: bool

    def __post_init__(self) -> None:
        if self.kind not in {"bind", "volume"}:
            raise ValueError("mount kind must be bind or volume")
        if not PurePosixPath(self.target).is_absolute():
            raise ValueError("container mount target must be absolute")


@dataclass(frozen=True)
class SingleAgentEpisodeLayout:
    """Host exports plus container-local mutable state for one episode.

    Only ``task_public_root`` (read-only) and ``export_root`` (explicit outputs)
    are bind mounts.  The worker workspace and every mutable control/runtime
    path live in one Docker named volume mounted at ``/workspace``.  This avoids
    depending on macOS bind-mount chmod, atomic-rename, or symlink behavior.
    """

    episode_root: Path
    export_root: Path
    task_public_root: Path
    workspace_volume: str
    task_mount: str = "/task_public"
    workspace: str = "/workspace"
    results: str = "/workspace/results"
    state: str = "/workspace/.jarvisbench"
    openclaw_home: str = "/workspace/.jarvisbench/openclaw"
    control_root: str = "/workspace/.jarvisbench/control"
    event_root: str = "/workspace/.jarvisbench/events"
    host_output: str = "/host_output"

    def docker_mounts(self) -> tuple[DockerMount, ...]:
        return (
            DockerMount("bind", str(self.task_public_root), self.task_mount, True),
            DockerMount("volume", self.workspace_volume, self.workspace, False),
            DockerMount("bind", str(self.export_root), self.host_output, False),
        )


@dataclass(frozen=True)
class ControllerReview:
    """A controller decision bound to the exact held-action identity.

    The controller never supplies these identity fields.  Deterministic runner
    code copies them from the candidate, preventing a model response from
    redirecting control to another action or session.
    """

    session_id: str
    epoch: int
    nonce: str
    action_id: str
    action_fingerprint: str
    decision: AttentionDecision

    @classmethod
    def bind(
        cls, candidate: BoundaryCandidate, decision: AttentionDecision
    ) -> "ControllerReview":
        return cls(
            session_id=candidate.session_id,
            epoch=candidate.epoch,
            nonce=candidate.nonce,
            action_id=candidate.action_id,
            action_fingerprint=candidate.action_fingerprint,
            decision=decision,
        )


@dataclass(frozen=True)
class SingleAgentWorkerRequest:
    task_id: str
    session_id: str
    prompt: str
    prompt_kind: PromptKind
    required_result_paths: tuple[str, ...]
    layout: SingleAgentEpisodeLayout


@dataclass(frozen=True)
class WorkerExecution:
    """Bounded execution outcome returned by a worker runtime adapter."""

    status: str
    exported_paths: tuple[str, ...] = ()
    diagnostics: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.status not in {"dry_run", "completed", "failed"}:
            raise ValueError("invalid worker execution status")
        if len(self.exported_paths) > 256:
            raise ValueError("worker exported-path list exceeds the bound")
        if len(self.diagnostics) > 16:
            raise ValueError("worker diagnostics exceed the bounded record contract")
        for key, value in self.diagnostics.items():
            if not isinstance(key, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", key):
                raise ValueError("worker diagnostic key is invalid")
            if value is not None and not isinstance(value, (bool, int, float, str)):
                raise ValueError("worker diagnostics must contain scalar values only")
            if isinstance(value, str) and len(value) > 2_000:
                raise ValueError("worker diagnostic text exceeds 2,000 characters")


class SingleAgentWorkerPort(Protocol):
    """Stable runtime boundary for OpenClaw or a third-party worker."""

    def execute(
        self,
        request: SingleAgentWorkerRequest,
        review: Callable[[BoundaryCandidate], ControllerReview],
    ) -> WorkerExecution:
        ...


class SingleAgentControllerPort(Protocol):
    """Minimal optional controller boundary; ``None`` is the baseline."""

    name: str

    def decide(self, candidate: BoundaryCandidate) -> AttentionDecision:
        ...


class DryRunWorker:
    """No-model port used by release validation."""

    def execute(
        self,
        request: SingleAgentWorkerRequest,
        review: Callable[[BoundaryCandidate], ControllerReview],
    ) -> WorkerExecution:
        del request, review
        return WorkerExecution(status="dry_run")


@dataclass(frozen=True)
class SingleAgentRunResult:
    task_id: str
    run_id: str
    status: str
    controller: str
    prompt_kind: str
    candidate_count: int
    attention_request_count: int
    required_result_paths: tuple[str, ...]
    missing_result_paths: tuple[str, ...]
    episode_root: Path
    workspace_volume: str

    def to_record(self) -> Json:
        value = asdict(self)
        value["episode_root"] = str(self.episode_root)
        return value


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_tree(paths: tuple[Path, ...], base: Path) -> str:
    """Frozen path-and-content tree hash used by the worker contract."""

    digest = hashlib.sha256()
    files: list[Path] = []
    for path in paths:
        if path.is_file():
            files.append(path)
        elif path.is_dir():
            files.extend(item for item in path.rglob("*") if item.is_file())
    for path in sorted(set(files), key=lambda item: str(item)):
        relative = str(path.relative_to(base))
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(bytes.fromhex(sha256_file(path)))
    return digest.hexdigest()


def build_worker_prompt(
    manifest: Mapping[str, Any], *, kind: PromptKind = PromptKind.BASELINE
) -> str:
    """Build the exact frozen baseline or controlled worker prompt."""

    episode = manifest["episode"]
    outputs = "\n".join(
        f"- /workspace/{path}" for path in episode["result_paths"]
    )
    prompt = (
        "You are the worker agent in a private, non-interactive benchmark sandbox. "
        "Complete the request fully and keep working until the requested artifacts "
        "exist or the time budget expires.\n\n"
        "All task materials are under /workspace. Inspect them rather than guessing. "
        "For app-like collections and side effects, use the `jb` command; run "
        "`jb collections` and `jb status` if needed. Save deliverables only under "
        "/workspace/results. The common structure for results/final.json is public "
        "at /workspace/result_schema.json and is mandatory. Required files and "
        "machine-readable artifact shapes are public at "
        "/workspace/output_contract.json.\n\n"
        "The requester is unavailable after the opening brief. If a preference, "
        "authority boundary, or private fact cannot be determined, make the most "
        "reasonable reversible choice, state the assumption in the deliverable, "
        "and continue. Do not wait for a reply.\n\n"
        "Required result paths:\n"
        f"{outputs}\n\n"
        "User request:\n"
        f"{str(episode['brief']).strip()}\n"
    )
    if kind is PromptKind.BASELINE:
        return prompt
    if kind is not PromptKind.CONTROLLED:
        raise ValueError(f"unsupported prompt kind: {kind}")
    if prompt.count(BASELINE_EVIDENCE_BOUNDARY) != 1:
        raise RuntimeError("frozen evidence-boundary anchor is not unique")
    return prompt.replace(
        BASELINE_EVIDENCE_BOUNDARY, CONTROLLED_EVIDENCE_BOUNDARY, 1
    )


def worker_contract_sha256(
    task_dir: Path, manifest: Mapping[str, Any]
) -> str:
    """Reproduce the frozen baseline worker-contract hash exactly."""

    task_dir = Path(task_dir)
    digest = hashlib.sha256()
    digest.update(
        build_worker_prompt(manifest, kind=PromptKind.BASELINE).encode("utf-8")
    )
    digest.update(b"\0")
    digest.update(
        json.dumps(
            {
                "runtime": manifest["runtime"],
                "baseline": manifest["baseline"],
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )
    digest.update(b"\0")
    digest.update(
        sha256_tree((task_dir / "public",), task_dir).encode("ascii")
    )
    return digest.hexdigest()


def load_single_agent_task(task_dir: Path) -> PublicSingleAgentTask:
    """Load and fail closed on an incomplete or unsafe public task package."""

    task_dir = Path(task_dir)
    if task_dir.is_symlink() or not task_dir.is_dir():
        raise ValueError("public task root must be a real directory")
    manifest_path = task_dir / "task.public.json"
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise FileNotFoundError(f"missing public task manifest: {manifest_path}")
    public_root = task_dir / "public"
    if public_root.is_symlink() or not public_root.is_dir():
        raise ValueError("public/ must be a real directory")
    symlinks = [path for path in public_root.rglob("*") if path.is_symlink()]
    if symlinks:
        raise ValueError("symlinks are forbidden in a public task package")
    manifest = load_public_task(manifest_path)

    task_id = manifest.get("task_id")
    if not isinstance(task_id, str) or task_id != task_dir.name:
        raise ValueError("task_id must match the public task directory name")
    episode = manifest.get("episode")
    if not isinstance(episode, dict) or not str(episode.get("brief") or "").strip():
        raise ValueError("public task requires a non-empty episode.brief")

    raw_result_paths = episode.get("result_paths")
    if not isinstance(raw_result_paths, list) or not raw_result_paths:
        raise ValueError("public task requires episode.result_paths")
    result_paths: list[str] = []
    for value in raw_result_paths:
        path = Path(str(value))
        assert_safe_relative_path(path)
        if not path.parts or path.parts[0] != "results":
            raise ValueError("each result path must be below results/")
        result_paths.append(path.as_posix())
    if len(result_paths) != len(set(result_paths)):
        raise ValueError("duplicate result path")

    assets = manifest.get("assets")
    raw_public = assets.get("public") if isinstance(assets, dict) else None
    if not isinstance(raw_public, list) or not raw_public:
        raise ValueError("public task requires an assets.public allowlist")
    public_assets: list[str] = []
    for value in raw_public:
        path = Path(str(value))
        assert_safe_relative_path(path)
        if not path.parts or path.parts[0] != "public":
            raise ValueError("each public asset must be below public/")
        if not (task_dir / path).is_file():
            raise ValueError(f"missing public asset: {path.as_posix()}")
        public_assets.append(path.as_posix())

    actual_public = {
        path.relative_to(task_dir).as_posix()
        for path in (task_dir / "public").rglob("*")
        if path.is_file()
    }
    if actual_public != set(public_assets):
        missing = sorted(actual_public - set(public_assets))
        stale = sorted(set(public_assets) - actual_public)
        raise ValueError(
            f"assets.public is not an exact allowlist; unlisted={missing}, missing={stale}"
        )

    return PublicSingleAgentTask(
        root=task_dir,
        manifest_path=manifest_path,
        manifest=manifest,
        task_id=task_id,
        brief=str(episode["brief"]).strip(),
        result_paths=tuple(result_paths),
        public_assets=tuple(public_assets),
    )


_SAFE_COMPONENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,95}\Z")


def _validate_component(value: str, label: str) -> None:
    if not _SAFE_COMPONENT.fullmatch(value) or ".." in value:
        raise ValueError(f"unsafe {label}: {value!r}")


def _mkdir_private(path: Path, *, exist_ok: bool) -> None:
    if path.is_symlink():
        raise ValueError(f"refusing symlink for private episode path: {path}")
    path.mkdir(parents=True, exist_ok=exist_ok)
    if not path.is_dir() or path.is_symlink():
        raise ValueError(f"episode path is not a real directory: {path}")
    path.chmod(0o700)


def _write_private_json(path: Path, value: Mapping[str, Any]) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    path.chmod(0o600)


def _append_private_jsonl(path: Path, value: Mapping[str, Any]) -> None:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    descriptor = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        os.write(descriptor, (payload + "\n").encode("utf-8"))
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def prepare_episode_layout(
    task: PublicSingleAgentTask, run_root: Path, run_id: str
) -> SingleAgentEpisodeLayout:
    _validate_component(task.task_id, "task_id")
    _validate_component(run_id, "run_id")
    run_root = Path(run_root).expanduser().resolve()
    task_runs = run_root / task.task_id
    _mkdir_private(task_runs, exist_ok=True)
    episode_root = task_runs / run_id
    _mkdir_private(episode_root, exist_ok=False)
    export_root = episode_root / "export"
    _mkdir_private(export_root, exist_ok=False)
    return SingleAgentEpisodeLayout(
        episode_root=episode_root,
        export_root=export_root,
        task_public_root=(task.root / "public").resolve(),
        # A run id is only unique inside one run root.  A random suffix prevents
        # two independent projects with the same task/run names from sharing
        # mutable workspace or control state.
        workspace_volume=f"jarvisbench-single-{secrets.token_hex(12)}",
    )


class SingleAgentRunner:
    """Run one task on one worker with an optional attention controller."""

    setting = "single_agent"
    execution_nodes = ("worker-0",)
    manager = None

    def __init__(
        self,
        worker: SingleAgentWorkerPort,
        controller: SingleAgentControllerPort | None = None,
    ) -> None:
        self.worker = worker
        self.controller = controller

    def run(
        self,
        task_dir: Path,
        run_root: Path,
        *,
        run_id: str,
    ) -> SingleAgentRunResult:
        task = load_single_agent_task(task_dir)
        layout = prepare_episode_layout(task, run_root, run_id)
        prompt_kind = (
            PromptKind.CONTROLLED if self.controller is not None else PromptKind.BASELINE
        )
        prompt = build_worker_prompt(task.manifest, kind=prompt_kind)
        prompt_path = layout.episode_root / "prompt.txt"
        prompt_path.write_text(prompt, encoding="utf-8")
        prompt_path.chmod(0o600)

        controller_name = (
            str(getattr(self.controller, "name", "external"))
            if self.controller is not None
            else "none"
        )
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", controller_name):
            raise ValueError("controller name must be a short safe identifier")
        plan = {
            "schema_version": "1.0",
            "setting": self.setting,
            "track": "agent_collaboration",
            "task_id": task.task_id,
            "run_id": run_id,
            "execution_nodes": list(self.execution_nodes),
            "manager": self.manager,
            "controller": controller_name,
            "prompt_kind": prompt_kind.value,
            "container": {
                "task_mount": layout.task_mount,
                "workspace": layout.workspace,
                "results": layout.results,
                "state": layout.state,
                "openclaw_home": layout.openclaw_home,
                "control_root": layout.control_root,
                "event_root": layout.event_root,
                "host_output": layout.host_output,
                "mounts": [
                    {
                        "kind": mount.kind,
                        "source_class": (
                            "docker_named_volume" if mount.kind == "volume" else
                            "public_task" if mount.read_only else "explicit_export"
                        ),
                        "target": mount.target,
                        "read_only": mount.read_only,
                    }
                    for mount in layout.docker_mounts()
                ],
            },
            "provenance": {
                "public_task_manifest_sha256": sha256_file(task.manifest_path),
                "public_assets_sha256": sha256_tree((task.root / "public",), task.root),
                "worker_contract_sha256": worker_contract_sha256(
                    task.root, task.manifest
                ),
                "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
                "frozen_single_agent": FROZEN_SINGLE_AGENT_PROVENANCE,
            },
        }
        _write_private_json(layout.episode_root / "plan.json", plan)

        session_id = "worker-0"
        candidate_count = 0
        attention_request_count = 0
        # This is a bounded, non-authoritative audit export.  Authoritative
        # control/event state remains below layout.state in the named volume.
        review_receipts_path = layout.episode_root / "review_receipts.jsonl"

        def review(candidate: BoundaryCandidate) -> ControllerReview:
            nonlocal candidate_count, attention_request_count
            if candidate.session_id != session_id:
                raise RuntimeError("single-agent candidate belongs to another session")
            candidate_count += 1
            if self.controller is None:
                decision = AttentionDecision(
                    False, "baseline controller disabled"
                )
            else:
                try:
                    proposed = self.controller.decide(candidate)
                    if not isinstance(proposed, AttentionDecision):
                        raise TypeError("controller returned the wrong response type")
                    if len(proposed.reason) > 2_000 or (
                        proposed.question is not None
                        and len(proposed.question) > 2_000
                    ):
                        raise ValueError("controller decision exceeds the bounded contract")
                    decision = proposed
                except Exception as exc:  # fail closed without logging secret text
                    decision = AttentionDecision(
                        False,
                        f"controller failed closed: {type(exc).__name__}",
                    )
            bound = ControllerReview.bind(candidate, decision)
            if decision.request_attention:
                attention_request_count += 1
            _append_private_jsonl(
                review_receipts_path,
                {
                    "type": "single_agent.controller_review.receipt",
                    "session_id": bound.session_id,
                    "epoch": bound.epoch,
                    "nonce": bound.nonce,
                    "action_id": bound.action_id,
                    "action_fingerprint": bound.action_fingerprint,
                    "controller": controller_name,
                    "request_attention": decision.request_attention,
                    "scope": decision.scope,
                    "reason": decision.reason,
                    "question": decision.question,
                },
            )
            return bound

        request = SingleAgentWorkerRequest(
            task_id=task.task_id,
            session_id=session_id,
            prompt=prompt,
            prompt_kind=prompt_kind,
            required_result_paths=task.result_paths,
            layout=layout,
        )
        execution = self.worker.execute(request, review)

        required = set(task.result_paths)
        claimed: set[str] = set()
        for value in execution.exported_paths:
            path = Path(value)
            assert_safe_relative_path(path)
            claimed.add(path.as_posix())
        export_entries = list(layout.export_root.rglob("*"))
        if any(path.is_symlink() for path in export_entries):
            raise RuntimeError("symlinks are forbidden in exported results")
        existing = {
            path.relative_to(layout.export_root).as_posix()
            for path in export_entries
            if path.is_file()
        }
        missing = tuple(sorted(required - (claimed & existing)))
        status = execution.status
        if status == "completed" and missing:
            status = "incomplete"

        results_manifest = {
            "schema_version": "1.0",
            "files": [
                {
                    "path": path,
                    "sha256": sha256_file(layout.export_root / path),
                    "bytes": (layout.export_root / path).stat().st_size,
                }
                for path in sorted(existing)
            ],
        }
        _write_private_json(
            layout.episode_root / "results_manifest.json", results_manifest
        )

        result = SingleAgentRunResult(
            task_id=task.task_id,
            run_id=run_id,
            status=status,
            controller=controller_name,
            prompt_kind=prompt_kind.value,
            candidate_count=candidate_count,
            attention_request_count=attention_request_count,
            required_result_paths=task.result_paths,
            missing_result_paths=missing,
            episode_root=layout.episode_root,
            workspace_volume=layout.workspace_volume,
        )
        record = result.to_record()
        record["schema_version"] = "1.0"
        record["worker_diagnostics"] = dict(execution.diagnostics)
        _write_private_json(layout.episode_root / "run.json", record)
        return result
