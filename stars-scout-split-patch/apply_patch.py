#!/usr/bin/env python3
"""Apply the singleton-scout patch to a local stars-ai checkout.

Baseline reviewed: Adament-Loki/stars-ai @ 880fc7cfdf40621449fc837aeb1c9b8b98610cf5.
The script creates .pre-scout-split.bak backups before changing files.
"""
from __future__ import annotations

from pathlib import Path
import shutil
import sys

ROOT = Path.cwd()
PAYLOAD = Path(__file__).resolve().parent / "files"

REPLACEMENTS = [
    Path("src/stars_ai/fleet_management.py"),
    Path("src/stars_ai/native_capabilities.py"),
]

HELPERS_ANCHOR = '''def _fleet_id_from_split_block(block: NativeBlock) -> int:\n    """Return the zero-based source fleet ID encoded by a Type-24 block."""\n    if block.type_id != 24 or len(block.data) != 2:\n        raise ValueError("FleetSplit must be a two-byte Type-24 block")\n    # StarsAPI's FleetSplitBlock uses the low byte plus bit zero of byte two.\n    return int.from_bytes(block.data, "little") & 0x1FF\n'''

HELPERS_INSERT = '''\n\ndef _singleton_scout_split_block(state:Any, source_fleet_id:int) -> NativeBlock:\n    """Create the Type-24 FleetSplit record for one singleton-scout transfer."""\n    if not 0 <= int(source_fleet_id) <= 0x1FF:\n        raise ValueError(f"FleetSplit source must be a 9-bit local fleet id; got {source_fleet_id}")\n    data=_u16(_raw_fleet_number(state,int(source_fleet_id)))\n    return NativeBlock(24,len(data),data)\n\n\ndef _singleton_scout_move_block(\n    state:Any,\n    *,\n    source_fleet_id:int,\n    destination_fleet_id:int,\n    scout_slot:int,\n) -> NativeBlock:\n    """Encode one decoded nine-byte Type-23 MoveShips singleton transfer.\n\n    The layout is destination u16, source u16, transfer flags 0x22, design-slot\n    mask u16, then one signed i16 transfer count. The count is positive from\n    the destination fleet's perspective, so +1 moves one scout into the newly\n    created split fleet.\n    """\n    slot=int(scout_slot)\n    if not 0 <= slot < 16:\n        raise ValueError(f"Scout design slot must be 0..15; got {scout_slot}")\n    data=(\n        _u16(_raw_fleet_number(state,int(destination_fleet_id)))\n        + _u16(_raw_fleet_number(state,int(source_fleet_id)))\n        + bytes([0x22])\n        + _u16(1 << slot)\n        + int(1).to_bytes(2,"little",signed=True)\n    )\n    return NativeBlock(23,len(data),data)\n\n\ndef _next_available_split_fleet_id(occupied:set[int]) -> int:\n    """Reserve and return the first unused 9-bit local fleet ID."""\n    for fleet_id in range(0x200):\n        if fleet_id not in occupied:\n            occupied.add(fleet_id)\n            return fleet_id\n    raise ValueError("No free fleet id is available for singleton scout splitting")\n\n# TODO: add codec regression tests for Type24/Type23 singleton scout transfers\n# and validate repeated same-source splits against a fresh Stars! client capture\n# plus host replay before promoting split_scout_fleet beyond EXPERIMENTAL.\n'''

ALLOC_ANCHOR = '''    touched_fleets=set()\n    client_split_blocks, client_split_sources, client_split_diagnostics = (\n'''
ALLOC_REPLACEMENT = '''    touched_fleets=set()\n    allocated_split_fleet_ids=set()\n    client_split_blocks, client_split_sources, client_split_diagnostics = (\n'''

HANDLER_ANCHOR = '''            elif o.kind=="merge_fleets":\n                target_fleet=int(o.payload.get("target_fleet_id",o.payload.get("fleet_id",-1)))\n'''

HANDLER_INSERT = '''            elif o.kind=="split_scout_fleet":\n                source_fleet_id=int(o.payload.get("fleet_id",-1))\n                scout_slot=int(o.payload.get("scout_slot",-1))\n                requested=max(0,int(o.payload.get("singleton_fleets_required",0) or 0))\n                if source_fleet_id in touched_fleets:\n                    skipped.append({"kind":o.kind,"reason":"Scout fleet already has a higher-priority native operation.","payload":o.payload})\n                    continue\n                source_fleet=next((\n                    fleet for fleet in state.fleets\n                    if int(fleet.id)==source_fleet_id and int(fleet.owner)==int(state.player_id)\n                ),None)\n                if source_fleet is None:\n                    skipped.append({"kind":o.kind,"reason":"Scout split source is not a current owned fleet.","payload":o.payload})\n                    continue\n                ship_count=list((source_fleet.native or {}).get("ship_count") or [])\n                live_count=(\n                    int(ship_count[scout_slot] or 0)\n                    if 0 <= scout_slot < len(ship_count) else 0\n                )\n                split_count=min(requested,max(0,live_count-1))\n                if split_count <= 0:\n                    skipped.append({"kind":o.kind,"reason":"Scout stack no longer contains excess ships in the requested design slot.","payload":o.payload})\n                    continue\n\n                occupied={\n                    int(fleet.id) for fleet in state.fleets\n                    if int(fleet.owner)==int(state.player_id) and 0 <= int(fleet.id) <= 0x1FF\n                }\n                occupied.update(allocated_split_fleet_ids)\n                new_fleet_ids=[]\n                block_trace=[]\n                for _ in range(split_count):\n                    destination_fleet_id=_next_available_split_fleet_id(occupied)\n                    allocated_split_fleet_ids.add(destination_fleet_id)\n                    split_block=_singleton_scout_split_block(state,source_fleet_id)\n                    move_block=_singleton_scout_move_block(\n                        state,\n                        source_fleet_id=source_fleet_id,\n                        destination_fleet_id=destination_fleet_id,\n                        scout_slot=scout_slot,\n                    )\n                    generated.extend([split_block,move_block])\n                    new_fleet_ids.append(destination_fleet_id)\n                    block_trace.append({\n                        "new_fleet_id":destination_fleet_id,\n                        "type24_hex":split_block.data.hex(),\n                        "type23_hex":move_block.data.hex(),\n                    })\n\n                touched_fleets.add(source_fleet_id)\n                touched_fleets.update(new_fleet_ids)\n                ep=dict(o.payload)\n                ep["new_fleet_ids"]=new_fleet_ids\n                ep["native_blocks"]=block_trace\n                ep["native_capability_status"]=cap.status\n                ep["native_reference"]=(\n                    "Experimental singleton scout split: one Type24 FleetSplit followed by one "\n                    "nine-byte Type23 MoveShips transfer per excess scout."\n                )\n                emitted.append({"kind":o.kind,"payload":ep,"reason":o.reason})\n            elif o.kind=="merge_fleets":\n                target_fleet=int(o.payload.get("target_fleet_id",o.payload.get("fleet_id",-1)))\n'''


def backup(path: Path) -> None:
    bak = path.with_name(path.name + ".pre-scout-split.bak")
    if not bak.exists():
        shutil.copy2(path, bak)


def replace_file(rel: Path) -> None:
    target = ROOT / rel
    source = PAYLOAD / rel
    if not target.exists():
        raise SystemExit(f"Missing expected repo file: {rel}")
    backup(target)
    target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    print(f"replaced {rel}")


def patch_x_writer() -> None:
    rel = Path("src/stars_ai/native/x_writer.py")
    path = ROOT / rel
    if not path.exists():
        raise SystemExit(f"Missing expected repo file: {rel}")
    text = path.read_text(encoding="utf-8")
    original = text

    if "def _singleton_scout_split_block(" not in text:
        if HELPERS_ANCHOR not in text:
            raise SystemExit("x_writer helper anchor not found; repo likely moved beyond reviewed baseline")
        text = text.replace(HELPERS_ANCHOR, HELPERS_ANCHOR + HELPERS_INSERT, 1)

    if "allocated_split_fleet_ids=set()" not in text:
        if ALLOC_ANCHOR not in text:
            raise SystemExit("x_writer allocation anchor not found; repo likely moved beyond reviewed baseline")
        text = text.replace(ALLOC_ANCHOR, ALLOC_REPLACEMENT, 1)

    if 'elif o.kind=="split_scout_fleet":' not in text:
        if HANDLER_ANCHOR not in text:
            raise SystemExit("x_writer order-handler anchor not found; repo likely moved beyond reviewed baseline")
        text = text.replace(HANDLER_ANCHOR, HANDLER_INSERT, 1)

    if text != original:
        backup(path)
        path.write_text(text, encoding="utf-8")
        print(f"patched {rel}")
    else:
        print(f"already patched {rel}")


def main() -> None:
    marker = ROOT / "src/stars_ai/native/x_writer.py"
    if not marker.exists():
        print("Run this script from the root of your stars-ai checkout.", file=sys.stderr)
        raise SystemExit(2)
    for rel in REPLACEMENTS:
        replace_file(rel)
    patch_x_writer()
    print("\nPatch applied. Recommended next commands:")
    print("  python -m compileall -q src/stars_ai")
    print("  pytest -q")
    print("  git diff -- src/stars_ai/fleet_management.py src/stars_ai/native_capabilities.py src/stars_ai/native/x_writer.py")
    print("\nIMPORTANT: split_scout_fleet remains EXPERIMENTAL pending fresh client/host validation.")


if __name__ == "__main__":
    main()
