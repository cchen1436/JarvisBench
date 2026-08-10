from __future__ import annotations

import json
import os
import secrets
from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping

from .contracts import DynamicMasContractError, SessionBinding, canonical_json, utc_now


MAX_REGISTRY_BYTES = 256 * 1024
MAX_TRANSCRIPT_TAIL_BYTES = 128 * 1024
TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})


def _read_json(path: Path, maximum: int) -> Any:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > maximum:
        raise DynamicMasContractError(f"unsafe or oversized JSON file: {path.name}")
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json_atomic(path: Path, value: object) -> None:
    encoded = canonical_json(value) + b"\n"
    if len(encoded) > MAX_REGISTRY_BYTES:
        raise DynamicMasContractError("registry exceeds its public bound")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(4)}")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(fd, encoded)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(temporary, path)
    directory_fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _workstream_ids(workspace: Path) -> tuple[str, ...]:
    path = workspace / "workstreams.json"
    if not path.is_file():
        return ()
    value = _read_json(path, MAX_REGISTRY_BYTES)
    records = value.get("workstreams") if isinstance(value, Mapping) else None
    if not isinstance(records, list):
        raise DynamicMasContractError("workstreams.json lacks a workstreams list")
    result: list[str] = []
    for record in records:
        if not isinstance(record, Mapping):
            raise DynamicMasContractError("workstream entry is malformed")
        workstream_id = str(record.get("id", ""))
        if not workstream_id:
            raise DynamicMasContractError("workstream id is empty")
        result.append(workstream_id)
    if len(result) != len(set(result)):
        raise DynamicMasContractError("workstream ids are not unique")
    return tuple(result)


def _child_status(sessions_root: Path, session_id: str) -> str:
    transcript = sessions_root / f"{session_id}.jsonl"
    if transcript.is_symlink() or not transcript.is_file():
        return "active"
    size = transcript.stat().st_size
    with transcript.open("rb") as stream:
        if size > MAX_TRANSCRIPT_TAIL_BYTES:
            stream.seek(size - MAX_TRANSCRIPT_TAIL_BYTES)
            stream.readline()
        raw_lines = stream.read().decode("utf-8", errors="replace").splitlines()
    for raw in reversed(raw_lines):
        try:
            record = json.loads(raw)
        except json.JSONDecodeError:
            continue
        message = record.get("message") if isinstance(record, Mapping) else None
        if not isinstance(message, Mapping) or message.get("role") != "assistant":
            continue
        stop_reason = str(message.get("stopReason", ""))
        if stop_reason == "stop":
            return "completed"
        if stop_reason in {"error", "aborted", "cancelled"}:
            return "failed" if stop_reason == "error" else "cancelled"
        return "active"
    return "active"


class DynamicChildRegistry:
    """Resolve native OpenClaw child sessions to stable logical workers.

    The sessions file is observed, never mutated.  Once a session id or key is
    bound, a later observation may update status but cannot change identity.
    """

    def __init__(
        self,
        *,
        project_id: str,
        parent_agent_id: str = "parent",
        parent_session_id: str = "chat",
        parent_session_key: str = "agent:main:chat",
    ) -> None:
        self.project_id = project_id
        self.parent_agent_id = parent_agent_id
        self.parent_session_id = parent_session_id
        self.parent_session_key = parent_session_key
        self._by_id: dict[str, SessionBinding] = {}
        self._aliases: dict[str, str] = {}

    def register(self, binding: SessionBinding) -> SessionBinding:
        if binding.project_id != self.project_id:
            raise DynamicMasContractError("session crossed project namespace")
        prior = self._by_id.get(binding.session_id)
        if prior is not None:
            if replace(prior, status=binding.status) != binding:
                raise DynamicMasContractError("session id was rebound to another worker")
            self._by_id[binding.session_id] = binding
            return binding
        alias = self._aliases.get(binding.session_key)
        if alias is not None and alias != binding.session_id:
            raise DynamicMasContractError("session key was rebound to another session")
        self._by_id[binding.session_id] = binding
        self._aliases[binding.session_id] = binding.session_id
        self._aliases[binding.session_key] = binding.session_id
        return binding

    def resolve(self, session_id_or_key: str) -> SessionBinding:
        canonical = self._aliases.get(session_id_or_key, session_id_or_key)
        try:
            return self._by_id[canonical]
        except KeyError as exc:
            raise DynamicMasContractError("unknown dynamic child session") from exc

    def active_workers(self) -> tuple[SessionBinding, ...]:
        return tuple(
            binding
            for binding in self._by_id.values()
            if binding.role == "worker" and binding.status not in TERMINAL_STATUSES
        )

    def parent(self) -> SessionBinding:
        return self.resolve(self.parent_session_id)

    def snapshot(self) -> dict[str, Any]:
        return {
            "schema_version": "1.0",
            "kind": "dynamic_child_registry",
            "project_id": self.project_id,
            "parent_agent_id": self.parent_agent_id,
            "parent_session_id": self.parent_session_id,
            "parent_session_key": self.parent_session_key,
            "sessions": {
                session_id: binding.to_dict()
                for session_id, binding in sorted(self._by_id.items())
            },
            "aliases": dict(sorted(self._aliases.items())),
            "updated_at": utc_now(),
        }

    def sync(self, *, sessions_root: Path, workspace: Path, output: Path) -> dict[str, Any]:
        workstreams = _workstream_ids(workspace)
        parent = SessionBinding(
            project_id=self.project_id,
            agent_id=self.parent_agent_id,
            session_id=self.parent_session_id,
            session_key=self.parent_session_key,
            parent_id=self.parent_agent_id,
            parent_session_id=self.parent_session_id,
            parent_session_key=self.parent_session_key,
            role="parent",
            workstream_id="",
            status="active",
        )
        self.register(parent)
        index_path = sessions_root / "sessions.json"
        if index_path.is_file():
            index = _read_json(index_path, MAX_REGISTRY_BYTES)
            if not isinstance(index, Mapping):
                raise DynamicMasContractError("OpenClaw session index is malformed")
            children: list[tuple[str, Mapping[str, Any]]] = []
            for session_key, raw in index.items():
                if not isinstance(raw, Mapping):
                    continue
                if str(raw.get("spawnedBy", "")) != self.parent_session_key:
                    continue
                children.append((str(session_key), raw))
            children.sort(key=lambda item: item[0])
            used_workstreams = {
                item.workstream_id for item in self._by_id.values() if item.role == "worker"
            }
            for session_key, raw in children:
                session_id = str(raw.get("sessionId", ""))
                if not session_id:
                    continue
                prior = self._by_id.get(session_id)
                label = str(raw.get("label", "")).strip()
                if prior is not None:
                    workstream_id = prior.workstream_id
                    agent_id = prior.agent_id
                else:
                    if label in workstreams and label not in used_workstreams:
                        workstream_id = label
                    else:
                        available = [item for item in workstreams if item not in used_workstreams]
                        if not available:
                            raise DynamicMasContractError("more child sessions than declared workstreams")
                        workstream_id = available[0]
                    used_workstreams.add(workstream_id)
                    agent_id = f"worker-{workstreams.index(workstream_id) + 1}"
                self.register(
                    SessionBinding(
                        project_id=self.project_id,
                        agent_id=agent_id,
                        session_id=session_id,
                        session_key=session_key,
                        parent_id=self.parent_agent_id,
                        parent_session_id=self.parent_session_id,
                        parent_session_key=self.parent_session_key,
                        role="worker",
                        workstream_id=workstream_id,
                        status=_child_status(sessions_root, session_id),
                    )
                )
        value = self.snapshot()
        _write_json_atomic(output, value)
        return value
