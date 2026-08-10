"""Canonical provenance for equivalence tests and release diagnostics."""

from __future__ import annotations

from .contracts import (
    CANONICAL_DYNAMIC_MAS_SOURCE_SHA256,
    CANONICAL_DYNAMIC_SUBMITTER_SHA256,
    CANONICAL_LEGACY_CONVERGENCE_SHA256,
    CANONICAL_MAS_UPSTREAM_SNAPSHOT_SHA256,
    CONTROL_PROTOCOL_VERSION,
)


PROVENANCE = {
    "canonical_dynamic_mas_source_sha256": CANONICAL_DYNAMIC_MAS_SOURCE_SHA256,
    "canonical_dynamic_submitter_sha256": CANONICAL_DYNAMIC_SUBMITTER_SHA256,
    "canonical_legacy_convergence_sha256": CANONICAL_LEGACY_CONVERGENCE_SHA256,
    "canonical_mas_upstream_snapshot_sha256": CANONICAL_MAS_UPSTREAM_SNAPSHOT_SHA256,
    "canonical_semantics": (
        "native_gateway_parent_delegation_children_complete_parent_integration",
        "dynamic_child_registration",
        "bounded_live_updates",
        "per_session_epoch_nonce_action_fingerprint",
        "single_project_attention_scheduler",
        "lazy_private_requester_channel",
        "targeted_delivery_receipts",
    ),
    "release_control_protocol_version": CONTROL_PROTOCOL_VERSION,
    "legacy_convergence_gate_is_formal_split": False,
    "equivalence_scope": "worker_visible_prompts_topology_protocol_and_control_contract_invariants",
    "release_extraction_is_byte_identical": False,
}
