from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

from .contracts import MAX_EVENT_LINE_BYTES, DynamicMasContractError, canonical_json
from .scheduler import DynamicMasScheduler


class DynamicMasService:
    """Tail bounded plugin events while native OpenClaw children are running."""

    def __init__(
        self,
        *,
        scheduler: DynamicMasScheduler,
        events_path: Path,
        sessions_root: Path,
        workspace: Path,
        registry_path: Path,
        diagnostics_path: Path,
        poll_seconds: float = 0.2,
    ) -> None:
        self.scheduler = scheduler
        self.events_path = Path(events_path)
        self.sessions_root = Path(sessions_root)
        self.workspace = Path(workspace)
        self.registry_path = Path(registry_path)
        self.diagnostics_path = Path(diagnostics_path)
        self.poll_seconds = max(0.025, float(poll_seconds))
        self._offset = 0
        self._remainder = b""
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._errors: list[str] = []

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("dynamic MAS service already started")
        self.events_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.events_path.touch(mode=0o600, exist_ok=True)
        self._thread = threading.Thread(
            target=self._run,
            name="jarvisbench-dynamic-mas",
            daemon=True,
        )
        self._thread.start()

    def stop(self, timeout: float = 10.0) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout)
            if thread.is_alive():
                raise RuntimeError("dynamic MAS service did not stop")
        # Drain complete physical lines emitted immediately before the runner
        # stopped the service (notably Parent application receipts).
        self.poll_once()
        self._write_diagnostics()

    def _read_new_event_lines(self) -> list[bytes]:
        """Read complete physical lines without committing their offsets.

        ``self._offset`` is the last successfully processed byte, not merely the
        last byte observed on disk.  Keeping the read and commit steps separate
        ensures a scheduler failure leaves that event (and every later event in
        the same chunk) available for the next poll.
        """

        if self.events_path.is_symlink() or not self.events_path.is_file():
            raise DynamicMasContractError("project event bus is unsafe")
        size = self.events_path.stat().st_size
        if size < self._offset:
            raise DynamicMasContractError("append-only project event bus was truncated")
        if size == self._offset:
            self._remainder = b""
            return []
        with self.events_path.open("rb") as stream:
            stream.seek(self._offset)
            chunk = stream.read()
        lines = chunk.split(b"\n")
        self._remainder = lines.pop()
        if len(self._remainder) > MAX_EVENT_LINE_BYTES:
            raise DynamicMasContractError("unterminated project event exceeds its bound")
        return lines

    def poll_once(self) -> int:
        self.scheduler.sync_registry(
            sessions_root=self.sessions_root,
            workspace=self.workspace,
            registry_path=self.registry_path,
        )
        count = 0
        for line in self._read_new_event_lines():
            next_offset = self._offset + len(line) + 1
            if not line:
                self._offset = next_offset
                continue
            if len(line) > MAX_EVENT_LINE_BYTES:
                raise DynamicMasContractError("project event exceeds its bound")
            event = json.loads(line)
            if not isinstance(event, dict):
                raise DynamicMasContractError("project event is not an object")
            self.scheduler.process_event(event)
            # Commit only after the exact event completed successfully.  If
            # processing raises, this line and all following lines are reread.
            self._offset = next_offset
            count += 1
        return count

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.poll_once()
            except Exception as exc:
                # Keep the Gateway path alive. Exact held reviews fail closed
                # at the plugin timeout; diagnostics make the infrastructure
                # failure visible to the episode admission layer.
                self._errors.append(type(exc).__name__)
            self._stop.wait(self.poll_seconds)

    def _write_diagnostics(self) -> None:
        value = {
            **self.scheduler.diagnostics(),
            "service_errors": self._errors[-32:],
            "event_offset": self._offset,
            "partial_event_bytes": len(self._remainder),
        }
        encoded = canonical_json(value) + b"\n"
        self.diagnostics_path.parent.mkdir(parents=True, exist_ok=True)
        self.diagnostics_path.write_bytes(encoded)
