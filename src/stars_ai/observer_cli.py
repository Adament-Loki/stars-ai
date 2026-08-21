
from __future__ import annotations
import argparse, json
from pathlib import Path
from .native_observer import (
    read_observer_turn, derive_turn_events, save_observer_turn,
    build_running_game_report,
)

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--checkpoints", default="10,25,50")
    ap.add_argument("--personas-json")
    args=ap.parse_args()

    root=Path(args.run_dir)
    personas={}
    if args.personas_json:
        personas=json.loads(Path(args.personas_json).read_text())["personas"]

    observer_root=root/"logs"/"observer"
    if not observer_root.is_dir():
        # Compatibility with older manually assembled run folders.
        observer_root=root/"observer"
    turn_jsons=sorted(observer_root.glob("turn-*.json"))
    if turn_jsons:
        from .native_observer import load_observer_turn
        history=[load_observer_turn(p) for p in turn_jsons]
        previous=None
        for current in history:
            # Events are derived output.  Recalculate them from the immutable
            # snapshots so an improved observer can enrich an old playtest.
            current.events=derive_turn_events(previous,current)
            previous=current
        checkpoints=[int(value) for value in args.checkpoints.split(",") if value.strip()]
        print(build_running_game_report(
            history,personas=personas,major_report_turns=checkpoints,
        ))
        return

    # A run without observer snapshots can still render a one-turn live view.
    live=root/"live"
    hst=next(live.glob("*.hst"))
    xy=next(live.glob("*.xy"))
    obs=read_observer_turn(hst,xy,0)
    print(build_running_game_report([obs],personas=personas))

if __name__=="__main__":
    main()
