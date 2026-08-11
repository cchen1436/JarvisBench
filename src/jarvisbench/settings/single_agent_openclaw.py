"""Executable one-worker OpenClaw transport for the public single setting."""

from __future__ import annotations

import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from jarvisbench.core.contracts import BoundaryCandidate
from jarvisbench.core.decision_ledger import DecisionLedger
from jarvisbench.core.providers import read_secret
from jarvisbench.reference.dynamic_mas.contracts import canonical_json
from jarvisbench.reference.single_agent.control import SingleAgentControlService

from .multi_agent_runtime import default_supervisor_plugin_dir
from .single_agent_runtime import (
    ControllerReview,
    PromptKind,
    SingleAgentWorkerRequest,
    WorkerExecution,
)


SUPERVISOR_PLUGIN_FILES = frozenset(
    {"index.ts", "openclaw.plugin.json", "package.json", "read_only_exec.ts"}
)

_WORKER_ENV_ALLOWLIST = frozenset(
    {
        "PATH",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "TZ",
        "TMPDIR",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "NODE_OPTIONS",
        "NODE_PATH",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "NO_PROXY",
        "http_proxy",
        "https_proxy",
        "no_proxy",
    }
)


def _is_plugin_dir(path: Path) -> bool:
    return path.is_dir() and all((path / name).is_file() for name in SUPERVISOR_PLUGIN_FILES)


@dataclass(frozen=True)
class OpenClawSingleAgentConfig:
    worker_model: str
    api_key_env: str = "ANTHROPIC_API_KEY"
    api_key_file: Path | None = None
    thinking: str = "provider_default"
    openclaw_executable: str = "openclaw"
    plugin_dir: Path | None = None
    runtime_root: Path | None = None
    workspace_root: Path | None = None
    environment_passthrough: tuple[str, ...] = ()
    poll_seconds: float = 0.1
    review_timeout_ms: int = 420_000
    project_id: str = ""

    def __post_init__(self) -> None:
        if "/" not in self.worker_model:
            raise ValueError("worker_model must use provider/model-id syntax")
        if not self.api_key_env or "=" in self.api_key_env:
            raise ValueError("api_key_env must name one environment variable")
        if self.thinking not in {"provider_default", "off", "low", "medium", "high"}:
            raise ValueError("unsupported thinking setting")
        if self.review_timeout_ms < 1_000:
            raise ValueError("review_timeout_ms is too small")
        if any(
            not name or "=" in name or "\x00" in name
            for name in self.environment_passthrough
        ):
            raise ValueError("environment_passthrough contains an invalid name")


class OpenClawSingleAgentWorker:
    """Run one fixed worker through a loopback Gateway (never ``--local``)."""

    def __init__(self, config: OpenClawSingleAgentConfig) -> None:
        self.config = config
        self.plugin_dir = (
            Path(config.plugin_dir)
            if config.plugin_dir is not None
            else default_supervisor_plugin_dir()
        )
        base = Path(config.runtime_root) if config.runtime_root is not None else Path(
            os.environ.get("JARVISBENCH_RUNTIME_ROOT", tempfile.gettempdir())
        ) / "jarvisbench-runtime"
        base.mkdir(parents=True, exist_ok=True, mode=0o700)
        base.chmod(0o700)
        self.runtime_root = Path(
            tempfile.mkdtemp(prefix="single-agent-", dir=base)
        )
        self.runtime_root.chmod(0o700)
        self.home = self.runtime_root / "home"
        self.openclaw_home = self.home / ".openclaw"
        configured_workspace = config.workspace_root or os.environ.get(
            "JARVISBENCH_WORKSPACE_ROOT"
        )
        self.workspace = (
            Path(configured_workspace)
            if configured_workspace
            else self.runtime_root / "workspace"
        )
        self.control_root = self.runtime_root / "control"
        self.events_root = self.runtime_root / "events"
        self.logs_root = self.runtime_root / "logs"
        self.events_path = self.events_root / "project_events.jsonl"
        self.registry_path = self.control_root / "registry.json"
        self.plugin_ready_path = self.control_root / "plugin_ready.json"

    @staticmethod
    def _free_port() -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            return int(sock.getsockname()[1])

    def _prepare(self, request: SingleAgentWorkerRequest) -> None:
        self.openclaw_home.mkdir(parents=True, exist_ok=False, mode=0o700)
        if self.workspace.exists():
            if self.workspace.is_symlink() or not self.workspace.is_dir():
                raise ValueError("worker workspace root is unsafe")
            if any(self.workspace.iterdir()):
                raise ValueError("worker workspace root must be empty")
        else:
            self.workspace.mkdir(parents=True, mode=0o700)
        for path in (
            self.workspace / "results",
            self.control_root,
            self.events_root,
            self.logs_root,
        ):
            path.mkdir(parents=True, exist_ok=True, mode=0o700)
        shutil.copytree(
            request.layout.task_public_root,
            self.workspace,
            dirs_exist_ok=True,
            symlinks=False,
        )
        self.workspace.chmod(0o700)
        workspace_link = self.openclaw_home / "workspace"
        workspace_link.symlink_to(self.workspace)
        if request.prompt_kind is PromptKind.CONTROLLED:
            if not _is_plugin_dir(self.plugin_dir):
                raise ValueError("OpenClaw supervisor plugin is missing")
            extension = self.openclaw_home / "extensions" / "jarvisbench-mas-supervisor"
            shutil.copytree(self.plugin_dir, extension)
        self._write_openclaw_config(controlled=request.prompt_kind is PromptKind.CONTROLLED)

    def _write_openclaw_config(self, *, controlled: bool) -> None:
        # OpenClaw owns provider selection and request formatting. JarvisBench
        # configures only its supervisor plugin; it never synthesizes a custom
        # OpenAI-compatible provider catalog.
        value: dict[str, Any] = {}
        if controlled:
            value["plugins"] = {
                "enabled": True,
                "allow": ["jarvisbench-mas-supervisor"],
                "entries": {
                    "jarvisbench-mas-supervisor": {
                        "enabled": True,
                        "hooks": {"allowPromptInjection": True},
                    }
                },
            }
        target = self.openclaw_home / "openclaw.json"
        target.write_bytes(canonical_json(value) + b"\n")
        target.chmod(0o600)

    def _environment(self, *, project_id: str, controlled: bool) -> dict[str, str]:
        allowed = _WORKER_ENV_ALLOWLIST.union(self.config.environment_passthrough)
        env = {name: value for name, value in os.environ.items() if name in allowed}
        worker_secret = read_secret(
            value_env=self.config.api_key_env,
            file_env="JARVISBENCH_WORKER_API_KEY_FILE",
            explicit_file=self.config.api_key_file,
        )
        if not worker_secret:
            raise ValueError(
                f"{self.config.api_key_env} or a worker credential file is required"
            )
        # This is the vendor-native environment contract OpenClaw expects. The
        # value is present only in child process memory and is never written to
        # openclaw.json, manifests, events, or diagnostics.
        env[self.config.api_key_env] = worker_secret
        env.update(
            {
                "HOME": str(self.home),
                "OPENCLAW_HOME": str(self.home),
                "OPENCLAW_STATE_DIR": str(self.openclaw_home),
                "OPENCLAW_CONFIG_PATH": str(self.openclaw_home / "openclaw.json"),
                "JB_WORKSPACE": str(self.workspace),
                "JARVIS_MAS_PROJECT_ID": project_id,
                "JARVIS_MAS_CONTROL_ROOT": str(self.control_root),
                "JARVIS_MAS_REGISTRY_JSON": str(self.registry_path),
                # The sentinel exempts no real session. The only registered and
                # executed node is worker-0; there is no synthetic manager.
                "JARVIS_MAS_PARENT_RUNTIME_SESSION_ID": "no-parent",
                "JARVIS_MAS_PARENT_SESSION_KEY": "no-parent",
                "JARVIS_HOOK_EVENTS_JSONL": str(self.events_path),
                "JARVIS_MAS_PLUGIN_READY_JSON": str(self.plugin_ready_path),
                "JARVIS_MAS_DYNAMIC_REQUIRED": "1" if controlled else "0",
                "JARVIS_WORKSPACE_ROOT": str(self.workspace),
                "JARVIS_FINAL_RECORD_PATH": str(self.workspace / "results" / "final.json"),
                "JARVIS_AUTONOMOUS_REVIEW": "1" if controlled else "0",
                "JARVIS_REVIEW_TIMEOUT_MS": str(self.config.review_timeout_ms),
                "JARVIS_REVIEW_POLL_MS": "100",
                "JARVIS_REGISTRATION_WAIT_MS": "1000",
            }
        )
        return env

    def _configure_openclaw(self, env: Mapping[str, str], *, gateway_port: int) -> None:
        commands = [
            [self.config.openclaw_executable, "models", "set", self.config.worker_model],
            [self.config.openclaw_executable, "config", "set", "tools.profile", "full"],
            [self.config.openclaw_executable, "config", "set", "gateway.mode", "local"],
            [self.config.openclaw_executable, "config", "set", "gateway.bind", "loopback"],
            [self.config.openclaw_executable, "config", "set", "gateway.port", str(gateway_port)],
            [self.config.openclaw_executable, "config", "set", "gateway.auth.mode", "none"],
        ]
        if self.config.thinking != "provider_default":
            commands.append(
                [
                    self.config.openclaw_executable,
                    "config",
                    "set",
                    "agents.defaults.thinkingDefault",
                    self.config.thinking,
                ]
            )
        for command in commands:
            completed = subprocess.run(
                command,
                cwd=self.workspace,
                env=dict(env),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=30,
                check=False,
            )
            if completed.returncode != 0:
                raise RuntimeError(f"OpenClaw configuration command failed: {command[1:3]}")

    def _plugin_ready(self, project_id: str) -> bool:
        if not self.plugin_ready_path.is_file() or self.plugin_ready_path.is_symlink():
            return False
        try:
            value = json.loads(self.plugin_ready_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        return bool(
            isinstance(value, dict)
            and value.get("ready") is True
            and value.get("project_id") == project_id
            and value.get("control_protocol_version") == "1.0-release"
            and value.get("hooks_registration_complete") is True
        )

    def _run_agent(
        self,
        request: SingleAgentWorkerRequest,
        env: Mapping[str, str],
        timeout_seconds: int,
    ) -> int:
        command = [
            self.config.openclaw_executable,
            "agent",
            "--session-id",
            request.session_id,
            "--timeout",
            str(timeout_seconds),
        ]
        if self.config.thinking != "provider_default":
            command.extend(("--thinking", self.config.thinking))
        command.extend(("--message", request.prompt))
        log_path = self.logs_root / "worker.log"
        with log_path.open("wb") as log:
            log_path.chmod(0o600)
            completed = subprocess.run(
                command,
                cwd=self.workspace,
                env=dict(env),
                stdout=log,
                stderr=subprocess.STDOUT,
                timeout=timeout_seconds + 120,
                check=False,
            )
        return int(completed.returncode)

    def _export(self, request: SingleAgentWorkerRequest) -> tuple[str, ...]:
        exported: list[str] = []
        for source in (self.workspace / "results").rglob("*"):
            if source.is_symlink():
                raise RuntimeError("worker result contains a symlink")
            relative = source.relative_to(self.workspace).as_posix()
            target = request.layout.export_root / relative
            if source.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            elif source.is_file():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
                exported.append(relative)
        return tuple(sorted(exported))

    def _export_evaluation_evidence(self, export_root: Path) -> None:
        """Preserve deterministic app actions at the frozen grader location.

        ``jb`` records mock side effects outside ``results/`` so they cannot be
        mistaken for requester-facing deliverables.  The frozen evaluator reads
        that audit stream from the root of the submitted artifact bundle,
        alongside (not inside) ``results/``.
        """

        audit_root = self.workspace / ".jarvisbench"
        for name in ("action_log.jsonl", "questions.jsonl"):
            source = audit_root / name
            if not source.exists():
                continue
            if source.is_symlink() or not source.is_file():
                raise RuntimeError(f"worker {name} is not a safe regular file")
            target = export_root / name
            if name == "questions.jsonl" and target.is_file():
                # The controller may already have logged a committed requester
                # turn. Preserve both that turn and any explicit worker `jb ask`.
                with source.open("rb") as input_stream, target.open("ab") as output_stream:
                    shutil.copyfileobj(input_stream, output_stream)
            else:
                shutil.copy2(source, target)

    @staticmethod
    def _copy_tree_private(source: Path, destination: Path) -> None:
        if not source.exists():
            return
        if source.is_symlink() or any(path.is_symlink() for path in source.rglob("*")):
            raise RuntimeError("private runtime trace contains a symlink")
        shutil.copytree(source, destination, dirs_exist_ok=False, symlinks=False)
        destination.chmod(0o700)
        for path in destination.rglob("*"):
            path.chmod(0o700 if path.is_dir() else 0o600)

    def _archive_private_runtime(self, episode_root: Path) -> None:
        """Persist raw diagnostics privately without exporting OpenClaw config.

        The config is intentionally omitted even though the native-provider
        version contains no credential path or value. Secret values are never
        serialized; an exact in-memory scan also prevents a provider/runtime
        from echoing a credential into logs or transcripts before archival.
        """

        destination = episode_root / "private" / "runtime"
        destination.mkdir(parents=True, exist_ok=False, mode=0o700)
        sessions = self.openclaw_home / "agents" / "main" / "sessions"
        for name, source in (
            ("logs", self.logs_root),
            ("sessions", sessions),
            ("events", self.events_root),
            ("control", self.control_root),
        ):
            self._copy_tree_private(source, destination / name)
        secret = read_secret(
            value_env=self.config.api_key_env,
            file_env="JARVISBENCH_WORKER_API_KEY_FILE",
            explicit_file=self.config.api_key_file,
        )
        if secret:
            needle = secret.encode("utf-8")
            for path in destination.rglob("*"):
                if path.is_file() and needle in path.read_bytes():
                    raise RuntimeError("credential material appeared in private runtime trace")

    def execute(
        self,
        request: SingleAgentWorkerRequest,
        review: Callable[[BoundaryCandidate], ControllerReview],
    ) -> WorkerExecution:
        self._prepare(request)
        project_id = self.config.project_id or f"{request.task_id}-{request.session_id}"
        controlled = request.prompt_kind is PromptKind.CONTROLLED
        env = self._environment(project_id=project_id, controlled=controlled)
        port = self._free_port()
        self._configure_openclaw(env, gateway_port=port)
        gateway_command = [
            self.config.openclaw_executable,
            "gateway",
            "run",
            "--port",
            str(port),
            "--bind",
            "loopback",
            "--auth",
            "none",
            "--allow-unconfigured",
            "--ws-log",
            "compact",
        ]
        gateway_log_path = self.logs_root / "gateway.log"
        gateway_env = dict(env)
        gateway_env["JARVIS_MAS_PLUGIN_ROLE"] = "gateway"
        service: SingleAgentControlService | None = None
        gateway_log: Any | None = None
        gateway: subprocess.Popen[bytes] | None = None
        ready = False
        plugin_ready = not controlled
        agent_rc = 70
        timeout_seconds = request.worker_timeout_seconds
        try:
            if controlled:
                service = SingleAgentControlService(
                    project_id=project_id,
                    session_id=request.session_id,
                    session_key=f"agent:main:{request.session_id}",
                    control_root=self.control_root,
                    events_path=self.events_path,
                    registry_path=self.registry_path,
                    diagnostics_path=self.runtime_root / "control_diagnostics.json",
                    decision_ledger=DecisionLedger(
                        request.layout.episode_root
                        / "private"
                        / "requester"
                        / "decision_ledger.jsonl"
                    ),
                    review=review,
                    on_attention_committed=getattr(
                        review, "on_attention_committed", None
                    ),
                    task_brief=request.task_brief,
                    required_result_paths=request.required_result_paths,
                    poll_seconds=self.config.poll_seconds,
                )
                service.start()
            gateway_log = gateway_log_path.open("wb")
            gateway_log_path.chmod(0o600)
            gateway = subprocess.Popen(
                gateway_command,
                cwd=self.workspace,
                env=gateway_env,
                stdout=gateway_log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            for _ in range(120):
                if gateway.poll() is not None:
                    break
                health = subprocess.run(
                    [self.config.openclaw_executable, "gateway", "health"],
                    cwd=self.workspace,
                    env=env,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=10,
                    check=False,
                )
                if health.returncode == 0:
                    ready = True
                    break
                time.sleep(0.5)
            if not ready:
                raise RuntimeError("OpenClaw loopback Gateway did not become healthy")
            if controlled:
                for _ in range(80):
                    if self._plugin_ready(project_id):
                        plugin_ready = True
                        break
                    if gateway.poll() is not None:
                        break
                    time.sleep(0.25)
                if not plugin_ready:
                    raise RuntimeError("single-agent supervisor did not become ready")
            agent_rc = self._run_agent(request, env, timeout_seconds)
        finally:
            primary_error = sys.exc_info()[0] is not None
            cleanup_errors: list[str] = []
            if gateway is not None:
                try:
                    if gateway.poll() is None:
                        os.killpg(gateway.pid, signal.SIGTERM)
                        try:
                            gateway.wait(timeout=15)
                        except subprocess.TimeoutExpired:
                            os.killpg(gateway.pid, signal.SIGKILL)
                            gateway.wait(timeout=5)
                except Exception as exc:
                    cleanup_errors.append(f"gateway:{type(exc).__name__}")
            if service is not None:
                try:
                    # The event source is now silent; stop() performs the final
                    # bounded drain without racing late Gateway receipts.
                    service.stop()
                except Exception as exc:
                    cleanup_errors.append(f"control_service:{type(exc).__name__}")
            if gateway_log is not None:
                try:
                    gateway_log.close()
                except Exception as exc:
                    cleanup_errors.append(f"gateway_log:{type(exc).__name__}")
            if cleanup_errors and not primary_error:
                raise RuntimeError(
                    "single-agent cleanup failed: " + ",".join(cleanup_errors)
                )

        exported = self._export(request)
        self._export_evaluation_evidence(request.layout.export_root)
        self._archive_private_runtime(request.layout.episode_root)
        if self.events_path.is_file():
            shutil.copy2(
                self.events_path,
                request.layout.episode_root / "bounded_control_trace.jsonl",
            )
        diagnostics_path = self.runtime_root / "control_diagnostics.json"
        if diagnostics_path.is_file():
            shutil.copy2(
                diagnostics_path,
                request.layout.episode_root / "control_diagnostics.json",
            )
        required_present = all(
            (request.layout.export_root / path).is_file()
            for path in request.required_result_paths
        )
        service_errors: list[Any] = []
        receipt_closure_valid = True
        if controlled:
            diagnostics_value = json.loads(diagnostics_path.read_text(encoding="utf-8"))
            raw_errors = diagnostics_value.get("service_errors", [])
            service_errors = raw_errors if isinstance(raw_errors, list) else ["malformed"]
            control = service.control_plane.read(request.session_id) if service else {}
            deliveries = {
                str(item.get("receipt_id"))
                for item in control.get("delivery_receipts", [])
                if isinstance(item, Mapping)
            }
            applications = {
                str(item.get("delivery_receipt_id"))
                for item in control.get("application_receipts", [])
                if isinstance(item, Mapping)
            }
            receipt_closure_valid = deliveries == applications
        status = (
            "completed"
            if agent_rc == 0
            and required_present
            and not service_errors
            and receipt_closure_valid
            else "failed"
        )
        return WorkerExecution(
            status,
            exported,
            diagnostics={
                "agent_exit_code": agent_rc,
                "gateway_healthy": ready,
                "plugin_ready": plugin_ready,
                "service_errors_count": len(service_errors),
                "receipt_closure_valid": receipt_closure_valid,
                "loopback_gateway": True,
                "local_agent_mode": False,
                "runtime_state_retained": True,
            },
        )
