"""Experimental bounded population-load encoder.

The controlled Stars!-generated colony sample encodes 25 kT population as:
  <raw fleet u16> 25 00 12 08 19
The only new assumption here is that the final byte remains a literal unsigned
kT quantity, matching the already-validated exact-quantity mineral pattern.
This is intentionally capped at 255 kT and remains PARTIAL until host/client
validation confirms at least one non-25 value.
"""
from __future__ import annotations

from typing import Any

from stars_ai.adapters.stars_native import NativeBlock


def raw_fleet_number(state: Any, fleet_id: int) -> int:
    return ((int(getattr(state, "player_id", 1)) - 1) << 9) | (int(fleet_id) & 0x1FF)


def population_load_block(state: Any, fleet_id: int, population_kt: int) -> NativeBlock:
    qty = int(population_kt)
    if not 1 <= qty <= 255:
        raise ValueError(f"Experimental population load must be 1..255 kT; got {qty}")
    fleet = next((
        f for f in getattr(state, "fleets", [])
        if f.owner == state.player_id and int(f.id) == int(fleet_id)
    ), None)
    capacity = int(
        getattr(fleet, "cargo_capacity", 0)
        or ((fleet.native or {}).get("cargo_capacity", 0) if fleet is not None else 0)
        or 0
    )
    current = 0
    if fleet is not None:
        cargo = (fleet.native or {}).get("cargo", {}) or {}
        current = sum(int(cargo.get(k, 0) or 0) for k in (
            "ironium", "boranium", "germanium", "population"
        ))
    if capacity > 0 and current + qty > capacity:
        raise ValueError(
            f"Requested population load {qty}kT exceeds conservative available cargo capacity "
            f"{max(0, capacity-current)}kT for fleet {fleet_id}"
        )
    data = (
        int(raw_fleet_number(state, fleet_id)).to_bytes(2, "little")
        + bytes.fromhex("25 00 12 08")
        + bytes([qty])
    )
    return NativeBlock(1, len(data), data)
