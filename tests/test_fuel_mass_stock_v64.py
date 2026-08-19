
from stars_ai.fuel_planner import stock_hull_fuel_specs
def test_stock_hulls():
    h=stock_hull_fuel_specs(); assert (h[4].base_mass,h[4].base_fuel)==(8,50); assert (h[21].base_mass,h[21].base_fuel)==(80,210)
