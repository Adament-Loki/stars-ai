"""Client-observed medium manual-load encoders.

Stars! uses distinct records for the two controlled population-load forms:

* 25 kT colony load: Type 1, ``<fleet> 25 00 12 08 19``.
* 200 kT transport load: Type 2, ``<fleet> 97 00 12 08 C8 00``.
* a merged two-Privateer transport: Type 2,
  ``<fleet> 97 00 12 0E 46 00 64 00 4A 01``.

The final control byte is a cargo-selection bitmask: I/B/G/Population are
bits 0/1/2/3, followed by a little-endian u16 amount for each selected cargo
type in that order. The merged-fleet client capture confirms the mixed
``0E`` form (B/G/Population) in one record. Type 2 must not be compressed into
the Type-1 one-byte form; that synthetic form stalls the host.
"""
from __future__ import annotations

from typing import Any

from stars_ai.adapters.stars_native import NativeBlock


def raw_fleet_number(state: Any, fleet_id: int) -> int:
    return ((int(getattr(state, "player_id", 1)) - 1) << 9) | (int(fleet_id) & 0x1FF)


def medium_load_block(
    state: Any,
    fleet_id: int,
    load: dict[str, int],
    *,
    capacity_override: int | None = None,
) -> NativeBlock:
    """Encode client-observed Type-2 medium cargo load(s) in one manifest."""
    ordered=("ironium", "boranium", "germanium", "population")
    amounts={key:int((load or {}).get(key,0) or 0) for key in ordered}
    if any(value < 0 or value > 0xFFFF for value in amounts.values()):
        raise ValueError(f"Native Type-2 load requires u16 quantities; got {amounts}")
    total=sum(amounts.values())
    if total <= 0:
        raise ValueError("Native Type-2 medium load requires at least one positive cargo quantity")
    fleet = next((
        f for f in getattr(state, "fleets", [])
        if f.owner == state.player_id and int(f.id) == int(fleet_id)
    ), None)
    capacity=int(capacity_override or 0)
    if capacity <= 0:
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
    if capacity > 0 and current + total > capacity:
        raise ValueError(
            f"Requested Type-2 load {total}kT exceeds conservative available cargo capacity "
            f"{max(0, capacity-current)}kT for fleet {fleet_id}"
        )
    fleet_bytes = int(raw_fleet_number(state, fleet_id)).to_bytes(2, "little")
    mask=sum(1 << index for index, key in enumerate(ordered) if amounts[key])
    data=(
        fleet_bytes
        + bytes.fromhex("97 00 12")
        + bytes([mask])
        + b"".join(amounts[key].to_bytes(2,"little") for key in ordered if amounts[key])
    )
    return NativeBlock(2, len(data), data)


def population_load_block(state: Any, fleet_id: int, population_kt: int) -> NativeBlock:
    qty=int(population_kt)
    if qty <= 0:
        raise ValueError(f"Native Type-2 population transport requires a positive quantity; got {qty}")
    return medium_load_block(state,fleet_id,{"population":qty})
