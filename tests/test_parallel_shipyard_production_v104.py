from stars_ai.models import Planet, Position
from stars_ai.objective_production import BuildRequest
from stars_ai.starbase_capabilities import starbase_capabilities
from stars_ai.strategy.economy import (
    _distribute_ship_builds,
    _preserved_ship_queue_items,
)


def test_empire_ship_request_is_split_across_operational_shipyards():
    home = Planet(0, "Home", Position(0, 0), owner=1, population=300_000, factories=500)
    hub = Planet(1, "Hub", Position(100, 0), owner=1, population=150_000, factories=300)
    request = BuildRequest("colony", 4, "Colonizer Mk II", 6, 200, "expansion race")

    assigned = _distribute_ship_builds([home, hub], [request], {})

    assert {item.design_name for queue in assigned.values() for item in queue} == {"Colonizer Mk II"}
    assert sum(item.quantity for queue in assigned.values() for item in queue) == 6
    assert all(sum(item.quantity for item in assigned[planet_id]) == 3 for planet_id in (0, 1))


def test_space_dock_does_not_receive_a_design_larger_than_200kt():
    dock=Planet(
        0,"Dock",Position(0,0),owner=1,population=300_000,
        native={"starbase_capabilities":starbase_capabilities(33)},
    )
    station=Planet(
        1,"Station",Position(100,0),owner=1,population=150_000,
        native={"starbase_capabilities":starbase_capabilities(34)},
    )
    request=BuildRequest("freighter",7,"Large Freighter",4,200,"logistics")

    assigned=_distribute_ship_builds(
        [dock,station],
        [request],
        {},
        design_masses_kt={7:201},
    )

    assert assigned[0]==[]
    assert sum(item.quantity for item in assigned[1])==4


def test_space_dock_can_receive_a_design_of_exactly_200kt():
    dock=Planet(
        0,"Dock",Position(0,0),owner=1,population=300_000,
        native={"starbase_capabilities":starbase_capabilities(33)},
    )
    station=Planet(
        1,"Station",Position(100,0),owner=1,population=150_000,
        native={"starbase_capabilities":starbase_capabilities(34)},
    )
    request=BuildRequest("freighter",7,"Medium Freighter",2,200,"logistics")

    assigned=_distribute_ship_builds(
        [dock,station],
        [request],
        {},
        design_masses_kt={7:200},
    )

    assert sum(item.quantity for item in assigned[0])==1
    assert sum(item.quantity for item in assigned[1])==1


def test_space_dock_drops_an_old_queue_item_that_is_too_large():
    state=type("State",(),{
        "native":{
            "design_profiles":[
                {
                    "design_number":7,
                    "dry_mass":201,
                    "name":"Large Freighter",
                    "role":"freighter",
                }
            ]
        }
    })()
    dock=Planet(
        0,"Dock",Position(0,0),owner=1,
        native={"starbase_capabilities":starbase_capabilities(33)},
    )

    preserved=_preserved_ship_queue_items(
        state,
        [{"item_type":4,"item_id":7,"count":2}],
        colony_launch_priority=False,
        shipyard=dock,
    )

    assert preserved==[]
