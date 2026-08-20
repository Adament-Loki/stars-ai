
from stars_ai.adapters.native_core_adapter import _estimate_habitability_from_homeworld
from stars_ai.models import *
from stars_ai.strategy.exploration import add_exploration_orders
from stars_ai.strategy.economy import add_economic_orders
from stars_ai.native.x_writer import _waypoint_add_block

class P:
    gravity=50
    temperature=50
    radiation=50

def test_homeworld_environment_estimate():
    assert _estimate_habitability_from_homeworld(P(),(50,50,50)) == 100

def test_colonize_waypoint_is_not_encoded_as_experimental_task():
    state=GameState("g",2400,1,RaceProfile(),Tech(),
        [Planet(4,"Green",Position(100,200),owner=None,observed=True,habitability=80)],[])
    b=_waypoint_add_block(state,{"fleet_id":3,"destination_planet_id":4,"warp":7,"mission":"colonize"})
    assert (b.data[10] & 0x0f) == 0

def test_early_colony_plan_generated():
    s=GameState("g",2400,1,RaceProfile(),Tech(),
      [Planet(0,"Home",Position(0,0),owner=1,habitability=100,population=500000),
       Planet(1,"Green",Position(50,0),owner=None,habitability=70,observed=True)],
      [Fleet(2,"Colony",1,Position(0,0),role="colony",speed=7)])
    o=OrderSet("g",2400,1)
    add_economic_orders(s,o,None)
    assert any(x.kind in ("move_fleet","colony_operation") and x.payload.get("mission")=="colonize" for x in o.orders)

def test_frontier_scout_gets_move():
    s=GameState("g",2400,1,RaceProfile(),Tech(),
      [Planet(0,"Home",Position(0,0),owner=1,observed=True),
       Planet(1,"Near",Position(20,0),observed=False),
       Planet(2,"Far",Position(100,0),observed=False)],
      [Fleet(1,"Scout",1,Position(0,0),role="scout",speed=9)])
    o=OrderSet("g",2400,1)
    add_exploration_orders(s,o,None)
    assert any(x.kind=="move_fleet" for x in o.orders)
