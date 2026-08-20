"""Pre-host structural verifier for v8.6 Type27 ship-design mutations.

Usage:
    python VERIFY_TYPE27_V86.py path\\to\\GAME.x1

This does not claim host/client validation.  It verifies only the invariants the
v8.6 writer itself intends to emit:
  * a delete is exactly 10 <slot>;
  * a free-slot create is exactly two records for one slot;
  * both create controls are 11 A0|slot;
  * staging has empty component slots, final has populated slots;
  * the same slot is never deleted and created in the same X file.
"""
from __future__ import annotations

import sys
from pathlib import Path

from stars_ai.adapters.stars_native import read_blocks
from stars_ai.native.design_change import parse_design_change_payload


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("Usage: python VERIFY_TYPE27_V86.py path\\to\\GAME.x#", file=sys.stderr)
        return 2
    path = Path(argv[1])
    if not path.exists():
        print(f"ERROR: missing {path}", file=sys.stderr)
        return 2

    _, blocks, _ = read_blocks(path)
    changes = [parse_design_change_payload(b.data) for b in blocks if b.type_id == 27]
    if not changes:
        print("PASS: no Type27 ship-design mutation is present in this X file.")
        return 0

    deletes: set[int] = set()
    creates: dict[int, list] = {}
    errors: list[str] = []
    for index, change in enumerate(changes, 1):
        if change.delete:
            if change.control != bytes([0x10, change.design_slot & 0x0F]):
                errors.append(f"Type27 #{index}: malformed delete control {change.control.hex(' ')}")
            deletes.add(change.design_slot)
            print(f"Type27 #{index}: DELETE slot {change.design_slot} control={change.control.hex(' ')}")
            continue
        expected = bytes([0x11, 0xA0 | (change.design_slot & 0x0F)])
        if change.control != expected:
            errors.append(
                f"Type27 #{index}: create slot {change.design_slot} control {change.control.hex(' ')} "
                f"!= expected {expected.hex(' ')}"
            )
        creates.setdefault(change.design_slot, []).append(change)
        populated = any(s.count > 0 for s in change.slots)
        print(
            f"Type27 #{index}: {'FINAL' if populated else 'STAGING'} CREATE slot {change.design_slot} "
            f"control={change.control.hex(' ')} hull={change.hull_id} slots={change.slot_count}"
        )

    for slot, records in creates.items():
        if slot in deletes:
            errors.append(f"slot {slot}: delete and create appear in the same X file (atomic replacement forbidden)")
        if len(records) != 2:
            errors.append(f"slot {slot}: expected exactly 2 create records, found {len(records)}")
            continue
        first_pop = any(s.count > 0 for s in records[0].slots)
        second_pop = any(s.count > 0 for s in records[1].slots)
        if first_pop:
            errors.append(f"slot {slot}: first create record is not empty staging")
        if not second_pop:
            errors.append(f"slot {slot}: second create record is not populated final")
        if records[0].hull_id != records[1].hull_id:
            errors.append(f"slot {slot}: staging/final hull IDs differ")

    if errors:
        print("FAIL:")
        for error in errors:
            print(f"  - {error}")
        return 1
    print("PASS: Type27 stream matches v8.6 structural lifecycle invariants.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
