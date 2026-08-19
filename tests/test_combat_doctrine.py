
from dataclasses import dataclass, field

from stars_ai.combat_doctrine import (
    WeaponDoctrine,
    ModernizationDecision,
    evaluate_ship_design,
    evaluate_fleet,
    compare_militaries,
    choose_modernization_plan,
    infer_research_gaps,
)

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
