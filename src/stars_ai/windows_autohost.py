
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

from .native.player_state import PlayerState
from .native.x_writer import write_ai_turn
from .native_observer import read_observer_turn, derive_turn_events, save_observer_turn, load_observer_turn, build_human_report

@dataclass
class WindowsAutoHostConfig:
    stars_exe: str
    seed_dir: str
    output_dir: str
    game_name: str
    player_ids: list[int] = field(default_factory=lambda: [1,2,3,4])
    turns: int = 50
    checkpoints: list[int] = field(default_factory=lambda: [10,25,50])
    host_password: str | None = None
    keep_every_turn: bool = True
    stop_on_missing_x: bool = True
    host_timeout_seconds: int = 60
    host_poll_seconds: float = 0.5
    host_settle_seconds: float = 1.5
    prevent_parallel_stars: bool = True
    use_seed_as_live: bool = True
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
        result=write_ai_turn(
            player_id=player_id,
            m_path=m_path,
            xy_path=xy_path,
            template_x_path=existing_x_path,
            output_x_path=output_x_path,
            persona_name=self.personas.get(str(player_id),"Balanced"),
            trace_path=turn_dir/f"{getattr(self,'turn_tag','current')}-player-{player_id:02d}-decision-native.json",
            friend_player_ids=self._friend_ids_for(int(player_id)),
            memory_path=(
                self.memory_root/f"player-{int(player_id):02d}-memory.json"
                if self.memory_root is not None else None
            ),
        )
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
    root.mkdir(parents=True,exist_ok=True)
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
) -> Path:
    """
    Capture initial known-good X files ONCE and then never depend on live X files.

    This is restart-safe: Stars! may consume/delete GAME.x# after hosting, and
    cleanup_output_on_start may delete logs. Persistent templates live elsewhere.
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

    root.mkdir(parents=True,exist_ok=True)

    for player_id in cfg.player_ids:
        current_m_hits=list(game.glob(f"*.m{player_id}"))
        if len(current_m_hits)!=1:
            raise FileNotFoundError(
                f"Expected one current .m{player_id} while validating X template; found {len(current_m_hits)}."
            )
        current_m=current_m_hits[0]
        dest=root/f"template.x{player_id}"

        if dest.exists() and _template_matches_game(dest,current_m,player_id):
            continue

        # First-run bootstrap. Search live game first, then original seed for
        # copied-live mode. A subsequent restart does not need either because
        # the persistent dest above survives.
        candidates=[]
        for folder in (game,seed):
            for cand in folder.glob(f"*.x{player_id}"):
                if cand.resolve() not in {x.resolve() for x in candidates}:
                    candidates.append(cand)
        valid=[c for c in candidates if _template_matches_game(c,current_m,player_id)]
        if len(valid)!=1:
            reason=(
                "No persistent template exists yet and no matching live initial X file was found. "
                f"Create/save one valid .x{player_id} for this game once, then rerun. "
                f"Persistent location: {dest}"
            )
            if dest.exists():
                reason=(
                    f"Existing persistent template {dest} belongs to a different game/player and "
                    f"no unique matching live .x{player_id} was found to refresh it."
                )
            raise FileNotFoundError(reason)
        shutil.copy2(valid[0],dest)

    manifest={
        "game_name":cfg.game_name,
        "seed_dir":str(seed),
        "templates":{
            str(pid):str(root/f"template.x{pid}") for pid in cfg.player_ids
        },
        "note":"Immutable bootstrap templates. Live GAME.x# files are regenerated each turn.",
    }
    (root/'templates.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    return root

def _copy_seed(seed: Path, game: Path) -> None:
    if game.exists():
        shutil.rmtree(game)
    shutil.copytree(seed, game)


def _safe_cleanup_output(root: Path, seed: Path) -> None:
    """
    Clear diagnostics/playtest output from a previous run without ever deleting
    the direct Stars! game directory.
    """
    root=root.resolve()
    seed=seed.resolve()
    if root == seed:
        raise RuntimeError("output_dir cannot equal seed_dir when cleanup_output_on_start is enabled")
    try:
        seed.relative_to(root)
        raise RuntimeError(
            "Refusing cleanup because seed_dir is inside output_dir. "
            "Move the live Stars! game outside the playtest/output directory."
        )
    except ValueError:
        pass
    if root.exists():
        shutil.rmtree(root)

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

def _snapshot(game: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    for p in game.iterdir():
        if p.is_file() and p.suffix.lower() in {".hst",".xy",".m1",".m2",".m3",".m4",".x1",".x2",".x3",".x4"}:
            shutil.copy2(p, dest / p.name)


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

    cp=subprocess.run(
        cmd,
        cwd=str(hst.parent),
        capture_output=True,
        text=True,
        timeout=cfg.host_timeout_seconds,
        creationflags=creationflags,
    )
    (turn_dir/f"{audit_tag}-host.stdout.txt").write_text(cp.stdout or "",encoding="utf-8")
    (turn_dir/f"{audit_tag}-host.stderr.txt").write_text(cp.stderr or "",encoding="utf-8")

    if cfg.prevent_parallel_stars:
        # Some legacy launchers return before the actual GUI/host child exits.
        _wait_until_no_stars_process(
            cfg.stars_exe,
            timeout_seconds=cfg.host_timeout_seconds,
            poll_seconds=cfg.host_poll_seconds,
        )

    settled=_wait_for_files_to_change_and_settle(
        tracked_outputs,before,
        timeout_seconds=cfg.host_timeout_seconds,
        poll_seconds=cfg.host_poll_seconds,
        settle_seconds=cfg.host_settle_seconds,
    )
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

        m_hits=list(game.glob(f"*.m{pid}"))
        if len(m_hits)!=1:
            row["error"]=f"expected one .m{pid}; found {len(m_hits)}"
            report["ready"]=False
            report["players"].append(row)
            continue

        try:
            mh,_,_=read_blocks(m_hits[0])
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
    seed = Path(cfg.seed_dir).resolve()
    root = Path(cfg.output_dir).resolve()
    if cfg.cleanup_output_on_start:
        _safe_cleanup_output(root, seed)
    root.mkdir(parents=True, exist_ok=True)

    # Direct-seed mode: Stars! operates on the actual configured game
    # directory. We do not copy/rename the game into turn-specific folders.
    game = seed if cfg.use_seed_as_live else (root / "live")
    if not cfg.use_seed_as_live:
        _copy_seed(seed, game)

    logs_root = root / "logs"
    logs_root.mkdir(exist_ok=True)
    checkpoints_root = logs_root / "checkpoints"
    checkpoints_root.mkdir(exist_ok=True)
    observer_root = logs_root / "observer"
    observer_root.mkdir(exist_ok=True)
    hst = _find_one(game, "*.hst")
    xy = _find_one(game, "*.xy")

    # Bootstrap once from manually-created Turn-0 X files. Thereafter the
    # persistent sibling store is the source template even when Stars! has
    # consumed every live .x# and this process has been restarted.
    templates_root = _bootstrap_persistent_x_templates(cfg,seed=seed,game=game)

    # v7.0: strategic memory is intentionally outside output_dir so a diagnostics
    # cleanup or process restart does not erase the empire's learned galaxy.
    ai_state_root=_persistent_ai_state_root(cfg,seed)
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

            # Each player completes synchronously before the next begins.
            for player_id in cfg.player_ids:
                m_hits = list(game.glob(f"*.m{player_id}"))
                if len(m_hits) != 1:
                    raise FileNotFoundError(
                        f"Expected exactly one .m{player_id} in direct game directory {game}; found {len(m_hits)}"
                    )
                m_path = m_hits[0]

                if year_before is None:
                    year_before = _read_year(m_path, xy)

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

            missing = [pid for pid in cfg.player_ids if not list(game.glob(f"*.x{pid}"))]
            if missing and cfg.stop_on_missing_x:
                raise RuntimeError(f"Missing native order files for players: {missing}")

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
                next(iter(game.glob(f"*.m{pid}")))
                for pid in cfg.player_ids
            ]

            cp, settled = _run_host_serialized(cfg, hst, tracked_outputs, turn_dir)

            first_m = _find_one(game, "*.m1")
            year_after = _read_year(first_m, xy)
            post_audit=_post_host_audit(
                cfg, game, hst, pre_audit, logs_root, turn, year_before, year_after
            )

            year_advanced=(year_before is None or year_after is None or year_after>year_before)
            x_consumed=bool(post_audit.get("all_consumed",False))
            success=cp.returncode==0 and settled and year_advanced and x_consumed
            msg=(
                "Host generated next turn and consumed all generated X order files."
                if success else
                f"Host/order registration failure: rc={cp.returncode}, settled={settled}, "
                f"year {year_before}->{year_after}, all_x_consumed={x_consumed}"
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
            e = TurnExecution(
                turn, order_files, None, year_before, None, False, False,
                f"{type(exc).__name__}: {exc}"
            )
            executions.append(e)
            (logs_root / f"{turn_tag}-execution.json").write_text(
                json.dumps(asdict(e), indent=2), encoding="utf-8"
            )
            break

    (root / "autoplay-result.json").write_text(
        json.dumps([asdict(e) for e in executions], indent=2),
        encoding="utf-8"
    )
    return executions
