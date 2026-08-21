from stars_ai.models import Planet, Position
from stars_ai.objective_production import BuildRequest
from stars_ai.strategy.economy import _distribute_ship_builds


def test_empire_ship_request_is_split_across_operational_shipyards():
    home = Planet(0, "Home", Position(0, 0), owner=1, population=300_000, factories=500)
    hub = Planet(1, "Hub", Position(100, 0), owner=1, population=150_000, factories=300)
    request = BuildRequest("colony", 4, "Colonizer Mk II", 6, 200, "expansion race")

    assigned = _distribute_ship_builds([home, hub], [request], {})

    assert {item.design_name for queue in assigned.values() for item in queue} == {"Colonizer Mk II"}
    assert sum(item.quantity for queue in assigned.values() for item in queue) == 6
    assert all(sum(item.quantity for item in assigned[planet_id]) == 3 for planet_id in (0, 1))
