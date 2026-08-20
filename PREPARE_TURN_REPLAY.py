from __future__ import annotations
import argparse, json, shutil
from pathlib import Path
import hashlib


def sha256(path: Path) -> str:
    h=hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda:f.read(1024*1024),b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    ap=argparse.ArgumentParser(description="Copy an archived turn phase to a separate replay/evidence workspace.")
    ap.add_argument("phase_dir", help="e.g. logs/turn-archive/turn-003/10-pre-host")
    ap.add_argument("output_dir", help="must not already contain files")
    args=ap.parse_args()
    phase=Path(args.phase_dir).resolve()
    out=Path(args.output_dir).resolve()
    manifest=json.loads((phase/"manifest.json").read_text(encoding="utf-8"))
    game=phase/"game"
    if not game.is_dir():
        raise SystemExit(f"archive has no game/ directory: {game}")
    if out.exists() and any(out.iterdir()):
        raise SystemExit(f"refusing non-empty replay destination: {out}")
    out.mkdir(parents=True,exist_ok=True)
    for source in sorted(game.iterdir()):
        if source.is_file():
            shutil.copy2(source,out/source.name)
    shutil.copy2(phase/"manifest.json",out/"SOURCE_ARCHIVE_MANIFEST.json")
    (out/"REPLAY_NOTES.txt").write_text(
        "This directory is a COPY of an immutable STARS! AI turn archive.\n"
        "It is safe to modify this copy for byte-level experiments.\n"
        "Do not edit the source archive.\n",
        encoding="utf-8",
    )
    print(f"Prepared replay copy: {out}")
    for p in sorted(out.iterdir()):
        if p.is_file() and p.name not in {"SOURCE_ARCHIVE_MANIFEST.json","REPLAY_NOTES.txt"}:
            print(f"  {p.name}: {p.stat().st_size} bytes sha256={sha256(p)}")
    return 0

if __name__=="__main__":
    raise SystemExit(main())
