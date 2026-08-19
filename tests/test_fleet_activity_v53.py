
from types import SimpleNamespace
from stars_ai.models import GameState, RaceProfile, Tech, Planet, Fleet, Position, OrderSet
from stars_ai.strategy.exploration import add_exploration_orders
from stars_ai.windows_autohost import WindowsAutoHostConfig, _safe_cleanup_output
from pathlib import Path
import pytest

def _state():
    planets=[
        Planet(0,"Home",Position(0,0),owner=1,observed=True,habitability=100),
        Planet(1,"Near",Position(25,0),owner=None,observed=False),
        Planet(2,"Far",Position(250,0),owner=None,observed=False),
        Planet(3,"Near2",Position(30,15),owner=None,observed=False),
    ]
    fleets=[
        Fleet(0,"Scout",1,Position(0,0),role="scout",speed=7),
        Fleet(1,"Escort",1,Position(0,0),role="combat",speed=7),
    ]
    return GameState("g",2400,1,RaceProfile(),Tech(),planets,fleets)

def test_scout_prefers_local_unknown_over_far_frontier():
    s=_state(); o=OrderSet("g",2400,1)
    add_exploration_orders(s,o,None)
    scout=[x for x in o.orders if x.kind=="move_fleet" and x.payload["fleet_id"]==0][0]
    assert scout.payload["destination_planet_id"] in (1,3)
    assert scout.payload["destination_planet_id"] != 2

def test_idle_combat_can_do_short_range_early_recon():
    s=_state(); o=OrderSet("g",2400,1)
    add_exploration_orders(s,o,None)
    assert any(x.kind=="move_fleet" and x.payload["fleet_id"]==1 for x in o.orders)

def test_cleanup_removes_old_output_but_not_seed(tmp_path):
    seed=tmp_path/"seed"; root=tmp_path/"playtests"
    seed.mkdir(); root.mkdir()
    (seed/"GAME.xy").write_text("seed")
    (root/"old.log").write_text("old")
    _safe_cleanup_output(root,seed)
    assert seed.exists()
    assert (seed/"GAME.xy").exists()
    assert not (root/"old.log").exists()

def test_cleanup_refuses_seed_inside_output(tmp_path):
    root=tmp_path/"playtests"; seed=root/"seed"
    seed.mkdir(parents=True)
    with pytest.raises(RuntimeError):
        _safe_cleanup_output(root,seed)

def test_cleanup_default_enabled():
    cfg=WindowsAutoHostConfig(
        stars_exe="stars!.exe",seed_dir="seed",output_dir="playtests",game_name="GAME"
    )
    assert cfg.cleanup_output_on_start is True
