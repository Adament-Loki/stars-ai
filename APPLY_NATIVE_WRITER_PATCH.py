"""Install v8.6 native population + explicit ship-design lifecycle support.

This patch rebases from the exact public-main x_writer.py blob rather than
patching an already-modified serializer in place.  If v8.5 is currently active,
its installer-created ``x_writer.py.pre-v85.bak`` is used as the known baseline.

Design lifecycle in v8.6:
  * create_ship_design: FREE SLOT ONLY, two 11/A0|slot Type27 records.
  * delete_ship_design: existing slot only, 10/<slot>, but only when zero live
    ships, zero queued builds, and zero remaining production are independently
    verified by the writer.
  * replace_ship_design: never serialized atomically.  Delete on turn N, verify
    the next M-file, create on turn N+1.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
import sys

EXPECTED_BLOB = "8d2c8d3216b4f8868a8b1952672116951d1dd763"
MARKER = "V8_6_NATIVE_DESIGN_LIFECYCLE_PATCH"
OLD_MARKER = "V8_5_NATIVE_ONION_PATCH"


def git_blob_sha(data: bytes) -> str:
    return hashlib.sha1(f"blob {len(data)}\0".encode() + data).hexdigest()


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one anchor, found {count}")
    return text.replace(old, new, 1)


def patch(text: str) -> str:
    if MARKER in text:
        return text
    if OLD_MARKER in text:
        raise RuntimeError("patch() requires the clean public-main baseline, not an already-v8.5-patched writer")

    text = replace_once(
        text,
        "from stars_ai.native_capabilities import capability\n",
        "from stars_ai.native_capabilities import capability\n"
        "from stars_ai.native.design_change import (\n"
        "    UnsafeShipDesignMutationError, assert_deletable_ship_design_slot,\n"
        "    assert_free_ship_design_slot, create_ship_design_blocks,\n"
        "    delete_existing_ship_design_block, encoded_ship_design_from_payload,\n"
        ")  # V8_6_NATIVE_DESIGN_LIFECYCLE_PATCH\n"
        "from stars_ai.native.population_transport import population_load_block\n",
        "v8.6 design/population imports",
    )

    text = replace_once(
        text,
        'if kind in {"transport_minerals","transport_unload_remainder"} or mission=="transport":\n',
        'if kind in {"transport_population","transport_minerals","transport_unload_remainder"} or mission=="transport":\n',
        "transport task classification",
    )

    anchor = '''def _transport_mineral_blocks(state:Any,payload:dict)->list[NativeBlock]:\n'''
    pop_func = '''def _transport_population_blocks(state:Any,payload:dict)->list[NativeBlock]:\n    """Load bounded population at source, fly normally, unload all at owned destination."""\n    fid=int(payload["fleet_id"])\n    pid=int(payload["destination_planet_id"])\n    warp=int(payload.get("warp",6))\n    qty=int(payload.get("population_kt",0) or 0)\n    movement={**payload,"mission":"transport"}\n    diagnostic=_native_waypoint_decision(state,movement,operation_kind="transport_population")\n    if diagnostic["result"]=="CONTINUE":\n        return []\n    if diagnostic["result"]!="ADD":\n        raise UnsafeWaypointMutationError(diagnostic)\n    route_type=0x51\n    return [\n        population_load_block(state,fid,qty),\n        _movement_to_planet_block(\n            state,{"fleet_id":fid,"destination_planet_id":pid,"warp":warp,"mission":"transport"},\n            initial_object_type=0x51,\n        ),\n        _waypoint_change_task_block(\n            state,fleet_id=fid,destination_planet_id=pid,warp=warp,task=1,\n            additional=TRANSPORT_UNLOAD_ALL_LOAD_OPTIMAL,object_type=route_type\n        ),\n    ]\n\n\n'''
    text = replace_once(text, anchor, pop_func + anchor, "population transport block builder")

    text = replace_once(
        text,
        '''    touched_fleets=set()\n    touched_planets=set()\n    touched_relations=set()\n    waypoint_diagnostics=[]\n''',
        '''    touched_fleets=set()\n    touched_planets=set()\n    touched_relations=set()\n    touched_design=False\n    waypoint_diagnostics=[]\n''',
        "writer touched state",
    )

    writer_anchor = '''            elif o.kind=="transport_minerals":\n'''
    writer_pop = '''            elif o.kind=="transport_population":\n                fid=int(o.payload["fleet_id"])\n                if fid in touched_fleets:\n                    skipped.append({"kind":o.kind,"reason":"Fleet already has a higher-priority native operation.","payload":o.payload})\n                    continue\n                movement_payload={**o.payload,"mission":"transport"}\n                diagnostic=_native_waypoint_decision(state,movement_payload,operation_kind=o.kind)\n                waypoint_diagnostics.append(diagnostic)\n                native_action=diagnostic["result"]\n                if native_action!="ADD":\n                    touched_fleets.add(fid)\n                    skipped.append({"kind":o.kind,"reason":diagnostic["reason"],"payload":{**o.payload,"native_waypoint_action":native_action}})\n                    continue\n                generated.extend(_transport_population_blocks(state,o.payload))\n                touched_fleets.add(fid)\n                ep=dict(o.payload); ep["native_waypoint_action"]=native_action\n                emitted.append({"kind":o.kind,"payload":ep,"reason":o.reason})\n'''
    text = replace_once(text, writer_anchor, writer_pop + writer_anchor, "writer population branch")

    design_anchor = '''            elif o.kind=="set_planet_queue":\n'''
    design_branch = '''            elif o.kind=="create_ship_design":\n                if touched_design:\n                    skipped.append({"kind":o.kind,"reason":"Only one native Type27 design mutation is allowed per turn.","payload":o.payload})\n                    continue\n                design=encoded_ship_design_from_payload(o.payload)\n                if design.replace_existing:\n                    skipped.append({"kind":o.kind,"reason":"Atomic replacement is forbidden in v8.6; delete the dead slot first, read back next M, then create.","payload":o.payload})\n                    continue\n                try:\n                    assert_free_ship_design_slot(state,design.slot)\n                except UnsafeShipDesignMutationError as exc:\n                    skipped.append({"kind":o.kind,"reason":str(exc),"payload":{**o.payload,"slot_safety":exc.diagnostic}})\n                    continue\n                generated.extend(create_ship_design_blocks(design))\n                touched_design=True\n                emitted.append({"kind":o.kind,"payload":dict(o.payload),"reason":o.reason})\n            elif o.kind=="delete_ship_design":\n                if touched_design:\n                    skipped.append({"kind":o.kind,"reason":"Only one native Type27 design mutation is allowed per turn.","payload":o.payload})\n                    continue\n                slot=int(o.payload["target_slot"])\n                try:\n                    assert_deletable_ship_design_slot(state,slot)\n                except UnsafeShipDesignMutationError as exc:\n                    skipped.append({"kind":o.kind,"reason":str(exc),"payload":{**o.payload,"slot_safety":exc.diagnostic}})\n                    continue\n                generated.append(delete_existing_ship_design_block(slot))\n                touched_design=True\n                emitted.append({"kind":o.kind,"payload":dict(o.payload),"reason":o.reason})\n            elif o.kind=="replace_ship_design":\n                skipped.append({"kind":o.kind,"reason":"Legacy atomic replace is blocked. v8.6 uses delete -> next-M readback -> create.","payload":o.payload})\n'''
    text = replace_once(text, design_anchor, design_branch + design_anchor, "writer Type27 lifecycle branches")

    text = text.replace(
        'if kind in ("move_fleet","colony_operation","transport_minerals","transport_unload_remainder"):',
        'if kind in ("move_fleet","colony_operation","transport_population","transport_minerals","transport_unload_remainder"):')
    text = text.replace(
        'design_orders=[o for o in orders.orders if o.kind=="create_design"]',
        'design_orders=[o for o in orders.orders if o.kind in ("create_design","create_ship_design","delete_ship_design","replace_ship_design")]')
    text = text.replace(
        '            elif kind=="transport_minerals":\n',
        '''            elif kind=="transport_population":\n                lines.append(\n                    f"{name} - EXPERIMENTAL POPULATION TRANSPORT -> {target_name} warp {warp} - "\n                    f"population={payload.get('population_colonists','?')} colonists / {payload.get('population_kt','?')} kT; "\n                    "loaded cargo flies normally and unloads before any later gate use."\n                )\n            elif kind=="transport_minerals":\n''',
        1,
    )
    return text


def main() -> int:
    repo = Path.cwd()
    path = repo / "src" / "stars_ai" / "native" / "x_writer.py"
    if not path.exists():
        print(f"ERROR: run from the stars-ai repo root; missing {path}", file=sys.stderr)
        return 2

    current_raw = path.read_bytes()
    current_text = current_raw.decode("utf-8")
    if MARKER in current_text:
        print("x_writer.py already contains the v8.6 design lifecycle patch.")
        return 0

    source_raw = current_raw
    source_label = "current x_writer.py"
    if git_blob_sha(source_raw) != EXPECTED_BLOB:
        pre_v85 = path.with_suffix(path.suffix + ".pre-v85.bak")
        if pre_v85.exists() and git_blob_sha(pre_v85.read_bytes()) == EXPECTED_BLOB:
            source_raw = pre_v85.read_bytes()
            source_label = str(pre_v85)
        else:
            print(
                "ERROR: cannot find the exact known public-main serializer baseline.\n"
                f"  expected blob: {EXPECTED_BLOB}\n"
                f"  current blob:  {git_blob_sha(current_raw)}\n"
                "Expected either an unmodified baseline x_writer.py or the v8.5 installer backup\n"
                "src/stars_ai/native/x_writer.py.pre-v85.bak. Refusing a blind serializer edit.",
                file=sys.stderr,
            )
            return 3

    baseline_text = source_raw.decode("utf-8")
    updated = patch(baseline_text)
    compile(updated, str(path), "exec")

    if current_raw != source_raw:
        quarantine = path.with_suffix(path.suffix + ".pre-v86-current.bak")
        if not quarantine.exists():
            quarantine.write_bytes(current_raw)
    baseline_backup = path.with_suffix(path.suffix + ".pre-v86-baseline.bak")
    if not baseline_backup.exists():
        baseline_backup.write_bytes(source_raw)
    path.write_text(updated, encoding="utf-8")

    print(f"Patched {path} with v8.6 explicit design lifecycle support")
    print(f"Rebased from: {source_label}")
    print(f"Baseline backup: {baseline_backup}")
    print("CREATE = free slot only; DELETE = zero live/queued/remaining only; atomic REPLACE blocked.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
