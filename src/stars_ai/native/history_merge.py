"""Native Stars! player-history (H) maintenance.

Stars! normally updates ``.h#`` files when a player opens the corresponding
``.m#`` file in the client.  Headless autohost never performs that client-side
step, so discoveries can disappear from the cumulative history.  This module
implements the relevant merge directly against decrypted native blocks.

The implementation is intentionally fail-closed: it builds and validates a
complete replacement beside the live H file, preserves an audit copy, and only
then atomically replaces the live file.  M files are read-only inputs.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any
import hashlib
import os
import shutil
import tempfile

from stars_ai.adapters.stars_native import NativeBlock, parse_file_header, read_blocks
from stars_ai.native.design import DesignRecord, parse_design
from stars_ai.native.x_writer import _encode_blocks


class HistoryMergeError(RuntimeError):
    """A native H/M pair could not be merged without risking history damage."""


def _u16(data: bytes, offset: int = 0) -> int:
    return int.from_bytes(data[offset:offset + 2], "little")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _need(data: bytes, offset: int, length: int, label: str) -> None:
    if offset < 0 or length < 0 or offset + length > len(data):
        raise HistoryMergeError(
            f"Truncated {label}: need bytes {offset}:{offset + length}, "
            f"payload has {len(data)}"
        )


def _variable_integer_length(code: int) -> int:
    return (0, 1, 2, 4)[code & 3]


@dataclass(frozen=True)
class _PlanetObservation:
    planet_id: int
    owner: int  # native zero-based owner; -1 means unowned
    flags: int
    environment_core: bytes | None
    estimates: bytes
    starbase_design: int | None
    turn: int
    source: str

    @property
    def has_environment(self) -> bool:
        return self.environment_core is not None

    @property
    def has_starbase(self) -> bool:
        return self.starbase_design is not None


def _parse_planet_observation(
    block: NativeBlock,
    *,
    default_turn: int,
    source: str,
) -> _PlanetObservation:
    if block.type_id not in (13, 14):
        raise HistoryMergeError(f"Expected Planet/PartialPlanet block, got {block.type_id}")
    data = block.data
    _need(data, 0, 4, f"planet block from {source}")
    planet_id = data[0] | ((data[1] & 0x07) << 8)
    owner = (data[1] & 0xF8) >> 3
    if owner == 31:
        owner = -1
    elif owner >= 16:
        raise HistoryMergeError(f"Planet {planet_id} has invalid owner {owner} in {source}")

    flags = _u16(data, 2)
    in_use = bool(flags & 0x0004)
    has_environment_flag = bool(flags & 0x0002)
    history_bit = bool(flags & 0x0001)
    has_surface = bool(flags & 0x2000)
    has_installations = bool(flags & 0x0800)
    terraformed = bool(flags & 0x0400)
    has_starbase = bool(flags & 0x0200)
    has_route = bool(flags & 0x4000)
    can_see_environment = has_environment_flag or (
        (has_surface or in_use) and not history_bit
    )

    offset = 4
    environment_core: bytes | None = None
    estimates = b"\0\0"
    if can_see_environment:
        _need(data, offset, 1, f"planet {planet_id} environment prefix")
        prefix = data[offset]
        if prefix & 0xC0:
            raise HistoryMergeError(
                f"Planet {planet_id} has unsupported environment prefix 0x{prefix:02x}"
            )
        prefix_length = 1 + ((prefix & 0x30) >> 4) + ((prefix & 0x0C) >> 2) + (prefix & 3)
        core_length = prefix_length + 6 + (3 if terraformed else 0)
        _need(data, offset, core_length, f"planet {planet_id} environment")
        environment_core = bytes(data[offset:offset + core_length])
        offset += core_length
        if owner >= 0:
            _need(data, offset, 2, f"planet {planet_id} population estimates")
            estimates = bytes(data[offset:offset + 2])
            offset += 2

    if has_surface:
        _need(data, offset, 1, f"planet {planet_id} surface-mineral lengths")
        lengths = data[offset]
        offset += 1
        payload_length = sum(
            _variable_integer_length((lengths >> shift) & 3)
            for shift in (0, 2, 4, 6)
        )
        _need(data, offset, payload_length, f"planet {planet_id} surface minerals")
        offset += payload_length

    if has_installations:
        _need(data, offset, 8, f"planet {planet_id} installations")
        offset += 8

    starbase_design = None
    if has_starbase:
        starbase_length = 1 if block.type_id == 14 else 4
        _need(data, offset, starbase_length, f"planet {planet_id} starbase")
        starbase_design = data[offset] & 0x0F
        offset += starbase_length

    if has_route and block.type_id == 13:
        _need(data, offset, 2, f"planet {planet_id} route")
        offset += 2

    remaining = len(data) - offset
    if remaining not in (0, 2):
        raise HistoryMergeError(
            f"Planet {planet_id} has {remaining} unsupported trailing byte(s) in {source}"
        )
    turn = _u16(data, offset) if remaining == 2 else int(default_turn)
    return _PlanetObservation(
        planet_id=planet_id,
        owner=owner,
        flags=flags,
        environment_core=environment_core,
        estimates=estimates,
        starbase_design=starbase_design,
        turn=turn,
        source=source,
    )


def _planet_history_block(observation: _PlanetObservation) -> NativeBlock:
    owner_bits = 31 if observation.owner < 0 else observation.owner
    data = bytearray(
        (
            observation.planet_id & 0xFF,
            ((observation.planet_id >> 8) & 0x07) | ((owner_bits & 0x1F) << 3),
        )
    )

    # Preserve harmless identity/status bits, explicitly remove live-only
    # minerals/installations, and mark the block as an H-file observation.
    flags = observation.flags
    flags &= ~(0x2000 | 0x1000 | 0x0800 | 0x0004)
    flags |= 0x0100 | 0x0001
    if observation.has_environment:
        flags |= 0x0002
    else:
        flags &= ~0x0002
    if observation.has_starbase:
        flags |= 0x0200
    else:
        flags &= ~0x0200
    data += int(flags).to_bytes(2, "little")

    if observation.environment_core is not None:
        data += observation.environment_core
        if observation.owner >= 0:
            data += observation.estimates
    if observation.starbase_design is not None:
        data.append(observation.starbase_design & 0x0F)
    data += int(observation.turn).to_bytes(2, "little")
    return NativeBlock(14, len(data), bytes(data))


def _merge_planet_observations(
    observations: list[_PlanetObservation],
) -> _PlanetObservation:
    if not observations:
        raise HistoryMergeError("Cannot merge an empty planet observation set")

    latest: _PlanetObservation | None = None
    latest_environment: _PlanetObservation | None = None
    latest_starbase: _PlanetObservation | None = None
    for candidate in observations:
        if candidate.has_starbase and (
            latest_starbase is None or candidate.turn > latest_starbase.turn
        ):
            latest_starbase = candidate
        if candidate.has_environment and (
            latest_environment is None or candidate.turn > latest_environment.turn
        ):
            latest_environment = candidate

        if latest is None or candidate.turn > latest.turn:
            latest = candidate
            continue
        if candidate.turn == latest.turn:
            if candidate.flags & 0x8000 and not latest.flags & 0x8000:
                latest = replace(latest, flags=latest.flags | 0x8000)
            if candidate.has_environment and not latest.has_environment:
                latest = candidate
            elif (
                candidate.has_environment == latest.has_environment
                and candidate.has_starbase
                and not latest.has_starbase
            ):
                latest = candidate

    assert latest is not None
    result = latest
    if not latest.has_environment and latest_environment is not None:
        result = replace(
            latest_environment,
            owner=latest.owner,
            flags=(latest_environment.flags & ~0x8200) | (latest.flags & 0x8200),
            starbase_design=latest.starbase_design,
            # The owner/starbase state is current even when its environmental
            # values came from an older rich observation.
            turn=latest.turn,
            source=f"{latest.source}+environment:{latest_environment.source}",
        )
    if (
        not result.has_starbase
        and latest_starbase is not None
        and latest_starbase.turn >= result.turn
    ):
        result = replace(
            result,
            flags=result.flags | 0x0200,
            starbase_design=latest_starbase.starbase_design,
            turn=max(result.turn, latest_starbase.turn),
            source=f"{result.source}+starbase:{latest_starbase.source}",
        )
    return result


@dataclass(frozen=True)
class _PlayerInfo:
    player: int
    ship_count: int
    planet_count: int
    fleet_count: int
    starbase_count: int
    logo: int
    byte7: int
    names: bytes
    block: NativeBlock
    from_m: bool


def _parse_player_info(block: NativeBlock, *, from_m: bool) -> _PlayerInfo:
    data = block.data
    _need(data, 0, 8, "player block")
    player = data[0]
    if player >= 16:
        raise HistoryMergeError(f"Invalid player number {player} in PLAYER block")
    full = bool(data[6] & 0x04)
    names_offset = 8
    if full:
        _need(data, 0, 0x71, f"full player {player + 1} block")
        relation_length = data[0x70]
        names_offset = 0x71 + relation_length
        _need(data, names_offset, 0, f"player {player + 1} names")
    names = bytes(data[names_offset:])
    if len(names) < 2:
        raise HistoryMergeError(f"Player {player + 1} has malformed encoded names")
    return _PlayerInfo(
        player=player,
        ship_count=data[1],
        planet_count=data[2] | ((data[3] & 3) << 8),
        fleet_count=data[4] | ((data[5] & 3) << 8),
        starbase_count=(data[5] >> 4) & 0x0F,
        logo=data[6] >> 3,
        byte7=data[7],
        names=names,
        block=block,
        from_m=from_m,
    )


def _history_player_block(
    info: _PlayerInfo,
    *,
    planets: int,
    fleets: int,
    ship_designs: int,
    starbase_designs: int,
) -> NativeBlock:
    if not (0 <= planets <= 0x3FF and 0 <= fleets <= 0x3FF):
        raise HistoryMergeError(f"Player {info.player + 1} H counters exceed 10 bits")
    if not (0 <= ship_designs <= 0xFF and 0 <= starbase_designs <= 0x0F):
        raise HistoryMergeError(f"Player {info.player + 1} design counters are invalid")
    data = bytearray(8)
    data[0] = info.player
    data[1] = ship_designs
    data[2] = planets & 0xFF
    data[3] = (planets >> 8) & 3
    data[4] = fleets & 0xFF
    data[5] = ((starbase_designs & 0x0F) << 4) | ((fleets >> 8) & 3)
    # H player records contain summary data, not the full private race payload.
    data[6] = ((info.logo & 0x1F) << 3) | 0x03
    data[7] = info.byte7
    data += info.names
    return NativeBlock(6, len(data), bytes(data))


@dataclass(frozen=True)
class _DesignInfo:
    owner: int
    record: DesignRecord
    block: NativeBlock
    from_m: bool

    @property
    def key(self) -> tuple[int, bool, int]:
        return (self.owner, self.record.is_starbase, self.record.design_number)


def _design_mass(record: DesignRecord) -> int | None:
    if not record.is_full_design:
        return record.partial_mass
    if record.is_starbase:
        return 0
    try:
        from stars_ai.fuel_planner import design_fuel_profile

        return int(design_fuel_profile(record).dry_mass)
    except Exception:
        return None


def _design_name_bytes(block: NativeBlock, record: DesignRecord) -> bytes:
    offset = 17 + 4 * int(record.slot_count or 0) if record.is_full_design else 6
    return bytes(block.data[offset:])


def _designs_compatible(left: _DesignInfo, right: _DesignInfo) -> bool:
    a, b = left.record, right.record
    if (
        a.is_starbase != b.is_starbase
        or a.is_transferred != b.is_transferred
        or a.design_number != b.design_number
        or a.hull_id != b.hull_id
        or a.pic != b.pic
    ):
        return False
    mass_a, mass_b = _design_mass(a), _design_mass(b)
    if mass_a is None or mass_b is None or mass_a != mass_b:
        return False
    if a.is_full_design and b.is_full_design:
        slots_a = [(s.category, s.item_id, s.count) for s in a.slots]
        slots_b = [(s.category, s.item_id, s.count) for s in b.slots]
        return (
            a.armor == b.armor
            and a.slot_count == b.slot_count
            and slots_a == slots_b
            and _design_name_bytes(left.block, a) == _design_name_bytes(right.block, b)
        )
    return True


def _file_player_and_designs(
    blocks: list[NativeBlock],
    *,
    from_m: bool,
) -> tuple[dict[int, _PlayerInfo], dict[tuple[int, bool, int], _DesignInfo]]:
    players: dict[int, _PlayerInfo] = {}
    ship_owners: list[int] = []
    starbase_owners: list[int] = []
    for block in blocks:
        if block.type_id != 6:
            continue
        info = _parse_player_info(block, from_m=from_m)
        players[info.player] = info
        ship_owners.extend([info.player] * info.ship_count)
        starbase_owners.extend([info.player] * info.starbase_count)

    ship_index = 0
    starbase_index = 0
    designs: dict[tuple[int, bool, int], _DesignInfo] = {}
    for block in blocks:
        if block.type_id != 26:
            continue
        try:
            record = parse_design(block.data)
        except Exception as exc:
            raise HistoryMergeError(f"Cannot decode DESIGN block: {exc}") from exc
        if record.is_starbase:
            if starbase_index >= len(starbase_owners):
                raise HistoryMergeError("More starbase DESIGN blocks than PLAYER counters declare")
            owner = starbase_owners[starbase_index]
            starbase_index += 1
        else:
            if ship_index >= len(ship_owners):
                raise HistoryMergeError("More ship DESIGN blocks than PLAYER counters declare")
            owner = ship_owners[ship_index]
            ship_index += 1
        info = _DesignInfo(owner, record, block, from_m)
        designs[info.key] = info
    if ship_index != len(ship_owners) or starbase_index != len(starbase_owners):
        raise HistoryMergeError(
            "PLAYER design counters do not match the number of DESIGN blocks"
        )
    return players, designs


def _select_designs(
    h_designs: dict[tuple[int, bool, int], _DesignInfo],
    m_designs: dict[tuple[int, bool, int], _DesignInfo],
    *,
    observer: int,
) -> dict[tuple[int, bool, int], _DesignInfo]:
    selected: dict[tuple[int, bool, int], _DesignInfo] = {}
    for key in sorted(set(h_designs) | set(m_designs)):
        if key[0] == observer:
            continue
        old = h_designs.get(key)
        current = m_designs.get(key)
        if current is None:
            choice = old
        elif current.record.is_full_design or old is None:
            choice = current
        elif old.record.is_full_design and _designs_compatible(old, current):
            choice = old
        else:
            choice = current
        if choice is not None:
            selected[key] = choice
    return selected


def _header_blocks(blocks: list[NativeBlock]) -> list[NativeBlock]:
    return [block for block in blocks if block.type_id == 8]


def _validate_input_headers(
    h_path: Path,
    h_blocks: list[NativeBlock],
    m_path: Path,
    m_blocks: list[NativeBlock],
) -> tuple[Any, Any]:
    h_headers = _header_blocks(h_blocks)
    m_headers = _header_blocks(m_blocks)
    if len(h_headers) != 1:
        raise HistoryMergeError(f"{h_path.name} must contain exactly one FileHeader")
    if not m_headers:
        raise HistoryMergeError(f"{m_path.name} has no FileHeader")
    h_header = parse_file_header(h_headers[0].data)
    m_header = parse_file_header(m_headers[-1].data)
    if h_header.file_type != 4:
        raise HistoryMergeError(f"{h_path.name} is file type {h_header.file_type}, not H type 4")
    if m_header.file_type != 3:
        raise HistoryMergeError(f"{m_path.name} is file type {m_header.file_type}, not M type 3")
    if h_header.game_id != m_header.game_id:
        raise HistoryMergeError(
            f"Game ID mismatch: {h_path.name}={h_header.game_id}, "
            f"{m_path.name}={m_header.game_id}"
        )
    if h_header.player_index != m_header.player_index:
        raise HistoryMergeError(
            f"Player mismatch: {h_path.name}=P{h_header.player_index + 1}, "
            f"{m_path.name}=P{m_header.player_index + 1}"
        )
    for raw in m_headers:
        header = parse_file_header(raw.data)
        if (
            header.file_type != 3
            or header.game_id != h_header.game_id
            or header.player_index != h_header.player_index
        ):
            raise HistoryMergeError(f"{m_path.name} contains an incompatible FileHeader")
    return h_header, m_header


def _planet_observations_by_id(
    blocks: list[NativeBlock],
    *,
    default_turn: int,
    source: str,
) -> dict[int, list[_PlanetObservation]]:
    result: dict[int, list[_PlanetObservation]] = {}
    active_turn = int(default_turn)
    for block in blocks:
        if block.type_id == 8:
            active_turn = int(parse_file_header(block.data).turn)
        elif block.type_id in (13, 14):
            observation = _parse_planet_observation(
                block, default_turn=active_turn, source=source
            )
            result.setdefault(observation.planet_id, []).append(observation)
    return result


def _history_structure(
    blocks: list[NativeBlock],
    *,
    source: str,
) -> tuple[NativeBlock, NativeBlock, list[NativeBlock]]:
    if not blocks or blocks[0].type_id != 8:
        raise HistoryMergeError(f"{source} does not begin with FileHeader")
    if len(blocks) < 3 or blocks[1].type_id != 32 or len(blocks[1].data) != 4:
        raise HistoryMergeError(f"{source} does not contain a valid H Counters block")
    declared = _u16(blocks[1].data)
    if len(blocks) < 2 + declared:
        raise HistoryMergeError(f"{source} declares {declared} planets but is truncated")
    for index in range(2, 2 + declared):
        if blocks[index].type_id != 14:
            raise HistoryMergeError(
                f"{source} planet #{index - 1} is block type {blocks[index].type_id}, not 14"
            )
    if any(block.type_id in (13, 14) for block in blocks[2 + declared:]):
        raise HistoryMergeError(f"{source} has planet blocks outside its declared planet section")
    return blocks[0], blocks[1], blocks[2 + declared:]


def _build_merged_history(
    h_path: Path,
    h_blocks: list[NativeBlock],
    m_path: Path,
    m_blocks: list[NativeBlock],
) -> tuple[bytes, dict[str, Any]]:
    h_header, m_header = _validate_input_headers(h_path, h_blocks, m_path, m_blocks)
    header_block, counter_block, tail = _history_structure(h_blocks, source=h_path.name)
    h_planets = _planet_observations_by_id(
        h_blocks, default_turn=h_header.turn, source=h_path.name
    )
    m_planets = _planet_observations_by_id(
        m_blocks, default_turn=m_header.turn, source=m_path.name
    )

    merged: dict[int, _PlanetObservation] = {}
    for planet_id in sorted(set(h_planets) | set(m_planets)):
        merged[planet_id] = _merge_planet_observations(
            h_planets.get(planet_id, []) + m_planets.get(planet_id, [])
        )

    h_players, h_designs = _file_player_and_designs(h_blocks, from_m=False)
    m_players, m_designs = _file_player_and_designs(m_blocks, from_m=True)
    observer = int(h_header.player_index)
    designs = _select_designs(h_designs, m_designs, observer=observer)

    # A foreign starbase reference is only safe if its design is also present
    # in the history.  The observing player's own design lives in the M file.
    omitted_starbase_planets: list[int] = []
    for planet_id, planet in list(merged.items()):
        if (
            planet.has_starbase
            and planet.owner >= 0
            and planet.owner != observer
            and (planet.owner, True, int(planet.starbase_design)) not in designs
        ):
            merged[planet_id] = replace(
                planet,
                flags=planet.flags & ~0x0200,
                starbase_design=None,
            )
            omitted_starbase_planets.append(planet_id)

    planet_blocks = [_planet_history_block(merged[key]) for key in sorted(merged)]
    counters = bytearray(counter_block.data)
    counters[0:2] = len(planet_blocks).to_bytes(2, "little")

    players: dict[int, _PlayerInfo] = {}
    for player in sorted(set(h_players) | set(m_players)):
        if player == observer:
            continue
        players[player] = m_players.get(player) or h_players[player]

    player_blocks: list[NativeBlock] = []
    for player, info in sorted(players.items()):
        ship_count = sum(1 for key in designs if key[0] == player and not key[1])
        starbase_count = sum(1 for key in designs if key[0] == player and key[1])
        old_fleets = h_players[player].fleet_count if player in h_players else 0
        owned_planets = sum(1 for planet in merged.values() if planet.owner == player)
        player_blocks.append(
            _history_player_block(
                info,
                planets=owned_planets,
                fleets=old_fleets,
                ship_designs=ship_count,
                starbase_designs=starbase_count,
            )
        )

    design_blocks = [
        designs[key].block
        for key in sorted(designs, key=lambda value: (value[1], value[0], value[2]))
    ]
    rebuilt_tail: list[NativeBlock] = []
    inserted = False
    for block in tail:
        if block.type_id in (6, 13, 14, 26):
            continue
        if not inserted and block.type_id in (45, 0):
            rebuilt_tail.extend(player_blocks)
            rebuilt_tail.extend(design_blocks)
            inserted = True
        rebuilt_tail.append(block)
    if not inserted:
        rebuilt_tail.extend(player_blocks)
        rebuilt_tail.extend(design_blocks)

    output_blocks = [NativeBlock(32, 4, bytes(counters)), *planet_blocks, *rebuilt_tail]
    payload = _encode_blocks(header_block, output_blocks)
    details = {
        "game_id": int(h_header.game_id),
        "player_id": observer + 1,
        "m_turn": int(m_header.turn),
        "h_planets_before": len(h_planets),
        "m_planets": len(m_planets),
        "h_planets_after": len(planet_blocks),
        "new_planet_ids": sorted(set(m_planets) - set(h_planets)),
        "updated_planet_ids": sorted(
            planet_id
            for planet_id in set(m_planets) & set(h_planets)
            if max(item.turn for item in m_planets[planet_id])
            >= max(item.turn for item in h_planets[planet_id])
        ),
        "omitted_foreign_starbase_planet_ids": omitted_starbase_planets,
        "enemy_players": len(player_blocks),
        "enemy_designs": len(design_blocks),
    }
    return payload, details


def inspect_history_coverage(h_path: str | Path, m_path: str | Path) -> dict[str, Any]:
    """Return semantic H coverage for the current M planet observations."""
    h_path = Path(h_path)
    m_path = Path(m_path)
    h_header, h_blocks, _ = read_blocks(h_path)
    m_header, m_blocks, _ = read_blocks(m_path)
    _validate_input_headers(h_path, h_blocks, m_path, m_blocks)
    _history_structure(h_blocks, source=h_path.name)
    h_planets = _planet_observations_by_id(
        h_blocks, default_turn=h_header.turn, source=h_path.name
    )
    m_planets = _planet_observations_by_id(
        m_blocks, default_turn=m_header.turn, source=m_path.name
    )
    missing: list[int] = []
    stale: list[dict[str, int]] = []
    for planet_id, current in sorted(m_planets.items()):
        historical = h_planets.get(planet_id)
        if not historical:
            missing.append(planet_id)
            continue
        current_turn = max(item.turn for item in current)
        history_turn = max(item.turn for item in historical)
        if history_turn < current_turn:
            stale.append(
                {
                    "planet_id": planet_id,
                    "history_turn": history_turn,
                    "m_turn": current_turn,
                }
            )
    return {
        "ready": not missing and not stale,
        "h_planets": len(h_planets),
        "m_planets": len(m_planets),
        "missing_planet_ids": missing,
        "stale_planets": stale,
        "m_turn": int(m_header.turn),
    }


def merge_history_file(
    h_path: str | Path,
    m_path: str | Path,
    *,
    backup_path: str | Path | None = None,
    merged_copy_path: str | Path | None = None,
) -> dict[str, Any]:
    """Transactionally merge one current M file into its cumulative H file."""
    h_path = Path(h_path)
    m_path = Path(m_path)
    if not h_path.is_file() or not m_path.is_file():
        raise HistoryMergeError(f"Missing H/M merge input: {h_path}, {m_path}")
    h_before = _sha256(h_path)
    h_original_bytes = h_path.read_bytes()
    m_before = _sha256(m_path)
    _, h_blocks, _ = read_blocks(h_path)
    _, m_blocks, _ = read_blocks(m_path)
    payload, details = _build_merged_history(h_path, h_blocks, m_path, m_blocks)

    backup = Path(backup_path) if backup_path is not None else None
    merged_copy = Path(merged_copy_path) if merged_copy_path is not None else None
    if backup is not None:
        backup.parent.mkdir(parents=True, exist_ok=True)
        if backup.exists():
            raise HistoryMergeError(f"Refusing to overwrite history audit backup: {backup}")
    if merged_copy is not None:
        merged_copy.parent.mkdir(parents=True, exist_ok=True)
        if merged_copy.exists():
            raise HistoryMergeError(f"Refusing to overwrite merged history audit: {merged_copy}")

    temporary: Path | None = None
    installed = False
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".{h_path.name}.merge-",
            suffix=".tmp",
            dir=h_path.parent,
            delete=False,
        ) as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
            temporary = Path(stream.name)

        # Parse the exact bytes that will be installed and verify semantic
        # coverage before touching the live H file.
        candidate_header, candidate_blocks, _ = read_blocks(temporary)
        original_header = parse_file_header(h_blocks[0].data)
        if (
            candidate_header.file_type != 4
            or candidate_header.game_id != original_header.game_id
            or candidate_header.player_index != original_header.player_index
        ):
            raise HistoryMergeError("Candidate H header identity changed during merge")
        _history_structure(candidate_blocks, source=temporary.name)

        candidate_planets = _planet_observations_by_id(
            candidate_blocks,
            default_turn=candidate_header.turn,
            source=temporary.name,
        )
        old_planets = _planet_observations_by_id(
            h_blocks,
            default_turn=original_header.turn,
            source=h_path.name,
        )
        lost = sorted(set(old_planets) - set(candidate_planets))
        if lost:
            raise HistoryMergeError(f"Candidate H would lose planet IDs: {lost}")
        for planet_id, old in old_planets.items():
            if max(item.turn for item in candidate_planets[planet_id]) < max(
                item.turn for item in old
            ):
                raise HistoryMergeError(
                    f"Candidate H would regress planet {planet_id}'s observation turn"
                )

        if _sha256(m_path) != m_before:
            raise HistoryMergeError(f"{m_path.name} changed while its history was being merged")
        if backup is not None:
            shutil.copy2(h_path, backup)
        os.replace(temporary, h_path)
        temporary = None
        installed = True
        if merged_copy is not None:
            shutil.copy2(h_path, merged_copy)

        coverage = inspect_history_coverage(h_path, m_path)
        if not coverage["ready"]:
            raise HistoryMergeError(
                f"Installed H failed current-M coverage validation: {coverage}"
            )
        if _sha256(m_path) != m_before:
            raise HistoryMergeError(f"{m_path.name} changed during final validation")
        h_after = _sha256(h_path)
        return {
            "status": "MERGED" if h_after != h_before else "VALIDATED_IDEMPOTENT",
            "h_path": str(h_path),
            "m_path": str(m_path),
            "backup_path": str(backup) if backup is not None else None,
            "merged_copy_path": str(merged_copy) if merged_copy is not None else None,
            "h_sha256_before": h_before,
            "h_sha256_after": h_after,
            "m_sha256_before": m_before,
            "m_sha256_after": _sha256(m_path),
            "m_unchanged": _sha256(m_path) == m_before,
            "coverage": coverage,
            **details,
        }
    except Exception as exc:
        if temporary is not None and temporary.exists():
            temporary.unlink()
        if installed:
            rollback: Path | None = None
            try:
                with tempfile.NamedTemporaryFile(
                    mode="wb",
                    prefix=f".{h_path.name}.rollback-",
                    suffix=".tmp",
                    dir=h_path.parent,
                    delete=False,
                ) as stream:
                    stream.write(h_original_bytes)
                    stream.flush()
                    os.fsync(stream.fileno())
                    rollback = Path(stream.name)
                os.replace(rollback, h_path)
                rollback = None
            except Exception as rollback_exc:
                if rollback is not None and rollback.exists():
                    rollback.unlink()
                raise HistoryMergeError(
                    f"History merge failed ({exc}) and atomic rollback also failed: "
                    f"{rollback_exc}"
                ) from rollback_exc
        raise
