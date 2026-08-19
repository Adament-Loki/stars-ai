from __future__ import annotations
import argparse, json
from pathlib import Path
from .player_state import PlayerState


def main()->int:
    p=argparse.ArgumentParser(description='StarsAPI-inspired native Stars! state inspector')
    p.add_argument('m_file'); p.add_argument('--xy'); p.add_argument('--x'); p.add_argument('--json-out'); p.add_argument('--full',action='store_true')
    a=p.parse_args(); state=PlayerState.from_files(a.m_file,a.xy,a.x)
    payload=state.to_dict() if a.full else state.summary()
    text=json.dumps(payload,indent=2,default=str)
    print(text)
    if a.json_out: Path(a.json_out).write_text(json.dumps(state.to_dict(),indent=2,default=str),encoding='utf-8')
    return 0

if __name__=='__main__': raise SystemExit(main())
