
from __future__ import annotations
from dataclasses import dataclass
from typing import Any

@dataclass
class InvasionPlan:
    planet_id: int
    action: str
    required_population: int
    bomber_priority: float
    escort_priority: float
    holdability: float
    reason: str

HOLD_HABITABILITY = 55


def worth_securing(planet: Any) -> bool:
    """A capture is worth the colonists and the garrison only on a strong world.

    Habitability of 55 or a large factory base can pay the cost back. A lesser
    enemy colony is a raid: destroy the hold and leave.
    """

    habitability = getattr(planet, "habitability", None)
    if habitability is not None and float(habitability) >= HOLD_HABITABILITY:
        return True
    return int(getattr(planet, "factories", 0) or 0) >= 80


def bomber_construction_authorized(plan: Any, territorial: Any) -> bool:
    """Bombers are an invasion tool, not a standing fleet.

    A hostile colony inside claimed space is not an invasion. Construction
    waits until strategic growth and a source-world campaign is authorized.
    """

    phase = str(getattr(plan, "phase", "") or "")
    if phase != "opportunistic_war":
        return False
    escalation = getattr(territorial, "source_escalation", None)
    return bool(getattr(escalation, "invasion_authorized", False))


def bomber_campaign_targets(state: Any, plan: Any, territorial: Any) -> list:
    """Planets a bomber can actually be sent to bomb.

    Authorization without a planet is not a target. Bombers stay off the
    queue until a source world or a hostile colony is named.
    """

    if not bomber_construction_authorized(plan, territorial):
        return []
    from .territorial_defense import hostile_colonies_inside_claimed_area

    found: list = []
    seen: set[int] = set()
    escalation = getattr(territorial, "source_escalation", None)
    source_id = getattr(escalation, "source_planet_id", None)
    if source_id is not None:
        source = next(
            (planet for planet in state.planets if int(planet.id) == int(source_id)),
            None,
        )
        owner = getattr(source, "owner", None) if source is not None else None
        if source is not None and owner is not None and int(owner) != int(state.player_id):
            found.append(source)
            seen.add(int(source.id))
    for planet in hostile_colonies_inside_claimed_area(state, plan):
        if int(planet.id) in seen:
            continue
        found.append(planet)
        seen.add(int(planet.id))
    return found


def empire_ready_to_press_border(state: Any, plan: Any) -> bool:
    """Border attacks wait until nearby growth is used up.

    Early expansion and a frontier that still has open claims stay on
    resources, defense, and colonies. Saturation is what releases the fleets.
    """

    if plan is None or str(getattr(plan, "phase", "") or "") == "early_expansion":
        return False
    assessment = getattr(plan, "phase_assessment", {}) or {}
    try:
        saturation = float(assessment.get("expansion_saturation") or 0.0)
    except (TypeError, ValueError):
        saturation = 0.0
    if saturation < 0.55:
        return False
    from .colony_planner import colony_support_distance
    from .util import distance

    owned = [planet for planet in state.planets if planet.owner == state.player_id]
    if len(owned) < 8:
        return False
    limit = colony_support_distance(state)
    open_claims = 0
    for planet in state.planets:
        if planet.owner is not None:
            continue
        nearest = min(distance(planet.position, world.position) for world in owned)
        if nearest > limit:
            continue
        # Unobserved space is a scouting problem. Counting it as an open
        # claim kept every seat out of border attacks for 100 turns while
        # enemy colonies were already on the frontier.
        if not planet.observed or int(planet.habitability or 0) <= 0:
            continue
        open_claims += 1
    return open_claims <= 3


def paper_tiger_colony(state: Any, planet: Any) -> bool:
    """An Alternate Reality colony is the orbital fort, not a defended world.

    The fort is empty of ground defenses and population. A single armed scout
    can destroy it. A real defense count or a warship on the world is not
    this case.
    """

    from .population_invasion import is_alternate_reality_target

    native = getattr(planet, "native", {}) or {}
    orbital = bool((native.get("starbase_capabilities") or {}).get("is_orbital_fort"))
    if not is_alternate_reality_target(state, planet) and not orbital:
        return False
    if int(getattr(planet, "defenses", 0) or 0) > 10:
        return False
    owner = int(getattr(planet, "owner", -1) or -1)
    for fleet in getattr(state, "fleets", []) or []:
        if int(getattr(fleet, "owner", -1) or -1) != owner:
            continue
        same_world = (
            abs(float(fleet.position.x) - float(planet.position.x)) <= 1.0
            and abs(float(fleet.position.y) - float(planet.position.y)) <= 1.0
        )
        if not same_world:
            continue
        role = str(getattr(fleet, "role", "") or "").casefold()
        if role in {"combat", "escort", "warship", "bomber"} or float(getattr(fleet, "combat_power", 0.0) or 0.0) > 0.0:
            return False
    return True


def weak_undefended_colony(state: Any, planet: Any) -> bool:
    """Whether a foreign colony is small enough for a colonist capture.

    Allies are filtered by the caller. Alternate Reality worlds never reach
    this check because population-capture discovery already excludes them.
    A world with a real defense count, or a fleet sitting on it, is left for
    a later combat plan instead of a transport drop.
    """

    defenses = int(getattr(planet, "defenses", 0) or 0)
    population = int(getattr(planet, "population", 0) or 0)
    if defenses > 10 or population <= 0 or population > 100_000:
        return False
    owner = int(getattr(planet, "owner", -1) or -1)
    enemy_power = 0.0
    our_power = 0.0
    for fleet in getattr(state, "fleets", []) or []:
        power = float(getattr(fleet, "combat_power", 0.0) or 0.0)
        if int(getattr(fleet, "owner", -1)) == int(getattr(state, "player_id", -1)):
            our_power += max(0.0, power)
            continue
        if int(getattr(fleet, "owner", -1)) != owner:
            continue
        same_world = (
            abs(float(fleet.position.x) - float(planet.position.x)) <= 1.0
            and abs(float(fleet.position.y) - float(planet.position.y)) <= 1.0
        )
        if same_world:
            enemy_power += max(1.0, power)
    if enemy_power > 0.0:
        return False
    # The colonist packet is the force against an empty colony. Give the
    # capture a nominal local advantage so habitability can qualify it.
    assessed = plan_invasion(
        planet,
        our_local_strength=max(our_power, 40.0),
        enemy_local_strength=max(1.0, float(defenses)),
        transport_capacity=max(100, population * 4),
    )
    return assessed.action == "CAPTURE"


def plan_invasion(planet: Any, *, our_local_strength: float, enemy_local_strength: float, transport_capacity: int) -> InvasionPlan:
    pop=int(getattr(planet,"population",0) or 0)
    defs=int(getattr(planet,"defenses",0) or 0)
    factories=int(getattr(planet,"factories",0) or 0)
    mines=int(getattr(planet,"mines",0) or 0)
    native=getattr(planet,"native",{}) or {}
    strategic=float(native.get("strategic_value",0.5))
    hab=float(getattr(planet,"habitability",0) or 0)/100.0
    infrastructure=min(1.0,(factories+mines)/500)
    strength_ratio=our_local_strength/max(1.0,enemy_local_strength)
    hold=min(1.0,0.4*min(1,strength_ratio/1.3)+0.35*strategic+0.25*hab)
    required=min(transport_capacity,max(100, int(pop*(1.1 + defs/100.0))))
    if hab<0.10 and strategic<0.45:
        action="BOMB_OR_BYPASS"
    elif hold>=0.60 and (hab>=0.30 or infrastructure>=0.5):
        action="CAPTURE"
    elif strategic>=0.75:
        action="NEUTRALIZE"
    else:
        action="BYPASS"
    bomber=min(1.0,0.35+defs/100.0+pop/1000000.0)
    escort=min(1.0,0.4+enemy_local_strength/max(1.0,our_local_strength+enemy_local_strength))
    return InvasionPlan(planet.id,action,required,bomber,escort,hold,
        f"Capture value balances habitability/infrastructure/strategic value against defenses, troop lift, and ability to hold the world.")
