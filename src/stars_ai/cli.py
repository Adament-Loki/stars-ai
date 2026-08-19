from __future__ import annotations
import argparse
from pathlib import Path

from .adapters.json_adapter import JsonTurnAdapter
from .adapters.native_core_adapter import NativeCoreTurnAdapter
from .agent import StarsAgent
from .host import run_manifest
from .memory import AgentMemory
from .adapters.stars_native import inspect_m_file, decode_x_orders


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="stars-ai", description="Stars! AI Player V1")
    sub = parser.add_subparsers(dest="command", required=True)

    play = sub.add_parser("play-turn", help="Generate AI orders for one normalized turn.")
    play.add_argument("--state", required=True, help="Input normalized JSON game-state file.")
    play.add_argument("--player", required=True, type=int, help="Player number.")
    play.add_argument("--out", required=True, help="Output JSON orders file.")
    play.add_argument("--memory", help="Persistent player memory JSON file.")

    native = sub.add_parser("play-native", help="Read native .m#/.xy state and generate normalized AI orders.")
    native.add_argument("--mfile", required=True)
    native.add_argument("--xy", required=True)
    native.add_argument("--xfile")
    native.add_argument("--player", required=True, type=int)
    native.add_argument("--out", required=True)
    native.add_argument("--state-out", help="Optional normalized GameState JSON for inspection.")
    native.add_argument("--memory")

    inspect = sub.add_parser("inspect-stars", help="Decode a native Stars! .m# file using its .xy galaxy file.")
    inspect.add_argument("--mfile", required=True)
    inspect.add_argument("--xy", required=True)
    inspect.add_argument("--out")

    orders = sub.add_parser("inspect-orders", help="Decode supported orders from a native Stars! .x# file.")
    orders.add_argument("--xfile", required=True)
    orders.add_argument("--xy", required=True)
    orders.add_argument("--out")

    host = sub.add_parser("host-turn", help="Run every AI player in a manifest.")
    host.add_argument("--manifest", required=True)

    return parser


def main() -> None:
    args = build_parser().parse_args()

    if args.command == "play-turn":
        adapter = JsonTurnAdapter()
        state = adapter.read_state(Path(args.state), args.player)
        memory = AgentMemory.load(args.memory)
        orders = StarsAgent(state, memory).play_turn()
        adapter.write_orders(orders, Path(args.out))
        memory.save(args.memory)
        print(f"Generated {len(orders.orders)} orders for player {args.player}: {args.out}")

    elif args.command == "play-native":
        import json
        adapter = NativeCoreTurnAdapter(args.xy, args.xfile)
        state = adapter.read_state(Path(args.mfile), args.player)
        if args.state_out:
            Path(args.state_out).write_text(json.dumps(state.to_dict(), indent=2), encoding="utf-8")
        memory = AgentMemory.load(args.memory)
        orders = StarsAgent(state, memory).play_turn()
        adapter.write_orders(orders, Path(args.out))
        memory.save(args.memory)
        print(f"Read native Stars! turn and generated {len(orders.orders)} normalized orders for player {args.player}: {args.out}")

    elif args.command == "inspect-stars":
        import json
        result = inspect_m_file(args.mfile, args.xy)
        text = json.dumps(result, indent=2)
        if args.out:
            Path(args.out).parent.mkdir(parents=True, exist_ok=True)
            Path(args.out).write_text(text, encoding="utf-8")
            print(f"Decoded native Stars! state: {args.out}")
        else:
            print(text)

    elif args.command == "inspect-orders":
        import json
        result = decode_x_orders(args.xfile, args.xy)
        text = json.dumps(result, indent=2)
        if args.out:
            Path(args.out).parent.mkdir(parents=True, exist_ok=True)
            Path(args.out).write_text(text, encoding="utf-8")
            print(f"Decoded native Stars! orders: {args.out}")
        else:
            print(text)

    elif args.command == "host-turn":
        outputs = run_manifest(args.manifest)
        print(f"Generated {len(outputs)} AI turn files:")
        for output in outputs:
            print(f"  {output}")


if __name__ == "__main__":
    main()
