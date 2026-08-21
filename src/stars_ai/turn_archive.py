from __future__ import annotations

from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any
import hashlib
import json
import re
import shutil
import time

_NATIVE_SUFFIX_RE = re.compile(r"\.(?:hst|xy|m\d+|x\d+|h\d+)$", re.IGNORECASE)
_SECRET_KEYS = ("password", "secret", "token", "credential", "api_key")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _jsonable(value: Any) -> Any:
    if is_dataclass(value):
        value = asdict(value)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            skey = str(key)
            if any(marker in skey.casefold() for marker in _SECRET_KEYS):
                out[skey] = "<redacted>" if item not in (None, "", False) else item
            else:
                out[skey] = _jsonable(item)
        return out
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return repr(value)


def _unique_phase_dir(root: Path, turn_tag: str, phase: str) -> Path:
    turn_root = root / turn_tag
    turn_root.mkdir(parents=True, exist_ok=True)
    candidate = turn_root / phase
    if not candidate.exists():
        return candidate
    for index in range(2, 1000):
        candidate = turn_root / f"{phase}-{index:02d}"
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"Too many archived copies for {turn_tag}/{phase}")


def _native_sources(game_dir: Path, basename: str) -> list[Path]:
    prefix = basename.casefold()
    out = []
    for path in game_dir.iterdir():
        if not path.is_file():
            continue
        name = path.name
        if not name.casefold().startswith(prefix + "."):
            continue
        suffix = name[len(basename):]
        if _NATIVE_SUFFIX_RE.fullmatch(suffix):
            out.append(path)
    return sorted(out, key=lambda p: p.name.casefold())


def _copy_with_inventory(source: Path, destination: Path) -> dict[str, Any]:
    before_size = source.stat().st_size
    before_mtime = source.stat().st_mtime_ns
    before_sha = _sha256(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    frozen_sha = _sha256(destination)
    after_size = source.stat().st_size
    after_mtime = source.stat().st_mtime_ns
    after_sha = _sha256(source)
    return {
        "source": str(source),
        "archive_path": str(destination),
        "size": destination.stat().st_size,
        "mtime_ns": destination.stat().st_mtime_ns,
        "sha256": frozen_sha,
        "source_sha256_before": before_sha,
        "source_sha256_after": after_sha,
        "source_stable_during_capture": (
            before_size == after_size
            and before_mtime == after_mtime
            and before_sha == after_sha == frozen_sha
        ),
    }


def _add_native_header(row: dict[str, Any], frozen: Path) -> None:
    try:
        from .adapters.stars_native import read_blocks
        header, blocks, _ = read_blocks(frozen)
        row["header"] = {
            "game_id": int(header.game_id),
            "turn": int(header.turn),
            "year": int(header.year),
            "player_index": int(header.player_index),
            "file_type": int(header.file_type),
            "turn_submitted": bool(header.turn_submitted),
        }
        row["blocks"] = [
            {"type_id": int(b.type_id), "size": int(b.size)} for b in blocks
        ]
    except Exception as exc:
        row["parse_error"] = f"{type(exc).__name__}: {exc}"


def _copy_turn_logs(logs_root: Path, turn_tag: str, dest: Path) -> dict[str, Any]:
    inventory: dict[str, Any] = {}
    candidates: list[Path] = []
    for path in logs_root.glob(f"{turn_tag}-*"):
        if path.is_file():
            candidates.append(path)
    history = logs_root / "history"
    if history.is_dir():
        candidates.extend(p for p in history.glob(f"{turn_tag}-*") if p.is_file())
    observer = logs_root / "observer" / f"{turn_tag}.json"
    if observer.is_file():
        candidates.append(observer)
    checkpoints = logs_root / "checkpoints"
    if checkpoints.is_dir():
        candidates.extend(p for p in checkpoints.glob(f"{turn_tag}-*") if p.is_file())

    seen: set[Path] = set()
    for source in sorted(candidates, key=lambda p: str(p).casefold()):
        resolved = source.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        try:
            rel = source.relative_to(logs_root)
        except ValueError:
            rel = Path(source.name)
        frozen = dest / rel
        inventory[str(rel)] = _copy_with_inventory(source, frozen)
    return inventory


def _copy_directory_files(source_root: Path | None, dest: Path, patterns: tuple[str, ...]) -> dict[str, Any]:
    inventory: dict[str, Any] = {}
    if source_root is None or not source_root.is_dir():
        return inventory
    candidates: list[Path] = []
    for pattern in patterns:
        candidates.extend(p for p in source_root.glob(pattern) if p.is_file())
    seen: set[Path] = set()
    for source in sorted(candidates, key=lambda p: p.name.casefold()):
        resolved = source.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        frozen = dest / source.name
        inventory[source.name] = _copy_with_inventory(source, frozen)
    return inventory




def _atomic_json_write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(_jsonable(value), indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def _update_json_archive_index(archive_root: Path, manifest: dict[str, Any], manifest_path: Path) -> None:
    """Maintain machine-readable per-turn and global archive indexes (v8.8)."""
    rel_manifest = str(manifest_path.relative_to(archive_root))
    turn_tag = str(manifest["turn_tag"])
    phase = str(manifest["phase"])
    turn_path = archive_root / turn_tag / "turn.json"
    if turn_path.exists():
        try:
            turn_doc = json.loads(turn_path.read_text(encoding="utf-8"))
        except Exception:
            turn_doc = {}
    else:
        turn_doc = {}
    phases = dict(turn_doc.get("phases") or {})
    phases[phase] = {
        "manifest": rel_manifest,
        "captured_at_ns": int(manifest["captured_at_ns"]),
        "native_file_count": len(manifest.get("native_files") or {}),
        "all_sources_stable": all(
            bool(row.get("source_stable_during_capture"))
            for section in ("native_files","x_templates","ai_state","turn_logs")
            for row in (manifest.get(section) or {}).values()
        ),
    }
    turn_doc = {
        "schema_version": 1,
        "turn_tag": turn_tag,
        "game_basename": manifest.get("game_basename"),
        "phases": phases,
    }
    _atomic_json_write(turn_path, turn_doc)

    index_path = archive_root / "index.json"
    if index_path.exists():
        try:
            index = json.loads(index_path.read_text(encoding="utf-8"))
        except Exception:
            index = {}
    else:
        index = {}
    turns = dict(index.get("turns") or {})
    turns[turn_tag] = {
        "turn_json": str(turn_path.relative_to(archive_root)),
        "phase_count": len(phases),
        "phases": sorted(phases),
    }
    index = {
        "schema_version": 1,
        "format": "stars-ai-turn-archive-index",
        "game_basename": manifest.get("game_basename"),
        "turns": turns,
    }
    _atomic_json_write(index_path, index)


def archive_turn_phase(
    archive_root: str | Path,
    *,
    turn_tag: str,
    phase: str,
    game_dir: str | Path,
    basename: str,
    logs_root: str | Path | None = None,
    templates_root: str | Path | None = None,
    ai_state_root: str | Path | None = None,
    config: Any = None,
    metadata: dict[str, Any] | None = None,
    json_index: bool = True,
) -> Path:
    """Freeze an immutable, hash-addressable diagnostic snapshot of one turn phase.

    This is intentionally evidence-oriented: the manifest is written last, source
    stability is checked around each copy, native files are parsed when possible,
    and existing archives are never overwritten.
    """
    archive_root = Path(archive_root).resolve()
    game_dir = Path(game_dir).resolve()
    logs_path = Path(logs_root).resolve() if logs_root is not None else None
    templates_path = Path(templates_root).resolve() if templates_root is not None else None
    ai_state_path = Path(ai_state_root).resolve() if ai_state_root is not None else None

    dest = _unique_phase_dir(archive_root, str(turn_tag), str(phase))
    dest.mkdir(parents=True)

    native_inventory: dict[str, Any] = {}
    native_sources = _native_sources(game_dir, basename)
    if not native_sources:
        raise FileNotFoundError(
            f"No native {basename} files found in {game_dir} for turn archive"
        )
    for source in native_sources:
        frozen = dest / "game" / source.name
        row = _copy_with_inventory(source, frozen)
        _add_native_header(row, frozen)
        native_inventory[source.name] = row

    template_inventory = _copy_directory_files(
        templates_path,
        dest / "x-templates",
        ("template.x*", "templates.json"),
    )
    state_inventory = _copy_directory_files(
        ai_state_path,
        dest / "ai-state",
        ("player-*-memory*.json", "*.pending.json"),
    )
    log_inventory = (
        _copy_turn_logs(logs_path, str(turn_tag), dest / "logs")
        if logs_path is not None else {}
    )

    manifest = {
        "schema_version": 1,
        "captured_at_ns": time.time_ns(),
        "turn_tag": str(turn_tag),
        "phase": str(phase),
        "game_basename": str(basename),
        "source_game_directory": str(game_dir),
        "archive_directory": str(dest),
        "native_files": native_inventory,
        "x_templates": template_inventory,
        "ai_state": state_inventory,
        "turn_logs": log_inventory,
        "config": _jsonable(config) if config is not None else None,
        "metadata": _jsonable(metadata or {}),
    }
    manifest_path = dest / "manifest.json"
    _atomic_json_write(manifest_path, manifest)
    if json_index:
        _update_json_archive_index(archive_root, manifest, manifest_path)
    (dest / "README_REPLAY.txt").write_text(
        "STARS! AI immutable turn archive\n"
        f"Turn: {turn_tag}\nPhase: {phase}\n\n"
        "The game/ directory is a byte-for-byte snapshot of the native game files at this phase.\n"
        "Do not overwrite the archive. Copy it to a separate replay workspace before experiments.\n"
        "manifest.json contains SHA-256 hashes and source-stability checks for every captured file.\n",
        encoding="utf-8",
    )
    return dest


def verify_turn_archive(phase_dir: str | Path) -> dict[str, Any]:
    phase_dir = Path(phase_dir).resolve()
    manifest_path = phase_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    failures = []
    checked = 0
    for section in ("native_files", "x_templates", "ai_state", "turn_logs"):
        for _, row in (manifest.get(section) or {}).items():
            archived = Path(row["archive_path"])
            # Manifests store the absolute original archive path. If the archive
            # folder was moved, resolve by section-relative basename as fallback.
            if not archived.exists():
                candidates = list(phase_dir.rglob(archived.name))
                archived = candidates[0] if len(candidates) == 1 else archived
            if not archived.exists():
                failures.append(f"missing archived file: {row['archive_path']}")
                continue
            checked += 1
            digest = _sha256(archived)
            if digest != row["sha256"]:
                failures.append(
                    f"hash mismatch: {archived} expected={row['sha256']} actual={digest}"
                )
    return {
        "phase_dir": str(phase_dir),
        "checked_files": checked,
        "ok": not failures,
        "failures": failures,
    }
