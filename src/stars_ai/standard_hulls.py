"""Complete stock Stars! ship/starbase hull legality matrix.

The bundled ``data_hulls.mod`` contains the 37 hull rows from the canonical
UNEDITED.MOD database. Slot counts are fixed by the stock hull layouts. Native
slot rules are decoded as Stars! category bitmasks plus per-slot capacity.
"""
from __future__ import annotations

import csv
from pathlib import Path

# Stock slot counts from the canonical Stars! hull layouts / slot IDs.
STANDARD_SLOT_COUNTS: dict[int, int] = {
    0:3, 1:3, 2:3, 3:4, 4:3, 5:4, 6:7, 7:7, 8:7, 9:11,
    10:13, 11:5, 12:9, 13:8, 14:2, 15:2, 16:2, 17:4, 18:5, 19:7,
    20:2, 21:4, 22:6, 23:6, 24:6, 25:2, 26:3, 27:4, 28:6, 29:13,
    30:7, 31:7, 32:5, 33:8, 34:12, 35:16, 36:16,
}


def load_standard_hull_rows() -> tuple[tuple[int, str, bool, tuple[tuple[int, int], ...]], ...]:
    path = Path(__file__).with_name("data_hulls.mod")
    out = []
    for parts in csv.reader(path.read_text(encoding="latin-1").splitlines()):
        mod_cat = int(parts[0])
        name = parts[2]
        nums = [int(x) if x else 0 for x in parts[3:]]
        hull_id = nums[0]
        count = STANDARD_SLOT_COUNTS[hull_id]
        slots: list[tuple[int, int]] = []
        if mod_cat == 15:
            # Ship slot 0 is the engine slot. StarsAPI identifies nums[17] as
            # engine count; following slots are (mask, capacity) pairs.
            slots.append((nums[17], 0x0001))
            for i in range(1, count):
                slots.append((nums[17 + 2*i], nums[16 + 2*i]))
        else:
            # Starbases do not have an implicit engine slot. All slots are direct
            # (mask, capacity) pairs starting at nums[16]/nums[17].
            for i in range(count):
                slots.append((nums[17 + 2*i], nums[16 + 2*i]))
        out.append((hull_id, name, mod_cat == 16, tuple(slots)))
    return tuple(out)

STANDARD_HULL_SLOTS = load_standard_hull_rows()
