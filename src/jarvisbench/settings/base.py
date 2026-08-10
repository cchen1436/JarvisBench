from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class DryRunResult:
    setting: str
    execution_nodes: tuple[str, ...]
    manager_is_jarvis: bool
    gateway_required: bool


class SettingAdapter(Protocol):
    name: str

    def dry_run(self) -> DryRunResult:
        ...

