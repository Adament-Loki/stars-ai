from __future__ import annotations
from pathlib import Path
import hashlib
import shutil

TARGET = Path("src/stars_ai/windows_autohost.py")
KNOWN_MAIN_GIT_BLOB = "78b201eee791b792f17dd425b77488ac694055ef"
MARKER = "turn_archive_enabled: bool = True"


def git_blob_sha(data: bytes) -> str:
    return hashlib.sha1(b"blob " + str(len(data)).encode("ascii") + b"\0" + data).hexdigest()


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count=text.count(old)
    if count!=1:
        raise RuntimeError(f"{label}: expected exactly one anchor, found {count}")
    return text.replace(old,new,1)


def patch(text: str) -> str:
    if MARKER in text:
        return text

    text=replace_once(
        text,
        "from .native_observer import read_observer_turn, derive_turn_events, save_observer_turn, load_observer_turn, build_human_report\n",
        "from .native_observer import read_observer_turn, derive_turn_events, save_observer_turn, load_observer_turn, build_human_report\n"
        "from .turn_archive import archive_turn_phase\n",
        "turn archive import",
    )
    text=replace_once(
        text,
        "    keep_every_turn: bool = True\n",
        "    keep_every_turn: bool = True\n"
        "    # v8.7.1: immutable before/after snapshots for native-order debugging.\n"
        "    turn_archive_enabled: bool = True\n"
        "    turn_archive_include_logs: bool = True\n",
        "turn archive config",
    )
    text=replace_once(
        text,
        "            year_before=_read_year(first_pre_m,xy)\n"
        "            # This is deliberately before deleting or generating any X file.\n",
        "            year_before=_read_year(first_pre_m,xy)\n"
        "            if cfg.turn_archive_enabled:\n"
        "                archive_turn_phase(\n"
        "                    logs_root/\"turn-archive\", turn_tag=turn_tag, phase=\"00-pre-write\",\n"
        "                    game_dir=game, basename=validated.basename,\n"
        "                    logs_root=(logs_root if cfg.turn_archive_include_logs else None),\n"
        "                    templates_root=templates_root, ai_state_root=ai_state_root,\n"
        "                    config=cfg,\n"
        "                    metadata={\"execution_turn\":turn,\"native_year_before\":year_before},\n"
        "                )\n"
        "            # This is deliberately before deleting or generating any X file.\n",
        "pre-write archive",
    )
    text=replace_once(
        text,
        "            if missing and cfg.stop_on_missing_x:\n"
        "                raise RuntimeError(f\"Missing native order files for players: {missing}\")\n\n"
        "            # Hard barrier. Stars! is not launched unless every X file passes.\n",
        "            if missing and cfg.stop_on_missing_x:\n"
        "                raise RuntimeError(f\"Missing native order files for players: {missing}\")\n\n"
        "            if cfg.turn_archive_enabled:\n"
        "                archive_turn_phase(\n"
        "                    logs_root/\"turn-archive\", turn_tag=turn_tag, phase=\"10-pre-host\",\n"
        "                    game_dir=game, basename=validated.basename,\n"
        "                    logs_root=(logs_root if cfg.turn_archive_include_logs else None),\n"
        "                    templates_root=templates_root, ai_state_root=ai_state_root,\n"
        "                    config=cfg,\n"
        "                    metadata={\"execution_turn\":turn,\"native_year_before\":year_before,\"order_files\":list(order_files)},\n"
        "                )\n\n"
        "            # Hard barrier. Stars! is not launched unless every X file passes.\n",
        "pre-host archive",
    )
    text=replace_once(
        text,
        "            host_success=cp.returncode==0 and settled and year_advanced and x_consumed\n"
        "            if cfg.keep_every_turn:\n",
        "            host_success=cp.returncode==0 and settled and year_advanced and x_consumed\n"
        "            if cfg.turn_archive_enabled:\n"
        "                archive_turn_phase(\n"
        "                    logs_root/\"turn-archive\", turn_tag=turn_tag, phase=\"20-post-host-attempt\",\n"
        "                    game_dir=game, basename=validated.basename,\n"
        "                    logs_root=(logs_root if cfg.turn_archive_include_logs else None),\n"
        "                    templates_root=templates_root, ai_state_root=ai_state_root,\n"
        "                    config=cfg,\n"
        "                    metadata={\n"
        "                        \"execution_turn\":turn,\"native_year_before\":year_before,\"native_year_after\":year_after,\n"
        "                        \"host_returncode\":cp.returncode,\"host_settled\":bool(settled),\n"
        "                        \"all_x_consumed\":x_consumed,\"host_success\":host_success,\n"
        "                    },\n"
        "                )\n"
        "            if cfg.keep_every_turn:\n",
        "post-host attempt archive",
    )
    text=replace_once(
        text,
        "            if isinstance(order_bridge, IntegratedNativeOrderBridge):\n"
        "                if success:\n"
        "                    order_bridge.commit_pending_memory()\n"
        "                else:\n"
        "                    order_bridge.discard_pending_memory()\n"
        "            msg=(\n",
        "            if isinstance(order_bridge, IntegratedNativeOrderBridge):\n"
        "                if success:\n"
        "                    order_bridge.commit_pending_memory()\n"
        "                else:\n"
        "                    order_bridge.discard_pending_memory()\n"
        "            if success and cfg.turn_archive_enabled:\n"
        "                archive_turn_phase(\n"
        "                    logs_root/\"turn-archive\", turn_tag=turn_tag, phase=\"30-committed\",\n"
        "                    game_dir=game, basename=validated.basename,\n"
        "                    logs_root=(logs_root if cfg.turn_archive_include_logs else None),\n"
        "                    templates_root=templates_root, ai_state_root=ai_state_root,\n"
        "                    config=cfg,\n"
        "                    metadata={\"execution_turn\":turn,\"native_year_before\":year_before,\"native_year_after\":year_after,\"success\":True},\n"
        "                )\n"
        "            msg=(\n",
        "committed archive",
    )
    text=replace_once(
        text,
        "        except Exception as exc:\n"
        "            if isinstance(order_bridge, IntegratedNativeOrderBridge):\n"
        "                order_bridge.discard_pending_memory()\n"
        "            e = TurnExecution(\n"
        "                turn, order_files, None, year_before, None, False, False,\n",
        "        except Exception as exc:\n"
        "            if getattr(cfg,\"turn_archive_enabled\",False):\n"
        "                try:\n"
        "                    archive_turn_phase(\n"
        "                        logs_root/\"turn-archive\", turn_tag=turn_tag, phase=\"99-failure\",\n"
        "                        game_dir=game, basename=validated.basename,\n"
        "                        logs_root=(logs_root if getattr(cfg,\"turn_archive_include_logs\",True) else None),\n"
        "                        templates_root=templates_root, ai_state_root=ai_state_root,\n"
        "                        config=cfg,\n"
        "                        metadata={\"execution_turn\":turn,\"native_year_before\":year_before,\"order_files\":list(order_files),\n"
        "                                  \"exception_type\":type(exc).__name__,\"exception\":str(exc)},\n"
        "                    )\n"
        "                except Exception as archive_exc:\n"
        "                    (logs_root/f\"{turn_tag}-TURN_ARCHIVE_ERROR.txt\").write_text(\n"
        "                        f\"{type(archive_exc).__name__}: {archive_exc}\",encoding=\"utf-8\"\n"
        "                    )\n"
        "            if isinstance(order_bridge, IntegratedNativeOrderBridge):\n"
        "                order_bridge.discard_pending_memory()\n"
        "            e = TurnExecution(\n"
        "                turn, order_files, None, year_before, None, False, False,\n",
        "failure archive",
    )
    return text


def main() -> int:
    if not TARGET.exists():
        raise SystemExit(f"Missing {TARGET}; run from repository root")
    raw=TARGET.read_bytes()
    text=raw.decode("utf-8")
    if MARKER in text:
        print(f"Already patched: {TARGET}")
        return 0
    sha=git_blob_sha(raw)
    if sha != KNOWN_MAIN_GIT_BLOB:
        # v8.7 did not intentionally modify windows_autohost.py, but local edits
        # are possible. Structural anchors remain fail-closed; require the exact
        # public-main baseline instead of guessing across an unknown runtime file.
        raise SystemExit(
            f"Refusing unknown windows_autohost.py blob {sha}; expected {KNOWN_MAIN_GIT_BLOB}. "
            "Do not bypass this guard; rebase the archive patch to the current file."
        )
    updated=patch(text)
    compile(updated,str(TARGET),"exec")
    backup=TARGET.with_suffix(TARGET.suffix+".pre-v871-turn-archive.bak")
    if backup.exists():
        raise SystemExit(f"Refusing to overwrite existing backup: {backup}")
    shutil.copy2(TARGET,backup)
    TARGET.write_text(updated,encoding="utf-8")
    print(f"Patched {TARGET}")
    print(f"Backup: {backup}")
    return 0


if __name__=="__main__":
    raise SystemExit(main())
