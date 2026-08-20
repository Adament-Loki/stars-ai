#!/usr/bin/env python3
"""Upgrade an installed v8.6/v8.6.1 native writer to v8.7 Type27 isolation.

This patch deliberately requires the v8.6 lifecycle marker.  It does not try to
edit an unknown serializer.  The StarsAPI DesignBlock body codec itself lives in
``src/stars_ai/native/starsapi_design_codec.py`` and is consumed by
``design_change.py``; this patch only adds the temporary clean-room Type27
playtest isolation to x_writer.py.
"""
from __future__ import annotations

from pathlib import Path
import shutil
import sys

ROOT=Path(__file__).resolve().parent
TARGET=ROOT/'src'/'stars_ai'/'native'/'x_writer.py'
BACKUP=TARGET.with_name('x_writer.py.pre-v87-starsapi.bak')
OLD_MARKER='V8_6_NATIVE_DESIGN_LIFECYCLE_PATCH'
NEW_MARKER='V8_7_STARSAPI_TYPE27_ISOLATION'


def replace_once(text:str, old:str, new:str, label:str)->str:
    count=text.count(old)
    if count!=1:
        raise RuntimeError(f'{label}: expected exactly one anchor, found {count}')
    return text.replace(old,new,1)


def patch(text:str)->str:
    if NEW_MARKER in text:
        return text
    if OLD_MARKER not in text:
        raise RuntimeError(
            'v8.7 requires an installed v8.6/v8.6.1 native design lifecycle writer; '
            f'missing marker {OLD_MARKER!r}. Run APPLY_NATIVE_WRITER_PATCH.py first.'
        )

    # Preflight only a mutation that is safe *right now*.  A stale/unsafe design
    # proposal must not suppress the rest of the turn merely because it exists.
    anchor='''    orders.orders.sort(key=lambda o:o.priority, reverse=True)\n\n    # Read current M header and known-good X template.\n'''
    insert='''    orders.orders.sort(key=lambda o:o.priority, reverse=True)\n\n    # V8_7_STARSAPI_TYPE27_ISOLATION\n    # During Type27 troubleshooting, a *safe executable* create/delete gets a\n    # clean native turn.  This separates DesignChange failures from interactions\n    # with movement/production/research blocks.  Strategy still computes every\n    # order, but the native writer emits only the design mutation for this player.\n    type27_isolation=False\n    type27_isolation_kind=None\n    type27_isolation_slot=None\n    for candidate in orders.orders:\n        try:\n            if candidate.kind=="create_ship_design":\n                d=encoded_ship_design_from_payload(candidate.payload)\n                if d.replace_existing:\n                    continue\n                assert_free_ship_design_slot(state,d.slot)\n                type27_isolation=True\n                type27_isolation_kind=candidate.kind\n                type27_isolation_slot=int(d.slot)\n                break\n            if candidate.kind=="delete_ship_design":\n                slot=int(candidate.payload["target_slot"])\n                assert_deletable_ship_design_slot(state,slot)\n                type27_isolation=True\n                type27_isolation_kind=candidate.kind\n                type27_isolation_slot=slot\n                break\n        except UnsafeShipDesignMutationError:\n            continue\n\n    # Read current M header and known-good X template.\n'''
    text=replace_once(text,anchor,insert,'type27 isolation preflight')

    loop='''    for o in orders.orders:\n        cap=capability(o.kind)\n        try:\n'''
    loop_new='''    for o in orders.orders:\n        cap=capability(o.kind)\n        if type27_isolation and o.kind not in {"create_ship_design","delete_ship_design"}:\n            skipped.append({\n                "kind":o.kind,\n                "reason":(\n                    f"V8.7 Type27 isolation: emitting only {type27_isolation_kind} "\n                    f"for slot {type27_isolation_slot} this turn so host acceptance "\n                    "tests DesignChange independently from other native order families."\n                ),\n                "payload":o.payload,\n            })\n            continue\n        try:\n'''
    text=replace_once(text,loop,loop_new,'writer isolation loop')

    create_old='''                generated.extend(create_ship_design_blocks(design))\n                touched_design=True\n                emitted.append({"kind":o.kind,"payload":dict(o.payload),"reason":o.reason})\n'''
    create_new='''                design_blocks=create_ship_design_blocks(design)\n                generated.extend(design_blocks)\n                touched_design=True\n                ep=dict(o.payload)\n                ep["type27_hex"]=[b.data.hex(" ") for b in design_blocks]\n                ep["design_body_codec"]="StarsAPI DesignBlock.encode/decode port"\n                emitted.append({"kind":o.kind,"payload":ep,"reason":o.reason})\n'''
    text=replace_once(text,create_old,create_new,'create Type27 trace bytes')

    delete_old='''                generated.append(delete_existing_ship_design_block(slot))\n                touched_design=True\n                emitted.append({"kind":o.kind,"payload":dict(o.payload),"reason":o.reason})\n'''
    delete_new='''                delete_block=delete_existing_ship_design_block(slot)\n                generated.append(delete_block)\n                touched_design=True\n                ep=dict(o.payload)\n                ep["type27_hex"]=[delete_block.data.hex(" ")]\n                ep["design_body_codec"]="existing-design delete; no DesignBlock body"\n                emitted.append({"kind":o.kind,"payload":ep,"reason":o.reason})\n'''
    text=replace_once(text,delete_old,delete_new,'delete Type27 trace bytes')

    submit='''    if template_submit is not None:\n        submit=NativeBlock(46,template_submit.size,template_submit.data)\n        order_stream.extend([\n            submit,\n            NativeBlock(46,submit.size,submit.data),\n            NativeBlock(46,submit.size,submit.data),\n        ])\n'''
    submit_new='''    if template_submit is not None:\n        submit=NativeBlock(46,template_submit.size,template_submit.data)\n        if type27_isolation:\n            # Controlled client ship-design X files use a single trailing Type46\n            # in the clean create case.  Keep the diagnostic X as close to that\n            # observed transaction shape as possible.\n            order_stream.append(submit)\n        else:\n            order_stream.extend([\n                submit,\n                NativeBlock(46,submit.size,submit.data),\n                NativeBlock(46,submit.size,submit.data),\n            ])\n'''
    text=replace_once(text,submit,submit_new,'submit isolation shape')

    trace='''                "filehash_order_length":int.from_bytes(filehash.data[:2],"little"),\n            },\n'''
    trace_new='''                "filehash_order_length":int.from_bytes(filehash.data[:2],"little"),\n                "type27_isolation":type27_isolation,\n                "type27_isolation_kind":type27_isolation_kind,\n                "type27_isolation_slot":type27_isolation_slot,\n            },\n'''
    text=replace_once(text,trace,trace_new,'trace isolation fields')
    return text


def main()->int:
    if not TARGET.exists():
        print(f'ERROR: missing {TARGET}',file=sys.stderr); return 2
    text=TARGET.read_text(encoding='utf-8')
    try:
        updated=patch(text)
    except Exception as exc:
        print(f'ERROR: {exc}',file=sys.stderr); return 3
    if updated==text:
        print('v8.7 StarsAPI Type27 isolation already installed.')
        return 0
    if not BACKUP.exists():
        shutil.copy2(TARGET,BACKUP)
    TARGET.write_text(updated,encoding='utf-8')
    print(f'Patched {TARGET}')
    print(f'Backup: {BACKUP}')
    return 0

if __name__=='__main__':
    raise SystemExit(main())
