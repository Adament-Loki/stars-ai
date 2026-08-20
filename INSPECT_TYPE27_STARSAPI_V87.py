#!/usr/bin/env python3
"""Inspect any client/AI X Type27 records with the StarsAPI DesignBlock codec."""
from __future__ import annotations
import argparse, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parent
SRC=ROOT/'src'
if str(SRC) not in sys.path: sys.path.insert(0,str(SRC))
from stars_ai.adapters.stars_native import read_blocks
from stars_ai.native.starsapi_design_codec import decode_design_block, starsapi_body_roundtrip


def main()->int:
    ap=argparse.ArgumentParser()
    ap.add_argument('xfile',type=Path)
    args=ap.parse_args()
    h,blocks,_=read_blocks(args.xfile)
    rows=[b.data for b in blocks if b.type_id==27]
    print(f'{args.xfile}: turn={h.turn} player={h.player_index+1} Type27={len(rows)}')
    bad=0
    for n,data in enumerate(rows,1):
        ctrl=data[:2]
        if len(data)<2:
            print(f'#{n} MALFORMED len={len(data)}'); bad+=1; continue
        if data[0] % 16 == 0:
            print(f'#{n} DELETE-LIKE control={ctrl.hex(" ")} slot={data[1]&0x0f} len={len(data)}')
            continue
        try:
            p=decode_design_block(data[2:],allow_type27_bit0_clear=True)
            exact=starsapi_body_roundtrip(data[2:],type27_embedded=p.type27_bit0_was_clear)==data[2:]
        except Exception as exc:
            print(f'#{n} BODY-INVALID control={ctrl.hex(" ")}: {type(exc).__name__}: {exc}')
            bad+=1; continue
        print(
            f'#{n} DESIGN control={ctrl.hex(" ")} slot={p.design_number} byte1=0x{p.raw_second_byte:02x} '
            f'bit0_clear={p.type27_bit0_was_clear} full={p.is_full_design} hull={p.hull_id} pic={p.pic} '
            f'armor={p.armor} slots={p.slot_count} turn={p.turn_designed} built={p.total_built} '
            f'remaining={p.total_remaining} name={p.name!r} roundtrip={"PASS" if exact else "FAIL"}'
        )
        if not exact: bad+=1
    return 2 if bad else 0

if __name__=='__main__':
    raise SystemExit(main())
