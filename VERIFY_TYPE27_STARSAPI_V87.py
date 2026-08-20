#!/usr/bin/env python3
r"""Pre-host Type27 verifier using the StarsAPI DesignBlock codec port.

Run from the stars-ai repository root:
    python .\VERIFY_TYPE27_STARSAPI_V87.py .\sandbox\GAME.x1

This does not prove the two-byte Type27 wrapper is accepted by the Stars! host.
It *does* prove every embedded ship body is structurally identical to a body
that StarsAPI DesignBlock.decode()/encode() accepts and round-trips exactly.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from stars_ai.adapters.stars_native import read_blocks
from stars_ai.native.starsapi_design_codec import (
    decode_design_block,
    starsapi_body_roundtrip,
)


def _is_delete(data: bytes) -> bool:
    return len(data) >= 2 and data[0] % 16 == 0


def _hex2(data: bytes) -> str:
    return data.hex(" ")


def verify(path: Path) -> int:
    header, blocks, _ = read_blocks(path)
    records = [b.data for b in blocks if b.type_id == 27]
    print(f"FILE: {path}")
    print(f"turn={header.turn} player={header.player_index + 1} type27_records={len(records)}")
    if not records:
        print("PASS: no Type27 ship-design mutations in this X file.")
        return 0

    errors: list[str] = []
    creates: list[tuple[int, bytes, object]] = []
    deletes: list[tuple[int, bytes]] = []

    for idx, data in enumerate(records, start=1):
        if len(data) < 2:
            errors.append(f"Type27 #{idx}: shorter than two wrapper bytes")
            continue
        control = bytes(data[:2])
        if _is_delete(data):
            slot = int(data[1] & 0x0F)
            deletes.append((slot, control))
            print(f"Type27 #{idx}: DELETE-like slot={slot} control={_hex2(control)}")
            if len(data) != 2:
                errors.append(f"Type27 #{idx}: delete-like record has unexpected body length {len(data)-2}")
            continue

        body = bytes(data[2:])
        try:
            parsed = decode_design_block(body, allow_type27_bit0_clear=True)
            rebuilt = starsapi_body_roundtrip(body, type27_embedded=True)
        except Exception as exc:
            errors.append(f"Type27 #{idx}: StarsAPI body decode failed: {type(exc).__name__}: {exc}")
            continue
        if rebuilt != body:
            errors.append(f"Type27 #{idx}: StarsAPI body re-encode differs from source bytes")
        if not parsed.is_full_design:
            errors.append(f"Type27 #{idx}: embedded design is not full")
        if parsed.is_starbase:
            errors.append(f"Type27 #{idx}: embedded design is a starbase, not a ship")
        if not parsed.type27_bit0_was_clear:
            errors.append(
                f"Type27 #{idx}: embedded byte1 bit0 is set (0x{parsed.raw_second_byte:02x}); "
                "AI fresh-create path must use the StarsAPI DesignChange bit0-clear form"
            )
        creates.append((idx, control, parsed))
        print(
            f"Type27 #{idx}: SHIP slot={parsed.design_number} control={_hex2(control)} "
            f"body_byte1=0x{parsed.raw_second_byte:02x}->0x{parsed.normalized_second_byte:02x} "
            f"hull={parsed.hull_id} pic={parsed.pic} armor={parsed.armor} "
            f"slots={parsed.slot_count} turn={parsed.turn_designed} name={parsed.name!r}"
        )

    # The AI permits only one design mutation per turn.  Fresh create is a
    # staging/final pair; delete is one record.
    if creates and deletes:
        errors.append("AI X contains both create and delete Type27 records in the same turn")
    if deletes and len(deletes) != 1:
        errors.append(f"AI X contains {len(deletes)} delete-like Type27 records; expected at most one")
    if creates:
        if len(creates) != 2:
            errors.append(f"AI fresh create requires exactly two Type27 ship records; found {len(creates)}")
        else:
            (_, c1, a), (_, c2, b) = creates
            if a.design_number != b.design_number:
                errors.append(f"staging/final design slots differ: {a.design_number} vs {b.design_number}")
            slot = int(a.design_number)
            expected_stage = bytes([0x11, 0xA0 | slot])
            expected_final = bytes([0x11, 0x60 | slot])
            if c1 != expected_stage:
                errors.append(
                    f"slot {slot}: staging control {_hex2(c1)} != empirical fresh-create {_hex2(expected_stage)}"
                )
            if c2 != expected_final:
                errors.append(
                    f"slot {slot}: final control {_hex2(c2)} != empirical fresh-create {_hex2(expected_final)}"
                )
            shared = (
                "design_number", "hull_id", "armor", "slot_count", "turn_designed",
                "total_built", "total_remaining", "is_starbase", "is_transferred",
            )
            for attr in shared:
                if getattr(a, attr) != getattr(b, attr):
                    errors.append(
                        f"slot {slot}: staging/final {attr} differs: {getattr(a, attr)!r} vs {getattr(b, attr)!r}"
                    )
            if any(int(x.count) for x in a.slots):
                errors.append(f"slot {slot}: staging record contains non-empty component slots")
            if not any(int(x.count) for x in b.slots):
                errors.append(f"slot {slot}: final record has no installed components")

    if errors:
        print("FAIL:")
        for err in errors:
            print(f"  - {err}")
        return 2
    print("PASS: every Type27 ship body is StarsAPI-valid and the AI lifecycle invariants hold.")
    print("NOTE: wrapper bytes are still empirical because StarsAPI does not implement DesignChangeBlock.encode().")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("xfile", type=Path)
    args = ap.parse_args()
    return verify(args.xfile)


if __name__ == "__main__":
    raise SystemExit(main())
