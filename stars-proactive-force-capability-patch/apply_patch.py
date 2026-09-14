#!/usr/bin/env python3
from pathlib import Path

ROOT = Path.cwd()
DESIGN = ROOT / "src/stars_ai/design_development.py"
TERRITORY = ROOT / "src/stars_ai/territorial_defense.py"
PRODUCTION = ROOT / "src/stars_ai/objective_production.py"
TEST = ROOT / "tests/test_proactive_force_capabilities.py"

def replace_once(text, old, new, label):
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)

def insert_before_once(text, marker, addition, label):
    count = text.count(marker)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one marker, found {count}")
    return text.replace(marker, addition + marker, 1)

# territorial_defense.py
territory = TERRITORY.read_text(encoding="utf-8")

helper_marker = "\ndef assess_territorial_defense(state: Any, plan: Any = None) -> TerritorialDefenseAssessment:\n"
helper = r'''
def hostile_colonies_inside_claimed_area(state: Any, plan: Any = None) -> tuple[Any, ...]:
    # Capability signal only; this does not authorize an attack.
    owned=[planet for planet in state.planets if planet.owner == state.player_id]
    if not owned:
        return ()
    radius=_claim_radius(plan)
    out=[]
    for planet in state.planets:
        owner=getattr(planet,"owner",None)
        if owner is None or int(owner)==int(state.player_id):
            continue
        if not bool(getattr(planet,"observed",False)):
            continue
        if _friendly_owner(state,plan,int(owner)):
            continue
        native=getattr(planet,"native",{}) or {}
        recently_lost=bool(native.get("recently_lost_by_player"))
        view=(getattr(plan,"diplomacy",{}) or {}).get(int(owner),{}) if plan else {}
        attitude=str(view.get("attitude","neutral") or "neutral").casefold()
        action=str((view.get("conflict",{}) or {}).get("recommended_action","coexist") or "coexist").casefold()
        hostile=recently_lost or attitude=="hostile" or action=="oppose"
        if not hostile:
            continue
        separation=min(distance(planet.position,anchor.position) for anchor in owned)
        if separation <= radius:
            out.append(planet)
    return tuple(sorted(out,key=lambda planet:int(planet.id)))


'''
if "def hostile_colonies_inside_claimed_area(" not in territory:
    territory = insert_before_once(territory, helper_marker, helper, "territorial hostile-colony helper")

territory = replace_once(
    territory,
    '            requires_minefield=severity >= 0.42,\n',
    '            requires_minefield=severity >= (0.32 if classification in {"transport", "armed_transport"} else 0.42),\n',
    "transport minefield threshold",
)

old_cover = r'''    violations.sort(key=lambda violation: (-violation.severity, violation.distance_ly, violation.fleet_id))
    covered = {
        int(violation.anchor_planet_id)
        for violation in violations
        if _anchor_has_our_minefield(
            state,
            next(planet for planet in owned if int(planet.id) == int(violation.anchor_planet_id)),
        )
    }
    uncovered = tuple(dict.fromkeys(
        int(violation.anchor_planet_id)
        for violation in violations
        if violation.requires_minefield and int(violation.anchor_planet_id) not in covered
    ))
    patrols = sum(1 for violation in violations if violation.requires_patrol)
    desired_minelayers = min(2, max(1, math.ceil(len(uncovered) / 2))) if uncovered else 0
    reason = (
        f"{len(violations)} non-friendly armed/transport territorial violation(s) inside the "
        f"{radius:.0f}-ly claim zone; patrol objectives={patrols}, uncovered minefield anchors={len(uncovered)}."
    )
'''
new_cover = r'''    violations.sort(key=lambda violation: (-violation.severity, violation.distance_ly, violation.fleet_id))

    # Pre-emptive edge deterrence. Significant hostile traffic just outside the
    # formal claim radius is where minefields have the greatest deterrent value.
    edge_anchor_ids=[]
    warning_radius=radius*1.35
    for fleet in state.fleets:
        owner=int(getattr(fleet,"owner",-1) or -1)
        if owner <= 0 or owner == int(state.player_id) or _friendly_owner(state,plan,owner):
            continue
        if _fleet_classification(fleet) is None:
            continue
        anchor=min(owned,key=lambda planet:distance(fleet.position,planet.position))
        separation=float(distance(fleet.position,anchor.position))
        if radius < separation <= warning_radius:
            edge_anchor_ids.append(int(anchor.id))
    for colony in hostile_colonies_inside_claimed_area(state,plan):
        anchor=min(owned,key=lambda planet:distance(colony.position,planet.position))
        edge_anchor_ids.append(int(anchor.id))

    candidate_anchor_ids=list(dict.fromkeys(
        [
            int(violation.anchor_planet_id)
            for violation in violations
            if violation.requires_minefield
        ] + edge_anchor_ids
    ))
    covered={
        planet_id
        for planet_id in candidate_anchor_ids
        if _anchor_has_our_minefield(
            state,
            next(planet for planet in owned if int(planet.id)==int(planet_id)),
        )
    }
    uncovered=tuple(
        planet_id for planet_id in candidate_anchor_ids
        if planet_id not in covered
    )
    patrols=sum(1 for violation in violations if violation.requires_patrol)
    desired_minelayers=(
        min(4,max(1,math.ceil(len(uncovered)*0.75)))
        if uncovered else 0
    )
    reason=(
        f"{len(violations)} non-friendly armed/transport territorial violation(s) inside the "
        f"{radius:.0f}-ly claim zone; patrol objectives={patrols}, "
        f"proactive edge-pressure anchors={len(set(edge_anchor_ids))}, "
        f"uncovered minefield anchors={len(uncovered)}."
    )
'''
territory = replace_once(territory, old_cover, new_cover, "territorial proactive minefield block")
TERRITORY.write_text(territory, encoding="utf-8")

# design_development.py
design = DESIGN.read_text(encoding="utf-8")
design = replace_once(
    design,
    "from .territorial_defense import assess_territorial_defense\n",
    "from .territorial_defense import assess_territorial_defense, hostile_colonies_inside_claimed_area\n"
    "from .population_invasion import population_capture_targets, is_recently_lost_world\n",
    "design development imports",
)

capture_insert_marker = '    colonies=_designs(state,"colony")\n'
capture_insert = r'''    # Pre-emptive population-capture transport capability.
    capture_targets=[
        target for target in population_capture_targets(state)
        if int(target.required_kt)>=800
    ]
    if capture_targets:
        needed_capture_capacity=min(int(target.required_kt) for target in capture_targets)
        best_existing_capture=max(
            (int(d.get("cargo_capacity",0) or 0) for d in freighters),
            default=0,
        )
        if best_existing_capture < needed_capture_capacity:
            capture_hull=best_legal_ship_hull(
                state,"freighter",minimum_cargo_kt=needed_capture_capacity,
            )
            if capture_hull is not None and int(capture_hull.cargo)>=needed_capture_capacity:
                recapture=False
                for target in capture_targets:
                    planet=next(
                        (p for p in state.planets if int(p.id)==int(target.planet_id)),
                        None,
                    )
                    if planet is not None and is_recently_lost_world(planet):
                        recapture=True
                        break
                proposals.append(DesignProposal(
                    role="freighter", name="Capture Transport AI", is_starbase=False,
                    desired_hull_id=capture_hull.hull_id,
                    desired_hull_name=capture_hull.name,
                    desired_engine=preferred_engine,
                    objectives=[
                        f"carry at least {needed_capture_capacity} kT population for a decisive capture packet",
                        "support guarded population capture without several small transport departures",
                        "retain fuel-safe range and remain useful as bulk transport outside capture operations",
                        "prefer a single legal large transport; validated fleet-merge groups remain a fallback for existing assets",
                    ],
                    reason=(
                        f"{len(capture_targets)} observed population-capture target(s) require 800+ kT; "
                        f"minimum required capacity={needed_capture_capacity} kT, best existing freighter design="
                        f"{best_existing_capture} kT. Seed capture capability before the assault planner needs it."
                    ),
                    priority=204 if recapture else 196,
                ))
        state.native["capture_transport_capability_demand"]={
            "observed_targets":len(capture_targets),
            "minimum_required_kt":needed_capture_capacity,
            "best_existing_design_kt":best_existing_capture,
            "large_transport_design_required":best_existing_capture < needed_capture_capacity,
            "policy":"Seed an 800+ kT capture transport when needed; execution retains breeder/range/diplomacy/AR safeguards.",
        }

'''
if "capture_transport_capability_demand" not in design:
    design = insert_before_once(design, capture_insert_marker, capture_insert, "capture transport design demand")

old_combat = r'''    combat=_designs(state,"combat")
    combat_hull=best_legal_ship_hull(state,"combat")
    if (
        combat and max(int(state.tech.weapons),int(state.tech.energy))>=6
        and combat_hull is not None
    ):
        newest_turn=max((int(d.get("turn_designed",0) or 0) for d in combat),default=0)
        if state.year-2400-newest_turn>=5:
            proposals.append(DesignProposal(
                role="combat", name="Fleet Combat Mk II", is_starbase=False,
                desired_hull_id=combat_hull.hull_id,
                desired_hull_name=combat_hull.name,
                desired_engine=preferred_engine,
                objectives=["use newly unlocked Weapons/Energy technology","serve shared combat and escort duty","balance weapons, shields/armor, and mass"],
                reason=f"Weapons={state.tech.weapons}, Energy={state.tech.energy}; existing combat architecture is at least five turns old.",
                priority=190,
            ))
'''
new_combat = r'''    # Bomber capability for hostile colonies inside claimed territory.
    hostile_colonies=hostile_colonies_inside_claimed_area(state,plan)
    bombers=_designs(state,"bomber")
    if hostile_colonies and not bombers:
        bomber_hull=best_legal_ship_hull(state,"bomber")
        if bomber_hull is not None:
            proposals.append(DesignProposal(
                role="bomber", name="Territorial Strike Bomber", is_starbase=False,
                desired_hull_id=bomber_hull.hull_id,
                desired_hull_name=bomber_hull.name,
                desired_engine=preferred_engine,
                objectives=[
                    "carry researched bombs for hostile colonies inside claimed territory",
                    "support deliberate colony suppression/removal after military authorization",
                    "retain enough protection and range to operate with an escort fleet",
                ],
                reason=(
                    f"{len(hostile_colonies)} observed hostile/opposed colony world(s) lie inside the "
                    f"{territorial.claim_radius_ly:.0f}-ly claimed area; no bomber design exists. "
                    "Seed bombing capability before a territorial clearing campaign is ordered."
                ),
                priority=194,
            ))

    combat=_designs(state,"combat")
    combat_hull=best_legal_ship_hull(state,"combat")
    border_pressure=bool(
        territorial.needs_response
        or territorial.needs_minefield
        or territorial.desired_patrols>0
    )
    if not combat and border_pressure and combat_hull is not None:
        proposals.append(DesignProposal(
            role="combat", name="Border Combat Seed", is_starbase=False,
            desired_hull_id=combat_hull.hull_id,
            desired_hull_name=combat_hull.name,
            desired_engine=preferred_engine,
            objectives=[
                "create the empire's first real combat/escort architecture before a border incident becomes an emergency",
                "carry researched weapons plus legal protection rather than relying on armed scouts or utility hulls",
                "serve patrol, escort, and territorial interception roles from one seed design",
            ],
            reason=(
                f"{territorial.reason} Border pressure exists but the empire has no combat design. "
                "Create an explicit seed combat design instead of waiting for an existing combat family to upgrade."
            ),
            priority=202,
        ))
    elif (
        combat and max(int(state.tech.weapons),int(state.tech.energy))>=6
        and combat_hull is not None
    ):
        newest_turn=max((int(d.get("turn_designed",0) or 0) for d in combat),default=0)
        if state.year-2400-newest_turn>=5:
            proposals.append(DesignProposal(
                role="combat", name="Fleet Combat Mk II", is_starbase=False,
                desired_hull_id=combat_hull.hull_id,
                desired_hull_name=combat_hull.name,
                desired_engine=preferred_engine,
                objectives=["use newly unlocked Weapons/Energy technology","serve shared combat and escort duty","balance weapons, shields/armor, and mass"],
                reason=f"Weapons={state.tech.weapons}, Energy={state.tech.energy}; existing combat architecture is at least five turns old.",
                priority=190,
            ))

    state.native["proactive_force_capability_demand"]={
        "border_pressure":border_pressure,
        "combat_seed_required":bool(border_pressure and not combat),
        "hostile_colonies_in_claimed_area":[int(planet.id) for planet in hostile_colonies],
        "bomber_design_required":bool(hostile_colonies and not bombers),
        "desired_minelayers":int(territorial.desired_minelayers),
        "minefield_anchor_ids":list(territorial.uncovered_minefield_anchor_ids),
    }
'''
design = replace_once(design, old_combat, new_combat, "seed combat and bomber design block")
DESIGN.write_text(design, encoding="utf-8")

# objective_production.py
# Anchor-based so this applies cleanly after the previous remote-mining patch.
production = PRODUCTION.read_text(encoding="utf-8")
production = replace_once(
    production,
    "from .territorial_defense import assess_territorial_defense\n",
    "from .territorial_defense import assess_territorial_defense, hostile_colonies_inside_claimed_area\n",
    "objective production territorial import",
)

capture_gate = '''    if (
        freighter_build_authorized
        and
        needed_capture_capacity
'''
capture_gate_new = '''    if (
        needed_capture_capacity
'''
if capture_gate in production:
    production = replace_once(production, capture_gate, capture_gate_new, "capture build authorization")
elif capture_gate_new not in production:
    raise RuntimeError("capture build authorization: capture block not found")

bomber_marker = '''    # Escort is a mission assigned to the shared combat architecture, not a
'''
bomber_block = r'''    # Build a limited territorial bomber element after the design exists.
    hostile_colonies=hostile_colonies_inside_claimed_area(state,plan)
    bomber_assets=_role_count(ds,counts,'bomber')
    desired_bombers=min(6,max(2,2*len(hostile_colonies))) if hostile_colonies else 0
    bomber_gap=max(0,desired_bombers-bomber_assets)
    bomber_design=_pick(state,'bomber')
    state.native["bomber_capability_demand"]={
        "target_planet_ids":[int(planet.id) for planet in hostile_colonies],
        "desired_bombers":desired_bombers,
        "available_or_queued":bomber_assets,
        "design_available":bool(bomber_design is not None),
        "policy":"Prepare a small escorted bombing capability only for hostile/opposed colonies inside claimed territory.",
    }
    if bomber_gap and bomber_design is not None:
        req.append(BuildRequest(
            'bomber',int(bomber_design['design_number']),bomber_design['name'],
            min(3,bomber_gap),184,
            f"{len(hostile_colonies)} hostile/opposed colony world(s) are inside the claimed area; "
            f"bombers available/queued={bomber_assets}, desired strike capability={desired_bombers}. "
            "Build a limited escorted bomber element; design capability was seeded separately."
        ))

'''
if 'state.native["bomber_capability_demand"]' not in production:
    production = insert_before_once(production, bomber_marker, bomber_block, "bomber production demand")

PRODUCTION.write_text(production, encoding="utf-8")

test_content = r'''from types import SimpleNamespace

from stars_ai.models import GameState, RaceProfile, Tech, Planet, Fleet, Position
from stars_ai.territorial_defense import assess_territorial_defense, hostile_colonies_inside_claimed_area


def _plan():
    return SimpleNamespace(
        defense_radius=100.0,
        diplomacy={
            2: {
                "attitude": "hostile",
                "native_relation": 0,
                "conflict": {"recommended_action": "oppose"},
            }
        },
        attack_strength_ratio=1.25,
        risk_tolerance=0.5,
    )


def _state():
    home=Planet(
        0,"Home",Position(0,0),owner=1,observed=True,habitability=100,
        population=400000,native={"is_homeworld":True},
    )
    enemy=Planet(
        1,"Enemy Edge",Position(90,0),owner=2,observed=True,habitability=70,
        population=50000,native={},
    )
    return GameState(
        "g",2425,1,RaceProfile(),Tech(),[home,enemy],[],
        native={"objects":[],"design_profiles":[],"production_by_planet":{}},
    )


def test_hostile_colony_inside_claimed_area_is_capability_signal():
    state=_state()
    targets=hostile_colonies_inside_claimed_area(state,_plan())
    assert [planet.id for planet in targets] == [1]


def test_neutral_colony_is_not_automatic_bomber_target():
    state=_state()
    plan=_plan()
    plan.diplomacy[2]["attitude"]="neutral"
    plan.diplomacy[2]["conflict"]["recommended_action"]="coexist"
    assert hostile_colonies_inside_claimed_area(state,plan) == ()


def test_significant_transport_just_outside_claim_radius_creates_edge_mine_demand():
    state=_state()
    state.planets[1].position=Position(400,0)
    state.fleets.append(Fleet(
        7,"Incoming Transport",2,Position(160,0),role="freighter",
        cargo_capacity=800,
        native={"cargo":{"population":800}},
    ))
    assessment=assess_territorial_defense(state,_plan())
    assert not assessment.needs_response
    assert assessment.needs_minefield
    assert assessment.desired_minelayers >= 1
    assert 0 in assessment.uncovered_minefield_anchor_ids


def test_transport_inside_claim_zone_gets_minefield_response():
    state=_state()
    state.planets[1].position=Position(400,0)
    state.fleets.append(Fleet(
        8,"Deep Transport",2,Position(120,0),role="freighter",
        cargo_capacity=800,
        native={"cargo":{"population":800}},
    ))
    assessment=assess_territorial_defense(state,_plan())
    assert assessment.violations
    assert assessment.violations[0].classification in {"transport","armed_transport"}
    assert assessment.violations[0].requires_minefield is True
'''
TEST.write_text(test_content, encoding="utf-8")

print("Applied proactive force capability patch.")
print("Changed:")
print(" - src/stars_ai/design_development.py")
print(" - src/stars_ai/territorial_defense.py")
print(" - src/stars_ai/objective_production.py")
print("Added:")
print(" - tests/test_proactive_force_capabilities.py")
print("")
print("Validate with:")
print("  python -m compileall -q src/stars_ai")
print("  pytest -q tests/test_proactive_force_capabilities.py")
print("  pytest -q")
