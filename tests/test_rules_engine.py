
from stars_ai.rules.turn_order import happens_before, can_react_same_turn
from stars_ai.rules.population import expected_population_growth, recommend_population_policy
from stars_ai.rules.fuel import fuel_required, max_range
from stars_ai.rules.gating import assess_overgate
from stars_ai.rules.minefields import MinefieldType, recommend_minefield_warp
from stars_ai.rules.packets import (
    PacketRaceClass, launch_year_distance, full_year_distance,
    packet_overhead_fraction, packet_decay, next_mass_after_decay,
)
from stars_ai.rules.salvage import battle_salvage_fraction, scrap_return, should_scrap_before_delete

def test_turn_order_packet_before_production_and_battle_after_production():
    assert happens_before("existing_packets_move_decay", "production_and_research")
    assert happens_before("production_and_research", "fleet_battles")
    assert not can_react_same_turn("existing_packets_move_decay", "production_and_research")

def test_population_breeder_policy():
    p = recommend_population_policy(0.40, good_export_world_available=True, factories_underutilized=False)
    assert p.export_recommended
    assert p.breeder_hold_fraction == 0.25

def test_population_growth_declines_with_crowding():
    a = expected_population_growth(100000, 0.15, 1.0, 0.20)
    b = expected_population_growth(100000, 0.15, 1.0, 0.80)
    assert a > b

def test_fuel_formula_and_range_are_inverses():
    fuel = fuel_required(mass_kt=330, distance_ly=100, fuel_usage_number=235, improved_fuel_efficiency=True)
    rng = max_range(fuel_mg=fuel, mass_kt=330, fuel_usage_number=235, improved_fuel_efficiency=True)
    assert abs(rng - 100) < 1e-9

def test_overgate_damage_zero_within_limits():
    a = assess_overgate(mass=200, distance=200, sending_mass_limit=300, receiving_mass_limit=300, sending_range_limit=500)
    assert a.total_damage_percent == 0
    assert a.legal_within_5x

def test_overgate_detects_risk():
    a = assess_overgate(mass=600, distance=700, sending_mass_limit=300, receiving_mass_limit=300, sending_range_limit=500)
    assert a.total_damage_percent > 0
    assert a.disappearance_risk_proxy > 0

def test_minefield_policy():
    r = recommend_minefield_warp(MinefieldType.SPEED_TRAP, distance_through_field=100, fleet_value=0.9, urgency=0.5)
    assert r.warp == 5

def test_packet_distance_and_overhead():
    assert launch_year_distance(10) == 50
    assert full_year_distance(10) == 100
    assert packet_overhead_fraction(PacketRaceClass.PACKET_PHYSICS) == 0
    assert packet_overhead_fraction(PacketRaceClass.OTHER) == 0.10
    assert packet_overhead_fraction(PacketRaceClass.INTERSTELLAR_TRAVELLER) == 0.20

def test_packet_decay_pp_is_half_other():
    other = packet_decay(race_class=PacketRaceClass.OTHER, firing_warp=10, driver_rating=7)
    pp = packet_decay(race_class=PacketRaceClass.PACKET_PHYSICS, firing_warp=10, driver_rating=7)
    assert other.yearly_fraction == 0.50
    assert pp.yearly_fraction == 0.25
    assert next_mass_after_decay(1000, pp) == 750

def test_salvage_and_scrap():
    assert abs(battle_salvage_fraction() - 1/3) < 1e-9
    assert scrap_return(at_starbase=True, ultimate_recycling=False).minerals_fraction == 0.80
    ur = scrap_return(at_starbase=True, ultimate_recycling=True)
    assert ur.minerals_fraction == 0.90
    assert ur.resources_fraction == 0.70
    assert should_scrap_before_delete(5)
