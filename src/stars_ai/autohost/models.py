"""Configuration and immutable value objects for the native autoplay loop."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class WindowsAutoHostConfig:
    """User-facing settings loaded from ``autoplay-config.json``."""

    stars_exe: str
    seed_dir: str
    output_dir: str
    # Retained for configuration compatibility only. The authoritative game
    # basename is discovered from seed_dir during fail-closed validation.
    game_name: str | None = None
    player_ids: list[int] = field(default_factory=lambda: [1, 2, 3, 4])
    turns: int = 50
    # False/default: restore the immutable seed before playing. True: validate
    # and continue the current game beside stars_exe for `turns` more turns.
    play_on: bool = False
    checkpoints: list[int] = field(default_factory=lambda: [10, 25, 50])
    host_password: str | None = None
    keep_every_turn: bool = True
    turn_archive_enabled: bool = True
    turn_archive_include_logs: bool = True
    turn_archive_json_index: bool = True
    # Merge each current M file into its cumulative H file in native Python.
    auto_merge_history: bool = True
    # Fail closed if post-merge semantic coverage cannot be proven.
    require_history_sync: bool = True
    stop_on_missing_x: bool = True
    host_timeout_seconds: int = 180
    host_poll_seconds: float = 0.5
    host_settle_seconds: float = 1.5
    prevent_parallel_stars: bool = True
    # Click OK/Yes on message boxes owned by this Stars! executable during hosting.
    dismiss_stars_dialogs: bool = True
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
    # None => all configured players; [] => observer output instead of detailed
    # per-player console logs.
    console_player_logs: list[int] | None = None
    # Mutual Stars! Friend groups. Every seat in a group is allied with every
    # other seat in that group. Example [[1, 2, 3, 4]] makes our four AI seats
    # one alliance. Expanded into allied_pairs when the config is loaded.
    allied_groups: list[list[int]] = field(default_factory=list)
    # Reciprocal Friend relationships. Example [[1, 2]] => P1 <-> P2.
    # Pairs listed here are kept in addition to every pair implied by allied_groups.
    allied_pairs: list[list[int]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.allied_pairs = expand_alliances(self.allied_groups, self.allied_pairs)
    personas: dict[str, str] = field(default_factory=lambda: {
        "1": "Balanced", "2": "Expansionist", "3": "Balanced", "4": "Balanced",
    })


@dataclass
class TurnExecution:
    """Outcome of one attempted native host generation."""

    turn: int
    player_order_files: list[str]
    host_returncode: int | None
    year_before: int | None
    year_after: int | None
    checkpoint_written: bool
    success: bool
    message: str


def expand_alliances(
    groups: list[list[int]] | None,
    pairs: list[list[int]] | None,
) -> list[list[int]]:
    """Return each unique Friend pair from alliance groups and explicit pairs.

    A group of seats is fully connected: ``[1, 2, 3]`` becomes ``[1, 2]``,
    ``[1, 3]``, and ``[2, 3]``. The native order file still stores one Stars!
    Friend declaration per other seat, which is the game's ally setting.
    """

    seen: set[tuple[int, int]] = set()
    expanded: list[list[int]] = []

    def add(left: int, right: int) -> None:
        first, second = int(left), int(right)
        if not 1 <= first <= 16 or not 1 <= second <= 16:
            raise ValueError(f"Ally player ids must be from 1 to 16, got {first} and {second}.")
        if first == second:
            return
        key = (first, second) if first < second else (second, first)
        if key in seen:
            return
        seen.add(key)
        expanded.append([key[0], key[1]])

    for group in groups or []:
        seats = [int(seat) for seat in group]
        for index, seat in enumerate(seats):
            for other in seats[index + 1:]:
                add(seat, other)
    for pair in pairs or []:
        if len(pair) != 2:
            raise ValueError(f"An allied pair must contain two player ids, got {pair}.")
        add(pair[0], pair[1])
    return expanded


class SeedValidationError(RuntimeError):
    """The immutable starting game is unsafe or incomplete."""


@dataclass(frozen=True)
class ValidatedSeedGame:
    """Native files proven consistent before a fresh run is staged."""

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
    """Native files proven consistent before a play-on run continues."""

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
