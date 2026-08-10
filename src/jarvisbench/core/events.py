from __future__ import annotations

import json
import os
from pathlib import Path

from .contracts import ProjectEvent


class EventBus:
    """Append-only project event bus; mutable control state lives elsewhere."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, event: ProjectEvent) -> None:
        payload = json.dumps(event.to_dict(), sort_keys=True, separators=(",", ":"))
        fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.write(fd, (payload + "\n").encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)

