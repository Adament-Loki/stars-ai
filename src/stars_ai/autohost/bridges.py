"""Order-file bridge implementations used by the native autoplay lifecycle."""

from __future__ import annotations

from pathlib import Path
import shutil
import subprocess

from ..ai.state_store import AIStateStore
from ..turn_execution import execute_native_ai_turn
from ..reporting import DecisionJournalReporter, turn_number_from_tag


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
        client_x_path: Path | None = None,
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
        human_player_ids: list[int] | None = None,
        memory_root: str | Path | None = None,
        report_root: str | Path | None = None,
    ):
        self.personas = personas or {}
        self.console_player_logs = (
            None if console_player_logs is None else {int(value) for value in console_player_logs}
        )
        self.allied_pairs = [list(map(int, pair)) for pair in (allied_pairs or [])]
        self.human_player_ids = [int(seat) for seat in (human_player_ids or [])]
        self.memory_root = Path(memory_root).resolve() if memory_root else None
        self.report_root = Path(report_root).resolve() if report_root else None
        # Retained for direct legacy callers/tests that used this diagnostic
        # surface before AIStateStore owned the transaction mechanics.
        self._pending_memories: dict[int, tuple[Path, Path]] = {}
        self._pending_memory_stores: dict[int, AIStateStore] = {}

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
        client_x_path=None,
    ):
        if existing_x_path is None or not existing_x_path.exists():
            raise RuntimeError(
                f"Integrated writer needs a persistent known-good .x{player_id} template."
            )
        state_store = (
            AIStateStore.for_player(self.memory_root, int(player_id))
            if self.memory_root is not None else None
        )
        execution = execute_native_ai_turn(
            player_id=player_id,
            m_path=m_path,
            xy_path=xy_path,
            template_x_path=existing_x_path,
            output_x_path=output_x_path,
            client_x_path=client_x_path,
            persona_name=self.personas.get(str(player_id), "Balanced"),
            trace_path=turn_dir / f"{getattr(self, 'turn_tag', 'current')}-player-{player_id:02d}-decision-native.json",
            friend_player_ids=self._friend_ids_for(int(player_id)),
            human_player_ids=self.human_player_ids,
            state_store=state_store,
        )
        result = execution.native_result
        if state_store is not None and state_store.has_staged_state:
            # The application layer stages semantic/native-confirmed memory;
            # the bridge alone promotes or rolls it back after host execution.
            self._pending_memories[int(player_id)] = (
                state_store.committed_path,
                state_store.pending_path,
            )
            self._pending_memory_stores[int(player_id)] = state_store

        report_model = execution.report_model
        if self.report_root is not None and report_model is not None:
            try:
                execution_turn = turn_number_from_tag(getattr(self, "turn_tag", None))
                if execution_turn is not None:
                    report_model = report_model.with_turn(execution_turn)
                DecisionJournalReporter(self.report_root).write_turn(report_model)
            except Exception as exc:
                # A human-report failure must not veto a validated X file.
                diagnostic = self.report_root / "verbose" / "diagnostics" / (
                    f"{getattr(self, 'turn_tag', 'current')}-player-{int(player_id):02d}-reporting-error.txt"
                )
                diagnostic.parent.mkdir(parents=True, exist_ok=True)
                diagnostic.write_text(f"{type(exc).__name__}: {exc}", encoding="utf-8")

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
            store = self._pending_memory_stores.get(player_id)
            if store is None:
                # Compatibility with callers that populated _pending_memories
                # directly before AIStateStore was introduced.
                store = AIStateStore(committed, pending)
                store.adopt_pending_file()
            store.commit()
        self._pending_memories.clear()
        self._pending_memory_stores.clear()

    def discard_pending_memory(self) -> None:
        for player_id, (committed, pending) in self._pending_memories.items():
            store = self._pending_memory_stores.get(player_id)
            if store is None:
                store = AIStateStore(committed, pending)
            store.rollback()
        self._pending_memories.clear()
        self._pending_memory_stores.clear()


class ExternalCommandOrderBridge(NativeOrderBridge):
    """Run an external writer command that creates a native order file."""

    def __init__(self, command_template: str, timeout_seconds: int = 60):
        self.command_template = command_template
        self.timeout_seconds = timeout_seconds

    def create_x_file(
        self, *, player_id, m_path, xy_path, existing_x_path, output_x_path, turn_dir,
        client_x_path=None,
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
        client_x_path=None,
    ):
        if existing_x_path is None or not existing_x_path.exists():
            raise RuntimeError(
                f"No existing .x{player_id} available. Noop bridge cannot invent native orders."
            )
        shutil.copy2(existing_x_path, output_x_path)
        return output_x_path
