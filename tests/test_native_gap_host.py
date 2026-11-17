"""Disposable Stars! host checks for newly encoded orders.

Each case copies playtests/seed/humanplus, writes one order for player 1, and
runs starsjrc4.exe -g. A supported order advances 2400 to 2401 and leaves the
expected state in GAME.m1.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

try:
    import pytest
except ImportError:  # The host probe can run under plain Python.
    pytest = None

from stars_ai.adapters.native_core_adapter import NativeCoreTurnAdapter
from stars_ai.models import OrderSet
from stars_ai.native.player_state import PlayerState
from stars_ai.native.x_writer import write_native_orders

SEED = Path(__file__).resolve().parents[1] / "playtests" / "seed" / "humanplus"
EXE = SEED / "starsjrc4.exe"

if pytest is not None:
    pytestmark = pytest.mark.skipif(not EXE.is_file(), reason="Stars! seed executable is not present")


def _stage(tmp_path: Path) -> Path:
    game = tmp_path / "game"
    shutil.copytree(SEED, game)
    return game


def _load(game: Path):
    return NativeCoreTurnAdapter(xy_path=game / "GAME.xy").read_state(game / "GAME.m1", 1)


def _write(game: Path, state, kind: str, payload: dict) -> None:
    state.native["actual_player_relations"] = list(state.race.native.get("player_relations", []))
    orders = OrderSet(game_name=state.game_name, year=state.year, player_id=1)
    orders.add(kind, payload, "host probe", priority=100)
    template = game / "template.x1"
    shutil.copyfile(game / "GAME.x1", template)
    write_native_orders(
        game_state=state,
        orders=orders,
        template_x_path=template,
        output_x_path=game / "GAME.x1",
    )


def _host(game: Path) -> int:
    completed = subprocess.run(
        [str(game / "starsjrc4.exe"), "-g", "GAME.hst"],
        cwd=game,
        timeout=30,
        check=False,
    )
    return int(completed.returncode)


def _turn(game: Path) -> int:
    state = PlayerState.from_files(game / "GAME.m1", game / "GAME.xy")
    return int(state.header.get("turn") if isinstance(state.header, dict) else state.header.turn)


def test_enemy_relation_is_applied(tmp_path: Path):
    game = _stage(tmp_path)
    state = _load(game)
    _write(game, state, "set_player_relation", {"player_id": 2, "relation": "enemy"})
    assert _host(game) == 0
    native = PlayerState.from_files(game / "GAME.m1", game / "GAME.xy")
    relations = list(native.players[0].player_relations)
    assert int(native.header["turn"]) == 1
    assert len(relations) >= 2
    assert int(relations[1]) == 2


def test_scrap_removes_one_idle_fleet(tmp_path: Path):
    game = _stage(tmp_path)
    state = _load(game)
    candidate = next(
        fleet for fleet in state.fleets
        if fleet.owner == 1 and int((fleet.native or {}).get("waypoint_count", 0) or 0) == 1
    )
    before = {int(fleet.id) for fleet in state.fleets if fleet.owner == 1}
    _write(game, state, "scrap_fleet", {"fleet_id": int(candidate.id)})
    assert _host(game) == 0
    after = _load(game)
    assert int(after.year) == 2401
    remaining = {int(fleet.id) for fleet in after.fleets if fleet.owner == 1}
    assert int(candidate.id) in before
    assert int(candidate.id) not in remaining


def test_battle_plan_index_is_stored(tmp_path: Path):
    game = _stage(tmp_path)
    state = _load(game)
    fleet = next(fleet for fleet in state.fleets if fleet.owner == 1)
    _write(game, state, "set_battle_plan", {"fleet_id": int(fleet.id), "plan_index": 1})
    assert _host(game) == 0
    after = _load(game)
    assert int(after.year) == 2401
    updated = next(item for item in after.fleets if item.owner == 1 and int(item.id) == int(fleet.id))
    assert int((updated.native or {}).get("battle_plan")) == 1


def test_patrol_waypoint_task_is_seven(tmp_path: Path):
    game = _stage(tmp_path)
    state = _load(game)
    fleet = next(
        fleet for fleet in state.fleets
        if fleet.owner == 1 and fleet.destination_planet_id is None
    )
    owned = [planet for planet in state.planets if planet.owner == 1]
    here = min(owned, key=lambda planet: abs(planet.position.x - fleet.position.x) + abs(planet.position.y - fleet.position.y))
    target = next(planet for planet in state.planets if planet.owner != 1)
    _write(game, state, "move_fleet", {
        "fleet_id": int(fleet.id),
        "destination_planet_id": int(target.id),
        "warp": 6,
        "mission": "patrol",
    })
    assert _host(game) == 0
    after = _load(game)
    assert int(after.year) == 2401
    updated = next(item for item in after.fleets if item.owner == 1 and int(item.id) == int(fleet.id))
    assert int(updated.destination_task) == 7
    assert int(updated.destination_planet_id) == int(target.id)
    assert here.id != target.id


def test_research_only_can_be_turned_off(tmp_path: Path):
    game = _stage(tmp_path)
    state = _load(game)
    planet = next(planet for planet in state.planets if planet.owner == 1)
    _write(game, state, "set_planet_research_mode", {"planet_id": int(planet.id), "leftover_only": True})
    assert _host(game) == 0
    mid = _load(game)
    assert int(mid.year) == 2401
    mid_planet = next(item for item in mid.planets if int(item.id) == int(planet.id))
    assert (mid_planet.native or {}).get("contribute_only_leftover_resources_to_research") is True
    # The host consumed the X files. Restore a template from the generated
    # backup only for the header salt; write the OFF order against the new M.
    template = game / "GAME.x1"
    if not template.exists():
        shutil.copyfile(game / "backup" / "GAME.x1", template)
    _write(game, mid, "set_planet_research_mode", {"planet_id": int(planet.id), "leftover_only": False})
    assert _host(game) == 0
    after = _load(game)
    assert int(after.year) == 2402
    updated = next(item for item in after.planets if int(item.id) == int(planet.id))
    assert (updated.native or {}).get("contribute_only_leftover_resources_to_research") is False


def test_repeat_orders_record_is_consumed(tmp_path: Path):
    game = _stage(tmp_path)
    state = _load(game)
    fleet = next(item for item in state.fleets if item.owner == 1)
    before = PlayerState.from_files(game / "GAME.m1", game / "GAME.xy")
    before_raw = next(item.raw_hex for item in before.fleets if item.owner == 1 and item.fleet_id == int(fleet.id))
    _write(game, state, "repeat_fleet_orders", {"fleet_id": int(fleet.id), "enabled": True})
    assert _host(game) == 0
    after = PlayerState.from_files(game / "GAME.m1", game / "GAME.xy")
    assert int(after.header["turn"]) == 1
    updated = next(item for item in after.fleets if item.owner == 1 and item.fleet_id == int(fleet.id))
    assert updated.raw_hex != before_raw

