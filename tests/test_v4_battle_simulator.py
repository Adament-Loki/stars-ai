
from stars_ai.battle_simulator import *

def test_starbase_range_bonus_and_battle_runs():
    beam=Weapon(WeaponKind.BEAM,20,2,initiative=5,count=2)
    ship=BattleDesign("Cruiser",100,50,2,100,weapons=[beam])
    base=BattleDesign("Base",300,200,0,300,weapons=[Weapon(WeaponKind.BEAM,30,2,initiative=6,count=3)])
    out=simulate_battle(BattleSide("A",[BattleStack(ship,5)]),BattleSide("B",[BattleStack(base,1)],starbase=True))
    assert out.rounds<=16
    assert "starbase" in " ".join(out.notes).lower()

def test_chaff_is_attractive_to_missiles():
    missile=Weapon(WeaponKind.CAPITAL_MISSILE,100,4,accuracy=.8)
    chaff=BattleStack(BattleDesign("Chaff",5,0,2,10,boranium_cost=0,weapons=[Weapon(WeaponKind.BEAM,1,1)]),1)
    capital=BattleStack(BattleDesign("Capital",500,500,2,900,boranium_cost=100,weapons=[missile]),1)
    assert attractiveness(chaff,WeaponKind.CAPITAL_MISSILE) > attractiveness(capital,WeaponKind.CAPITAL_MISSILE)
