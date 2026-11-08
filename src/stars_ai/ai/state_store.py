"""Transactional storage for persistent AI state.

The application turn-execution layer stages a pending memory snapshot after
semantic orders have passed native serialization. ``AIStateStore`` makes the
resulting lifecycle explicit: only a successful Stars! host pass may promote
that snapshot; failed or abandoned passes discard it. The stored JSON remains
the established :class:`AgentMemory` schema.
"""

from __future__ import annotations

from pathlib import Path
from typing import Union

from ..memory import AgentMemory
from .ai_state import PersistentAIState


PersistentInput = Union[PersistentAIState, AgentMemory]


class AIStateStore:
    """Load, stage, commit, and roll back one player's durable AI memory.

    ``committed_path`` and ``pending_path`` are optional together so direct,
    non-host callers can use the same state-transition semantics in memory.
    Disk-backed stores keep the historical ``.pending.json`` file convention.
    """

    def __init__(
        self,
        committed_path: str | Path | None = None,
        pending_path: str | Path | None = None,
    ) -> None:
        self.committed_path = Path(committed_path) if committed_path else None
        self.pending_path = Path(pending_path) if pending_path else self._pending_for(
            self.committed_path
        )
        if (self.committed_path is None) != (self.pending_path is None):
            raise ValueError("Committed and pending AI-memory paths must be provided together")
        self._staged_state: PersistentAIState | None = None
        self._committed_state: PersistentAIState | None = None
        self._pending_file_adopted = False

    @staticmethod
    def _pending_for(committed_path: Path | None) -> Path | None:
        if committed_path is None:
            return None
        return committed_path.with_name(
            f"{committed_path.stem}.pending{committed_path.suffix}"
        )

    @classmethod
    def for_player(cls, memory_root: str | Path, player_id: int) -> "AIStateStore":
        root = Path(memory_root)
        stem = f"player-{int(player_id):02d}-memory"
        return cls(root / f"{stem}.json", root / f"{stem}.pending.json")

    @property
    def has_staged_state(self) -> bool:
        return self._staged_state is not None or self._pending_file_adopted

    def load(self) -> PersistentAIState:
        """Return a detached typed view of the authoritative state."""

        if self.committed_path is None:
            if self._committed_state is None:
                self._committed_state = PersistentAIState.from_agent_memory(AgentMemory())
            return PersistentAIState.from_agent_memory(self._committed_state.to_agent_memory())
        return PersistentAIState.from_agent_memory(AgentMemory.load(self.committed_path))

    def stage(self, updated_state: PersistentInput) -> PersistentAIState:
        """Write a pending, non-authoritative representation of ``updated_state``."""

        if isinstance(updated_state, AgentMemory):
            staged = PersistentAIState.from_agent_memory(updated_state)
        elif isinstance(updated_state, PersistentAIState):
            staged = PersistentAIState.from_agent_memory(updated_state.to_agent_memory())
        else:
            raise TypeError("AIStateStore.stage expects PersistentAIState or AgentMemory")
        self._staged_state = staged
        if self.pending_path is not None:
            staged.to_agent_memory().save(self.pending_path)
            self._pending_file_adopted = True
        return PersistentAIState.from_agent_memory(staged.to_agent_memory())

    def adopt_pending_file(self) -> None:
        """Register an already-written pending file without interpreting it.

        Retained for callers migrating from pre-Phase-7 pending artifacts.
        ``stage_existing`` remains the typed inspection API for valid
        saved-memory JSON.
        """

        if self.pending_path is None or not self.pending_path.exists():
            raise RuntimeError(f"Pending AI memory is missing: {self.pending_path}")
        self._pending_file_adopted = True

    def stage_existing(self) -> PersistentAIState:
        """Adopt a pending snapshot written by the unchanged native writer.

        This is the narrow adapter for a pre-existing pending snapshot; the
        store alone owns its eventual promotion or rollback.
        """

        if self.pending_path is None:
            raise RuntimeError("An in-memory store has no pending file to adopt")
        if not self.pending_path.exists():
            raise RuntimeError(f"Pending AI memory is missing: {self.pending_path}")
        self._staged_state = PersistentAIState.from_agent_memory(
            AgentMemory.load(self.pending_path)
        )
        self._pending_file_adopted = True
        return PersistentAIState.from_agent_memory(self._staged_state.to_agent_memory())

    def commit(self) -> None:
        """Promote the staged snapshot after a successful host lifecycle."""

        if self._staged_state is None and not self._pending_file_adopted:
            raise RuntimeError("No staged AI memory is available to commit")
        if self.committed_path is not None:
            if self.pending_path is None or not self.pending_path.exists():
                raise RuntimeError(f"Pending AI memory is missing: {self.pending_path}")
            self.committed_path.parent.mkdir(parents=True, exist_ok=True)
            self.pending_path.replace(self.committed_path)
        if self._staged_state is not None:
            self._committed_state = self._staged_state
        self._staged_state = None
        self._pending_file_adopted = False

    def rollback(self) -> None:
        """Discard the staged snapshot, leaving committed memory unchanged."""

        if self.pending_path is not None:
            self.pending_path.unlink(missing_ok=True)
        self._staged_state = None
        self._pending_file_adopted = False
