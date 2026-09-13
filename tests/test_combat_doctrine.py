
from dataclasses import dataclass, field

from stars_ai.combat_doctrine import (
    WeaponDoctrine,
    ModernizationDecision,
    calculate_offensive_rating,
    evaluate_ship_design,
    evaluate_fleet,
    compare_militaries,
    choose_modernization_plan,
    infer_research_gaps,
)
from stars_ai.design_legality import ComponentRef
from stars_ai.ship_design_synth import (
    _combat_design_offensive_rating,
    _combat_weapon_slot_rating,
    _refit_combat_support_components,
    _role_component_choice_key,
)
from stars_ai.starsapi_items import BEAM, ELECTRICAL, ENGINE, MECHANICAL, TORPEDO, component_spec, stock_hulls

@dataclass
class Slot:
    category: int
    item_id: int
    count: int

@dataclass
class Design:
    name: str
    hull_id: int
    mass: int
    armor: int
    slots: list
    resource_cost: int
    tech_generation: float

@dataclass
class Fleet:
    owner_id: int
    ship_counts: dict
    mass: int = 0

def test_beam_vs_torpedo_doctrine():
    beamship = Design("Beam Cruiser", 10, 120, 100, [Slot(16,4,4), Slot(4,2,2)], 150, 8)
    torpship = Design("Torp Cruiser", 10, 130, 110, [Slot(32,4,4), Slot(8,2,2)], 160, 8)
    b=evaluate_ship_design(beamship)
    t=evaluate_ship_design(torpship)
    assert b.doctrine == WeaponDoctrine.BEAM
    assert t.doctrine == WeaponDoctrine.TORPEDO
    assert b.beam_strength > 0 and b.torpedo_strength == 0
    assert t.torpedo_strength > 0 and t.beam_strength == 0


def test_offensive_rating_uses_stock_weapon_and_hull_initiative():
    scout = Design(
        "Laser Scout", 4, 8, 20,
        [Slot(1, 1, 1), Slot(16, 0, 1)],
        20, 1,
    )
    rating = calculate_offensive_rating(scout)

    # Laser: 10 damage, one-point range and weapon initiative 9.  A scout
    # adds initiative 1; at the target-neutral midpoint a beam averages 95%.
    assert rating.expected_damage_per_volley == 9.5
    assert rating.average_firing_initiative == 10
    assert rating.battle_movement == 0.25
    assert rating.display_rating == 11


def test_offensive_rating_applies_battle_computer_to_torpedo_accuracy_and_initiative():
    baseline = Design(
        "Alpha Scout", 4, 8, 20,
        [Slot(1, 1, 1), Slot(32, 0, 1)],
        20, 1,
    )
    computer = Design(
        "Computed Alpha Scout", 4, 8, 20,
        [Slot(1, 1, 1), Slot(32, 0, 1), Slot(2048, 5, 1)],
        26, 1,
    )
    plain = calculate_offensive_rating(baseline)
    enhanced = calculate_offensive_rating(computer)

    # Alpha torpedo: 35% hit chance, plus 1/8 shield collateral on a miss.
    assert plain.expected_damage_per_volley == 5 * (0.35 + 0.65 / 8)
    assert enhanced.expected_damage_per_volley == 5 * (0.48 + 0.52 / 8)
    assert enhanced.average_firing_initiative == plain.average_firing_initiative + 1
    assert enhanced.rating > plain.rating


def test_offensive_rating_rewards_published_battle_movement_and_starbase_range():
    slow = Design(
        "Slow Laser Scout", 4, 8, 20,
        [Slot(1, 1, 1), Slot(16, 0, 1)],
        20, 1,
    )
    fast = Design(
        "Fast Laser Scout", 4, 8, 20,
        [Slot(1, 7, 1), Slot(16, 0, 1)],
        35, 1,
    )
    starbase = {
        "name": "Armed Orbital Fort", "hull_id": 32, "mass": 0,
        "slots": [{"category": 16, "item_id": 0, "count": 1}],
    }
    stationary_ship = {
        "name": "Stationary Laser Scout", "hull_id": 4, "mass": 8,
        "battle_movement": 0,
        "slots": [{"category": 16, "item_id": 0, "count": 1}],
    }

    assert calculate_offensive_rating(fast).battle_movement > calculate_offensive_rating(slow).battle_movement
    assert calculate_offensive_rating(fast).rating > calculate_offensive_rating(slow).rating
    assert calculate_offensive_rating(starbase).average_weapon_range == 2
    assert calculate_offensive_rating(starbase).rating > calculate_offensive_rating(stationary_ship).rating


def test_combat_ship_fitting_uses_battle_rating_for_mark_iv_vs_epsilon():
    # Stock rows: Mark IV is a 66-damage, range-2, initiative-7 beam;
    # Epsilon is a 48-damage, range-5, initiative-2, 65%-accuracy torpedo.
    # The fitting comparison must see the beam's markedly higher expected
    # anti-ship volley, rather than treating native item ID as a quality tier.
    hull = stock_hulls()[5]  # Frigate
    mark_iv = component_spec(BEAM, 11)
    epsilon = component_spec(TORPEDO, 3)

    mark_iv_score = _combat_weapon_slot_rating(hull, 1, mark_iv, 1)
    epsilon_score = _combat_weapon_slot_rating(hull, 1, epsilon, 1)

    assert mark_iv_score > epsilon_score
    slot = next(slot for slot in hull.slots if slot.allows(BEAM) and slot.allows(TORPEDO))
    assert max(
        [epsilon, mark_iv],
        key=lambda component: _role_component_choice_key("combat", hull, 1, slot, component),
    ) == mark_iv


def test_torpedo_design_adds_stacked_battle_computers_when_the_whole_ship_improves():
    hull = stock_hulls()[9]  # Battleship: dedicated Electrical slots plus torpedo banks.
    epsilon = component_spec(TORPEDO, 3)
    components = [
        epsilon,
        component_spec(ELECTRICAL, 5),  # Battle Computer
        component_spec(ELECTRICAL, 6),  # Battle Super Computer
        component_spec(ELECTRICAL, 7),  # Battle Nexus
        component_spec(MECHANICAL, 7),  # Maneuvering Jet
        component_spec(MECHANICAL, 8),  # Overthruster
    ]
    slots = [ComponentRef(0, 0, 0) for _ in hull.slots]
    slots[0] = ComponentRef(ENGINE, 1, hull.engine_count)
    for slot in hull.slots[1:]:
        if slot.allows(TORPEDO) and not slot.allows(ELECTRICAL):
            slots[slot.index] = ComponentRef(TORPEDO, 3, slot.capacity)

    baseline = _combat_design_offensive_rating(hull, slots)
    fitted = _refit_combat_support_components(hull, slots, components)

    assert sum(slot.count for slot in fitted if slot.category == TORPEDO) > 0
    assert sum(
        slot.count for slot in fitted
        if slot.category == ELECTRICAL and slot.item_id in {5, 6, 7}
    ) > 0
    assert _combat_design_offensive_rating(hull, fitted) > baseline

def test_obsolete_design_detected_against_better_enemy():
    old=Design("Old DD", 8, 60, 30, [Slot(16,1,2)], 90, 5)
    enemy=Design("Enemy BB", 20, 250, 300, [Slot(16,8,8),Slot(4,6,6)], 260, 12)
    ep=evaluate_ship_design(enemy)
    op=evaluate_ship_design(old, enemy_profiles=[ep])
    assert op.obsolete_score > 0.4
    assert ep.combat_value_per_resource > op.combat_value_per_resource

def test_fleet_value_uses_design_counts_not_raw_ship_count():
    small=Design("Small",4,30,20,[Slot(16,1,1)],40,5)
    big=Design("Big",20,250,300,[Slot(16,8,8),Slot(4,5,5)],250,10)
    sp=evaluate_ship_design(small)
    bp=evaluate_ship_design(big)
    ours=evaluate_fleet(Fleet(1,{0:60}),{0:sp})
    enemy=evaluate_fleet(Fleet(2,{0:20}),{0:bp})
    assert ours.ship_count > enemy.ship_count
    assert enemy.effective_combat_value > ours.effective_combat_value

def test_tech_then_rebuild_when_fringe_loss_is_affordable():
    old=Design("Old",8,60,20,[Slot(16,1,2)],90,5)
    new=Design("New",20,180,220,[Slot(16,6,6),Slot(4,4,4)],170,10)
    enemy=Design("Enemy",20,200,250,[Slot(16,7,7),Slot(4,4,4)],190,11)
    op=evaluate_ship_design(old)
    np=evaluate_ship_design(new)
    ep=evaluate_ship_design(enemy)
    ourf=evaluate_fleet(Fleet(1,{0:20}),{0:op})
    enf=evaluate_fleet(Fleet(2,{0:14}),{0:ep})
    a=compare_militaries([ourf],[enf])
    plan=choose_modernization_plan(
        a,
        current_design_efficiency=op.combat_value_per_resource,
        prospective_design_efficiency=np.combat_value_per_resource,
        turns_to_upgrade=3,
        territorial_risk=0.20,
        sacrifice_budget=0.35,
        core_at_risk=False,
    )
    assert plan.decision in (ModernizationDecision.TECH_THEN_REBUILD, ModernizationDecision.HOLD_AND_TECH)

def test_core_risk_prevents_casual_sacrifice():
    old=Design("Old",8,60,20,[Slot(16,1,2)],90,5)
    enemy=Design("Enemy",20,200,250,[Slot(16,7,7),Slot(4,4,4)],190,11)
    op=evaluate_ship_design(old); ep=evaluate_ship_design(enemy)
    a=compare_militaries(
        [evaluate_fleet(Fleet(1,{0:20}),{0:op})],
        [evaluate_fleet(Fleet(2,{0:20}),{0:ep})]
    )
    plan=choose_modernization_plan(
        a,
        current_design_efficiency=0.4,
        prospective_design_efficiency=1.0,
        turns_to_upgrade=3,
        territorial_risk=0.85,
        sacrifice_budget=0.20,
        core_at_risk=True,
    )
    assert plan.decision in (ModernizationDecision.FIGHT_NOW, ModernizationDecision.RETREAT_AND_PRESERVE)

def test_research_gap_prioritizes_weapons_and_construction():
    ranked=infer_research_gaps(
        {"weapons":5,"construction":5,"electronics":6,"propulsion":7,"energy":6,"biotech":4},
        {"weapons":10,"construction":9,"electronics":8,"propulsion":8,"energy":7,"biotech":4},
    )
    fields=[x[0] for x in ranked[:3]]
    assert "weapons" in fields
    assert "construction" in fields
