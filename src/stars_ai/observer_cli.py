
from __future__ import annotations
import argparse, json
from pathlib import Path
from .native_observer import read_observer_turn, derive_turn_events, save_observer_turn, build_human_report

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--checkpoints", default="10,25,50")
    ap.add_argument("--personas-json")
    args=ap.parse_args()

    root=Path(args.run_dir)
    live=root/"live"
    hst=next(live.glob("*.hst"))
    xy=next(live.glob("*.xy"))
    personas={}
    if args.personas_json:
        personas=json.loads(Path(args.personas_json).read_text())["personas"]

    # This rebuild command reports the current live state; historical observer
    # reports require the per-turn snapshots produced by v4.7.
    turn_jsons=sorted((root/"observer").glob("turn-*.json"))
    if not turn_jsons:
        obs=read_observer_turn(hst,xy,0)
        print(build_human_report(obs,[obs],personas=personas))
        return
    from .native_observer import load_observer_turn
    history=[load_observer_turn(p) for p in turn_jsons]
    print(build_human_report(history[-1],history,personas=personas,checkpoint_from=history[0]))

if __name__=="__main__":
    main()
