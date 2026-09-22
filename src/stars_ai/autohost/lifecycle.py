"""Application-level coordination for one hosted Stars! turn.

The native writer, host process, persistent-state store, reports, and archive
each retain their own responsibilities.  This module only makes their
ordering and the persistent-memory transaction boundary explicit for
``windows_autohost``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Protocol

from ..turn_archive import archive_approved_next_turn_actions, archive_turn_phase


class _PendingMemoryTransaction(Protocol):
    """Compatibility surface offered by the integrated AI order bridge."""

    def commit_pending_memory(self) -> None:
        ...

    def discard_pending_memory(self) -> None:
        ...


class TurnLifecyclePhase(str, Enum):
    """The application-level state of one attempted host turn."""

    IDLE = "IDLE"
    OBSERVING = "OBSERVING"
    STAGING = "STAGING"
    COMMITTED = "COMMITTED"
    ROLLED_BACK = "ROLLED_BACK"


@dataclass
class HostedTurnLifecycle:
    """Coordinate staged AI memory around native writing and host outcome.

    ``activate`` intentionally does *not* discard pending memory.  The caller
    can therefore archive the exact pre-write state before ``begin_staging``
    clears a stale or abandoned pending snapshot.  Memory is promoted only by
    ``settle_after_host(True)``; every other exit uses ``rollback``.
    """

    order_bridge: object
    turn_tag: str | None = None
    phase: TurnLifecyclePhase = TurnLifecyclePhase.IDLE
    order_files: list[Path] = field(default_factory=list)

    def activate(self, turn_tag: str) -> None:
        if self.phase is TurnLifecyclePhase.STAGING:
            raise RuntimeError("Cannot activate a new turn while memory is staged")
        self.turn_tag = str(turn_tag)
        self.phase = TurnLifecyclePhase.OBSERVING
        self.order_files.clear()
        # ``turn_tag`` is a legacy bridge diagnostic/reporting API.  Preserve
        # it for regular Python bridge objects without requiring a field on
        # third-party bridge protocols (some use ``__slots__``).
        try:
            setattr(self.order_bridge, "turn_tag", self.turn_tag)
        except (AttributeError, TypeError):
            pass

    def begin_staging(self) -> None:
        if self.phase is not TurnLifecyclePhase.OBSERVING:
            raise RuntimeError("AI memory staging requires an active observed turn")
        self._discard_pending_memory()
        self.phase = TurnLifecyclePhase.STAGING

    def record_order_file(self, path: str | Path) -> None:
        if self.phase is not TurnLifecyclePhase.STAGING:
            raise RuntimeError("Native orders may only be recorded while staging")
        self.order_files.append(Path(path))

    def settle_after_host(self, host_succeeded: bool) -> TurnLifecyclePhase:
        """Commit staged memory exactly when the complete host lifecycle passed."""

        if self.phase is not TurnLifecyclePhase.STAGING:
            raise RuntimeError("A host outcome requires staged AI memory")
        if host_succeeded:
            self._commit_pending_memory()
            self.phase = TurnLifecyclePhase.COMMITTED
        else:
            self._discard_pending_memory()
            self.phase = TurnLifecyclePhase.ROLLED_BACK
        return self.phase

    def rollback(self) -> TurnLifecyclePhase:
        """Discard pending memory after a native-write, audit, or host error."""

        self._discard_pending_memory()
        self.phase = TurnLifecyclePhase.ROLLED_BACK
        return self.phase

    def _commit_pending_memory(self) -> None:
        transaction = self._memory_transaction()
        if transaction is not None:
            transaction.commit_pending_memory()

    def _discard_pending_memory(self) -> None:
        transaction = self._memory_transaction()
        if transaction is not None:
            transaction.discard_pending_memory()

    def _memory_transaction(self) -> _PendingMemoryTransaction | None:
        bridge = self.order_bridge
        if (
            callable(getattr(bridge, "commit_pending_memory", None))
            and callable(getattr(bridge, "discard_pending_memory", None))
        ):
            return bridge  # type: ignore[return-value]
        return None


@dataclass(frozen=True)
class TurnArchiver:
    """Bind immutable archive inputs once for an application run.

    Native files, reports, templates, and persistent memory remain copied by
    the established :mod:`stars_ai.turn_archive` implementation.  This small
    adapter prevents the host loop from repeatedly reconstructing that same
    evidence context at every lifecycle boundary.
    """

    archive_root: Path
    game_dir: Path
    basename: str
    logs_root: Path
    templates_root: Path
    ai_state_root: Path
    config: Any
    include_logs: bool = True
    json_index: bool = True

    def capture(
        self,
        *,
        turn_tag: str,
        phase: str,
        metadata: dict[str, Any] | None = None,
    ) -> Path:
        return archive_turn_phase(
            self.archive_root,
            turn_tag=turn_tag,
            phase=phase,
            game_dir=self.game_dir,
            basename=self.basename,
            logs_root=self.logs_root if self.include_logs else None,
            templates_root=self.templates_root,
            ai_state_root=self.ai_state_root,
            config=self.config,
            json_index=self.json_index,
            metadata=metadata,
        )

    def capture_approved_actions(
        self,
        *,
        turn_tag: str,
        decision_turn_tag: str,
        metadata: dict[str, Any] | None = None,
    ) -> Path:
        return archive_approved_next_turn_actions(
            self.archive_root,
            turn_tag=turn_tag,
            decision_turn_tag=decision_turn_tag,
            game_dir=self.game_dir,
            basename=self.basename,
            logs_root=self.logs_root,
            json_index=self.json_index,
            metadata=metadata,
        )
