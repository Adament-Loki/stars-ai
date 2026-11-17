
from __future__ import annotations
import argparse
from .autohost import (
    ExternalCommandOrderBridge,
    IntegratedNativeOrderBridge,
    NoopOrderBridge,
    load_windows_autohost_config,
    normalize_human_turn_timeout,
)
from .windows_autohost import run_50_turn_game

def build_parser() -> argparse.ArgumentParser:
    ap=argparse.ArgumentParser(description="Automate a 4-AI Stars! host game for up to 50 turns.")
    ap.add_argument("--config", required=True)
    ap.add_argument("--writer-command", help="External native writer command template.")
    ap.add_argument("--noop", action="store_true", help="Host-loop diagnostic only; reuses existing .x files.")
    ap.add_argument("--external-writer", action="store_true", help="Use --writer-command instead of the integrated native writer.")
    ap.add_argument(
        "--human-turn-timeout-seconds",
        default=None,
        help=(
            "Override human_turn_timeout_seconds from the autoplay JSON. "
            "A positive number of seconds, or 'forever' to wait with no limit. "
            "When the limit expires the turn is not hosted."
        ),
    )
    return ap

def apply_cli_overrides(cfg, args) -> None:
    """Apply command-line overrides onto a config loaded from autoplay JSON."""
    raw=getattr(args, "human_turn_timeout_seconds", None)
    if raw is not None:
        cfg.human_turn_timeout_seconds=normalize_human_turn_timeout(raw)

def main():
    args=build_parser().parse_args()

    cfg=load_windows_autohost_config(args.config)
    try:
        apply_cli_overrides(cfg, args)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    if args.noop:
        bridge=NoopOrderBridge()
    elif args.external_writer:
        if not args.writer_command:
            raise SystemExit("--external-writer requires --writer-command")
        bridge=ExternalCommandOrderBridge(args.writer_command)
    else:
        bridge=IntegratedNativeOrderBridge(
            cfg.personas, cfg.console_player_logs, cfg.allied_pairs, cfg.human_player_ids,
        )

    results=run_50_turn_game(cfg,bridge)
    ok=sum(1 for r in results if r.success)
    print(f"Completed {ok}/{cfg.turns} host generations")
    if results and not results[-1].success:
        print(results[-1].message)

if __name__=="__main__":
    main()
