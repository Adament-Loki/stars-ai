
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Callable, Any
import json
import shutil
import subprocess
import time
import os
import hashlib
import re

from .native.player_state import PlayerState
from .native.x_writer import ORDER_BLOCK_TYPES, write_ai_turn
from .native.history_merge import (
    inspect_history_coverage,
    merge_history_file,
)
from .native_observer import read_observer_turn, derive_turn_events, save_observer_turn, load_observer_turn, build_human_report
from .turn_archive import archive_turn_phase

@dataclass
class WindowsAutoHostConfig:
    stars_exe: str
    seed_dir: str
    output_dir: str
    # Retained for configuration compatibility only. The authoritative game
    # basename is discovered from seed_dir during fail-closed validation.
    game_name: str | None = None
    player_ids: list[int] = field(default_factory=lambda: [1,2,3,4])
    turns: int = 50
    # False/default: restore the immutable seed before playing. True: validate
    # and continue the current game beside stars_exe for `turns` more turns.
    play_on: bool = False
    checkpoints: list[int] = field(default_factory=lambda: [10,25,50])
    host_password: str | None = None
    keep_every_turn: bool = True
    # v8.7.1: immutable before/after snapshots for native-order debugging.
    turn_archive_enabled: bool = True
    turn_archive_include_logs: bool = True
    # Merge each current M file into its cumulative H file in native Python.
    # This replaces the Stars! client-open step required by headless hosting.
    auto_merge_history: bool = True
    # Fail closed if post-merge semantic coverage cannot be proven. Retained as
    # a compatibility/safety switch; it no longer requests a manual client step.
    require_history_sync: bool = True
    stop_on_missing_x: bool = True
    host_timeout_seconds: int = 180
    host_poll_seconds: float = 0.5
    host_settle_seconds: float = 1.5
    prevent_parallel_stars: bool = True
    # Deprecated compatibility field. Native operations always run beside the
    # configured Stars! executable; seed_dir is immutable.
    use_seed_as_live: bool = False
    pre_host_audit: bool = True
    print_observer_each_turn: bool = True
    cleanup_output_on_start: bool = True
    # Permanent known-good X templates. If omitted, defaults to a sibling of
    # seed_dir so output cleanup and Stars! host processing cannot remove it.
    x_template_dir: str | None = None
    # Persistent strategic memory. Defaults to a sibling of seed_dir so output
    # cleanup/restarts do not erase what each AI player has learned.
    ai_state_dir: str | None = None
    # Which players' detailed AI summaries/decision reports are echoed to the console.
    # None => all configured players; [] => suppress per-player console detail.
    console_player_logs: list[int] | None = None
    # Reciprocal Friend relationships. Example [[1,2]] => P1<->P2.
    allied_pairs: list[list[int]] = field(default_factory=list)
    personas: dict[str,str] = field(default_factory=lambda: {
        "1":"Balanced","2":"Expansionist","3":"Balanced","4":"Balanced"
    })

@dataclass
class TurnExecution:
    turn: int
    player_order_files: list[str]
    host_returncode: int | None
    year_before: int | None
    year_after: int | None
    checkpoint_written: bool
    success: bool
    message: str


class SeedValidationError(RuntimeError):
    """The immutable starting game is unsafe or incomplete."""


@dataclass(frozen=True)
class ValidatedSeedGame:
    seed_dir: Path
    basename: str
    game_id: int
    turn: int
    files: tuple[Path, ...]
    hst: Path
    xy: Path
    m_files: dict[int, Path]
    x_files: dict[int, Path]
    x_sha256: dict[int, str]


@dataclass(frozen=True)
class ValidatedLiveGame:
    game_dir: Path
    basename: str
    game_id: int
    turn: int
    files: tuple[Path, ...]
    hst: Path
    xy: Path
    m_files: dict[int, Path]


class LiveGameValidationError(RuntimeError):
    """The requested play-on game is incomplete, mismatched, or unsafe."""


class HistorySyncError(RuntimeError):
    """Automatic history merge or its semantic coverage check failed."""

class NativeOrderBridge:
    """
    Interface between semantic AI decisions and a real Stars! .x# file.

    v4.3 intentionally refuses to fake unsupported native serialization.
    Implementations must return a path to a valid native .x# file.
    """
    def create_x_file(
        self,
        *,
        player_id: int,
        m_path: Path,
        xy_path: Path,
        existing_x_path: Path | None,
        output_x_path: Path,
        turn_dir: Path,
    ) -> Path:
        raise NotImplementedError


class IntegratedNativeOrderBridge(NativeOrderBridge):
    """
    Built-in semantic OrderSet -> native .x# bridge.

    Requires one known-good X template per player. The autohost bootstraps those
    templates once from the manually-created initial X files, stores immutable
    copies outside the live game/output directories, and regenerates a fresh X
    file from the current M header every turn.
    """
    def __init__(
        self,
        personas: dict[str,str] | None = None,
        console_player_logs: list[int] | None = None,
        allied_pairs: list[list[int]] | None = None,
        memory_root: str | Path | None = None,
    ):
        self.personas=personas or {}
        self.console_player_logs=None if console_player_logs is None else {int(x) for x in console_player_logs}
        self.allied_pairs=[list(map(int,pair)) for pair in (allied_pairs or [])]
        self.memory_root=Path(memory_root).resolve() if memory_root else None
        self._pending_memories:dict[int,tuple[Path,Path]]={}

    def _friend_ids_for(self, player_id:int) -> list[int]:
        out=set()
        for pair in self.allied_pairs:
            if len(pair)!=2:
                continue
            a,b=int(pair[0]),int(pair[1])
            if a==player_id and b!=player_id:
                out.add(b)
            elif b==player_id and a!=player_id:
                out.add(a)
        return sorted(out)

    def create_x_file(self, *, player_id, m_path, xy_path, existing_x_path, output_x_path, turn_dir):
        if existing_x_path is None or not existing_x_path.exists():
            raise RuntimeError(
                f"Integrated writer needs a persistent known-good .x{player_id} template."
            )
        memory_path=(
            self.memory_root/f"player-{int(player_id):02d}-memory.json"
            if self.memory_root is not None else None
        )
        pending_memory_path=(
            self.memory_root/f"player-{int(player_id):02d}-memory.pending.json"
            if self.memory_root is not None else None
        )
        result=write_ai_turn(
            player_id=player_id,
            m_path=m_path,
            xy_path=xy_path,
            template_x_path=existing_x_path,
            output_x_path=output_x_path,
            persona_name=self.personas.get(str(player_id),"Balanced"),
            trace_path=turn_dir/f"{getattr(self,'turn_tag','current')}-player-{player_id:02d}-decision-native.json",
            friend_player_ids=self._friend_ids_for(int(player_id)),
            memory_path=memory_path,
            memory_output_path=pending_memory_path,
        )
        if memory_path is not None and pending_memory_path is not None:
            self._pending_memories[int(player_id)]=(memory_path,pending_memory_path)
        moves=[
            e for e in result.emitted
            if e.get("kind")=="move_fleet"
        ]
        skipped_moves=[
            e for e in result.skipped
            if e.get("kind")=="move_fleet"
        ]
        move_text=", ".join(
            f"F{m['payload'].get('fleet_id')}->P{m['payload'].get('destination_planet_id')}@W{m['payload'].get('warp','?')}"
            for m in moves
        ) or "none"
        show_console = (
            self.console_player_logs is None
            or int(player_id) in self.console_player_logs
        )
        if show_console:
            print(
                f"[AI P{player_id} Y{result.year}] emitted moves: {move_text}; "
                f"skipped moves: {len(skipped_moves)}",
                flush=True
            )
        report_path=turn_dir/f"{getattr(self,'turn_tag','current')}-player-{player_id:02d}-DECISION_REPORT.txt"
        if show_console and report_path.exists():
            print(report_path.read_text(encoding="utf-8"), flush=True)
        return output_x_path

    def commit_pending_memory(self) -> None:
        for player_id,(committed,pending) in sorted(self._pending_memories.items()):
            if not pending.exists():
                raise RuntimeError(
                    f"Pending AI memory is missing for player {player_id}: {pending}"
                )
            pending.replace(committed)
        self._pending_memories.clear()

    def discard_pending_memory(self) -> None:
        for _,pending in self._pending_memories.values():
            pending.unlink(missing_ok=True)
        self._pending_memories.clear()

class ExternalCommandOrderBridge(NativeOrderBridge):
    """
    Allows the native order writer to be developed/tested independently.

    Command placeholders:
      {player_id} {m} {xy} {existing_x} {output_x} {turn_dir}

    Example:
      python native_writer.py --player {player_id} --m "{m}" --xy "{xy}" --out "{output_x}"
    """
    def __init__(self, command_template: str, timeout_seconds: int = 60):
        self.command_template = command_template
        self.timeout_seconds = timeout_seconds

    def create_x_file(self, *, player_id, m_path, xy_path, existing_x_path, output_x_path, turn_dir):
        cmd = self.command_template.format(
            player_id=player_id,
            m=str(m_path),
            xy=str(xy_path),
            existing_x=str(existing_x_path or ""),
            output_x=str(output_x_path),
            turn_dir=str(turn_dir),
        )
        cp = subprocess.run(
            cmd,
            shell=True,
            cwd=str(turn_dir),
            capture_output=True,
            text=True,
            timeout=self.timeout_seconds,
        )
        (turn_dir / f"player-{player_id:02d}-native-writer.stdout.txt").write_text(cp.stdout or "", encoding="utf-8")
        (turn_dir / f"player-{player_id:02d}-native-writer.stderr.txt").write_text(cp.stderr or "", encoding="utf-8")
        if cp.returncode != 0:
            raise RuntimeError(f"Native writer failed for P{player_id}: rc={cp.returncode}")
        if not output_x_path.exists():
            raise RuntimeError(f"Native writer did not create {output_x_path}")
        return output_x_path

class NoopOrderBridge(NativeOrderBridge):
    """
    Diagnostic bridge only.

    Reuses an existing native .x# if supplied. It does NOT represent AI play
    and exists only to verify the host automation/snapshot/observer loop.
    """
    def create_x_file(self, *, player_id, m_path, xy_path, existing_x_path, output_x_path, turn_dir):
        if existing_x_path is None or not existing_x_path.exists():
            raise RuntimeError(
                f"No existing .x{player_id} available. Noop bridge cannot invent native orders."
            )
        shutil.copy2(existing_x_path, output_x_path)
        return output_x_path


def _persistent_x_template_root(cfg: WindowsAutoHostConfig, seed: Path) -> Path:
    if cfg.x_template_dir:
        return Path(cfg.x_template_dir).expanduser().resolve()
    # Keep this OUTSIDE seed_dir (Stars! live files) and output_dir (which may be
    # deleted on startup). A sibling survives both hosting and diagnostic cleanup.
    return seed.parent / f"{seed.name}-stars-ai-x-templates"



def _persistent_ai_state_root(cfg: WindowsAutoHostConfig, seed: Path) -> Path:
    if cfg.ai_state_dir:
        root=Path(cfg.ai_state_dir).expanduser().resolve()
    else:
        root=seed.parent / f"{seed.name}-stars-ai-state"
    return root


def _template_matches_game(template:Path, current_m:Path, player_id:int)->bool:
    try:
        from .adapters.stars_native import read_blocks
        th,_,_=read_blocks(template)
        mh,_,_=read_blocks(current_m)
        return int(th.game_id)==int(mh.game_id) and int(th.player_index)==int(player_id-1)
    except Exception:
        return False


def _bootstrap_persistent_x_templates(
    cfg: WindowsAutoHostConfig,
    *,
    seed: Path,
    game: Path,
    source_x_files: dict[int,Path] | None = None,
    preserve_matching: bool = False,
) -> Path:
    """
    Capture initial known-good X files from the validated staged game.

    During the turn loop Stars! may consume/delete GAME.x# after hosting, so the
    refreshed templates live outside both the execution and output directories.
    """
    root=_persistent_x_template_root(cfg,seed)
    output_root=Path(cfg.output_dir).resolve()
    root_resolved=root.resolve()
    try:
        root_resolved.relative_to(output_root)
        raise RuntimeError(
            "x_template_dir must not be inside output_dir because cleanup_output_on_start may delete it."
        )
    except ValueError:
        pass
    try:
        root_resolved.relative_to(game.resolve())
        raise RuntimeError(
            "x_template_dir must not be inside the live Stars! game directory. Use a sibling directory."
        )
    except ValueError:
        pass
    try:
        root_resolved.relative_to(seed.resolve())
        raise RuntimeError(
            "x_template_dir must not be inside immutable seed_dir. Use a separate sibling directory."
        )
    except ValueError:
        pass

    root.mkdir(parents=True,exist_ok=True)

    template_sources={}
    for player_id in cfg.player_ids:
        current_m=_live_game_file(game,str(cfg.game_name),f".m{player_id}")
        dest=root/f"template.x{player_id}"

        if preserve_matching and dest.exists() and _template_matches_game(
            dest,current_m,player_id
        ):
            template_sources[str(player_id)]={
                "path":str(dest),
                "source":"existing persistent template",
            }
            continue

        # Seed-reset mode refreshes from the fully validated staged X. Play-on
        # mode may have no live X because the prior host consumed it, so it
        # bootstraps a missing/stale persistent template from the validated seed.
        if source_x_files is not None:
            source=source_x_files.get(int(player_id))
            candidates=[source] if source is not None else []
            source_label="validated seed X"
        else:
            try:
                candidates=[_live_game_file(game,str(cfg.game_name),f".x{player_id}")]
            except FileNotFoundError:
                candidates=[]
            source_label="validated staged X"
        valid=[c for c in candidates if _template_matches_game(c,current_m,player_id)]
        if len(valid)!=1:
            raise FileNotFoundError(
                f"Validated template source .x{player_id} is missing or no longer matches "
                f"the live M file; refusing template bootstrap at {dest}."
            )
        shutil.copy2(valid[0],dest)
        template_sources[str(player_id)]={
            "path":str(valid[0]),
            "source":source_label,
        }

    manifest={
        "game_name":cfg.game_name,
        "seed_dir":str(seed),
        "templates":{
            str(pid):str(root/f"template.x{pid}") for pid in cfg.player_ids
        },
        "template_sources":template_sources,
        "play_on":bool(cfg.play_on),
        "note":"Immutable bootstrap templates. Live GAME.x# files are regenerated each turn.",
    }
    (root/'templates.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    return root

_NATIVE_GAME_SUFFIX = re.compile(r"\.(?:hst|xy|[hmx](?:[1-9]|1[0-6]))\Z", re.IGNORECASE)


def _native_game_basename(path: Path) -> str | None:
    match=_NATIVE_GAME_SUFFIX.search(path.name)
    if match is None:
        return None
    return path.name[:match.start()]


def _live_game_file(game: Path, basename: str, suffix: str) -> Path:
    expected=f"{basename}{suffix}".casefold()
    hits=[p for p in game.iterdir() if p.is_file() and p.name.casefold()==expected]
    if len(hits)!=1:
        raise FileNotFoundError(
            f"Expected exactly one live {basename}{suffix} in {game}; found {len(hits)}"
        )
    return hits[0]


def _path_is_within(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _paths_overlap(a: Path, b: Path) -> bool:
    return _path_is_within(a,b) or _path_is_within(b,a)


def _stars_execution_dir(cfg: WindowsAutoHostConfig) -> Path:
    return Path(cfg.stars_exe).expanduser().resolve().parent


def _seed_failure(errors: list[str]) -> SeedValidationError:
    detail="\n".join(f"  - {e}" for e in errors)
    return SeedValidationError(f"Seed validation failed:\n{detail}")


def _validate_seed_game(cfg: WindowsAutoHostConfig) -> ValidatedSeedGame:
    """Parse and validate the complete seed without changing any directory."""
    from .adapters.stars_native import read_blocks

    seed=Path(cfg.seed_dir).expanduser().resolve()
    errors=[]
    if not seed.is_dir():
        raise _seed_failure([f"seed_dir does not exist or is not a directory: {seed}"])

    player_ids=[int(x) for x in cfg.player_ids]
    if not player_ids:
        errors.append("player_ids must contain at least one human-controlled AI seat")
    if len(set(player_ids)) != len(player_ids):
        errors.append(f"player_ids contains duplicate seats: {player_ids}")
    invalid_players=[x for x in player_ids if not 1 <= x <= 16]
    if invalid_players:
        errors.append(f"Stars! player seats must be in 1..16; got {invalid_players}")

    native_files=[]
    basenames={}
    for path in seed.iterdir():
        if not path.is_file():
            continue
        basename=_native_game_basename(path)
        if basename is None:
            continue
        native_files.append(path)
        basenames.setdefault(basename.casefold(),set()).add(basename)
    if len(basenames) != 1:
        names=sorted({name for variants in basenames.values() for name in variants})
        errors.append(
            "expected exactly one Stars! game basename in seed_dir; "
            f"found {len(basenames)} ({', '.join(names) if names else 'none'})"
        )
        raise _seed_failure(errors)

    basename=next(iter(next(iter(basenames.values()))))
    game_files=[p for p in native_files if (_native_game_basename(p) or "").casefold()==basename.casefold()]
    by_suffix={}
    for path in game_files:
        by_suffix.setdefault(path.suffix.casefold(),[]).append(path)

    def require_one(suffix: str) -> Path | None:
        hits=by_suffix.get(suffix.casefold(),[])
        if len(hits) != 1:
            errors.append(f"{basename}{suffix}: expected exactly one file; found {len(hits)}")
            return None
        return hits[0]

    hst=require_one(".hst")
    xy=require_one(".xy")
    m_files={pid:path for pid in player_ids if (path:=require_one(f".m{pid}")) is not None}
    h_files={
        pid:path for pid in player_ids
        if (cfg.auto_merge_history or cfg.require_history_sync)
        and (path:=require_one(f".h{pid}")) is not None
    }
    x_files={pid:path for pid in player_ids if (path:=require_one(f".x{pid}")) is not None}
    if errors:
        raise _seed_failure(errors)

    parsed_headers={}
    parsed_blocks={}
    for label,path in [("HST",hst),("XY",xy)]+[
        (f"M{pid}",m_files[pid]) for pid in player_ids
    ]+[
        (f"H{pid}",h_files[pid]) for pid in player_ids if pid in h_files
    ]+[
        (f"X{pid}",x_files[pid]) for pid in player_ids
    ]:
        try:
            header,blocks,_=read_blocks(path)
            parsed_headers[label]=header
            parsed_blocks[label]=blocks
        except Exception as exc:
            errors.append(f"{path.name} is not structurally parseable: {type(exc).__name__}: {exc}")
    if errors:
        raise _seed_failure(errors)

    game_id=int(parsed_headers["HST"].game_id)
    for label,header in parsed_headers.items():
        if int(header.game_id) != game_id:
            errors.append(f"{label} game id {header.game_id} != HST game id {game_id}")

    starting_turn=None
    for pid in player_ids:
        mh=parsed_headers[f"M{pid}"]
        xh=parsed_headers[f"X{pid}"]
        xb=parsed_blocks[f"X{pid}"]
        if int(xh.player_index) != pid-1:
            errors.append(
                f"{x_files[pid].name} declares player {xh.player_index+1}, expected configured seat {pid}"
            )
        if int(mh.player_index) != pid-1:
            errors.append(
                f"{m_files[pid].name} declares player {mh.player_index+1}, expected configured seat {pid}"
            )
        if int(mh.file_type) != 3:
            errors.append(
                f"{m_files[pid].name} file type {mh.file_type} is not M type 3"
            )
        if pid in h_files:
            hh=parsed_headers[f"H{pid}"]
            if int(hh.player_index) != pid-1:
                errors.append(
                    f"{h_files[pid].name} declares player {hh.player_index+1}, expected configured seat {pid}"
                )
            if int(hh.file_type) != 4:
                errors.append(
                    f"{h_files[pid].name} file type {hh.file_type} is not H type 4"
                )
        if int(xh.game_id) != int(mh.game_id):
            errors.append(
                f"{x_files[pid].name} game id {xh.game_id} != {m_files[pid].name} game id {mh.game_id}"
            )
        if int(xh.turn) != int(mh.turn):
            errors.append(
                f"{x_files[pid].name} turn {xh.turn} != {m_files[pid].name} turn {mh.turn}"
            )
        if starting_turn is None:
            starting_turn=int(mh.turn)
        elif int(mh.turn) != starting_turn:
            errors.append(
                f"{m_files[pid].name} turn {mh.turn} != starting turn {starting_turn}"
            )

        checks=[
            (int(xh.file_type)==1,f"{x_files[pid].name} file type {xh.file_type} is not X type 1"),
            (bool(xh.turn_submitted),f"{x_files[pid].name} is not marked turnSubmitted"),
            (len(xb)>0 and xb[0].type_id==8 and xb[0].size==16,
             f"{x_files[pid].name} lacks the canonical leading 16-byte FileHeader"),
            (len(xb)>0 and xb[0].data[:4]==b"J3J3",
             f"{x_files[pid].name} FileHeader lacks the Stars! J3J3 signature"),
            (sum(1 for b in xb if b.type_id==8)==1,
             f"{x_files[pid].name} must contain exactly one FileHeader"),
            (len(xb)>0 and xb[-1].type_id==0 and xb[-1].size==0,
             f"{x_files[pid].name} lacks the required terminal FileFooter"),
        ]
        errors.extend(message for ok,message in checks if not ok)

        hashes=[b for b in xb if b.type_id==9]
        if len(hashes)!=1 or len(hashes[0].data)!=17:
            errors.append(
                f"{x_files[pid].name} must contain exactly one canonical 17-byte FileHash"
            )
        else:
            stored=int.from_bytes(hashes[0].data[:2],"little")
            actual=sum(2+len(b.data) for b in xb if b.type_id in ORDER_BLOCK_TYPES)
            if stored != actual:
                errors.append(
                    f"{x_files[pid].name} FileHash order length {stored} != actual {actual}"
                )

    if errors:
        raise _seed_failure(errors)

    return ValidatedSeedGame(
        seed_dir=seed,
        basename=basename,
        game_id=game_id,
        turn=int(starting_turn or 0),
        files=tuple(sorted(game_files,key=lambda p:p.name.casefold())),
        hst=hst,
        xy=xy,
        m_files=m_files,
        x_files=x_files,
        x_sha256={pid:_sha256(path) for pid,path in x_files.items()},
    )


def _live_failure(errors: list[str]) -> LiveGameValidationError:
    detail="\n".join(f"  - {e}" for e in errors)
    return LiveGameValidationError(f"Play-on live game validation failed:\n{detail}")


def _validate_live_game(
    cfg: WindowsAutoHostConfig,
    expected: ValidatedSeedGame,
) -> ValidatedLiveGame:
    """Validate the current executable-directory game without changing it."""
    from .adapters.stars_native import read_blocks

    game=_stars_execution_dir(cfg)
    errors=[]
    if not game.is_dir():
        raise _live_failure([f"Stars! execution directory does not exist: {game}"])

    game_files=tuple(sorted(
        (
            path for path in game.iterdir()
            if path.is_file()
            and (_native_game_basename(path) or "").casefold()==expected.basename.casefold()
        ),
        key=lambda path:path.name.casefold(),
    ))
    by_suffix={}
    for path in game_files:
        by_suffix.setdefault(path.suffix.casefold(),[]).append(path)

    def require_one(suffix: str) -> Path | None:
        hits=by_suffix.get(suffix.casefold(),[])
        if len(hits)!=1:
            errors.append(
                f"{expected.basename}{suffix}: expected exactly one live file; found {len(hits)}"
            )
            return None
        return hits[0]

    hst=require_one(".hst")
    xy=require_one(".xy")
    player_ids=[int(x) for x in cfg.player_ids]
    m_files={
        pid:path for pid in player_ids
        if (path:=require_one(f".m{pid}")) is not None
    }
    h_files={
        pid:path for pid in player_ids
        if (cfg.auto_merge_history or cfg.require_history_sync)
        and (path:=require_one(f".h{pid}")) is not None
    }
    if errors:
        raise _live_failure(errors)

    parsed={}
    for label,path in [("HST",hst),("XY",xy)]+[
        (f"M{pid}",m_files[pid]) for pid in player_ids
    ]+[
        (f"H{pid}",h_files[pid]) for pid in player_ids if pid in h_files
    ]:
        try:
            header,_,_=read_blocks(path)
            parsed[label]=header
        except Exception as exc:
            errors.append(
                f"{path.name} is not structurally parseable: {type(exc).__name__}: {exc}"
            )
    if errors:
        raise _live_failure(errors)

    game_id=int(parsed["HST"].game_id)
    if game_id!=int(expected.game_id):
        errors.append(
            f"live HST game id {game_id} != validated seed game id {expected.game_id}"
        )
    if int(parsed["HST"].file_type)!=2:
        errors.append(
            f"{hst.name} file type {parsed['HST'].file_type} is not HST type 2"
        )
    if int(parsed["XY"].file_type)!=0:
        errors.append(
            f"{xy.name} file type {parsed['XY'].file_type} is not XY type 0"
        )
    for label,header in parsed.items():
        if int(header.game_id)!=game_id:
            errors.append(f"{label} game id {header.game_id} != live HST game id {game_id}")

    live_turn=int(parsed["HST"].turn)
    for pid in player_ids:
        header=parsed[f"M{pid}"]
        if int(header.file_type)!=3:
            errors.append(
                f"{m_files[pid].name} file type {header.file_type} is not M type 3"
            )
        if int(header.player_index)!=pid-1:
            errors.append(
                f"{m_files[pid].name} declares player {header.player_index+1}, "
                f"expected configured seat {pid}"
            )
        if int(header.turn)!=live_turn:
            errors.append(
                f"{m_files[pid].name} turn {header.turn} != live HST turn {live_turn}"
            )
        if pid in h_files:
            history_header=parsed[f"H{pid}"]
            if int(history_header.file_type)!=4:
                errors.append(
                    f"{h_files[pid].name} file type {history_header.file_type} is not H type 4"
                )
            if int(history_header.player_index)!=pid-1:
                errors.append(
                    f"{h_files[pid].name} declares player {history_header.player_index+1}, "
                    f"expected configured seat {pid}"
                )
    if errors:
        raise _live_failure(errors)

    return ValidatedLiveGame(
        game_dir=game,
        basename=expected.basename,
        game_id=game_id,
        turn=live_turn,
        files=game_files,
        hst=hst,
        xy=xy,
        m_files=m_files,
    )


def _remove_stale_game_files(game: Path, basename: str) -> list[Path]:
    removed=[]
    for path in game.iterdir():
        if not path.is_file():
            continue
        found=_native_game_basename(path)
        if found is None or found.casefold()!=basename.casefold():
            continue
        path.unlink()
        removed.append(path)
    return removed


def _stage_seed_game(validated: ValidatedSeedGame, game: Path) -> list[Path]:
    game=game.resolve()
    if _paths_overlap(validated.seed_dir,game):
        raise RuntimeError(
            "seed_dir and the Stars! execution directory must be separate, non-nested locations"
        )
    game.mkdir(parents=True,exist_ok=True)
    _remove_stale_game_files(game,validated.basename)
    staged=[]
    for source in validated.files:
        destination=game/source.name
        shutil.copy2(source,destination)
        staged.append(destination)
    return staged


def _write_bootstrap_snapshot(
    validated: ValidatedSeedGame,
    game: Path,
    output_root: Path,
    *,
    starting_turn: int | None = None,
    mode: str = "seed_reset",
) -> Path:
    dest=output_root/"bootstrap"
    dest.mkdir(parents=True,exist_ok=True)
    snapshot_files=[]
    live_files=sorted(
        (
            path for path in game.iterdir()
            if path.is_file()
            and (_native_game_basename(path) or "").casefold()==validated.basename.casefold()
        ),
        key=lambda path:path.name.casefold(),
    )
    for live in live_files:
        shutil.copy2(live,dest/live.name)
        snapshot_files.append(live.name)
    manifest={
        "game_basename":validated.basename,
        "game_id":validated.game_id,
        "starting_turn":int(validated.turn if starting_turn is None else starting_turn),
        "mode":str(mode),
        "play_on":bool(mode=="play_on"),
        "configured_players":sorted(validated.x_files),
        "seed_directory":str(validated.seed_dir),
        "execution_directory":str(game),
        "snapshot_files":snapshot_files,
        # Compatibility key retained for existing snapshot consumers. In
        # play-on mode these files were copied for evidence, not staged live.
        "staged_files":snapshot_files,
        "initial_x_sha256":{
            str(pid):digest for pid,digest in sorted(validated.x_sha256.items())
        },
    }
    (dest/"manifest.json").write_text(json.dumps(manifest,indent=2),encoding="utf-8")
    return dest


def _safe_cleanup_output(root: Path, seed: Path, *protected: Path) -> None:
    """
    Clear diagnostics/playtest output from a previous run without ever deleting
    the direct Stars! game directory.
    """
    root=root.resolve()
    seed=seed.resolve()
    for path,label in [(seed,"seed_dir")]+[(Path(p).resolve(),"protected directory") for p in protected]:
        if _paths_overlap(root,path):
            raise RuntimeError(
                f"Refusing output cleanup because output_dir overlaps {label}: {path}"
            )
    if root.exists():
        shutil.rmtree(root)


def _validate_workspace_layout(
    *,
    seed: Path,
    game: Path,
    output: Path,
    ai_state: Path,
    x_templates: Path,
) -> None:
    named={
        "seed_dir":seed.resolve(),
        "Stars! execution directory":game.resolve(),
        "output_dir":output.resolve(),
        "ai_state_dir":ai_state.resolve(),
        "x_template_dir":x_templates.resolve(),
    }
    items=list(named.items())
    conflicts=[]
    for i,(left_name,left) in enumerate(items):
        for right_name,right in items[i+1:]:
            if _paths_overlap(left,right):
                conflicts.append(f"{left_name} ({left}) overlaps {right_name} ({right})")
    if conflicts:
        raise RuntimeError(
            "Autoplay locations must be logically separate:\n  - " + "\n  - ".join(conflicts)
        )

def _find_one(game: Path, pattern: str) -> Path:
    hits = list(game.glob(pattern))
    if len(hits) != 1:
        raise FileNotFoundError(f"Expected exactly one {pattern} in {game}; found {len(hits)}")
    return hits[0]

def _read_year(m_path: Path, xy_path: Path) -> int | None:
    try:
        return int(PlayerState.from_files(m_path, xy_path).header.get("year"))
    except Exception:
        return None

def _snapshot(
    game: Path,
    dest: Path,
    *,
    basename: str,
    metadata: dict[str, Any] | None = None,
) -> Path:
    """Freeze one complete native game state and write its manifest last."""
    from .adapters.stars_native import read_blocks

    if dest.exists():
        raise FileExistsError(f"Refusing to overwrite immutable native snapshot: {dest}")
    dest.mkdir(parents=True)
    sources=sorted(
        (
            path for path in game.iterdir()
            if path.is_file()
            and (_native_game_basename(path) or "").casefold()==basename.casefold()
        ),
        key=lambda path:path.name.casefold(),
    )
    if not sources:
        raise FileNotFoundError(f"No native {basename} game files found to snapshot in {game}")

    inventory={}
    for source in sources:
        frozen=dest/source.name
        shutil.copy2(source,frozen)
        row={
            "size":frozen.stat().st_size,
            "mtime_ns":frozen.stat().st_mtime_ns,
            "sha256":_sha256(frozen),
        }
        try:
            header,_,_=read_blocks(frozen)
            row["header"]={
                "game_id":int(header.game_id),
                "turn":int(header.turn),
                "year":int(header.year),
                "player_index":int(header.player_index),
                "file_type":int(header.file_type),
                "turn_submitted":bool(header.turn_submitted),
            }
        except Exception as exc:
            row["parse_error"]=f"{type(exc).__name__}: {exc}"
        inventory[source.name]=row

    manifest={
        "schema_version":1,
        "captured_at_ns":time.time_ns(),
        "source_directory":str(game),
        "game_basename":basename,
        "snapshot_files":list(inventory),
        "files":inventory,
        **dict(metadata or {}),
    }
    (dest/"manifest.json").write_text(
        json.dumps(manifest,indent=2),encoding="utf-8"
    )
    return dest


def _history_sync_report(
    game: Path,
    basename: str,
    player_ids: list[int],
) -> dict[str, Any]:
    """Prove that each H file covers every current M planet observation."""
    from .adapters.stars_native import read_blocks

    rows=[]
    for player_id in [int(value) for value in player_ids]:
        row={"player_id":player_id,"ready":False,"errors":[]}
        try:
            m_path=_live_game_file(game,basename,f".m{player_id}")
            row["m_path"]=str(m_path)
            row["m_mtime_ns"]=m_path.stat().st_mtime_ns
            mh,_,_=read_blocks(m_path)
            row["m_turn"]=int(mh.turn)
            row["m_year"]=int(mh.year)
            row["m_sha256"]=_sha256(m_path)
        except Exception as exc:
            row["errors"].append(
                f"current M file is unavailable or invalid: {type(exc).__name__}: {exc}"
            )
            rows.append(row)
            continue

        try:
            h_path=_live_game_file(game,basename,f".h{player_id}")
            row["h_path"]=str(h_path)
            row["h_mtime_ns"]=h_path.stat().st_mtime_ns
            hh,_,_=read_blocks(h_path)
            row["h_header_turn"]=int(hh.turn)
            row["h_sha256"]=_sha256(h_path)
            if int(hh.file_type)!=4:
                row["errors"].append(
                    f"{h_path.name} file type {hh.file_type} is not H type 4"
                )
            if int(hh.game_id)!=int(mh.game_id):
                row["errors"].append(
                    f"{h_path.name} game id {hh.game_id} != {m_path.name} game id {mh.game_id}"
                )
            if int(hh.player_index)!=player_id-1:
                row["errors"].append(
                    f"{h_path.name} declares player {hh.player_index+1}, expected player {player_id}"
                )
            coverage=inspect_history_coverage(h_path,m_path)
            row["coverage"]=coverage
            if coverage["missing_planet_ids"]:
                row["errors"].append(
                    f"{h_path.name} is missing current-M planet IDs "
                    f"{coverage['missing_planet_ids']}"
                )
            if coverage["stale_planets"]:
                stale=", ".join(
                    f"P{item['planet_id']} H{item['history_turn']}<M{item['m_turn']}"
                    for item in coverage["stale_planets"]
                )
                row["errors"].append(
                    f"{h_path.name} contains stale planet observations ({stale})"
                )
        except Exception as exc:
            row["errors"].append(
                f"history file is unavailable or invalid: {type(exc).__name__}: {exc}"
            )
        row["ready"]=not row["errors"]
        rows.append(row)

    return {
        "game_directory":str(game),
        "game_basename":basename,
        "ready":bool(rows) and all(row["ready"] for row in rows),
        "players":rows,
    }


def _auto_merge_histories(
    cfg: WindowsAutoHostConfig,
    game: Path,
    basename: str,
    logs_root: Path,
    *,
    phase_tag: str,
    execution_turn: int,
) -> dict[str, Any]:
    """Merge every configured player's current M into H and write an audit."""
    audit_root=logs_root/"history"
    audit_root.mkdir(parents=True,exist_ok=True)
    rows=[]
    for player_id in [int(value) for value in cfg.player_ids]:
        h_path=_live_game_file(game,basename,f".h{player_id}")
        m_path=_live_game_file(game,basename,f".m{player_id}")
        try:
            row=merge_history_file(
                h_path,m_path,
                backup_path=(
                    audit_root/f"{phase_tag}-player-{player_id:02d}-premerge.h{player_id}"
                ),
                merged_copy_path=(
                    audit_root/f"{phase_tag}-player-{player_id:02d}-merged.h{player_id}"
                ),
            )
            row["ready"]=True
        except Exception as exc:
            row={
                "player_id":player_id,
                "h_path":str(h_path),
                "m_path":str(m_path),
                "ready":False,
                "status":"FAILED",
                "error":f"{type(exc).__name__}: {exc}",
            }
        rows.append(row)

    ready=bool(rows) and all(bool(row.get("ready")) for row in rows)
    report={
        "schema_version":1,
        "status":"MERGED_AND_VALIDATED" if ready else "AUTOMATIC_MERGE_FAILED",
        "ready":ready,
        "execution_turn":int(execution_turn),
        "phase_tag":phase_tag,
        "game_directory":str(game),
        "game_basename":basename,
        "players":rows,
    }
    json_path=logs_root/f"{phase_tag}-HISTORY_MERGE.json"
    text_path=logs_root/f"{phase_tag}-HISTORY_MERGE.txt"
    json_path.write_text(json.dumps(report,indent=2),encoding="utf-8")
    lines=[f"{report['status']} - native Python M-to-H history merge"]
    for row in rows:
        if row.get("ready"):
            lines.append(
                f"READY - Player {row['player_id']}: {row['status']}; "
                f"{row['h_planets_before']} -> {row['h_planets_after']} history planets; "
                f"new={row['new_planet_ids']}; current M unchanged={row['m_unchanged']}."
            )
        else:
            lines.append(
                f"WARNING - Player {row['player_id']} automatic history merge failed: "
                f"{row.get('error','unknown error')}."
            )
    if not ready:
        lines.append(
            "No new turn will be submitted. The pre-merge H and current M remain "
            "available in the native/audit logs for diagnosis."
        )
    text_path.write_text("\n".join(lines)+"\n",encoding="utf-8")
    if not ready:
        print(f"[AUTOMATIC HISTORY MERGE FAILED] See {text_path}",flush=True)
        raise HistorySyncError(
            f"Automatic native M-to-H merge failed. See {text_path}"
        )
    print(
        f"[HISTORY MERGED] {len(rows)} player H file(s) updated and validated "
        f"for {phase_tag}.",
        flush=True,
    )
    return report


def _history_sync_barrier(
    cfg: WindowsAutoHostConfig,
    game: Path,
    basename: str,
    logs_root: Path,
    turn: int,
) -> dict[str, Any]:
    """Write semantic history status and fail before creating any X file."""
    turn_tag=f"turn-{turn:03d}"
    report=_history_sync_report(game,basename,cfg.player_ids)
    report["execution_turn"]=turn
    report["required"]=bool(cfg.require_history_sync)
    report["status"]=(
        "READY" if report["ready"] else
        "AUTOMATIC_HISTORY_MERGE_REQUIRED" if cfg.require_history_sync else
        "WARNING_BYPASSED"
    )
    json_path=logs_root/f"{turn_tag}-HISTORY_SYNC.json"
    text_path=logs_root/f"{turn_tag}-HISTORY_SYNC.txt"
    json_path.write_text(json.dumps(report,indent=2),encoding="utf-8")

    lines=[f"{report['status']} - pre-submit player history check"]
    for row in report["players"]:
        if row["ready"]:
            lines.append(
                f"READY - Player {row['player_id']} history semantically covers "
                f"current M turn {row.get('m_turn')}."
            )
            continue
        for error in row["errors"]:
            lines.append(f"WARNING - Player {row['player_id']}: {error}.")
    if not report["ready"]:
        lines.append(
            "The automatic native history merge did not establish current-M coverage. "
            "No X file has been generated for this execution turn."
        )
    text_path.write_text("\n".join(lines)+"\n",encoding="utf-8")

    if not report["ready"]:
        prefix=(
            "[AUTOMATIC HISTORY MERGE REQUIRED]" if cfg.require_history_sync
            else "[WARNING - HISTORY SYNC BYPASSED]"
        )
        print(f"{prefix} See {text_path}",flush=True)
    if not report["ready"] and cfg.require_history_sync:
        raise HistorySyncError(
            f"AUTOMATIC HISTORY MERGE REQUIRED before submitting {basename} turn files. "
            f"See {text_path} and the corresponding HISTORY_MERGE audit."
        )
    return report


def _stars_processes_running(exe_path: str) -> list[int]:
    """
    Return PIDs for currently-running processes whose image name matches the
    configured Stars executable. Uses tasklist because it works on stock Windows.
    """
    if os.name != "nt":
        return []
    image = Path(exe_path).name
    try:
        cp = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {image}", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=10
        )
        pids=[]
        for line in (cp.stdout or "").splitlines():
            if not line or line.startswith("INFO:"):
                continue
            # CSV format: "image","pid",...
            parts=[x.strip().strip('"') for x in line.split('","')]
            if len(parts) >= 2:
                try:
                    pids.append(int(parts[1].strip('"')))
                except ValueError:
                    pass
        return pids
    except Exception:
        return []

def _file_signature(paths: list[Path]) -> dict[str, tuple[int,int]]:
    sig={}
    for p in paths:
        try:
            st=p.stat()
            sig[str(p)]=(st.st_size, st.st_mtime_ns)
        except FileNotFoundError:
            sig[str(p)]=(-1,-1)
    return sig

def _wait_for_files_to_change_and_settle(
    paths: list[Path],
    before: dict[str, tuple[int,int]],
    *,
    timeout_seconds: float,
    poll_seconds: float,
    settle_seconds: float,
) -> bool:
    """
    Wait until at least one output file changes, then require the whole tracked
    set to remain unchanged for settle_seconds. This prevents the next host turn
    from starting while Stars! is still writing.
    """
    deadline=time.time()+timeout_seconds
    changed=False
    stable_since=None
    last=None
    while time.time() < deadline:
        cur=_file_signature(paths)
        if cur != before:
            changed=True
        if changed:
            if cur == last:
                if stable_since is None:
                    stable_since=time.time()
                elif time.time()-stable_since >= settle_seconds:
                    return True
            else:
                stable_since=None
        last=cur
        time.sleep(max(0.05,poll_seconds))
    return False

def _wait_until_no_stars_process(
    exe_path: str,
    *,
    timeout_seconds: float,
    poll_seconds: float,
) -> bool:
    deadline=time.time()+timeout_seconds
    while time.time() < deadline:
        if not _stars_processes_running(exe_path):
            return True
        time.sleep(max(0.05,poll_seconds))
    return False

def _run_host_serialized(cfg: WindowsAutoHostConfig, hst: Path, tracked_outputs: list[Path], turn_dir: Path):
    """
    Launch exactly one Stars! host generation and do not return until:
      1) the launch process has returned,
      2) Stars! itself is no longer running (when detectable), and
      3) generated files have changed and settled.
    """
    if cfg.prevent_parallel_stars:
        existing=_stars_processes_running(cfg.stars_exe)
        if existing:
            raise RuntimeError(
                f"Refusing to launch another Stars! instance; already running PID(s): {existing}. "
                "Close the existing Stars! window/process and rerun."
            )

    before=_file_signature(tracked_outputs)
    cmd=_host_command(cfg,hst)
    # Caller may reuse one log directory for every turn.
    audit_tag=getattr(cfg,"_active_turn_tag","current")
    (turn_dir/f"{audit_tag}-host-command.json").write_text(json.dumps(cmd,indent=2),encoding="utf-8")

    # CREATE_NEW_PROCESS_GROUP is harmless for normal Win32 executables and makes
    # process ownership clearer. subprocess.run still waits for the launched process.
    creationflags=0
    if os.name=="nt":
        creationflags=getattr(subprocess,"CREATE_NEW_PROCESS_GROUP",0)

    started=time.time()
    try:
        cp=subprocess.run(
            cmd,
            cwd=str(hst.parent),
            capture_output=True,
            text=True,
            timeout=cfg.host_timeout_seconds,
            creationflags=creationflags,
        )
    except subprocess.TimeoutExpired as exc:
        stdout=exc.stdout or ""
        stderr=exc.stderr or ""
        if isinstance(stdout,bytes):
            stdout=stdout.decode(errors="replace")
        if isinstance(stderr,bytes):
            stderr=stderr.decode(errors="replace")
        (turn_dir/f"{audit_tag}-host.stdout.txt").write_text(stdout,encoding="utf-8")
        (turn_dir/f"{audit_tag}-host.stderr.txt").write_text(stderr,encoding="utf-8")
        after=_file_signature(tracked_outputs)
        pids=_stars_processes_running(cfg.stars_exe)
        files_changed=after!=before
        if pids and not files_changed:
            condition="likely modal/error condition: Stars! is still present and tracked game files did not change"
        elif pids:
            condition="slow hosting or a child process still running: Stars! remains present after file activity"
        elif files_changed:
            condition="slow hosting/launcher return: tracked files changed but the launch exceeded its deadline"
        else:
            condition="host launch exceeded its deadline without observable output or a detectable Stars! process"
        diagnostic={
            "timeout_seconds":cfg.host_timeout_seconds,
            "elapsed_seconds":round(time.time()-started,3),
            "command":cmd,
            "working_directory":str(hst.parent),
            "stars_process_ids":pids,
            "tracked_files_changed":files_changed,
            "tracked_before":before,
            "tracked_after":after,
            "assessment":condition,
        }
        diagnostic_path=turn_dir/f"{audit_tag}-HOST-TIMEOUT.json"
        diagnostic_path.write_text(json.dumps(diagnostic,indent=2),encoding="utf-8")
        raise RuntimeError(
            f"Stars! host timed out after {cfg.host_timeout_seconds}s; {condition}. "
            f"See {diagnostic_path}"
        ) from exc
    (turn_dir/f"{audit_tag}-host.stdout.txt").write_text(cp.stdout or "",encoding="utf-8")
    (turn_dir/f"{audit_tag}-host.stderr.txt").write_text(cp.stderr or "",encoding="utf-8")

    if cfg.prevent_parallel_stars:
        # Some legacy launchers return before the actual GUI/host child exits.
        process_exited=_wait_until_no_stars_process(
            cfg.stars_exe,
            timeout_seconds=cfg.host_timeout_seconds,
            poll_seconds=cfg.host_poll_seconds,
        )
        if not process_exited:
            pids=_stars_processes_running(cfg.stars_exe)
            diagnostic_path=turn_dir/f"{audit_tag}-HOST-TIMEOUT.json"
            diagnostic_path.write_text(json.dumps({
                "timeout_seconds":cfg.host_timeout_seconds,
                "phase":"waiting_for_Stars_process_exit",
                "command":cmd,
                "working_directory":str(hst.parent),
                "stars_process_ids":pids,
                "tracked_before":before,
                "tracked_current":_file_signature(tracked_outputs),
                "assessment":"slow hosting or likely modal/error condition; Stars! remained running",
            },indent=2),encoding="utf-8")
            raise RuntimeError(
                f"Stars! remained running for {cfg.host_timeout_seconds}s after launch return; "
                f"possible modal/error condition. See {diagnostic_path}"
            )

    settled=_wait_for_files_to_change_and_settle(
        tracked_outputs,before,
        timeout_seconds=cfg.host_timeout_seconds,
        poll_seconds=cfg.host_poll_seconds,
        settle_seconds=cfg.host_settle_seconds,
    )
    if not settled:
        after=_file_signature(tracked_outputs)
        diagnostic_path=turn_dir/f"{audit_tag}-HOST-TIMEOUT.json"
        diagnostic_path.write_text(json.dumps({
            "timeout_seconds":cfg.host_timeout_seconds,
            "phase":"waiting_for_generated_files_to_change_and_settle",
            "command":cmd,
            "working_directory":str(hst.parent),
            "stars_process_ids":_stars_processes_running(cfg.stars_exe),
            "tracked_files_changed":after!=before,
            "tracked_before":before,
            "tracked_current":after,
            "assessment":(
                "tracked files changed but did not settle; hosting may still be slow"
                if after!=before else
                "tracked files never changed; likely modal/error condition or rejected order files"
            ),
        },indent=2),encoding="utf-8")
    return cp, settled


def _sha256(path: Path) -> str:
    h=hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda:f.read(65536),b""):
            h.update(chunk)
    return h.hexdigest()


def _pre_host_audit(cfg: WindowsAutoHostConfig, game: Path, hst: Path, xy: Path, log_dir: Path, turn: int) -> dict:
    from .adapters.stars_native import read_blocks, header_dict
    order_types={1,2,3,4,5,10,19,23,24,25,27,29,34,35,36,37,38,40,42,44,46}
    report={"turn":turn,"game_dir":str(game),"ready":True,"players":[]}

    for pid in cfg.player_ids:
        exact=game/f"{hst.stem}.x{pid}"
        row={"player_id":pid,"ready":False,"expected_path":str(exact)}
        if not exact.exists():
            row["error"]=f"exact live file missing: {exact.name}"
            report["ready"]=False
            report["players"].append(row)
            continue

        try:
            current_m=_live_game_file(game,hst.stem,f".m{pid}")
        except FileNotFoundError as exc:
            row["error"]=str(exc)
            report["ready"]=False
            report["players"].append(row)
            continue

        try:
            mh,_,_=read_blocks(current_m)
            xh,blocks,_=read_blocks(exact)
            fh=next((b for b in blocks if b.type_id==9),None)
            stored=int.from_bytes(fh.data[:2],"little") if fh and len(fh.data)>=2 else None
            actual=sum(2+len(b.data) for b in blocks if b.type_id in order_types)

            row.update({
                "path":str(exact),
                "size":exact.stat().st_size,
                "sha256":_sha256(exact),
                "header":header_dict(xh),
                "m_game_id":mh.game_id,
                "m_turn":mh.turn,
                "filehash_order_len":stored,
                "actual_order_len":actual,
                "blocks":[
                    {"type_id":b.type_id,"name":b.name,"size":b.size,"data_hex":b.data.hex(" ")}
                    for b in blocks
                ],
                "ready":True,
            })

            checks=[
                (xh.game_id==mh.game_id, f"X game {xh.game_id} != M game {mh.game_id}"),
                (xh.turn==mh.turn, f"X turn {xh.turn} != M turn {mh.turn}"),
                (xh.player_index==pid-1, f"X player {xh.player_index} != {pid-1}"),
                (xh.file_type==1, f"fileType {xh.file_type} != 1"),
                (xh.turn_submitted, "turnSubmitted is false"),
                (fh is not None and len(fh.data)==17, "missing/noncanonical FileHash"),
                (stored==actual, f"FileHash orderLen {stored} != actual {actual}"),
            ]
            failures=[msg for ok,msg in checks if not ok]
            if failures:
                row["ready"]=False
                row["error"]="; ".join(failures)
                report["ready"]=False
        except Exception as exc:
            row["error"]=f"{type(exc).__name__}: {exc}"
            report["ready"]=False

        report["players"].append(row)

    (log_dir/f"turn-{turn:03d}-PRE_HOST_AUDIT.json").write_text(
        json.dumps(report,indent=2),encoding="utf-8"
    )
    lines=[f"PRE-HOST REGISTRATION AUDIT — turn {turn}",f"Game directory: {game}"]
    for r in report["players"]:
        status="READY" if r.get("ready") else "FAIL"
        lines.append(f"P{r['player_id']}: {status} {r.get('path',r.get('expected_path',''))} {r.get('error','')}".rstrip())
        if r.get("header"):
            h=r["header"]
            lines.append(
                f"  header game={h.get('game_id')} turn={h.get('turn')} player={h.get('player_number')} "
                f"fileType={h.get('file_type')} submitted={h.get('turn_submitted')}"
            )
            lines.append(f"  FileHash orderLen={r.get('filehash_order_len')} actual={r.get('actual_order_len')}")
            for b in r.get("blocks",[]):
                if b["type_id"] in {1,3,4,5,29,34,38,46}:
                    lines.append(f"  block {b['type_id']} {b['name']} len={b['size']} data={b['data_hex']}")
    lines.append(f"READY TO HOST: {'YES' if report['ready'] else 'NO'}")
    (log_dir/f"turn-{turn:03d}-PRE_HOST_AUDIT.txt").write_text("\n".join(lines)+"\n",encoding="utf-8")
    return report


def _post_host_audit(cfg: WindowsAutoHostConfig, game: Path, hst: Path, before: dict, log_dir: Path, turn: int, year_before, year_after) -> dict:
    result={"all_consumed":True,"players":[]}
    lines=[
        f"POST-HOST REGISTRATION AUDIT — turn {turn}",
        f"Game directory: {game}",
        f"Year: {year_before} -> {year_after}",
    ]
    for r in before.get("players",[]):
        pid=int(r["player_id"])
        exact=game/f"{hst.stem}.x{pid}"
        consumed=not exact.exists()
        result["players"].append({"player_id":pid,"consumed":consumed,"path":str(exact)})
        if not consumed:
            result["all_consumed"]=False
        lines.append(
            f"P{pid} {exact.name}: {'CONSUMED/REMOVED' if consumed else 'STILL PRESENT — HOST DID NOT CONSUME ORDER FILE'}"
        )
    lines.append(f"ALL GENERATED X FILES CONSUMED: {'YES' if result['all_consumed'] else 'NO'}")
    (log_dir/f"turn-{turn:03d}-POST_HOST_AUDIT.txt").write_text("\n".join(lines)+"\n",encoding="utf-8")
    (log_dir/f"turn-{turn:03d}-POST_HOST_AUDIT.json").write_text(json.dumps(result,indent=2),encoding="utf-8")
    return result

def _print_observer_summary(obs, personas: dict[str,str]|None=None) -> None:
    personas=personas or {}
    ps=sorted(obs.players,key=lambda p:p.score,reverse=True)
    if not ps:
        print(f"[Observer T{obs.turn}] no player data decoded", flush=True)
        return
    leader=ps[0]
    standing=" | ".join(
        f"P{p.player_id} {personas.get(str(p.player_id),p.name)}: "
        f"{p.planets}pl {p.population:,}pop {p.ships}sh tech{p.tech_sum}"
        for p in ps
    )
    captures=[e for e in obs.events if e.get("type")=="capture"]
    losses=[e for e in obs.events if e.get("type")=="major_fleet_loss"]
    event_bits=[]
    if captures: event_bits.append(f"{len(captures)} capture(s)")
    if losses: event_bits.append(f"{len(losses)} major fleet-loss event(s)")
    events=", ".join(event_bits) if event_bits else "no clear combat events"
    print(
        f"[Observer T{obs.turn} Y{obs.year}] Leader P{leader.player_id} | "
        f"{standing} | {events}",
        flush=True
    )

def _host_command(cfg: WindowsAutoHostConfig, hst: Path) -> list[str]:
    # Stars! manual: stars!.exe -g gamename.hst forces generation and exits.
    cmd = [cfg.stars_exe, "-g"]
    if cfg.host_password:
        cmd += ["-p", cfg.host_password]
    cmd += [str(hst)]
    return cmd

def run_50_turn_game(cfg: WindowsAutoHostConfig, order_bridge: NativeOrderBridge) -> list[TurnExecution]:
    seed = Path(cfg.seed_dir).expanduser().resolve()
    root = Path(cfg.output_dir).expanduser().resolve()
    try:
        validated=_validate_seed_game(cfg)
    except SeedValidationError as exc:
        print(f"[SEED VALIDATION FAILED] {exc}",flush=True)
        raise

    stars_exe=Path(cfg.stars_exe).expanduser().resolve()
    if not stars_exe.is_file():
        raise FileNotFoundError(f"Configured Stars! executable does not exist: {stars_exe}")
    cfg.stars_exe=str(stars_exe)
    cfg.game_name=validated.basename
    game=_stars_execution_dir(cfg)
    ai_state_root=_persistent_ai_state_root(cfg,seed)
    templates_root=_persistent_x_template_root(cfg,seed)
    _validate_workspace_layout(
        seed=seed,
        game=game,
        output=root,
        ai_state=ai_state_root,
        x_templates=templates_root,
    )

    live_validation=(
        _validate_live_game(cfg,validated)
        if cfg.play_on else None
    )

    if cfg.cleanup_output_on_start:
        _safe_cleanup_output(root,seed,game,ai_state_root,templates_root)
    root.mkdir(parents=True, exist_ok=True)

    if live_validation is None:
        # Default reset behavior: the immutable seed has passed every native
        # safety check, so replace only this game's live files.
        _stage_seed_game(validated,game)
        starting_turn=validated.turn
        bootstrap_mode="seed_reset"
    else:
        # PLAY ON: validation above was read-only. Do not stage, remove, or
        # replace any live game file before the bootstrap evidence snapshot.
        starting_turn=live_validation.turn
        bootstrap_mode="play_on"
        print(
            f"[PLAY ON] Continuing {validated.basename} from year "
            f"{2400+starting_turn} for {cfg.turns} additional turn(s).",
            flush=True,
        )
    _write_bootstrap_snapshot(
        validated,game,root,
        starting_turn=starting_turn,
        mode=bootstrap_mode,
    )

    logs_root = root / "logs"
    logs_root.mkdir(exist_ok=True)
    checkpoints_root = logs_root / "checkpoints"
    checkpoints_root.mkdir(exist_ok=True)
    observer_root = logs_root / "observer"
    observer_root.mkdir(exist_ok=True)
    hst = _live_game_file(game,validated.basename,".hst")
    xy = _live_game_file(game,validated.basename,".xy")

    # Recover a play-on game whose latest M files have not yet reached H, and
    # normalize a reset seed through the same tested path. The bootstrap
    # snapshot above preserves the exact pre-merge native state.
    if cfg.auto_merge_history and cfg.turns > 0:
        _auto_merge_histories(
            cfg,game,validated.basename,logs_root,
            phase_tag="bootstrap",
            execution_turn=0,
        )

    # Reset mode refreshes from staged Turn-0 X files. Play-on mode preserves a
    # matching persistent template or safely rebuilds it from the validated
    # seed because the current host normally consumed every live X file.
    templates_root = _bootstrap_persistent_x_templates(
        cfg,seed=seed,game=game,
        source_x_files=(validated.x_files if cfg.play_on else None),
        preserve_matching=bool(cfg.play_on),
    )

    # v7.0: strategic memory is intentionally outside output_dir so a diagnostics
    # cleanup or process restart does not erase the empire's learned galaxy.
    ai_state_root.mkdir(parents=True,exist_ok=True)
    if isinstance(order_bridge, IntegratedNativeOrderBridge):
        order_bridge.memory_root=ai_state_root

    observer_history = []
    last_checkpoint_observer = None
    previous_observer = None
    # Capture baseline before any AI turn.
    try:
        baseline = read_observer_turn(hst, xy, 0)
        baseline.events = []
        save_observer_turn(observer_root / "turn-000.json", baseline)
        observer_history.append(baseline)
        previous_observer = baseline
        last_checkpoint_observer = baseline
    except Exception as exc:
        (observer_root / "observer-baseline-error.txt").write_text(
            f"{type(exc).__name__}: {exc}", encoding="utf-8"
        )

    executions: list[TurnExecution] = []

    for turn in range(1, cfg.turns + 1):
        turn_tag = f"turn-{turn:03d}"
        turn_dir = logs_root
        year_before = None
        order_files = []

        try:
            cfg._active_turn_tag = turn_tag
            if isinstance(order_bridge, IntegratedNativeOrderBridge):
                order_bridge.turn_tag = turn_tag

            first_pre_m=_live_game_file(
                game,validated.basename,f".m{cfg.player_ids[0]}"
            )
            year_before=_read_year(first_pre_m,xy)
            if cfg.turn_archive_enabled:
                archive_turn_phase(
                    logs_root/"turn-archive", turn_tag=turn_tag, phase="00-pre-write",
                    game_dir=game, basename=validated.basename,
                    logs_root=(logs_root if cfg.turn_archive_include_logs else None),
                    templates_root=templates_root, ai_state_root=ai_state_root,
                    config=cfg,
                    metadata={"execution_turn":turn,"native_year_before":year_before},
                )
            # This is deliberately before deleting or generating any X file.
            # A current M that has not been consumed by the Stars! client must
            # not be superseded by another submitted turn.
            _history_sync_barrier(
                cfg,game,validated.basename,logs_root,turn
            )
            if isinstance(order_bridge, IntegratedNativeOrderBridge):
                order_bridge.discard_pending_memory()

            # Each player completes synchronously before the next begins.
            for player_id in cfg.player_ids:
                m_path=_live_game_file(game,validated.basename,f".m{player_id}")

                # Never depend on a live/pre-existing GAME.x# here. Always
                # synthesize the new live order file from the immutable template.
                template_x = templates_root / f"template.x{player_id}"
                if not template_x.exists():
                    raise RuntimeError(
                        f"Persistent native template missing for player {player_id}: {template_x}"
                    )

                out_x = game / f"{hst.stem}.x{player_id}"
                if out_x.exists():
                    # Keep old X only in logs, never in a Stars! game subfolder.
                    shutil.copy2(out_x, logs_root / f"{turn_tag}-prewrite-{out_x.name}")
                    out_x.unlink()

                generated = order_bridge.create_x_file(
                    player_id=player_id,
                    m_path=m_path,
                    xy_path=xy,
                    existing_x_path=template_x,
                    output_x_path=out_x,
                    turn_dir=turn_dir,
                )
                order_files.append(str(generated))
                shutil.copy2(
                    generated,
                    logs_root/f"{turn_tag}-player-{player_id:02d}-GENERATED.x{player_id}"
                )

            missing = [
                pid for pid in cfg.player_ids
                if not any(
                    p.is_file() and p.name.casefold()==f"{validated.basename}.x{pid}".casefold()
                    for p in game.iterdir()
                )
            ]
            if missing and cfg.stop_on_missing_x:
                raise RuntimeError(f"Missing native order files for players: {missing}")

            if cfg.turn_archive_enabled:
                archive_turn_phase(
                    logs_root/"turn-archive", turn_tag=turn_tag, phase="10-pre-host",
                    game_dir=game, basename=validated.basename,
                    logs_root=(logs_root if cfg.turn_archive_include_logs else None),
                    templates_root=templates_root, ai_state_root=ai_state_root,
                    config=cfg,
                    metadata={"execution_turn":turn,"native_year_before":year_before,"order_files":list(order_files)},
                )

            # Hard barrier. Stars! is not launched unless every X file passes.
            pre_audit = (
                _pre_host_audit(cfg, game, hst, xy, logs_root, turn)
                if cfg.pre_host_audit else {"ready": True, "players": []}
            )
            if not pre_audit.get("ready", False):
                raise RuntimeError(
                    f"Pre-host audit failed for turn {turn}. See "
                    f"{logs_root / f'{turn_tag}-PRE_HOST_AUDIT.txt'}"
                )

            tracked_outputs = [hst, xy] + [
                _live_game_file(game,validated.basename,f".m{pid}")
                for pid in cfg.player_ids
            ]

            cp, settled = _run_host_serialized(cfg, hst, tracked_outputs, turn_dir)

            first_m = _live_game_file(
                game,validated.basename,f".m{cfg.player_ids[0]}"
            )
            year_after = _read_year(first_m, xy)
            post_audit=_post_host_audit(
                cfg, game, hst, pre_audit, logs_root, turn, year_before, year_after
            )

            year_advanced=(year_before is None or year_after is None or year_after>year_before)
            x_consumed=bool(post_audit.get("all_consumed",False))
            host_success=cp.returncode==0 and settled and year_advanced and x_consumed
            if cfg.turn_archive_enabled:
                archive_turn_phase(
                    logs_root/"turn-archive", turn_tag=turn_tag, phase="20-post-host-attempt",
                    game_dir=game, basename=validated.basename,
                    logs_root=(logs_root if cfg.turn_archive_include_logs else None),
                    templates_root=templates_root, ai_state_root=ai_state_root,
                    config=cfg,
                    metadata={
                        "execution_turn":turn,"native_year_before":year_before,"native_year_after":year_after,
                        "host_returncode":cp.returncode,"host_settled":bool(settled),
                        "all_x_consumed":x_consumed,"host_success":host_success,
                    },
                )
            if cfg.keep_every_turn:
                _snapshot(
                    game,
                    logs_root/"native"/f"{turn_tag}-post-host",
                    basename=validated.basename,
                    metadata={
                        "phase":"post_host",
                        "execution_turn":turn,
                        "native_year_before":year_before,
                        "native_year_after":year_after,
                        "native_turn_before":(
                            None if year_before is None else int(year_before)-2400
                        ),
                        "native_turn_after":(
                            None if year_after is None else int(year_after)-2400
                        ),
                        "host_returncode":cp.returncode,
                        "host_settled":bool(settled),
                        "all_x_consumed":x_consumed,
                        "host_success":host_success,
                    },
                )
            history_ready=True
            if host_success and cfg.auto_merge_history:
                history_report=_auto_merge_histories(
                    cfg,game,validated.basename,logs_root,
                    phase_tag=turn_tag,
                    execution_turn=turn,
                )
                history_ready=bool(history_report.get("ready",False))
            elif host_success:
                history_ready=bool(
                    _history_sync_report(
                        game,validated.basename,cfg.player_ids
                    ).get("ready",False)
                )
            success=host_success and history_ready
            if isinstance(order_bridge, IntegratedNativeOrderBridge):
                if success:
                    order_bridge.commit_pending_memory()
                else:
                    order_bridge.discard_pending_memory()
            if success and cfg.turn_archive_enabled:
                archive_turn_phase(
                    logs_root/"turn-archive", turn_tag=turn_tag, phase="30-committed",
                    game_dir=game, basename=validated.basename,
                    logs_root=(logs_root if cfg.turn_archive_include_logs else None),
                    templates_root=templates_root, ai_state_root=ai_state_root,
                    config=cfg,
                    metadata={"execution_turn":turn,"native_year_before":year_before,"native_year_after":year_after,"success":True},
                )
            msg=(
                "Host generated next turn, consumed all generated X order files, "
                "and cumulative player histories were merged and validated."
                if success else
                f"Host/order registration failure: rc={cp.returncode}, settled={settled}, "
                f"year {year_before}->{year_after}, all_x_consumed={x_consumed}, "
                f"history_ready={history_ready}"
            )

            # Omniscient observer reads the same direct Stars! game directory.
            current_observer = None
            try:
                current_observer = read_observer_turn(hst, xy, turn)
                current_observer.events = derive_turn_events(
                    previous_observer, current_observer
                )
                save_observer_turn(
                    observer_root / f"{turn_tag}.json", current_observer
                )
                observer_history.append(current_observer)
                previous_observer = current_observer

                if cfg.print_observer_each_turn:
                    _print_observer_summary(current_observer, cfg.personas)
            except Exception as exc:
                (logs_root / f"{turn_tag}-observer-error.txt").write_text(
                    f"{type(exc).__name__}: {exc}", encoding="utf-8"
                )

            checkpoint_written = turn in cfg.checkpoints
            if checkpoint_written and current_observer is not None:
                report = build_human_report(
                    current_observer,
                    observer_history,
                    personas=cfg.personas,
                    checkpoint_from=last_checkpoint_observer,
                )
                (checkpoints_root / f"{turn_tag}-OBSERVER_REPORT.txt").write_text(
                    report, encoding="utf-8"
                )
                (checkpoints_root / f"{turn_tag}-OBSERVER_REPORT.md").write_text(
                    "```\n" + report + "\n```\n", encoding="utf-8"
                )
                (root / "LATEST_OBSERVER_REPORT.txt").write_text(
                    report, encoding="utf-8"
                )
                last_checkpoint_observer = current_observer

                if cfg.print_observer_each_turn:
                    print("")
                    print(report)
                    print("")

            e = TurnExecution(
                turn, order_files, cp.returncode, year_before, year_after,
                checkpoint_written, success, msg
            )
            executions.append(e)
            (logs_root / f"{turn_tag}-execution.json").write_text(
                json.dumps(asdict(e), indent=2), encoding="utf-8"
            )

            if not success:
                break

        except Exception as exc:
            if getattr(cfg,"turn_archive_enabled",False):
                try:
                    archive_turn_phase(
                        logs_root/"turn-archive", turn_tag=turn_tag, phase="99-failure",
                        game_dir=game, basename=validated.basename,
                        logs_root=(logs_root if getattr(cfg,"turn_archive_include_logs",True) else None),
                        templates_root=templates_root, ai_state_root=ai_state_root,
                        config=cfg,
                        metadata={"execution_turn":turn,"native_year_before":year_before,"order_files":list(order_files),
                                  "exception_type":type(exc).__name__,"exception":str(exc)},
                    )
                except Exception as archive_exc:
                    (logs_root/f"{turn_tag}-TURN_ARCHIVE_ERROR.txt").write_text(
                        f"{type(archive_exc).__name__}: {archive_exc}",encoding="utf-8"
                    )
            if isinstance(order_bridge, IntegratedNativeOrderBridge):
                order_bridge.discard_pending_memory()
            e = TurnExecution(
                turn, order_files, None, year_before, None, False, False,
                f"{type(exc).__name__}: {exc}"
            )
            executions.append(e)
            (logs_root / f"{turn_tag}-execution.json").write_text(
                json.dumps(asdict(e), indent=2), encoding="utf-8"
            )
            print(f"[AUTOPLAY FAILED] {e.message}",flush=True)
            break

    (root / "autoplay-result.json").write_text(
        json.dumps([asdict(e) for e in executions], indent=2),
        encoding="utf-8"
    )
    return executions
