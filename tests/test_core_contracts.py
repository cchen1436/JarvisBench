from pathlib import Path

import pytest

from jarvisbench.core.contracts import BoundaryCandidate, ReducedUpdate
from jarvisbench.core.control_store import ControlStore
from jarvisbench.settings.multi_agent import DynamicSessionRegistry, SessionRegistration


def test_reduced_update_is_bounded():
    with pytest.raises(ValueError):
        ReducedUpdate("ok", "goal", tuple(str(i) for i in range(9)))


def test_per_session_epoch_and_stale_action(tmp_path: Path):
    store = ControlStore(tmp_path)
    a = store.hold("child-a", "a1", "write A")
    b = store.hold("child-b", "b1", "write B")
    assert a.epoch == b.epoch == 1
    delivered = store.transition(a, status="delivered", decision_id="d1")
    assert store.snapshot("child-b")["actions"]["b1"]["status"] == "held"
    store.transition(delivered, status="applied", decision_id="d1")
    with pytest.raises(RuntimeError):
        store.transition(a, status="invalidated")


def test_new_epoch_invalidates_only_session_siblings(tmp_path: Path):
    store = ControlStore(tmp_path)
    old = store.hold("child-a", "batch-old", "old")
    other = store.hold("child-b", "batch-other", "other")
    store.hold("child-a", "batch-new", "new")
    assert store.snapshot("child-a")["actions"][old.action_id]["status"] == "invalidated"
    assert store.snapshot("child-b")["actions"][other.action_id]["status"] == "held"


def test_dynamic_registration_cannot_rebind_session():
    registry = DynamicSessionRegistry()
    registry.register(SessionRegistration("key", "worker-1", "parent"))
    with pytest.raises(RuntimeError):
        registry.register(SessionRegistration("key", "worker-2", "parent"))

