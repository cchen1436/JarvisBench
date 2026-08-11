from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from jarvisbench.core.decision_ledger import DecisionLedger
from jarvisbench.core.providers import OpenAIProvider, TextProvider
from jarvisbench.reference.jarvis import ReferenceJarvis
from jarvisbench.reference.luna import LunaUser

from .control_plane import SessionControlPlane
from .registry import DynamicChildRegistry
from .scheduler import DynamicMasScheduler


@dataclass(frozen=True)
class ReferenceDynamicMasConfig:
    jarvis_model: str
    user_model: str
    jarvis_reasoning: str = "medium"
    max_attention_requests: int = 2

    def __post_init__(self) -> None:
        if not self.jarvis_model or not self.user_model:
            raise ValueError("Jarvis and user model ids must be configured")
        if type(self.max_attention_requests) is not int or self.max_attention_requests < 0:
            raise ValueError("max_attention_requests must be a non-negative integer")


def build_reference_scheduler(
    *,
    project_id: str,
    control_root: Path,
    private_ledger_path: Path,
    requester_context_loader: Callable[[], str],
    config: ReferenceDynamicMasConfig,
    provider: TextProvider | None = None,
) -> DynamicMasScheduler:
    """Build the optional reference controller on OpenAI's official API.

    ``OpenAIProvider`` reads ``OPENAI_API_KEY`` or the mounted file named by
    ``OPENAI_API_KEY_FILE`` when no provider is supplied. Neither the
    resolved value nor the file contents are serialized into episode manifests,
    event logs, or control receipts.
    """

    text_provider = provider or OpenAIProvider()
    registry = DynamicChildRegistry(project_id=project_id)
    return DynamicMasScheduler(
        project_id=project_id,
        controller=ReferenceJarvis(
            text_provider,
            config.jarvis_model,
            reasoning=config.jarvis_reasoning,
        ),
        requester=LunaUser(text_provider, config.user_model),
        requester_context_loader=requester_context_loader,
        registry=registry,
        control_plane=SessionControlPlane(control_root, project_id=project_id),
        decision_ledger=DecisionLedger(private_ledger_path),
        max_attention_requests=config.max_attention_requests,
    )
