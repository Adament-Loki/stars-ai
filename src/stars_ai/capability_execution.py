"""Shared strategic/native design-capability lifecycle helpers.

The planner must reason from what the host has actually accepted, rather than
from a semantic request that the native writer later rejected.  This module is
intentionally dependency-free so the strategic, persistence, and native layers
all use the same stable capability keys.
"""

from __future__ import annotations

from typing import Any


PLANNED = "PLANNED"
NATIVE_READY = "NATIVE_READY"
EMITTED = "EMITTED"
HOST_CONFIRMED = "HOST_CONFIRMED"
BLOCKED_NATIVE = "BLOCKED_NATIVE"

DESIGN_CAPABILITY_STATUSES = frozenset({
    PLANNED, NATIVE_READY, EMITTED, HOST_CONFIRMED, BLOCKED_NATIVE,
})
DESIGN_CREATION_KINDS = frozenset({"create_design", "create_ship_design"})


def design_capability_id(
    role: object,
    *,
    is_starbase: object = False,
    purpose: object = None,
) -> str:
    """Return the persistent key for one requested design capability."""
    normalized_role = str(role or "unknown").strip().casefold() or "unknown"
    if bool(is_starbase) or normalized_role == "starbase":
        normalized_purpose = str(purpose or "general").strip().casefold() or "general"
        return f"design:starbase:{normalized_purpose}"
    return f"design:{normalized_role}"


def design_descriptor(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    """Normalize a semantic/native creation payload into a ledger descriptor.

    Generic requests gain their final Type-27 slot only after compilation.  An
    emitted writer record therefore carries ``native_conversion``; merge it
    beneath the semantic request so intent (notably starbase purpose) survives
    while the allocated slot is retained.
    """
    outer = dict(payload or {})
    converted = outer.get("native_conversion")
    data = dict(converted) if isinstance(converted, dict) else {}
    data.update(outer)
    role = str(data.get("role") or "").strip().casefold()
    is_starbase = bool(data.get("is_starbase", False)) or role == "starbase"
    if not role:
        return None
    purpose = str(data.get("purpose") or "general").strip().casefold() if is_starbase else None
    slot = data.get("target_slot")
    try:
        slot = int(slot) if slot is not None else None
    except (TypeError, ValueError):
        slot = None
    return {
        "capability_id": str(data.get("capability_id") or design_capability_id(
            role, is_starbase=is_starbase, purpose=purpose,
        )),
        "role": role,
        "purpose": purpose,
        "is_starbase": is_starbase,
        "design_name": str(data.get("name") or data.get("design_name") or ""),
        "target_slot": slot,
    }


def capability_ledger(state_or_native: Any) -> dict[str, dict[str, Any]]:
    """Return the valid capability ledger attached to a state/native payload."""
    native = (
        getattr(state_or_native, "native", None)
        if not isinstance(state_or_native, dict) else state_or_native
    ) or {}
    raw = native.get("capability_execution", {})
    return {
        str(key): dict(value)
        for key, value in raw.items()
        if isinstance(value, dict)
    } if isinstance(raw, dict) else {}


def capability_status(state_or_native: Any, capability_id: str) -> str | None:
    entry = capability_ledger(state_or_native).get(str(capability_id))
    status = entry.get("status") if entry else None
    return str(status) if status in DESIGN_CAPABILITY_STATUSES else None


def design_is_blocked(
    state_or_native: Any,
    role: object,
    *,
    is_starbase: object = False,
    purpose: object = None,
) -> bool:
    return capability_status(
        state_or_native,
        design_capability_id(role, is_starbase=is_starbase, purpose=purpose),
    ) == BLOCKED_NATIVE


def profile_is_blocked(state_or_native: Any, profile: dict[str, Any], *, is_starbase: bool) -> bool:
    """Reject a synthetic/stale profile that represents a blocked creation.

    A blocked *new* support design must not hide a different, already-hosted
    support base.  Matching the writer-recorded namespace slot keeps that
    distinction precise.
    """
    try:
        slot = int(profile.get("design_number"))
    except (AttributeError, TypeError, ValueError):
        return False
    for entry in capability_ledger(state_or_native).values():
        if entry.get("status") != BLOCKED_NATIVE:
            continue
        if bool(entry.get("is_starbase", False)) != bool(is_starbase):
            continue
        try:
            if int(entry.get("target_slot")) == slot:
                return True
        except (TypeError, ValueError):
            continue
    return False


def blocked_capability_entries(state_or_native: Any) -> list[dict[str, Any]]:
    return [
        dict(entry) for entry in capability_ledger(state_or_native).values()
        if entry.get("status") == BLOCKED_NATIVE
    ]
