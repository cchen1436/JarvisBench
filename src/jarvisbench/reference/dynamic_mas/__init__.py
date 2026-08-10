"""Optional reference implementation for formal dynamic MAS control.

The benchmark runner depends only on the small protocols exposed here.  A
participant can replace :class:`DynamicMasScheduler` without changing the
multi-agent topology or any worker-visible benchmark input.
"""

from .contracts import HeldAction, ReviewRequest, SessionBinding
from .control_plane import SessionControlPlane
from .factory import ReferenceDynamicMasConfig, build_reference_scheduler
from .registry import DynamicChildRegistry
from .scheduler import DynamicMasScheduler, SchedulerOutcome
from .service import DynamicMasService

__all__ = [
    "DynamicChildRegistry",
    "DynamicMasScheduler",
    "DynamicMasService",
    "HeldAction",
    "ReviewRequest",
    "ReferenceDynamicMasConfig",
    "SchedulerOutcome",
    "SessionBinding",
    "SessionControlPlane",
    "build_reference_scheduler",
]
