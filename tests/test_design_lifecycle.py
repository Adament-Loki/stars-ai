
from dataclasses import dataclass
from stars_ai.design_lifecycle import DesignDisposition, slot_pressure, assess_design_lifecycle, secondary_role_value

@dataclass
class D:
    design_number: int
    is_starbase: bool = False

def test_slot_limits_and_pressure():
    designs=[D(i) for i in range(15)] + [D(i,True) for i in range(9)]
    p=slot_pressure(designs)
    assert p.used_ship_slots==15
    assert p.free_ship_slots==1
    assert p.used_starbase_slots==9
    assert p.free_starbase_slots==1
    assert p.ship_pressure>0.7
    assert p.starbase_pressure>0.6

def test_old_ship_with_secondary_value_is_kept_when_slots_available():
    a=assess_design_lifecycle(design_slot=3,label="Legacy Destroyer",is_starbase=False,active_count=40,combat_efficiency=0.4,obsolete_score=0.75,secondary_role_value=0.75,uniqueness_value=0.10,replacement_value=0.45,slot_pressure_value=0.30)
    assert a.disposition==DesignDisposition.KEEP_SECOND_LINE

def test_old_ship_is_expendable_when_slot_pressure_high():
    a=assess_design_lifecycle(design_slot=3,label="Legacy Destroyer",is_starbase=False,active_count=40,combat_efficiency=0.4,obsolete_score=0.85,secondary_role_value=0.55,uniqueness_value=0.05,replacement_value=0.95,slot_pressure_value=0.95)
    assert a.disposition==DesignDisposition.EXPEND

def test_empty_obsolete_slot_is_recycled():
    a=assess_design_lifecycle(design_slot=7,label="Old Scout",is_starbase=False,active_count=0,combat_efficiency=0.2,obsolete_score=0.9,secondary_role_value=0.1,uniqueness_value=0.0,replacement_value=0.9,slot_pressure_value=0.9)
    assert a.disposition==DesignDisposition.RECYCLE

def test_unique_specialist_is_not_sacrificed_just_for_slot():
    a=assess_design_lifecycle(design_slot=9,label="Only Miner",is_starbase=False,active_count=12,combat_efficiency=0.0,obsolete_score=1.0,secondary_role_value=0.2,uniqueness_value=0.95,replacement_value=0.9,slot_pressure_value=0.9)
    assert a.disposition==DesignDisposition.KEEP_SPECIALIZED

def test_secondary_role_formula_rewards_starbase_screening():
    v=secondary_role_value(can_screen=True,useful_at_starbase=True,can_overmatch_unarmed=True,useful_as_escort=True)
    assert v>0.65
