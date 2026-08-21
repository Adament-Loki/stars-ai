from stars_ai.models import GameState, Planet, Position, RaceProfile, Tech
from stars_ai.objective_production import _desired_scout_force, plan_objective_ship_builds
from stars_ai.scout_policy import custom_scout_missions, enemy_contact_summary


def _contact_state(*, mission=False):
    native={
        "design_profiles":[{
            "design_number":1,"name":"Long Range Scout","role":"scout",
            "dry_mass":25,"fuel_capacity":300,"engine_id":2,
        }],
        "production_by_planet":{},
    }
    if mission:
        native["custom_scout_missions"]=[{
            "id":"border-lane-7","kind":"border_recon","purpose":"watch the new border",
            "target_planet_id":1,"priority":133,
        }]
    return GameState(
        "contact",2412,1,RaceProfile(native={"lrts":["IFE"]}),Tech(propulsion=2),[
            Planet(0,"Home",Position(0,0),owner=1,observed=True,habitability=100,population=300_000,
                   native={"is_homeworld":True}),
            Planet(1,"Unknown",Position(80,0),owner=None,observed=False),
            # Contact is far enough away that it does not create an automatic
            # border mission; the second case below must be explicitly named.
            Planet(2,"Foreign",Position(700,0),owner=2,observed=True),
        ],[],native=native,
    )


def test_foreign_contact_keeps_expansion_race_scout_force():
    state=_contact_state()
    contact=enemy_contact_summary(state)
    assert contact["enemy_contact"] is True
    assert custom_scout_missions(state)==[]
    desired,reason=_desired_scout_force(state,None,0)
    assert desired==6
    assert "uncontested exploration and border intelligence" in reason
    requests=[r for r in plan_objective_ship_builds(state) if r.role=="scout"]
    assert len(requests)==1
    assert requests[0].quantity<=6
    assert state.native["scout_build_policy"]["classic_exploration_enabled"] is True
    assert state.native["scout_build_policy"]["contact_limited_scout_building"] is True


def test_foreign_contact_includes_named_custom_missions_in_scout_screen():
    state=_contact_state(mission=True)
    desired,reason=_desired_scout_force(state,None,0)
    assert desired==6
    assert "border-lane-7" in reason
    requests=[r for r in plan_objective_ship_builds(state) if r.role=="scout"]
    assert len(requests)==1
    assert "expansion-race scout screen" in requests[0].reason
