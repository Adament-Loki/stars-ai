"""Native Type-27 ship design lifecycle support.

v8.7 moves the embedded ship body onto an explicit Python port of StarsAPI's
``DesignBlock.encode()/decode()``.  This module now owns only the Type-27
lifecycle wrapper and mutation safety rules:

* CREATE in a genuinely free slot: empirical two-record Type27 wrapper around
  a StarsAPI-authored DesignBlock body.
* DELETE of an existing design: the separately observed existing-design form.
* REPLACE is not atomic: delete a provably dead design, read back the next M,
  then create into the now-free slot on a later turn.

StarsAPI itself leaves ``DesignChangeBlock.encode()`` unimplemented, so the
first two Type27 bytes remain empirical and are kept visibly separate from the
known DesignBlock body.  Every generated body is decoded and re-encoded using
the StarsAPI rules before it is returned to the native writer.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any

from stars_ai.adapters.stars_native import NativeBlock
from stars_ai.design_legality import ComponentRef
from stars_ai.native.starsapi_design_codec import (
    StarsApiDesign, decode_design_block, encode_stars_string,
    encode_type27_embedded_design, starsapi_body_roundtrip,
)


class UnsafeShipDesignMutationError(RuntimeError):
    def __init__(self, diagnostic: dict):
        self.diagnostic = dict(diagnostic)
        super().__init__(str(self.diagnostic.get("reason", "unsafe ship design mutation blocked")))



@dataclass(frozen=True)
class EncodedShipDesign:
    slot: int
    hull_id: int
    pic: int
    armor: int
    turn_designed: int
    slots: tuple[ComponentRef, ...]
    name: str
    staging_name: str | None = None
    # Compatibility field retained in payloads.  v8.6 never creates and
    # replaces atomically; True means "delete this dead slot this turn, then
    # retry creation after next-M readback" at the planning layer.
    replace_existing: bool = False

    def signature(self) -> str:
        parts = [f"H{self.hull_id}"]
        for s in self.slots:
            parts.append(f"{int(s.category)}:{int(s.item_id)}x{int(s.count)}")
        return "|".join(parts + [self.name])


def encode_type27_design_body(
    design: EncodedShipDesign, *, empty_slots: bool = False, name_override: str | None = None
) -> bytes:
    """Encode the embedded full DesignBlock using the StarsAPI body codec.

    This function no longer lays out hull metadata itself.  It constructs the
    semantic DesignBlock and delegates every body byte to the port of StarsAPI
    ``DesignBlock.encode()``.  The Type27-specific bit-0 clearing is performed
    by ``encode_type27_embedded_design()`` exactly as documented by StarsAPI's
    ``DesignChangeBlock.decode()``.
    """
    slots = tuple(
        ComponentRef(0, 0, 0) if empty_slots else ComponentRef(
            int(slot.category), int(slot.item_id), int(slot.count)
        )
        for slot in design.slots
    )
    starsapi = StarsApiDesign(
        design_number=int(design.slot),
        hull_id=int(design.hull_id),
        pic=int(design.pic),
        armor=int(design.armor),
        slot_count=len(slots),
        turn_designed=int(design.turn_designed),
        total_built=0,
        total_remaining=0,
        slots=slots,
        name=str(design.name if name_override is None else name_override),
        is_full_design=True,
        is_transferred=False,
        is_starbase=False,
    )
    body = encode_type27_embedded_design(starsapi)
    # An additional byte-for-byte self-check at this API boundary makes native
    # corruption fail before the block enters the X order stream.
    if starsapi_body_roundtrip(body, type27_embedded=True) != body:
        raise RuntimeError("StarsAPI DesignBlock body failed exact Type27 round-trip")
    return body


def create_ship_design_blocks(design: EncodedShipDesign) -> list[NativeBlock]:
    """Create a new design in a free slot using the client-observed lifecycle.

    Fresh-design evidence now includes the user's controlled turn-3/turn-4
    ``GAME.x2`` and the earlier Medium-Freighter sample:
      staging: 11 A4 + empty design body
      final:   11 64 + populated design body

    A separate client UI path has also produced A4/A4, so the high control bits
    are operation-contextual rather than a universal slot marker.  The AI uses
    only the fresh-from-scratch lifecycle here: A0|slot -> 60|slot.  It does not
    accept ``replace_existing=True``; replacement is staged across turns.
    """
    if design.replace_existing:
        raise ValueError("Atomic replace is forbidden; delete the dead slot, read back, then create")
    slot = int(design.slot)
    staging_control = bytes([0x11, 0xA0 | slot])
    final_control = bytes([0x11, 0x60 | slot])
    # Controlled client samples stage under the base hull name and only apply
    # a custom name in the populated final record.
    staging_name = str(design.staging_name or design.name)
    staging = staging_control + encode_type27_design_body(
        design, empty_slots=True, name_override=staging_name
    )
    final = final_control + encode_type27_design_body(design, empty_slots=False)
    return [NativeBlock(27, len(staging), staging), NativeBlock(27, len(final), final)]


def delete_existing_ship_design_block(slot: int) -> NativeBlock:
    """Directly observed existing-M design deletion form: ``10 <slot>``."""
    if not 0 <= int(slot) <= 15:
        raise ValueError("Ship design slot must be 0..15")
    payload = bytes([0x10, int(slot) & 0x0F])
    return NativeBlock(27, len(payload), payload)


def _design_dicts(state: Any) -> list[dict]:
    return [
        dict(x) for x in (((getattr(state, "native", {}) or {}).get("designs", [])) or [])
        if not bool(x.get("is_starbase"))
    ]


def _live_ship_count(state: Any, slot: int) -> int:
    total = 0
    for fleet in getattr(state, "fleets", []) or []:
        if int(getattr(fleet, "owner", -1)) != int(getattr(state, "player_id", -2)):
            continue
        counts = ((getattr(fleet, "native", {}) or {}).get("ship_count", []) or [])
        if isinstance(counts, int):
            continue
        if 0 <= int(slot) < len(counts):
            total += int(counts[int(slot)] or 0)
    return total


def _queued_ship_count(state: Any, slot: int) -> int:
    total = 0
    production = ((getattr(state, "native", {}) or {}).get("production_by_planet", {}) or {})
    for queue in production.values():
        for item in queue or []:
            if int(item.get("item_type", 0) or 0) != 4:
                continue
            if int(item.get("item_id", -1) or -1) != int(slot):
                continue
            total += max(0, int(item.get("count", item.get("quantity", 0)) or 0))
    return total


@dataclass(frozen=True)
class ShipDesignSlotSafety:
    slot: int
    design_exists: bool
    live_ship_count: int
    queued_ship_count: int
    total_remaining: int
    design_name: str | None

    def to_dict(self) -> dict:
        return asdict(self)


def ship_design_slot_safety(state: Any, slot: int) -> ShipDesignSlotSafety:
    slot = int(slot)
    if not 0 <= slot <= 15:
        raise ValueError("Ship design slot must be 0..15")
    design = next((d for d in _design_dicts(state) if int(d.get("design_number", -1)) == slot), None)
    return ShipDesignSlotSafety(
        slot=slot,
        design_exists=design is not None,
        live_ship_count=_live_ship_count(state, slot),
        queued_ship_count=_queued_ship_count(state, slot),
        total_remaining=max(0, int((design or {}).get("total_remaining", 0) or 0)),
        design_name=str((design or {}).get("name")) if design is not None and (design or {}).get("name") is not None else None,
    )


def assert_free_ship_design_slot(state: Any, slot: int) -> ShipDesignSlotSafety:
    safety = ship_design_slot_safety(state, slot)
    if safety.design_exists or safety.live_ship_count or safety.queued_ship_count:
        d = safety.to_dict()
        d.update({"operation": "create_ship_design", "result": "BLOCK", "reason": (
            f"Ship design slot {slot} is not free: exists={safety.design_exists}, "
            f"live={safety.live_ship_count}, queued={safety.queued_ship_count}."
        )})
        raise UnsafeShipDesignMutationError(d)
    return safety


def assert_deletable_ship_design_slot(state: Any, slot: int) -> ShipDesignSlotSafety:
    safety = ship_design_slot_safety(state, slot)
    if not safety.design_exists:
        d = safety.to_dict(); d.update({"operation": "delete_ship_design", "result": "BLOCK", "reason": f"Ship design slot {slot} has no existing design to delete."})
        raise UnsafeShipDesignMutationError(d)
    if safety.live_ship_count or safety.queued_ship_count or safety.total_remaining:
        d = safety.to_dict()
        d.update({"operation": "delete_ship_design", "result": "BLOCK", "reason": (
            f"Refusing to delete ship design slot {slot}: live={safety.live_ship_count}, "
            f"queued={safety.queued_ship_count}, total_remaining={safety.total_remaining}."
        )})
        raise UnsafeShipDesignMutationError(d)
    return safety


@dataclass(frozen=True)
class ParsedDesignChange:
    delete: bool
    design_slot: int
    control: bytes
    hull_id: int | None = None
    pic: int | None = None
    armor: int | None = None
    slot_count: int = 0
    slots: tuple[ComponentRef, ...] = ()
    embedded_slot_byte: int | None = None


def parse_design_change_payload(data: bytes) -> ParsedDesignChange:
    if len(data) < 2:
        raise ValueError("Type27 DesignChange payload is missing control bytes")
    control = bytes(data[:2])
    if data[0] % 16 == 0:
        return ParsedDesignChange(True, int(data[1] & 0x0F), control)
    body = bytes(data[2:])
    parsed = decode_design_block(body, allow_type27_bit0_clear=True)
    if not parsed.is_full_design:
        raise ValueError("Type27 ship creation requires a full StarsAPI DesignBlock body")
    if parsed.is_starbase:
        raise ValueError("Ship DesignChange parser received a starbase design body")
    return ParsedDesignChange(
        False, int(parsed.design_number), control,
        hull_id=int(parsed.hull_id), pic=int(parsed.pic), armor=int(parsed.armor or 0),
        slot_count=int(parsed.slot_count), slots=tuple(parsed.slots),
        embedded_slot_byte=int(parsed.raw_second_byte),
    )


def encoded_ship_design_from_payload(payload: dict) -> EncodedShipDesign:
    return EncodedShipDesign(
        slot=int(payload["target_slot"]),
        hull_id=int(payload["hull_id"]),
        pic=int(payload["pic"]),
        armor=int(payload["armor"]),
        turn_designed=int(payload.get("turn_designed", 0) or 0),
        slots=tuple(ComponentRef(
            int(x.get("category", 0)), int(x.get("item_id", 0)), int(x.get("count", 0))
        ) for x in (payload.get("slots") or [])),
        name=str(payload["name"]),
        staging_name=str(payload.get("staging_name") or payload.get("hull_name") or payload["name"]),
        replace_existing=bool(payload.get("replace_existing", False)),
    )
