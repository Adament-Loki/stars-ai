
from __future__ import annotations
import argparse, json
from pathlib import Path
from .autohost import (
    ExternalCommandOrderBridge,
    IntegratedNativeOrderBridge,
    NoopOrderBridge,
    WindowsAutoHostConfig,
)
from .windows_autohost import run_50_turn_game

def main():
    ap=argparse.ArgumentParser(description="Automate a 4-AI Stars! host game for up to 50 turns.")
    ap.add_argument("--config", required=True)
    ap.add_argument("--writer-command", help="External native writer command template.")
    ap.add_argument("--noop", action="store_true", help="Host-loop diagnostic only; reuses existing .x files.")
    ap.add_argument("--external-writer", action="store_true", help="Use --writer-command instead of the integrated native writer.")
    args=ap.parse_args()

    cfg=WindowsAutoHostConfig(**json.loads(Path(args.config).read_text(encoding="utf-8")))
    if args.noop:
        bridge=NoopOrderBridge()
    elif args.external_writer:
        if not args.writer_command:
            raise SystemExit("--external-writer requires --writer-command")
        bridge=ExternalCommandOrderBridge(args.writer_command)
    else:
        bridge=IntegratedNativeOrderBridge(cfg.personas, cfg.console_player_logs, cfg.allied_pairs)

    results=run_50_turn_game(cfg,bridge)
    ok=sum(1 for r in results if r.success)
    print(f"Completed {ok}/{cfg.turns} host generations")
    if results and not results[-1].success:
        print(results[-1].message)

if __name__=="__main__":
    main()
