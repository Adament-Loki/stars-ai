from __future__ import annotations
import argparse, json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if SRC.exists():
    sys.path.insert(0, str(SRC))

from stars_ai.turn_archive import verify_turn_archive


def main() -> int:
    ap = argparse.ArgumentParser(description="Verify SHA-256 integrity of a STARS! AI turn archive phase.")
    ap.add_argument("phase_dir")
    args = ap.parse_args()
    result = verify_turn_archive(args.phase_dir)
    print(json.dumps(result, indent=2))
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
