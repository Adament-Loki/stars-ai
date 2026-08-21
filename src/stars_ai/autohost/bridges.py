"""Order-file bridge implementations used by the native autoplay lifecycle."""

from __future__ import annotations

from pathlib import Path
import shutil
import subprocess

from ..native.x_writer import write_ai_turn


class NativeOrderBridge:
    """Create a valid native ``.x#`` file from a player turn context."""

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
    """Compile semantic AI decisions into a native order file.

    The host bootstraps one immutable, known-good template per player. Each
    turn is generated from that template and the current M-file header.
    """

    def __init__(
        self,
        personas: dict[str, str] | None = None,
        console_player_logs: list[int] | None = None,
        allied_pairs: list[list[int]] | None = None,
        memory_root: str | Path | None = None,
    ):
        self.personas = personas or {}
        self.console_player_logs = (
            None if console_player_logs is None else {int(value) for value in console_player_logs}
        )
        self.allied_pairs = [list(map(int, pair)) for pair in (allied_pairs or [])]
        self.memory_root = Path(memory_root).resolve() if memory_root else None
        self._pending_memories: dict[int, tuple[Path, Path]] = {}

    def _friend_ids_for(self, player_id: int) -> list[int]:
        friend_ids = set()
        for pair in self.allied_pairs:
            if len(pair) != 2:
                continue
            first, second = int(pair[0]), int(pair[1])
            if first == player_id and second != player_id:
                friend_ids.add(second)
            elif second == player_id and first != player_id:
                friend_ids.add(first)
        return sorted(friend_ids)

    def create_x_file(
        self, *, player_id, m_path, xy_path, existing_x_path, output_x_path, turn_dir,
    ):
        if existing_x_path is None or not existing_x_path.exists():
            raise RuntimeError(
                f"Integrated writer needs a persistent known-good .x{player_id} template."
            )
        memory_path = (
            self.memory_root / f"player-{int(player_id):02d}-memory.json"
            if self.memory_root is not None else None
        )
        pending_memory_path = (
            self.memory_root / f"player-{int(player_id):02d}-memory.pending.json"
            if self.memory_root is not None else None
        )
        result = write_ai_turn(
            player_id=player_id,
            m_path=m_path,
            xy_path=xy_path,
            template_x_path=existing_x_path,
            output_x_path=output_x_path,
            persona_name=self.personas.get(str(player_id), "Balanced"),
            trace_path=turn_dir / f"{getattr(self, 'turn_tag', 'current')}-player-{player_id:02d}-decision-native.json",
            friend_player_ids=self._friend_ids_for(int(player_id)),
            memory_path=memory_path,
            memory_output_path=pending_memory_path,
        )
        if memory_path is not None and pending_memory_path is not None:
            self._pending_memories[int(player_id)] = (memory_path, pending_memory_path)

        moves = [event for event in result.emitted if event.get("kind") == "move_fleet"]
        skipped_moves = [event for event in result.skipped if event.get("kind") == "move_fleet"]
        move_text = ", ".join(
            f"F{move['payload'].get('fleet_id')}->P{move['payload'].get('destination_planet_id')}"
            f"@W{move['payload'].get('warp', '?')}"
            for move in moves
        ) or "none"
        show_console = (
            self.console_player_logs is None
            or int(player_id) in self.console_player_logs
        )
        if show_console:
            print(
                f"[AI P{player_id} Y{result.year}] emitted moves: {move_text}; "
                f"skipped moves: {len(skipped_moves)}",
                flush=True,
            )
        report_path = turn_dir / (
            f"{getattr(self, 'turn_tag', 'current')}-player-{player_id:02d}-DECISION_REPORT.txt"
        )
        if show_console and report_path.exists():
            print(report_path.read_text(encoding="utf-8"), flush=True)
        return output_x_path

    def commit_pending_memory(self) -> None:
        for player_id, (committed, pending) in sorted(self._pending_memories.items()):
            if not pending.exists():
                raise RuntimeError(
                    f"Pending AI memory is missing for player {player_id}: {pending}"
                )
            pending.replace(committed)
        self._pending_memories.clear()

    def discard_pending_memory(self) -> None:
        for _, pending in self._pending_memories.values():
            pending.unlink(missing_ok=True)
        self._pending_memories.clear()


class ExternalCommandOrderBridge(NativeOrderBridge):
    """Run an external writer command that creates a native order file."""

    def __init__(self, command_template: str, timeout_seconds: int = 60):
        self.command_template = command_template
        self.timeout_seconds = timeout_seconds

    def create_x_file(
        self, *, player_id, m_path, xy_path, existing_x_path, output_x_path, turn_dir,
    ):
        command = self.command_template.format(
            player_id=player_id,
            m=str(m_path),
            xy=str(xy_path),
            existing_x=str(existing_x_path or ""),
            output_x=str(output_x_path),
            turn_dir=str(turn_dir),
        )
        completed = subprocess.run(
            command,
            shell=True,
            cwd=str(turn_dir),
            capture_output=True,
            text=True,
            timeout=self.timeout_seconds,
        )
        (turn_dir / f"player-{player_id:02d}-native-writer.stdout.txt").write_text(
            completed.stdout or "", encoding="utf-8",
        )
        (turn_dir / f"player-{player_id:02d}-native-writer.stderr.txt").write_text(
            completed.stderr or "", encoding="utf-8",
        )
        if completed.returncode != 0:
            raise RuntimeError(f"Native writer failed for P{player_id}: rc={completed.returncode}")
        if not output_x_path.exists():
            raise RuntimeError(f"Native writer did not create {output_x_path}")
        return output_x_path


class NoopOrderBridge(NativeOrderBridge):
    """Diagnostic bridge that copies an existing native order file unchanged."""

    def create_x_file(
        self, *, player_id, m_path, xy_path, existing_x_path, output_x_path, turn_dir,
    ):
        if existing_x_path is None or not existing_x_path.exists():
            raise RuntimeError(
                f"No existing .x{player_id} available. Noop bridge cannot invent native orders."
            )
        shutil.copy2(existing_x_path, output_x_path)
        return output_x_path
