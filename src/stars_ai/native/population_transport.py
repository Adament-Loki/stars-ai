"""Client-observed manual population-load encoders.

The two bytes after ``<fleet>`` are the *source planet id* as a little-endian
u16, not an encoding discriminator.  For example, the captured population
loads from planets 87 and 151 contain ``57 00`` and ``97 00`` respectively.

Population-only manual loads use the following host-verified boundary:

* Type 1: ``<fleet> <source> 12 08 <u8 quantity>`` for 1..127 kT;
* Type 2: ``<fleet> <source> 12 08 <u16 quantity>`` from 128 kT upward;
* Type 2 is also required for mixed mineral/population manifests.

The Type-2 control byte is a cargo-selection bitmask: I/B/G/Population are
bits 0/1/2/3, followed by a little-endian u16 amount for each selected cargo
type in that order.  The merged-fleet client capture confirms the mixed
``0E`` form (B/G/Population) in one record.

Consequently, Type 2 has no *format* transition at 1,000 kT: each selected
cargo field can represent 0..65,535 kT.  The usable amount is still limited
by free fleet capacity and the source stockpile.  Do not infer a Type-25
"large load" replacement merely because a load exceeds 1,000 kT; Type 25 is
intentionally unsupported until a reproducible native-host failure identifies
an actual semantic boundary or its exact payload.
"""
from __future__ import annotations

from typing import Any

from stars_ai.adapters.stars_native import NativeBlock


MAX_SMALL_POPULATION_LOAD_KT = 127
"""Largest population-only Type-1 manual load accepted by the native host."""

MIN_MEDIUM_POPULATION_LOAD_KT = MAX_SMALL_POPULATION_LOAD_KT + 1

MAX_MEDIUM_MANIFEST_FIELD_KT = 0xFFFF
"""Protocol ceiling for one Type-2 I/B/G/Population field, not a host limit."""


def raw_fleet_number(state: Any, fleet_id: int) -> int:
    return ((int(getattr(state, "player_id", 1)) - 1) << 9) | (int(fleet_id) & 0x1FF)


def source_planet_bytes(source_planet_id: int) -> bytes:
    """Return the native u16 source-planet field used by manual load blocks."""
    source = int(source_planet_id)
    if not 0 <= source <= 0xFFFF:
        raise ValueError(f"Native manual load source planet must fit u16; got {source}")
    return source.to_bytes(2, "little")


def medium_load_block(
    state: Any,
    fleet_id: int,
    load: dict[str, int],
    *,
    source_planet_id: int,
    capacity_override: int | None = None,
) -> NativeBlock:
    """Encode a Type-2 manifest through its u16 field ceiling; never guess Type 25."""
    ordered=("ironium", "boranium", "germanium", "population")
    amounts={key:int((load or {}).get(key,0) or 0) for key in ordered}
    if any(value < 0 or value > MAX_MEDIUM_MANIFEST_FIELD_KT for value in amounts.values()):
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
        + source_planet_bytes(source_planet_id)
        + b"\x12"
        + bytes([mask])
        + b"".join(amounts[key].to_bytes(2,"little") for key in ordered if amounts[key])
    )
    return NativeBlock(2, len(data), data)


def population_load_block(
    state: Any,
    fleet_id: int,
    population_kt: int,
    *,
    source_planet_id: int,
) -> NativeBlock:
    qty=int(population_kt)
    if qty <= 0:
        raise ValueError(f"Native Type-2 population transport requires a positive quantity; got {qty}")
    return medium_load_block(
        state,
        fleet_id,
        {"population":qty},
        source_planet_id=source_planet_id,
    )


def small_population_load_block(
    state: Any,
    fleet_id: int,
    population_kt: int,
    *,
    source_planet_id: int,
) -> NativeBlock:
    """Encode a host-accepted population-only Type-1 load of 1..127 kT."""
    qty = int(population_kt)
    if not 1 <= qty <= MAX_SMALL_POPULATION_LOAD_KT:
        raise ValueError(
            "Native Type-1 population load requires 1.."
            f"{MAX_SMALL_POPULATION_LOAD_KT} kT; got {qty}"
        )
    data=(
        int(raw_fleet_number(state, fleet_id)).to_bytes(2, "little")
        + source_planet_bytes(source_planet_id)
        + bytes.fromhex("12 08")
        + bytes([qty])
    )
    return NativeBlock(1, len(data), data)
