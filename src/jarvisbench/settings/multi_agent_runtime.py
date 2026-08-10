from __future__ import annotations

import hashlib
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
import uuid
from importlib import resources
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from jarvisbench.reference.dynamic_mas.contracts import (
    CANONICAL_DYNAMIC_MAS_SOURCE_SHA256,
    canonical_json,
)
from jarvisbench.reference.dynamic_mas.scheduler import DynamicMasScheduler
from jarvisbench.reference.dynamic_mas.service import DynamicMasService
from jarvisbench.core.providers import resolve_secret_file


PARENT_SESSION_ID = "chat"
PARENT_SESSION_KEY = "agent:main:chat"
PROTOCOL = ("parent_delegation", "children_complete", "parent_integration")
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


def _is_supervisor_plugin_dir(path: Path) -> bool:
    return path.is_dir() and all((path / name).is_file() for name in SUPERVISOR_PLUGIN_FILES)


def default_supervisor_plugin_dir() -> Path:
    """Resolve the installed wheel resource, with a source-checkout fallback.

    A normal wheel installation expands package resources onto the filesystem,
    which OpenClaw requires because it loads the extension by directory.  The
    fallback preserves development directly from a source checkout; it is not a
    server path or an alternate packaged copy.
    """

    packaged = resources.files("jarvisbench").joinpath(
        "_resources", "openclaw", "jarvis_supervisor"
    )
    packaged_path = Path(str(packaged))
    if _is_supervisor_plugin_dir(packaged_path):
        return packaged_path
    return Path(__file__).resolve().parents[3] / "plugins" / "openclaw" / "jarvis_supervisor"


@dataclass(frozen=True)
class MultiAgentRuntimeConfig:
    task_dir: Path
    episode_root: Path
    worker_model: str
    provider_base_url: str = ""
    api_key_env: str = "JARVISBENCH_WORKER_API_KEY"
    api_key_file: Path | None = None
    thinking: str = "provider_default"
    openclaw_executable: str = "openclaw"
    plugin_dir: Path | None = None
    workspace_root: Path | None = None
    environment_passthrough: tuple[str, ...] = ()
    poll_seconds: float = 0.2
    project_id: str = ""

    def __post_init__(self) -> None:
        if "/" not in self.worker_model:
            raise ValueError("worker_model must use provider/model-id syntax")
        if not self.api_key_env or "=" in self.api_key_env:
            raise ValueError("api_key_env must name one environment variable")
        if any(
            not name or "=" in name or "\x00" in name
            for name in self.environment_passthrough
        ):
            raise ValueError("environment_passthrough contains an invalid name")


@dataclass(frozen=True)
class MultiAgentRuntimePlan:
    task_id: str
    project_id: str
    protocol: tuple[str, ...]
    parent_session_id: str
    gateway_bind: str
    gateway_auth: str
    native_children: bool
    local_agent_mode: bool
    expected_children: int
    delegation_prompt_sha256: str
    integration_prompt_sha256: str
    canonical_dynamic_source_sha256: str
    mutable_state_roots: tuple[str, ...]


@dataclass(frozen=True)
class MultiAgentRunResult:
    task_id: str
    project_id: str
    status: str
    delegate_exit_code: int
    integration_exit_code: int
    children_discovered: int
    children_completed: int
    required_results_present: bool
    output_dir: str


def _load_public_manifest(task_dir: Path) -> dict[str, Any]:
    path = Path(task_dir) / "task.public.json"
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 256 * 1024:
        raise ValueError("task.public.json is missing or unsafe")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("task.public.json must be an object")
    forbidden = {"attention", "grading", "private", "rubric", "reference_solution"}
    if forbidden.intersection(value):
        raise ValueError("public task manifest contains evaluator/requester-only fields")
    episode = value.get("episode")
    runtime = value.get("runtime")
    if not isinstance(episode, Mapping) or not isinstance(runtime, Mapping):
        raise ValueError("public task manifest lacks episode/runtime")
    if type(episode.get("worker_count")) is not int or int(episode["worker_count"]) < 1:
        raise ValueError("multi-agent task has invalid worker_count")
    result_paths = episode.get("result_paths")
    if not isinstance(result_paths, list) or not result_paths:
        raise ValueError("multi-agent task lacks result paths")
    for item in result_paths:
        if not isinstance(item, str) or not item.startswith("results/") or ".." in item.split("/"):
            raise ValueError("result path leaves results/")
    return value


def build_delegation_prompt(manifest: Mapping[str, Any]) -> str:
    """Frozen worker-visible parent delegation prompt from canonical MAS v1."""

    episode = manifest["episode"]
    worker_count = int(episode["worker_count"])
    outputs = "\n".join(f"- /workspace/{path}" for path in episode["result_paths"])
    return (
        "You are the parent coordinator in a private, non-interactive multi-agent "
        "benchmark sandbox. Complete the project fully and keep working until the "
        "requested artifacts exist or the time budget expires.\n\n"
        "All task materials are under /workspace. Inspect them rather than guessing. "
        f"The file /workspace/workstreams.json declares exactly {worker_count} substantive "
        "workstreams. Spawn one native OpenClaw sub-agent for each workstream using "
        "sessions_spawn. Pass only runtime, mode, label, and task; omit agentId and "
        "streamTo so each child uses the configured native sub-agent runtime. "
        f"Start all {worker_count} workers before waiting so their work overlaps. Give each child the "
        "corresponding brief verbatim, use isolated context, and have it write only to "
        f"its declared results directory. After exactly {worker_count} accepted spawns, do not poll, "
        "do not perform any worker stream yourself, and do not produce integrated outputs "
        "yet. End this delegation turn with DELEGATION_STARTED. The benchmark harness "
        f"will resume this same session after all {worker_count} native children finish.\n\n"
        "For app-like collections and side effects, use the `jb` command; run "
        "`jb collections` and `jb status` if needed. Save deliverables only under "
        "/workspace/results. The project decision structure at "
        "/workspace/result_schema.json applies to the task's declared project-level "
        "JSON artifact and is mandatory. Required files and "
        "machine-readable artifact shapes are public at "
        "/workspace/output_contract.json.\n\n"
        "The requester is unavailable after the opening brief. If a preference, "
        "authority boundary, or private fact cannot be determined, make the most "
        "reasonable reversible choice, state the assumption in the deliverable, "
        "and continue. Do not wait for a reply.\n\n"
        "Required result paths:\n"
        f"{outputs}\n\n"
        "Project request:\n"
        f"{str(episode['brief']).strip()}\n"
    )


def build_integration_prompt(manifest: Mapping[str, Any]) -> str:
    """Frozen worker-visible parent integration prompt from canonical MAS v1."""

    episode = manifest["episode"]
    worker_count = int(episode["worker_count"])
    outputs = "\n".join(f"- /workspace/{path}" for path in episode["result_paths"])
    return (
        f"All {worker_count} native sub-agents have now completed. This is the integration phase of "
        "the same project. Do not spawn any more workers. Inspect all workers' actual "
        "artifacts under /workspace/results, verify their evidence against the public "
        "task materials, reconcile cross-stream conflicts, and finish every integrated "
        "project deliverable. The requester remains unavailable: preserve any "
        "owner-controlled uncertainty, choose the most reasonable reversible default, "
        "state the assumption, and continue. Keep working until every required path "
        "exists.\n\nRequired result paths:\n"
        f"{outputs}\n"
    )


def _provider_and_model(model: str) -> tuple[str, str]:
    provider, model_id = model.split("/", 1)
    if not provider or not model_id:
        raise ValueError("worker_model must use provider/model-id syntax")
    return provider, model_id


class MultiAgentRuntime:
    """Docker-local formal MAS runner using the native loopback Gateway.

    Parent is only an execution manager.  The optional scheduler is a distinct
    object with its own state, prompts, model calls, and requester channel.
    """

    def __init__(
        self,
        config: MultiAgentRuntimeConfig,
        *,
        scheduler: DynamicMasScheduler | None = None,
    ) -> None:
        self.config = config
        self.scheduler = scheduler
        self.manifest = _load_public_manifest(config.task_dir)
        self.task_id = str(self.manifest["task_id"])
        scheduler_project = scheduler.project_id if scheduler is not None else ""
        self.project_id = config.project_id or scheduler_project or f"{self.task_id}:{uuid.uuid4().hex}"
        if scheduler is not None and scheduler.project_id != self.project_id:
            raise ValueError("runtime and Jarvis scheduler must share one project_id")
        self.episode_root = Path(config.episode_root)
        self.home = self.episode_root / "private" / "home"
        self.openclaw_home = self.home / ".openclaw"
        configured_workspace = config.workspace_root or os.environ.get(
            "JARVISBENCH_WORKSPACE_ROOT"
        )
        self.workspace = (
            Path(configured_workspace)
            if configured_workspace
            else self.episode_root / "workspace"
        )
        self.output = self.episode_root / "output"
        self.control_root = self.episode_root / "private" / "control"
        self.events_root = self.episode_root / "private" / "events"
        self.logs_root = self.episode_root / "private" / "logs"
        self.events_path = self.events_root / "project_events.jsonl"
        self.registry_path = self.control_root / "registry.json"
        self.sessions_root = self.openclaw_home / "agents" / "main" / "sessions"
        self.plugin_dir = (
            Path(config.plugin_dir)
            if config.plugin_dir is not None
            else default_supervisor_plugin_dir()
        )
        if scheduler is not None and scheduler.control_plane.root.resolve() != self.control_root.resolve():
            raise ValueError("scheduler control state must use this episode's private control root")

    def plan(self) -> MultiAgentRuntimePlan:
        delegation = build_delegation_prompt(self.manifest)
        integration = build_integration_prompt(self.manifest)
        return MultiAgentRuntimePlan(
            task_id=self.task_id,
            project_id=self.project_id,
            protocol=PROTOCOL,
            parent_session_id=PARENT_SESSION_ID,
            gateway_bind="loopback",
            gateway_auth="loopback_none",
            native_children=True,
            local_agent_mode=False,
            expected_children=int(self.manifest["episode"]["worker_count"]),
            delegation_prompt_sha256=hashlib.sha256(delegation.encode("utf-8")).hexdigest(),
            integration_prompt_sha256=hashlib.sha256(integration.encode("utf-8")).hexdigest(),
            canonical_dynamic_source_sha256=CANONICAL_DYNAMIC_MAS_SOURCE_SHA256,
            mutable_state_roots=("private/home", "private/control", "private/events", "workspace"),
        )

    def _prepare(self) -> None:
        if self.episode_root.exists() and any(self.episode_root.iterdir()):
            raise ValueError("episode_root must be empty for session isolation")
        if self.workspace.exists() and self.workspace != self.episode_root / "workspace":
            if self.workspace.is_symlink() or not self.workspace.is_dir():
                raise ValueError("worker workspace root is unsafe")
            if any(self.workspace.iterdir()):
                raise ValueError("worker workspace root must be empty")
        private_root = self.episode_root / "private"
        private_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        private_root.chmod(0o700)
        for path in (
            self.openclaw_home,
            self.workspace / "results",
            self.output,
            self.control_root,
            self.events_root,
            self.logs_root,
        ):
            path.mkdir(parents=True, exist_ok=True, mode=0o700)
            if private_root == path or private_root in path.parents:
                path.chmod(0o700)
        public = Path(self.config.task_dir) / "public"
        if public.is_symlink() or not public.is_dir():
            raise ValueError("task public assets are missing or unsafe")
        shutil.copytree(public, self.workspace, dirs_exist_ok=True, symlinks=False)
        # ``copytree`` applies the read-only public root's mode to the existing
        # workspace destination. Public descendants stay read-only, while the
        # episode-owned root must remain writable for AGENTS.md and results.
        self.workspace.chmod(0o700)
        (self.workspace / "AGENTS.md").write_text(
            "# Benchmark runtime coordination\n\n"
            "When the only user message is a runtime-generated internal task-completion\n"
            "event from a sub-agent, reply exactly `CHILD_RESULT_ACK`. Do not summarize the\n"
            "child, inspect artifacts, call tools, ask a question, or integrate the project.\n"
            "The benchmark harness sends a separate integration message after all declared\n"
            "workers finish.\n",
            encoding="utf-8",
        )
        workspace_link = self.openclaw_home / "workspace"
        workspace_link.symlink_to(self.workspace)
        if self.scheduler is not None:
            extension = self.openclaw_home / "extensions" / "jarvisbench-mas-supervisor"
            if not _is_supervisor_plugin_dir(self.plugin_dir):
                raise ValueError("OpenClaw supervisor plugin is missing")
            shutil.copytree(self.plugin_dir, extension)
        self._write_openclaw_config()
        self.events_path.touch(mode=0o600)
        (self.output / "delegation_prompt.txt").write_text(
            build_delegation_prompt(self.manifest), encoding="utf-8"
        )
        (self.output / "integration_prompt.txt").write_text(
            build_integration_prompt(self.manifest), encoding="utf-8"
        )

    def _write_openclaw_config(self) -> None:
        if not self.config.provider_base_url:
            raise ValueError("provider_base_url is required for an executable run")
        secret_provider = "jarvisbench_worker"
        secret_value = os.environ.get(self.config.api_key_env, "")
        if secret_value:
            # Keep the resolved value out of Python-owned files. OpenClaw
            # resolves this SecretRef only into its in-memory snapshot.
            secret_ref: dict[str, str] = {
                "source": "env",
                "provider": secret_provider,
                "id": self.config.api_key_env,
            }
            secret_config: dict[str, Any] = {
                "source": "env",
                "allowlist": [self.config.api_key_env],
            }
        else:
            secret_file = resolve_secret_file(
                file_env="JARVISBENCH_WORKER_API_KEY_FILE",
                explicit_file=self.config.api_key_file,
            )
            if secret_file is None:
                raise ValueError(
                    f"{self.config.api_key_env} or a worker credential file is required "
                    "for an executable run"
                )
            secret_ref = {
                "source": "file",
                "provider": secret_provider,
                "id": "value",
            }
            secret_config = {
                "source": "file",
                "path": str(secret_file),
                "mode": "singleValue",
            }
        provider, model_id = _provider_and_model(self.config.worker_model)
        model: dict[str, Any] = {"id": model_id, "name": model_id, "input": ["text", "image"]}
        if self.config.thinking not in {"", "off", "provider_default"}:
            model.update({"reasoning": True, "compat": {"supportsReasoningEffort": True}})
        value: dict[str, Any] = {
            "secrets": {"providers": {secret_provider: secret_config}},
            "models": {
                "providers": {
                    provider: {
                        "baseUrl": self.config.provider_base_url,
                        "apiKey": secret_ref,
                        "api": "openai-completions",
                        "models": [model],
                    }
                }
            },
        }
        if self.scheduler is not None:
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
        path = self.openclaw_home / "openclaw.json"
        path.write_bytes(canonical_json(value) + b"\n")
        path.chmod(0o600)

    def _environment(self, gateway_token: str | None = None) -> dict[str, str]:
        allowed = _WORKER_ENV_ALLOWLIST.union(self.config.environment_passthrough)
        env = {name: value for name, value in os.environ.items() if name in allowed}
        if self.config.api_key_env in os.environ:
            env[self.config.api_key_env] = os.environ[self.config.api_key_env]
        env.update(
            {
                "HOME": str(self.home),
                # OpenClaw gives OPENCLAW_HOME precedence over HOME and then
                # normally appends ``.openclaw``.  Pin all three public path
                # controls so an image-level default can never redirect one
                # episode into mutable state shared by another episode.
                "OPENCLAW_HOME": str(self.home),
                "OPENCLAW_STATE_DIR": str(self.openclaw_home),
                "OPENCLAW_CONFIG_PATH": str(self.openclaw_home / "openclaw.json"),
                "JB_WORKSPACE": str(self.workspace),
                "JARVIS_MAS_PROJECT_ID": self.project_id,
                "JARVIS_MAS_CONTROL_ROOT": str(self.control_root),
                "JARVIS_MAS_REGISTRY_JSON": str(self.registry_path),
                "JARVIS_MAS_PARENT_SESSION_KEY": PARENT_SESSION_KEY,
                "JARVIS_MAS_PARENT_RUNTIME_SESSION_ID": PARENT_SESSION_ID,
                "JARVIS_HOOK_EVENTS_JSONL": str(self.events_path),
                "JARVIS_MAS_PLUGIN_READY_JSON": str(self.control_root / "plugin_ready.json"),
                "JARVIS_MAS_DYNAMIC_REQUIRED": "1",
                "JARVIS_WORKSPACE_ROOT": str(self.workspace),
                "JARVIS_AUTONOMOUS_REVIEW": "1" if self.scheduler is not None else "0",
                "JARVIS_REVIEW_TIMEOUT_MS": "420000",
                "JARVIS_REVIEW_POLL_MS": "100",
            }
        )
        if gateway_token:
            env["OPENCLAW_GATEWAY_TOKEN"] = gateway_token
        return env

    def _configure_openclaw(self, env: Mapping[str, str], *, gateway_port: int) -> None:
        expected = int(self.manifest["episode"]["worker_count"])
        timeout_seconds = int(self.manifest["runtime"]["worker_timeout_seconds"])
        required: list[list[str]] = [
            [self.config.openclaw_executable, "models", "set", self.config.worker_model],
            [self.config.openclaw_executable, "config", "set", "tools.profile", "full"],
            [
                self.config.openclaw_executable,
                "config",
                "set",
                "agents.defaults.subagents.maxConcurrent",
                str(expected),
            ],
            [
                self.config.openclaw_executable,
                "config",
                "set",
                "agents.defaults.subagents.maxChildrenPerAgent",
                str(expected),
            ],
            [
                self.config.openclaw_executable,
                "config",
                "set",
                "agents.defaults.subagents.maxSpawnDepth",
                "1",
            ],
            [
                self.config.openclaw_executable,
                "config",
                "set",
                "agents.defaults.subagents.runTimeoutSeconds",
                str(timeout_seconds),
            ],
            [self.config.openclaw_executable, "config", "set", "gateway.mode", "local"],
            [self.config.openclaw_executable, "config", "set", "gateway.bind", "loopback"],
            [
                self.config.openclaw_executable,
                "config",
                "set",
                "gateway.port",
                str(gateway_port),
            ],
            [self.config.openclaw_executable, "config", "set", "gateway.auth.mode", "none"],
        ]
        if self.config.thinking and self.config.thinking != "provider_default":
            required.append(
                [
                    self.config.openclaw_executable,
                    "config",
                    "set",
                    "agents.defaults.thinkingDefault",
                    self.config.thinking,
                ]
            )
        for command in required:
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

    @staticmethod
    def _free_port() -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            return int(sock.getsockname()[1])

    def _run_agent(self, message: str, log_name: str, env: Mapping[str, str]) -> int:
        timeout_seconds = int(self.manifest["runtime"]["worker_timeout_seconds"])
        command = [
            self.config.openclaw_executable,
            "agent",
            "--session-id",
            PARENT_SESSION_ID,
            "--timeout",
            str(timeout_seconds),
        ]
        if self.config.thinking and self.config.thinking != "provider_default":
            command.extend(("--thinking", self.config.thinking))
        command.extend(("--message", message))
        # Agent/Gateway output can contain raw model text.  It is research
        # trace, never part of the compact release-facing result export.
        log_path = self.logs_root / log_name
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

    def _session_index(self) -> dict[str, Any]:
        path = self.sessions_root / "sessions.json"
        if not path.is_file() or path.is_symlink():
            return {}
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}

    def _children(self) -> list[Mapping[str, Any]]:
        return [
            value
            for value in self._session_index().values()
            if isinstance(value, Mapping) and value.get("spawnedBy") == PARENT_SESSION_KEY
        ]

    def _children_completed(self) -> int:
        completed = 0
        for child in self._children():
            session_id = child.get("sessionId")
            if not isinstance(session_id, str):
                continue
            transcript = self.sessions_root / f"{session_id}.jsonl"
            if not transcript.is_file() or transcript.is_symlink():
                continue
            for raw in reversed(transcript.read_text(encoding="utf-8", errors="replace").splitlines()):
                try:
                    record = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                message = record.get("message") if isinstance(record, Mapping) else None
                if not isinstance(message, Mapping) or message.get("role") != "assistant":
                    continue
                if message.get("stopReason") == "stop":
                    completed += 1
                break
        return completed

    def _wait_for_children(self, gateway: subprocess.Popen[bytes]) -> tuple[int, int]:
        expected = int(self.manifest["episode"]["worker_count"])
        deadline = time.monotonic() + int(self.manifest["runtime"]["worker_timeout_seconds"])
        discovered = 0
        completed = 0
        while time.monotonic() < deadline:
            if gateway.poll() is not None:
                break
            discovered = len(self._children())
            completed = self._children_completed()
            if discovered == expected and completed == expected:
                return discovered, completed
            time.sleep(max(0.05, self.config.poll_seconds))
        return discovered, completed

    def _effective_integration_prompt(self) -> str:
        original = build_integration_prompt(self.manifest)
        if self.scheduler is None:
            return original
        evidence = tuple(
            f"{binding['agent_id']} ({binding['workstream_id']}) status={binding['status']}"
            for binding in self.scheduler.registry.snapshot()["sessions"].values()
            if binding["role"] == "worker"
        )
        self.scheduler.evaluate_parent_final_gate(
            project_summary="All declared child sessions reached their terminal execution boundary.",
            evidence=evidence,
            integration_prompt_sha256=hashlib.sha256(original.encode("utf-8")).hexdigest(),
        )
        # Keep the frozen user message byte-identical. Parent guidance is
        # delivered once by the supervisor as authenticated system context at
        # this new model boundary, producing exact delivery/application events.
        return original

    def _plugin_ready(self) -> bool:
        path = self.control_root / "plugin_ready.json"
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 32 * 1024:
            return False
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        return bool(
            isinstance(value, dict)
            and value.get("ready") is True
            and value.get("plugin_id") == "jarvisbench-mas-supervisor"
            and value.get("control_protocol_version") == "1.0-release"
            and value.get("project_id") == self.project_id
            and value.get("hooks_registration_complete") is True
            and int(value.get("ready_event_seq", 0)) > 0
        )

    def _dynamic_admission(self, expected_children: int) -> bool:
        if self.scheduler is None:
            return True
        path = self.output / "dynamic_mas_diagnostics.json"
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 64 * 1024:
            return False
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        return bool(
            value.get("project_id") == self.project_id
            and value.get("raw_trace_included") is False
            and value.get("attention_channel_serialized") is True
            and value.get("registered_workers") == expected_children
            and value.get("live_updates_before_completion") == expected_children
            and value.get("receipt_closure_valid") is True
            and value.get("unresolved_delivery_receipts") == 0
            and value.get("orphan_application_receipts") == 0
            and value.get("service_errors") == []
            and self._plugin_ready()
        )

    def _export_results(self) -> bool:
        destination = self.output / "results"
        destination.mkdir(parents=True, exist_ok=True)
        for path in (self.workspace / "results").rglob("*"):
            if path.is_symlink():
                raise RuntimeError("worker result contains a symlink")
            relative = path.relative_to(self.workspace / "results")
            target = destination / relative
            if path.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            elif path.is_file():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target)
        return all(
            (self.workspace / str(path)).is_file()
            and not (self.workspace / str(path)).is_symlink()
            and (self.workspace / str(path)).stat().st_size > 0
            for path in self.manifest["episode"]["result_paths"]
        )

    def run(self) -> MultiAgentRunResult:
        self._prepare()
        port = self._free_port()
        env = self._environment()
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
        service: DynamicMasService | None = None
        gateway_log: Any | None = None
        gateway: subprocess.Popen[bytes] | None = None
        delegate_rc = 70
        integration_rc = 71
        discovered = 0
        completed = 0
        try:
            if self.scheduler is not None:
                service = DynamicMasService(
                    scheduler=self.scheduler,
                    events_path=self.events_path,
                    sessions_root=self.sessions_root,
                    workspace=self.workspace,
                    registry_path=self.registry_path,
                    diagnostics_path=self.output / "dynamic_mas_diagnostics.json",
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
            ready = False
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
            if self.scheduler is not None:
                plugin_ready = False
                for _ in range(80):
                    if self._plugin_ready():
                        plugin_ready = True
                        break
                    if gateway.poll() is not None:
                        break
                    time.sleep(0.25)
                if not plugin_ready:
                    raise RuntimeError("dynamic MAS supervisor did not become ready")
            delegate_rc = self._run_agent(
                build_delegation_prompt(self.manifest),
                "parent_delegate.log",
                env,
            )
            discovered, completed = self._wait_for_children(gateway)
            expected = int(self.manifest["episode"]["worker_count"])
            if discovered == expected and completed == expected:
                # Preserve the native protocol: same Gateway, same Parent session.
                time.sleep(2.0)
                integration_rc = self._run_agent(
                    self._effective_integration_prompt(),
                    "parent_integrate.log",
                    env,
                )
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
                    # Stop the event source first, then perform the service's
                    # final drain so late Parent/child receipts cannot be lost.
                    service.stop()
                except Exception as exc:
                    cleanup_errors.append(f"control_service:{type(exc).__name__}")
            if gateway_log is not None:
                try:
                    gateway_log.close()
                except Exception as exc:
                    cleanup_errors.append(f"gateway_log:{type(exc).__name__}")
            if cleanup_errors and not primary_error:
                raise RuntimeError("MAS cleanup failed: " + ",".join(cleanup_errors))

        results_present = self._export_results()
        expected = int(self.manifest["episode"]["worker_count"])
        dynamic_admitted = self._dynamic_admission(expected)
        status = (
            "completed"
            if delegate_rc == 0
            and integration_rc == 0
            and discovered == expected
            and completed == expected
            and results_present
            and dynamic_admitted
            else "failed"
        )
        result = MultiAgentRunResult(
            task_id=self.task_id,
            project_id=self.project_id,
            status=status,
            delegate_exit_code=delegate_rc,
            integration_exit_code=integration_rc,
            children_discovered=discovered,
            children_completed=completed,
            required_results_present=results_present,
            output_dir=str(self.output),
        )
        (self.output / "run.json").write_bytes(
            canonical_json(
                {
                    **asdict(result),
                    "worker_model": self.config.worker_model,
                    "thinking": self.config.thinking,
                    "protocol": list(PROTOCOL),
                    "controller": "reference" if self.scheduler is not None else "none",
                    "dynamic_control_admitted": dynamic_admitted,
                    "canonical_dynamic_source_sha256": CANONICAL_DYNAMIC_MAS_SOURCE_SHA256,
                }
            )
            + b"\n"
        )
        return result
