from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from enum import Enum
import math
import re
from typing import Any

from .expansion_network import ExpansionNetworkSnapshot, evaluate_expansion_network
from .expansion_research import expansion_research_demands
from .colony_planner import colony_planet_is_eligible
from .models import GameState
from .persona import StrategicPlan
from .planet_economy import decode_race_economy, estimated_operating_resources, projected_population_growth
from .research_capabilities import ResearchCapability, stock_capability_catalog
from .research_cost import field_cost_label, field_cost_percent, research_turns, resources_to_requirements
from .research_preferences import preference_points
from .starsapi_items import (
    BEAM,
    ELECTRICAL,
    TORPEDO,
    MINING_ROBOT,
    component_spec,
    hull_available_to_race,
    hull_unlocked,
    prt_id,
    lrts,
    proven_available_components,
    stock_component_database,
    stock_hulls,
)
from .terraforming import evaluate_terraforming
from .turn_state import mapping as turn_mapping
from .util import distance


FIELDS = ("energy", "weapons", "propulsion", "construction", "electronics", "biotechnology")
EXPANSION_CATEGORIES = {"expansion", "logistics", "terraforming"}
# Battle hulls worth a construction program once the Large Freighter exists.
# Ordered by the stock construction gate: Cruiser 9, Galleon 11, Battleship 13,
# Nubian 26. The planner chases only the next locked one.
BATTLE_HULLS = ("Cruiser", "Galleon", "Battleship", "Nubian")
BATTLE_HULL_MIN_CONSTRUCTION = 8
ONION_NETWORK_TAG = "onion_network"
OPENING_GROWTH_TURNS = 10


class ResearchPosture(str, Enum):
    EXPANSION_FIRST = "EXPANSION_FIRST"
    TARGETED = "TARGETED"
    SPRINT = "SPRINT"
    MILITARY_EMERGENCY = "MILITARY_EMERGENCY"
    MATURE_SURGE = "MATURE_SURGE"
    RECOVERY = "RECOVERY"


@dataclass(frozen=True)
class ResearchDemand:
    capability: ResearchCapability
    need: float
    urgency: float
    utilization: float
    value: float
    military_emergency: bool = False
    explanation: str = ""
    production_readiness: float = 1.0
    production_follow_through: str = ""
    deadline_turns: int | None = None
    fieldable_before_deadline: bool = True


@dataclass(frozen=True)
class ResearchDecision:
    capability_id: str
    capability_name: str
    category: str
    posture: str
    current_field: str
    next_field: str
    allocation_percent: int
    score: float
    estimated_turns: int
    estimate_confidence: str
    requirements: dict[str, int]
    remaining_requirements: dict[str, int]
    contributor_planet_ids: tuple[int, ...]
    protected_production_planet_ids: tuple[int, ...]
    post_unlock_action: str
    reason: str
    candidate_scores: tuple[dict[str, Any], ...] = ()
    recently_unlocked: tuple[str, ...] = ()
    sprint_stalled: bool = False
    production_readiness: float = 1.0
    production_follow_through: str = ""
    expected_completion_year: int | None = None
    sprint_executable: bool = True
    sprint_blocked_reason: str | None = None
    expected_contributor_resources: dict[int, int] | None = None
    # Commitment is the planner/production contract.  Empty supports older
    # persisted decisions; consumers derive it from posture for one release.
    commitment: str = ""
    execution_status: str = "ACTIVE"
    expected_resources: int = 0
    sprint_execution_failure: bool = False
    execution_failure_reason: str | None = None

    @property
    def field(self) -> str:
        return self.current_field

    def to_payload(self) -> dict[str, Any]:
        return asdict(self) | {"field": self.current_field}


def _cost_setting_phrase(state: GameState, requirements: dict) -> str:
    """Name the racial price of each field this goal still has to buy."""
    parts = []
    for field in FIELDS:
        if field not in (requirements or {}):
            continue
        if int(requirements[field]) <= int(getattr(state.tech, field, 0) or 0):
            continue
        percent = field_cost_percent(state.race, field)
        parts.append(f"{field} {field_cost_label(percent)}")
    return ", ".join(parts) if parts else "no unpaid levels"


def _watchdog(state: GameState) -> dict[str, Any]:
    return turn_mapping(state, "strategic_watchdog")


def _competitive_tech_pressure(state: GameState) -> dict[str, Any]:
    """Return a modest public-score research signal, never hidden intel."""
    native=state.native or {}
    public=str(native.get("score_visibility","private") or "private").lower()=="public"
    records=[
        dict(record) for record in (native.get("player_scores") or [])
        if int(record.get("player_id",0) or 0)>0
    ]
    own_total=sum(int(getattr(state.tech,field,0) or 0) for field in FIELDS)
    if not public or not records:
        return {
            "public":public,"our_tech_total":own_total,"best_competitor_tech_total":None,
            "tech_gap":0,"research_pressure":1.0,"score_bonus":0.0,
        }
    rivals=[
        int(record.get("tech_total",0) or 0)
        for record in records
        if int(record.get("player_id",0) or 0)!=int(state.player_id)
    ]
    best=max(rivals,default=own_total)
    gap=max(0,best-own_total)
    # A gap of four levels starts a gentle push.  It is intentionally small:
    # public total-tech information cannot tell us which field wins a battle.
    pressure=1.0 if gap<4 else min(1.30,1.0+0.04*gap)
    return {
        "public":True,"our_tech_total":own_total,
        "best_competitor_tech_total":best,"tech_gap":gap,
        "research_pressure":round(pressure,3),
        "score_bonus":round(min(14.0,max(0.0,(gap-3)*1.5)),3),
    }


def _nearby_threats(state: GameState, plan: StrategicPlan | None) -> list:
    owned = [p for p in state.planets if p.owner == state.player_id]
    if not owned:
        return []
    radius = float(plan.defense_radius if plan else 100.0)
    threats=[]
    for fleet in state.fleets:
        if fleet.owner in (None,state.player_id):
            continue
        if min(distance(fleet.position, planet.position) for planet in owned) > radius:
            continue
        counts=(fleet.native or {}).get("ship_count", []) or []
        hulls=sum(max(0,int(count or 0)) for count in counts)
        mass=float((fleet.native or {}).get("mass", fleet.combat_power) or 0)
        # A three-scout (or similarly tiny) contact group proves contact and
        # still informs normal combat/escort production.  It does not prove an
        # imminent battle worth locking empire research into Weapons every turn.
        # Missing native fleet composition is uncertainty, not evidence that a
        # contact is harmless.  Only a positively observed small/light group is
        # classified as reconnaissance.
        if 0 < hulls <= 3 and 0 < mass <= 100:
            continue
        threats.append(fleet)
    return threats


def _tech_after(state: GameState, requirements: dict[str, int]) -> GameState:
    tech = replace(
        state.tech,
        **{
            field: max(int(getattr(state.tech, field, 0) or 0), int(requirements.get(field, 0)))
            for field in FIELDS
        },
    )
    return replace(state, tech=tech)


def _terraform_value(state: GameState, capability: ResearchCapability) -> tuple[float, int]:
    future = _tech_after(state, capability.requirements)
    gain = 0
    improved = 0
    for planet in state.planets:
        # Terraforming is an empire-growth investment.  Foreign worlds must
        # never inflate it; visible unowned claims remain relevant only while
        # the colony planner considers them attainable.
        if not planet.observed or (
            planet.owner != state.player_id
            and not (planet.owner is None and colony_planet_is_eligible(state, planet))
        ):
            continue
        before = evaluate_terraforming(state, planet).tech_habitability
        after = evaluate_terraforming(future, planet).tech_habitability
        if before is None or after is None or int(after) <= int(before):
            continue
        gain += int(after) - int(before)
        improved += 1
    return min(3.0, gain / 35.0), improved


def _terraform_growth(state: GameState, capability: ResearchCapability) -> tuple[int, int, int]:
    """Extra yearly colonists, and how many of the improved worlds are weak.

    A low-habitability world is one under 50%. The growth delta uses the same
    crowding model as the population planner, with habitability raised to the
    level this research step makes available.
    """
    future = _tech_after(state, capability.requirements)
    extra = low = improved = 0
    for planet in state.planets:
        if planet.owner != state.player_id or not planet.observed:
            continue
        before = evaluate_terraforming(state, planet)
        after = evaluate_terraforming(future, planet)
        current_hab = before.current_habitability
        tech_hab = after.tech_habitability
        if current_hab is None or tech_hab is None or int(tech_hab) <= int(current_hab):
            continue
        improved += 1
        if int(planet.habitability or current_hab) < 50:
            low += 1
        grown = replace(planet, habitability=int(tech_hab))
        extra += max(0, projected_population_growth(grown, state.race) - projected_population_growth(planet, state.race))
    return extra, low, improved


def _ar_energy_demand(state: GameState) -> ResearchDemand | None:
    """Energy is Alternate Reality's factory. Each level raises the resource term."""
    if not decode_race_economy(state.race).alternate_reality:
        return None
    current = int(state.tech.energy or 0)
    if current >= 26:
        return None
    target = current + 1
    ratio = (target + 1) / max(1, current + 1)
    capability = ResearchCapability(
        capability_id=f"race:ar_energy:{target}",
        name=f"Energy {target} resource multiplier",
        category="economy",
        requirements={"energy": target},
        post_unlock_action=(
            "Re-estimate empire resources. Alternate Reality production is the "
            "population term times (Energy + 1) / 10."
        ),
        source="Alternate Reality energy resource rule",
        tags=("race_resource", "growth_unlock"),
        unlocks=(f"Energy {target}",),
    )
    return ResearchDemand(
        capability,
        min(3.0, 1.4 + ratio),
        min(3.0, 0.8 + ratio),
        1.0,
        min(3.0, ratio),
        False,
        (
            f"Alternate Reality resources scale with Energy. Level {current} uses "
            f"multiplier {(current + 1) / 10:.1f}; level {target} uses {(target + 1) / 10:.1f}."
        ),
    )


def _component_is_legal(state: GameState, spec) -> bool:
    if spec.required_prt is not None and prt_id(state) != int(spec.required_prt):
        return False
    if spec.required_lrt is not None and str(spec.required_lrt).upper() not in lrts(state):
        return False
    if spec.is_mystery_trader_item:
        return False
    return True


def _battle_computer_demand(state: GameState, designs: list[dict]) -> ResearchDemand | None:
    """A short Electronics detour when the next battle computer is nearly funded."""
    combat = any(str(design.get("role", "")).casefold() == "combat" for design in designs)
    if not combat:
        return None
    best = None
    for (category, item_id), _raw in stock_component_database().components.items():
        if int(category) != ELECTRICAL:
            continue
        spec = component_spec(category, item_id)
        if "battle" not in spec.name.casefold() or not _component_is_legal(state, spec):
            continue
        requirements = {
            field: int(level)
            for field, level in zip(FIELDS, spec.tech_required)
            if int(level) > 0
        }
        if not requirements or all(
            int(getattr(state.tech, field, 0) or 0) >= level for field, level in requirements.items()
        ):
            continue
        cost = resources_to_requirements(state.race, state.tech, requirements)
        if best is None or cost < best[0]:
            best = (cost, spec, requirements)
    if best is None:
        return None
    _cost, spec, requirements = best
    turns, cost, _budget = research_turns(state, requirements)
    if turns > 3:
        return None
    capability = ResearchCapability(
        capability_id=f"component:battle_computer:{spec.item_id}",
        name=spec.name,
        category="military",
        requirements=requirements,
        post_unlock_action=f"Refit combat designs with {spec.name} before spending the next year on a same-gun hull.",
        source="stock electrical component; short Electronics unlock",
        tags=("military", "short_unlock"),
        unlocks=(spec.name,),
        enables_roles=("combat",),
        requires_production_plan=True,
    )
    return ResearchDemand(
        capability,
        2.2,
        2.0,
        1.0,
        2.4,
        False,
        (
            f"{spec.name} is {turns} funded year(s) away ({cost:,} resources). "
            "Take the Electronics detour, then return to the longer weapon or hull goal."
        ),
    )


def _goal_capabilities(state: GameState, plan: StrategicPlan | None) -> list[ResearchCapability]:
    if plan is None:
        return []
    result = []
    for goal_id, progress in plan.goal_progress.items():
        match = re.fullmatch(r"reach-tech:([a-z]+):(\d+)", str(goal_id))
        if not match or float(progress) >= 1.0:
            continue
        field, target = match.group(1), int(match.group(2))
        if field not in FIELDS:
            continue
        result.append(ResearchCapability(
            capability_id=f"goal:{goal_id}",
            name=f"Explicit {field.title()} {target} goal",
            category="goal",
            requirements={field: target},
            post_unlock_action=f"Re-evaluate the user goal that explicitly requested {field.title()} {target}.",
            source="StrategicPlan goal",
            tags=("explicit_goal",),
        ))
    return result


def _demand_from_raw(raw: dict, index: int, *, default_source: str) -> ResearchDemand | None:
    if not isinstance(raw, dict):
        return None
    req = {
        field: int(level)
        for field, level in dict(raw.get("requirements") or raw.get("tech_required") or {}).items()
        if field in FIELDS and int(level) > 0
    }
    if not req:
        return None
    capability = ResearchCapability(
        capability_id=str(raw.get("capability_id") or f"external:{index}"),
        name=str(raw.get("name") or raw.get("capability_id") or f"Strategic demand {index + 1}"),
        category=str(raw.get("category") or "strategic"),
        requirements=req,
        post_unlock_action=str(raw.get("post_unlock_action") or "Re-evaluate the requesting strategy module."),
        source=str(raw.get("source") or default_source),
        executable=bool(raw.get("executable", True)),
        tags=tuple(raw.get("tags") or ()),
        unlocks=tuple(str(value) for value in (raw.get("unlocks") or ())),
        enables_roles=tuple(str(value) for value in (raw.get("enables_roles") or ())),
        requires_production_plan=bool(raw.get("requires_production_plan", False)),
        expiry_condition=str(raw.get("expiry_condition") or ""),
    )
    return ResearchDemand(
        capability=capability,
        need=float(raw.get("need", 1.0)),
        urgency=float(raw.get("urgency", 1.0)),
        utilization=float(raw.get("utilization", 1.0)),
        value=float(raw.get("value", 1.0)),
        military_emergency=bool(raw.get("military_emergency", False)),
        explanation=str(raw.get("explanation") or "Demand supplied by another strategy module."),
    )


def _external_demands(state: GameState) -> list[ResearchDemand]:
    out = []
    for index, raw in enumerate((state.native or {}).get("research_demands", []) or []):
        demand = _demand_from_raw(raw, index, default_source="state.native research_demands")
        if demand is not None:
            out.append(demand)
    return out


def _remote_miner_research_demand(state: GameState, designs: list[dict]) -> ResearchDemand | None:
    """Return the first legal mining-robot unlock when real remote work exists."""
    targets=[]
    for planet in state.planets:
        concentrations=(planet.native or {}).get("mineral_concentrations") or []
        if (
            planet.owner is None and planet.observed and len(concentrations) >= 3
            and all(value is not None for value in concentrations[:3])
            and sum(max(0,int(value)) for value in concentrations[:3]) >= 150
        ):
            targets.append(planet)
    if not targets or any(str(design.get("role", "")) == "miner" for design in designs):
        return None

    race_prt=(state.race.native or {}).get("prt_id")
    race_lrts={str(value).upper() for value in ((state.race.native or {}).get("lrts", []) or [])}
    candidates=[]
    for (category,item_id), raw in stock_component_database().components.items():
        if int(category) != MINING_ROBOT:
            continue
        spec=component_spec(category,item_id)
        if spec.required_prt is not None and int(race_prt if race_prt is not None else -1) != int(spec.required_prt):
            continue
        if spec.required_lrt is not None and str(spec.required_lrt).upper() not in race_lrts:
            continue
        requirements={
            field:int(level) for field,level in zip(FIELDS,raw.tech_required)
            if int(level) > 0
        }
        remaining=sum(max(0,int(level)-int(getattr(state.tech,field,0) or 0)) for field,level in requirements.items())
        candidates.append((remaining,sum(requirements.values()),int(item_id),raw,spec,requirements))
    if not candidates:
        return None
    remaining,_total,_item,raw,spec,requirements=min(candidates,key=lambda row:row[:3])
    if remaining <= 0:
        # The design synthesis path will handle the now-available component.
        return None
    capability=ResearchCapability(
        capability_id=f"component:remote_miner:{int(raw.item_id)}",
        name=spec.name,
        category="logistics",
        requirements=requirements,
        post_unlock_action="Create and queue one race-legal remote miner for observed unowned mineral worlds.",
        source="bundled StarsAPI UNEDITED.MOD mining-robot requirement",
        tags=("remote_mining", "expansion_enabler"),
        unlocks=(spec.name,),
        enables_roles=("miner",),
        requires_production_plan=True,
    )
    concentration=sum(
        sum(max(0,int(value)) for value in (planet.native or {}).get("mineral_concentrations", [])[:3])
        for planet in targets
    )
    return ResearchDemand(
        capability=capability,
        need=min(3.0,1.0+len(targets)*0.55),
        urgency=1.45,
        utilization=1.0,
        value=min(3.0,1.2+concentration/450.0),
        explanation=(
            f"{len(targets)} observed unowned remote-mining target(s), combined concentration={concentration}; "
            f"{spec.name} is the first component legal for this race, with remaining research {requirements}."
        ),
    )


def _next_weapon_upgrade(state: GameState, current_weapon_tier: int) -> tuple[int, tuple[str, ...]] | None:
    """Find the next race-legal weapon capability from current stock data.

    This deliberately derives its target from component prerequisites instead
    of treating a selected Weapons level as a universal fleet milestone.  A
    component is useful only when every non-Weapons prerequisite is already
    satisfied and the race may legally fit it after reaching that level.
    """
    database = stock_component_database()
    candidate_levels = sorted({
        int(spec.tech_required[1])
        for spec in database.components.values()
        if int(spec.category) in (BEAM, TORPEDO)
        and int(spec.tech_required[1]) > int(current_weapon_tier)
    })
    for level in candidate_levels:
        future = _tech_after(state, {"weapons": level})
        available = proven_available_components(future)
        upgrades = sorted(
            spec.name
            for spec in available.values()
            if int(spec.category) in (BEAM, TORPEDO)
            and int(spec.tech_required[1]) == level
        )
        if upgrades:
            return level, tuple(upgrades)
    return None


def _next_battle_hull_demand(state: GameState) -> ResearchDemand | None:
    """Chase the next Cruiser, Galleon, Battleship, or Nubian.

    These hulls are the construction levels that turn a freighter empire into
    one that can fight. Gravity terraforming spends the same years on
    propulsion, so this demand exists only after construction 8, when the
    Large Freighter is already available.
    """
    if int(state.tech.construction or 0) < BATTLE_HULL_MIN_CONSTRUCTION:
        return None
    locked: list[tuple[int, int, Any]] = []
    for hull in stock_hulls().values():
        if hull.name not in BATTLE_HULLS or hull.is_starbase:
            continue
        if not hull_available_to_race(hull, state) or hull_unlocked(hull, state):
            continue
        locked.append((
            int(hull.tech_required[3]),
            BATTLE_HULLS.index(hull.name),
            hull,
        ))
    if not locked:
        return None
    hull = sorted(locked)[0][2]
    construction = int(hull.tech_required[3])
    return ResearchDemand(
        ResearchCapability(
            capability_id=f"hull:{hull.hull_id}",
            name=hull.name,
            category="combat",
            requirements={"construction": construction},
            post_unlock_action=(
                f"Create a combat design on the {hull.name} and queue it from a shipyard "
                "once the next M-file confirms the hull."
            ),
            source="stock battle-hull construction ladder",
            tags=("combat_hull", "hull"),
            unlocks=(hull.name,),
            enables_roles=("combat",),
            expiry_condition="A heavier race-legal battle hull becomes the next construction goal.",
        ),
        need=2.6,
        urgency=2.2,
        utilization=1.0,
        value=2.8,
        explanation=(
            f"{hull.name} is the next battle hull and needs Construction {construction}. "
            "That hull gives the empire a real combat slot layout; another gravity "
            "terraform step would spend the same research on propulsion."
        ),
    )


def _death_star_demand(state: GameState) -> ResearchDemand | None:
    """Offer the Death Star as a construction candidate for a race that can build it.

    Preference points, not a fixed second place, decide whether it is next.
    """
    hull = next((item for item in stock_hulls().values() if item.name == "Death Star"), None)
    if hull is None or not hull_available_to_race(hull, state) or hull_unlocked(hull, state):
        return None
    construction = int(hull.tech_required[3])
    return ResearchDemand(
        ResearchCapability(
            capability_id=f"hull:{hull.hull_id}",
            name=hull.name,
            category="combat",
            requirements={"construction": construction},
            post_unlock_action=(
                "Design a Death Star once Construction reaches the hull and a shipyard can fund it."
            ),
            source="stock Death Star construction gate",
            tags=("combat_hull", "hull"),
            unlocks=(hull.name,),
            enables_roles=("starbase",),
        ),
        need=2.2,
        urgency=1.8,
        utilization=1.0,
        value=2.4,
        explanation=(
            f"Death Star needs Construction {construction}. "
            "For Alternate Reality that hull is the construction payoff after Energy."
        ),
    )


def _weapon_parity_demand(state: GameState) -> ResearchDemand | None:
    """Offer weapons research up to one level above the fleets we can actually see."""
    from .research_preferences import observed_enemy_weapon_level
    enemy = observed_enemy_weapon_level(state)
    if enemy is None:
        return None
    target = int(enemy) + 1
    if int(state.tech.weapons or 0) >= target:
        return None
    return ResearchDemand(
        ResearchCapability(
            capability_id=f"military:weapon_parity:{target}",
            name=f"Weapons {target} to match enemy fleets",
            category="combat",
            requirements={"weapons": target},
            post_unlock_action="Refit combat designs once weapons match or slightly exceed the observed enemy fleet.",
            source="observed enemy weapon level",
            tags=("weapon_parity",),
            expiry_condition="Observed enemy weapon tech is no longer above our own.",
        ),
        need=2.4,
        urgency=2.2,
        utilization=1.0,
        value=2.2,
        explanation=(
            f"Observed enemy weapons are {enemy}. "
            f"The matching target is Weapons {target}, one level above that fleet."
        ),
    )


def _combat_weapon_upgrade_demand(
    state: GameState,
    designs: list[dict],
    threats: list,
) -> ResearchDemand | None:
    """Prefer the next usable weapon upgrade to a cosmetic same-gun refresh."""
    enemy_contact=any(fleet.owner not in (None, state.player_id) for fleet in state.fleets)
    if not enemy_contact:
        return None
    combat_slots={
        int(design.get("design_number", -1))
        for design in designs
        if str(design.get("role", "")).casefold() == "combat"
        and int(design.get("design_number", -1)) >= 0
    }
    raw_designs={
        int(design.get("design_number", -1)): design
        for design in ((state.native or {}).get("designs", []) or [])
        if not bool(design.get("is_starbase", False))
    }
    # Native role decoding can lag one M-file after creation.  A fitted beam or
    # torpedo is enough to identify a combat architecture for this purpose.
    for slot,design in raw_designs.items():
        if any(
            int(component.get("category", 0) or 0) in (BEAM, TORPEDO)
            and int(component.get("count", 0) or 0) > 0
            for component in (design.get("slots", []) or [])
        ):
            combat_slots.add(slot)
    if not combat_slots:
        return None

    combat_assets=0
    for fleet in state.fleets:
        if fleet.owner != state.player_id:
            continue
        combat_assets += sum(
            max(0, int(count or 0))
            for slot,count in enumerate((fleet.native or {}).get("ship_count", []) or [])
            if slot in combat_slots
        )
    for queue in ((state.native or {}).get("production_by_planet", {}) or {}).values():
        combat_assets += sum(
            max(0, int(item.get("count", 0) or 0))
            for item in queue or []
            if int(item.get("item_type", 0) or 0) == 4
            and int(item.get("item_id", -1) or -1) in combat_slots
        )
    # A nearby threat is sufficient evidence to prepare a new battle family;
    # otherwise wait until there is a real fleet investment to improve.
    if not threats and combat_assets < 3:
        return None

    fitted_tier=0
    for slot in combat_slots:
        for component in (raw_designs.get(slot, {}).get("slots", []) or []):
            category=int(component.get("category", 0) or 0)
            if category not in (BEAM, TORPEDO) or int(component.get("count", 0) or 0) <= 0:
                continue
            fitted_tier=max(fitted_tier, int(component_spec(
                category, int(component.get("item_id", 0) or 0),
            ).tech_required[1]))
    current=max(int(state.tech.weapons or 0), fitted_tier)
    next_upgrade = _next_weapon_upgrade(state, current)
    if next_upgrade is None:
        return None
    target, weapon_names = next_upgrade
    weapon_name = ", ".join(weapon_names)
    capability=ResearchCapability(
        capability_id=f"military:weapons_upgrade:{target}",
        name=f"Weapons {target}: {weapon_name}",
        category="military",
        requirements={"weapons":target},
        post_unlock_action=(
            f"Re-evaluate combat designs around {weapon_name}; retire zero-ship "
            "same-gun variants only after native fleet and queue read-back."
        ),
        source="race-legal stock-component fleet modernization doctrine",
        # This is a demonstrated fleet-modernization breakpoint, not a
        # speculative military project.  The scoring policy consequently
        # preserves it through expansion debt in preference to investing in a
        # larger hull that would retain the same guns.
        tags=("military", "weapon_upgrade", "battlefield_breakthrough"),
        unlocks=tuple(weapon_names),
        enables_roles=("combat",),
        requires_production_plan=True,
        expiry_condition="A better race-legal weapon package is fielded or the threatened front is resolved.",
    )
    return ResearchDemand(
        capability,
        # A fielded combat family facing contact is a direct modernization
        # investment.  Let the next usable weapon capability outrank a
        # cosmetic same-gun hull refresh; expansion still wins when there is
        # no fleet investment or no enemy contact.
        need=3.0,
        urgency=3.0 if threats else 2.2,
        utilization=1.0,
        value=3.0,
        military_emergency=False,
        explanation=(
            f"Enemy contact confirmed; combat/escort hulls live or queued={combat_assets}. "
            f"Current fleet armament is Weapons {fitted_tier or int(state.tech.weapons or 0)}. "
            f"Prioritize the next race-legal weapon upgrade, Weapons {target} ({weapon_name}), "
            "before another same-gun hull, shield, or engine variant."
        ),
    )


def _build_demands(
    state: GameState,
    plan: StrategicPlan | None,
    network: ExpansionNetworkSnapshot | None = None,
) -> list[ResearchDemand]:
    native = state.native or {}
    lrts = {str(x).upper() for x in ((state.race.native or {}).get("lrts", []) or [])}
    network = network or evaluate_expansion_network(state)

    # Expansion/logistics owns the detailed opening-network demand.  It uses the
    # existing external-demand schema but is generated from actual empire
    # geometry, breeder backlog, hub maturity, mineral bootstrap debt, and race
    # legality.  Duplicate generic capabilities are suppressed below.
    expansion_rows = expansion_research_demands(state, network)
    expansion_demands = [
        d for i, raw in enumerate(expansion_rows)
        if (d := _demand_from_raw(raw, i, default_source="expansion_research onion-network doctrine")) is not None
    ]
    expansion_ids = {d.capability.capability_id for d in expansion_demands}

    catalog = stock_capability_catalog(
        include_ife="IFE" in lrts,
        total_terraforming="TT" in lrts,
        improved_starbases="ISB" in lrts,
        prt_id=((state.race.native or {}).get("prt_id")),
    )
    catalog.extend(_goal_capabilities(state, plan))

    watchdog = _watchdog(state)
    colonization_pressure = float(watchdog.get("colonization_pressure", 1.0))
    exploration_pressure = float(watchdog.get("exploration_pressure", 1.0))
    expansion_debt = bool(
        network.expansion_network_debt
        or watchdog.get("colonization_below_minimum")
        or watchdog.get("exploration_below_minimum")
        or colonization_pressure > 1.15
        or exploration_pressure > 1.15
    )
    designs = list(native.get("design_profiles", []) or [])
    best_cargo = max(
        (int(d.get("cargo_capacity", 0) or 0) for d in designs if d.get("role") == "freighter"),
        default=0,
    )
    operational_bases = sum(
        1 for p in state.planets
        if p.owner == state.player_id
        and bool(((p.native or {}).get("starbase_capabilities") or {}).get("can_build_ships"))
    )
    demands: list[ResearchDemand] = []

    remote_mining_demand=_remote_miner_research_demand(state,designs)
    if remote_mining_demand is not None:
        demands.append(remote_mining_demand)
    energy_demand = _ar_energy_demand(state)
    if energy_demand is not None:
        demands.append(energy_demand)

    for capability in catalog:
        if capability.unlocked(state.tech):
            continue
        if capability.capability_id in expansion_ids:
            # The network-aware demand contains stronger current-state evidence
            # than the generic stock catalog entry.
            continue
        if capability.capability_id.startswith("goal:"):
            demands.append(ResearchDemand(
                capability, 2.0, 1.6, 1.0, 2.0, False,
                "An explicit strategic goal requests this named tech breakpoint.",
            ))
            continue
        if capability.capability_id == "component:fuel_mizer":
            need = max(exploration_pressure, colonization_pressure)
            demands.append(ResearchDemand(
                capability, need, 1.35, 0.55, 1.5, False,
                "IFE expansion has a direct Fuel Mizer requirement; no generic Propulsion catch-up is used.",
            ))
            continue
        if capability.capability_id.startswith("hull:"):
            hull_id = int(capability.capability_id.split(":", 1)[1])
            if hull_id in (1, 2, 3):
                cargo = {1: 210, 2: 1200, 3: 3000}[hull_id]
                if best_cargo >= cargo:
                    continue
                construction_requirements = {210: 3, 1200: 8, 3000: 13}
                smaller_locked = [
                    x for x in (210, 1200, 3000)
                    if best_cargo < x < cargo
                    and int(state.tech.construction or 0) < construction_requirements[x]
                ]
                if smaller_locked:
                    continue
                need = max(1.0, colonization_pressure, (plan.objective("logistics") if plan else 1.0))
                demands.append(ResearchDemand(
                    capability,
                    need,
                    1.15 if expansion_debt else 1.0,
                    0.70,
                    min(3.0, cargo / max(210, best_cargo or 210)),
                    False,
                    f"Best known freighter carries {best_cargo} kT; {capability.name} carries {cargo} kT.",
                ))
            elif hull_id == 33 and operational_bases == 0:
                # This catalog entry exists only for ISB races.
                demands.append(ResearchDemand(
                    capability,
                    max(1.2, expansion_pressure(state)),
                    1.3,
                    0.60,
                    2.2,
                    False,
                    "ISB race has no operational shipyard/refuel base; Space Dock is its cheap frontier hub hull.",
                ))
            elif hull_id == 35 and operational_bases > 0 and int(state.year) >= 2430:
                # Ultra Station is also ISB-only and is deliberately deferred
                # until after the opening expansion window.
                demands.append(ResearchDemand(
                    capability,
                    0.8,
                    0.8,
                    0.45,
                    1.3,
                    False,
                    "After the opening expansion window, an ISB empire can consider Ultra Station when stronger network demands are satisfied.",
                ))
            continue
        if capability.category == "terraforming":
            value, improved = _terraform_value(state, capability)
            if improved == 0 or value <= 0:
                continue
            extra, low_worlds, growth_worlds = _terraform_growth(state, capability)
            growth = low_worlds >= 3 and extra >= 1_000
            if growth:
                capability = replace(capability, tags=tuple(capability.tags) + ("growth_unlock",))
                need = min(3.0, 1.15 + low_worlds * 0.3)
                urgency = 1.8
                value = max(value, min(3.0, extra / 12_000))
            else:
                need = max(1.0, colonization_pressure)
                urgency = 1.25 if expansion_debt else 1.0
            explanation = (
                f"This exact breakpoint improves {improved} owned or attainable colony world(s); "
                f"aggregate habitability gain={round(value * 35)}."
            )
            if growth:
                explanation += (
                    f" {low_worlds} of those worlds are under 50% habitability; "
                    f"the step adds about {extra:,} colonists/year of growth."
                )
            demands.append(ResearchDemand(
                capability,
                need,
                urgency,
                1.0,
                value,
                False,
                explanation,
            ))

    demands.extend(expansion_demands)
    battle_hull = _next_battle_hull_demand(state)
    if battle_hull is not None:
        demands.append(battle_hull)
    death_star = _death_star_demand(state)
    if death_star is not None:
        demands.append(death_star)
    weapon_parity = _weapon_parity_demand(state)
    if weapon_parity is not None:
        demands.append(weapon_parity)

    threats = _nearby_threats(state, plan)
    weapon_upgrade=_combat_weapon_upgrade_demand(state, designs, threats)
    if weapon_upgrade is not None:
        demands.append(weapon_upgrade)
    if threats:
        combat_slots={
            int(design.get("design_number", -1))
            for design in designs if str(design.get("role", "")) == "combat"
        }
        combat_assets=0
        for fleet in state.fleets:
            if fleet.owner != state.player_id:
                continue
            combat_assets += sum(
                max(0,int(count or 0))
                for slot,count in enumerate((fleet.native or {}).get("ship_count", []) or [])
                if slot in combat_slots
            )
        for queue in ((state.native or {}).get("production_by_planet", {}) or {}).values():
            combat_assets += sum(
                max(0,int(item.get("count", 0) or 0))
                for item in queue or []
                if int(item.get("item_type", 0) or 0) == 4
                and int(item.get("item_id", -1) or -1) in combat_slots
            )
        emergency=combat_assets < 3
        level = int(state.tech.weapons or 0) + 1
        capability = ResearchCapability(
            capability_id=f"military:weapons_readiness:{level}",
            name=f"Weapons readiness {level}",
            category="military",
            requirements={"weapons": level},
            post_unlock_action="Re-score combat designs and local defensive production against the nearby threat.",
            source="nearby hostile-fleet emergency breakpoint",
            tags=("military",),
            enables_roles=("combat", "starbase_defense"),
            requires_production_plan=True,
            expiry_condition="The local defensive force is restored or the hostile fleet leaves the defense radius.",
        )
        demands.append(ResearchDemand(
            capability,
            min(3.0, 1.0 + len(threats) * 0.5),
            2.0 if emergency else 0.75,
            1.0,
            2.0,
            emergency,
            (
                f"{len(threats)} hostile fleet(s) are inside the defense radius; "
                f"combat/escort hulls live or queued={combat_assets}. "
                + ("Emergency readiness remains below the minimum defensive force." if emergency
                   else "Production has met the minimum defensive force, so this is no longer an emergency research lock.")
            ),
        ))

    computer = _battle_computer_demand(state, designs)
    if computer is not None:
        demands.append(computer)
    demands.extend(_external_demands(state))
    return demands


def expansion_pressure(state: GameState) -> float:
    watchdog = _watchdog(state)
    network = evaluate_expansion_network(state)
    network_pressure = 1.45 if network.expansion_network_debt else 1.0
    return max(
        network_pressure,
        float(watchdog.get("colonization_pressure", 1.0)),
        float(watchdog.get("exploration_pressure", 1.0)),
    )


def _threat_deadline_turns(state: GameState) -> int | None:
    """Return the shortest verified arrival horizon for an observed threat.

    Native fleet movement does not always expose a trustworthy ETA.  This
    therefore consumes only explicitly decoded/planner-provided horizon data
    and never invents a deadline from a guessed warp speed.
    """
    values=[]
    root=(state.native or {})
    for key in ("threat_eta_turns", "incoming_threat_eta_turns", "defense_deadline_turns"):
        value=root.get(key)
        if value is not None:
            try:
                values.append(max(0,int(value)))
            except (TypeError,ValueError):
                pass
    for fleet in state.fleets:
        if fleet.owner in (None,state.player_id):
            continue
        raw=fleet.native or {}
        for key in ("eta_turns", "arrival_turns", "turns_to_target"):
            value=raw.get(key)
            if value is not None:
                try:
                    values.append(max(0,int(value)))
                except (TypeError,ValueError):
                    pass
    return min(values) if values else None


def _production_readiness(
    state: GameState,
    demand: ResearchDemand,
) -> tuple[float, str, int | None, bool]:
    """Assess whether a researched capability can become useful ships in time.

    This is intentionally a conservative operational gate, not a fake exact
    ship-cost calculator.  The stock catalog supplies equipment cost data, but
    the native state does not yet expose verified per-player miniaturization
    prices; we therefore use confirmed shipyard capacity and only apply a
    mineral/resource rejection when an adapter explicitly marks its budget as
    complete.  That prevents the planner from claiming a combat unlock can be
    fielded at a dead empire while avoiding unverified price arithmetic.
    """
    capability=demand.capability
    requires=bool(
        capability.requires_production_plan
        or capability.category == "military"
        or "military" in capability.tags
    )
    if not requires:
        return 1.0, "No immediate ship-production follow-through is required.", None, True

    yards=[
        planet for planet in state.planets
        if planet.owner == state.player_id
        and bool(((planet.native or {}).get("starbase_capabilities") or {}).get("can_build_ships"))
    ]
    deadline=_threat_deadline_turns(state)
    if not yards:
        return (
            0.0,
            "No verified shipyard can convert this unlock into a useful design and fleet.",
            deadline,
            False,
        )

    # A compact four-ship defensive packet is the minimum useful follow-up;
    # multiple yards reduce its build horizon but do not pretend to guarantee
    # exact completion without the native queue/cost serializer.
    quantity=max(2, min(4, 2 * len(yards)))
    build_turns=max(1, math.ceil(quantity / len(yards)))
    research_turns=max(1, sum(capability.remaining(state.tech).values()))
    fieldable=deadline is None or research_turns + build_turns <= deadline

    confirmed=(state.native or {}).get("confirmed_production_budget")
    if isinstance(confirmed,dict):
        # This optional adapter contract represents real, complete stockpiles
        # and queue reservations.  Do not infer it from partial M-file views.
        required={
            "resources": quantity * 20,
            "ironium": quantity * 10,
            "boranium": quantity * 6,
            "germanium": quantity * 4,
        }
        if any(int(confirmed.get(key,0) or 0) < value for key,value in required.items()):
            fieldable=False
            budget_note=(
                "confirmed stock/reservation is below the conservative four-ship packet floor "
                f"R/I/B/G={required['resources']}/{required['ironium']}/"
                f"{required['boranium']}/{required['germanium']}"
            )
        else:
            budget_note="confirmed production stock can fund the initial packet"
    else:
        budget_note="native miniaturized queue cost is not yet decoded; capacity check uses verified yards only"

    yard_names=", ".join(planet.name for planet in yards[:3])
    action=(
        f"Queue {quantity} {', '.join(capability.enables_roles) or 'capability'} hull(s) "
        f"at {yard_names} after the design is confirmed in the next M-file"
    )
    note=f"{action}; estimated build horizon={build_turns} turn(s), {budget_note}."
    readiness=1.0 if fieldable else 0.0
    return readiness,note,deadline,fieldable


def _with_production_readiness(state: GameState, demand: ResearchDemand) -> ResearchDemand:
    readiness,follow,deadline,fieldable=_production_readiness(state,demand)
    return replace(
        demand,
        production_readiness=readiness,
        production_follow_through=follow,
        deadline_turns=deadline,
        fieldable_before_deadline=fieldable,
    )


def _remaining_total(capability: ResearchCapability, state: GameState) -> int:
    return sum(capability.remaining(state.tech).values())


def _score(
    demand: ResearchDemand,
    state: GameState,
    plan: StrategicPlan | None,
    expansion_debt: bool,
    opening_network_debt: bool,
) -> float:
    remaining = demand.capability.remaining(state.tech)
    field_factor = max(
        (float(plan.research(field)) if plan else 1.0)
        for field in remaining
    ) if remaining else 1.0
    score = (
        28.0 * demand.need
        + 24.0 * demand.urgency
        + 18.0 * demand.value
    ) * demand.utilization * min(1.6, field_factor)
    turns, _cost, _budget = research_turns(state, demand.capability.requirements)
    # A long level climb is not a one-year project. Short, useful unlocks keep
    # a modest advantage without letting a one-level vanity goal outrank a hull.
    score -= min(18.0, 0.45 * turns)

    if demand.military_emergency:
        score += 100.0
    elif "explicit_goal" in demand.capability.tags:
        score += 160.0
    elif opening_network_debt:
        # Before ~T30 the default research problem is expansion infrastructure:
        # range, freighter throughput, legal frontier bases, and useful gates.
        # Terraforming can still matter, but normally after mobility/logistics.
        if ONION_NETWORK_TAG in demand.capability.tags:
            score *= 1.75
        elif "growth_unlock" in demand.capability.tags:
            # Many weak worlds gaining a real growth step outrank a routine
            # terraforming nibble, including during the opening.
            score *= 1.2
        elif demand.capability.category == "terraforming":
            score *= 0.55
        elif demand.capability.category in EXPANSION_CATEGORIES:
            score *= 1.20
        elif "battlefield_breakthrough" in demand.capability.tags:
            # Contact plus an actual fleet is a sufficiently concrete military
            # investment to retain normal weighting during the opening.  It is
            # still below a true nearby-fleet emergency.
            score *= 1.0
        else:
            score *= 0.12
    elif expansion_debt:
        if "battlefield_breakthrough" in demand.capability.tags:
            score *= 1.10
        else:
            score *= 1.35 if demand.capability.category in EXPANSION_CATEGORIES else 0.25

    if "short_unlock" in demand.capability.tags and turns <= 3:
        # A battle computer, shield, or similar fitting that is only a couple
        # of funded years away is a detour, not a new research program.
        score += 14.0 * max(0.5, demand.value)
    if not demand.capability.executable:
        score *= 0.78
    requires_production=(
        demand.capability.requires_production_plan
        or demand.capability.category == "military"
        or "military" in demand.capability.tags
    )
    if requires_production:
        # A research goal without a shipyard/arrival window is a potential
        # future option, not an emergency answer.  The hard filter in
        # ``plan_research`` handles verified imminent deadlines; this smooth
        # factor also prevents speculative military tech from crowding out a
        # usable economy/logistics capability.
        score *= 0.20 + 0.80 * max(0.0,min(1.0,demand.production_readiness))
    preference_total, preference_rows = preference_points(state, demand.capability)
    score += preference_total
    trace = state.native.setdefault("preference_trace", {})
    trace[demand.capability.capability_id] = [
        {"name": name, "points": points} for name, points in preference_rows
    ]
    return round(score, 3)


def _field_sequence(
    capability: ResearchCapability,
    state: GameState,
    plan: StrategicPlan | None,
) -> tuple[str, str]:
    remaining = capability.remaining(state.tech)
    ranked = sorted(
        remaining,
        key=lambda field: (
            -remaining[field],
            -(float(plan.research(field)) if plan else 1.0),
            FIELDS.index(field),
        ),
    )
    current = ranked[0]
    next_field = ranked[1] if len(ranked) > 1 else current
    return current, next_field


def _contributors(
    state: GameState,
    sprint: bool,
    expansion_debt: bool,
    military_emergency: bool,
    planet_actions: dict | None = None,
) -> tuple[tuple[int, ...], tuple[int, ...]]:
    owned = [p for p in state.planets if p.owner == state.player_id]
    existing = (state.native or {}).get("production_by_planet", {}) or {}
    economy = decode_race_economy(state.race)
    network = evaluate_expansion_network(state)
    # An ungraduated P1/P2 is actively receiving the population and base
    # support that makes the next colony/relay layer executable.  That is a
    # critical colonization action, not a routine mine/factory queue.
    growth_hub_ids={
        int(hub.planet_id)
        for hub in network.hubs
        if int(getattr(hub,"promotion_tier",3) or 3) in (1,2)
        and not bool(getattr(hub,"graduated",False))
    }
    protected: set[int] = set()
    actions = {int(key): value for key, value in (planet_actions or {}).items()}
    tech_ids = {planet_id for planet_id, role in actions.items() if role == "tech"}
    candidates = []
    for planet in owned:
        queue = existing.get(str(planet.id), existing.get(planet.id, [])) or []
        custom = any(
            int(q.get("item_type", 0) or 0) == 4
            and int(q.get("count", 0) or 0) > 0
            for q in queue
        )
        shipyard = bool(((planet.native or {}).get("starbase_capabilities") or {}).get("can_build_ships"))
        fragile = int(planet.population or 0) < 50000
        if (
            custom
            or fragile
            or int(planet.id) in growth_hub_ids
            or actions.get(int(planet.id)) in {"ships", "hold_line", "industrialize"}
            # A real military emergency can reserve a shipyard.  Expansion
            # debt alone must not make every mature home/core world ineligible:
            # that was the systemic zero-contributor sprint failure.
            or (shipyard and military_emergency)
        ):
            protected.add(int(planet.id))
            continue
        resources = int(estimated_operating_resources(
            planet, economy, energy_tech=int(state.tech.energy or 0),
        )["estimated_resources"])
        candidates.append((resources, int(planet.population or 0), int(planet.id)))
    if not sprint or not candidates:
        return (), tuple(sorted(protected))
    if tech_ids:
        tech_candidates = [row for row in candidates if row[2] in tech_ids]
        if tech_candidates:
            candidates = tech_candidates
    candidates.sort(reverse=True)
    count = max(1, min(3, math.ceil(len(owned) / 3)))
    selected = tuple(pid for resources, _, pid in candidates[:count] if resources > 0)
    return selected, tuple(sorted(protected))


def _incumbent(
    memory,
    candidates: list[tuple[ResearchDemand, float]],
) -> tuple[ResearchDemand, float] | None:
    if memory is None:
        return None
    goal_id = str((memory.research_state or {}).get("capability_id") or "")
    return next(
        (row for row in candidates if row[0].capability.capability_id == goal_id),
        None,
    )


def _fallback_demand(
    state: GameState,
    network: ExpansionNetworkSnapshot | None = None,
) -> ResearchDemand:
    network = network or evaluate_expansion_network(state)
    if network.turn <= 30 and network.expansion_network_debt:
        level = int(state.tech.construction or 0) + 1
        capability = ResearchCapability(
            capability_id=f"expansion:construction_consolidation:{level}",
            name=f"Expansion Construction consolidation {level}",
            category="expansion",
            requirements={"construction": level},
            post_unlock_action="Re-evaluate freighter, hull, base, and ring-building options while the opening network consolidates.",
            source="opening onion-network fallback",
            tags=("fallback", "expansion_enabler", ONION_NETWORK_TAG),
        )
        return ResearchDemand(
            capability,
            0.9,
            0.75,
            1.0,
            0.9,
            False,
            "No stronger named expansion unlock is currently locked; keep low-intensity Construction progress while population/mineral logistics execute.",
        )

    level = int(state.tech.energy or 0) + 1
    capability = ResearchCapability(
        capability_id=f"economy:energy_efficiency:{level}",
        name=f"Energy economic breakpoint {level}",
        category="economy",
        requirements={"energy": level},
        post_unlock_action="Re-evaluate mature planetary and defensive energy utilization.",
        source="named strategic fallback breakpoint",
        tags=("fallback",),
    )
    return ResearchDemand(
        capability,
        0.8,
        0.7,
        1.0,
        0.8,
        False,
        "No higher-value actionable unlock is currently visible.",
    )


def plan_research(
    state: GameState,
    plan: StrategicPlan | None = None,
    memory=None,
) -> ResearchDecision:
    network = evaluate_expansion_network(state)
    state.native["expansion_network"] = network.to_dict()

    demands = [
        d for d in _build_demands(state, plan, network)
        if not d.capability.unlocked(state.tech)
    ]
    demands=[_with_production_readiness(state,demand) for demand in demands]
    # A verified arrival deadline is a hard operational constraint.  Do not
    # select military research that cannot yield even its minimum fleet packet
    # before contact when another actionable capability exists.
    actionable=[
        demand for demand in demands
        if demand.fieldable_before_deadline
        or not (
            demand.capability.category == "military"
            or "military" in demand.capability.tags
        )
    ]
    if actionable:
        demands=actionable
    if not demands:
        demands = [_with_production_readiness(state,_fallback_demand(state, network))]

    watchdog = _watchdog(state)
    expansion_debt = bool(
        network.expansion_network_debt
        or watchdog.get("colonization_below_minimum")
        or watchdog.get("exploration_below_minimum")
        or expansion_pressure(state) > 1.15
    )
    opening_network_debt = bool(network.turn <= 30 and network.expansion_network_debt)
    competitive_pressure=_competitive_tech_pressure(state)
    state.native["competitive_tech_pressure"]=competitive_pressure
    turn = max(0, int(state.year) - 2400)
    # The opening must establish its mineral economy and colony pipeline
    # before it sacrifices one quarter of empire resources to a technology
    # breakpoint.  Research continues at the normal background allocation,
    # but no sprint (including a military-selected one) is allowed until turn
    # 10.  This is shared economic doctrine, not a persona preference.
    opening_growth_lock = turn < OPENING_GROWTH_TURNS
    state.native["preference_trace"] = {}

    candidates = sorted(
        (
            (
                demand,
                _score(
                    demand,
                    state,
                    plan,
                    expansion_debt,
                    opening_network_debt,
                ) + (
                    float(competitive_pressure["score_bonus"])
                    if demand.capability.category in {"expansion","logistics","military"}
                    else 0.0
                ),
            )
            for demand in demands
        ),
        key=lambda row: row[1],
        reverse=True,
    )
    chosen, chosen_score = candidates[0]
    incumbent = _incumbent(memory, candidates)
    hysteresis_note = ""
    if incumbent is not None and chosen.capability.capability_id != incumbent[0].capability.capability_id:
        if chosen_score < incumbent[1] * 1.25 and not chosen.military_emergency:
            chosen, chosen_score = incumbent
            hysteresis_note = " Incumbent retained because the challenger was less than 25% stronger."

    remaining = chosen.capability.remaining(state.tech)
    estimated_turns, resource_cost, yearly_budget = research_turns(state, chosen.capability.requirements)
    if yearly_budget <= 0:
        estimated_turns = max(1, sum(remaining.values()) or 1)
    previous = dict(getattr(memory, "research_state", {}) or {}) if memory is not None else {}
    recently_unlocked: list[str] = []
    previous_req = dict(previous.get("requirements") or {})
    if previous.get("capability_id") and previous_req and all(
        int(getattr(state.tech, field, 0) or 0) >= int(level)
        for field, level in previous_req.items()
    ):
        recently_unlocked.append(
            str(previous.get("capability_name") or previous["capability_id"])
        )

    sprint_stalled = False
    sprint_execution_failure = False
    execution_failure_reason = None
    if (
        previous.get("capability_id") == chosen.capability.capability_id
        and previous.get("posture") == ResearchPosture.SPRINT.value
    ):
        start_year = int(previous.get("selected_year", state.year))
        expected = int(previous.get("estimated_turns", estimated_turns))
        start_remaining = int(previous.get("remaining_total", sum(remaining.values())))
        no_progress=(sum(remaining.values()) >= start_remaining)
        elapsed=int(state.year) - start_year
        if expected==1 and elapsed>=1 and no_progress:
            # A one-turn prediction is a concrete promise: the next M file
            # must show the level or capability completed.  Waiting several
            # extra turns hid unfunded sprints in the old diagnostics.
            sprint_execution_failure = True
            execution_failure_reason=(
                "Predicted one-turn sprint showed no measurable technology progress in the next turn; "
                "downgraded until an executable contributor plan is re-established."
            )
            sprint_stalled = True
        elif elapsed > expected + 2 and no_progress:
            sprint_stalled = True

    emergency = bool(chosen.military_emergency) and not opening_growth_lock
    sprint = bool(
        not sprint_stalled
        and 1 <= estimated_turns <= 5
        and chosen_score >= 55.0
        and chosen.utilization >= 0.55
    )
    if opening_growth_lock:
        sprint = False
    if emergency:
        posture = ResearchPosture.MILITARY_EMERGENCY
        sprint = True
    elif opening_growth_lock:
        posture = ResearchPosture.EXPANSION_FIRST
    elif sprint_stalled:
        posture = ResearchPosture.RECOVERY
    elif sprint:
        posture = ResearchPosture.SPRINT
    elif expansion_debt:
        posture = ResearchPosture.EXPANSION_FIRST
    elif int(state.year) >= 2440 and chosen_score >= 60:
        posture = ResearchPosture.MATURE_SURGE
    else:
        posture = ResearchPosture.TARGETED

    current_field, next_field = _field_sequence(chosen.capability, state, plan)
    contributors, protected = _contributors(
        state, sprint, expansion_debt, emergency, getattr(plan, "planet_actions", {}) or {},
    )
    contributor_resources={
        int(planet.id):int(estimated_operating_resources(
            planet, decode_race_economy(state.race), energy_tech=int(state.tech.energy or 0),
        )["estimated_resources"])
        for planet in state.planets
        if int(planet.id) in contributors
    }
    sprint_blocked_reason=None
    if sprint and not contributor_resources:
        # A 25% research declaration without a real contributor is advisory
        # fiction. Downgrade it at the source so execution/reporting cannot
        # advertise a sprint that no planet will fund.
        sprint=False
        posture=(
            ResearchPosture.RECOVERY if emergency else
            (ResearchPosture.EXPANSION_FIRST if expansion_debt else ResearchPosture.TARGETED)
        )
        sprint_blocked_reason=(
            "No mature non-emergency contributor has positive expected resources; "
            "deferred the high-commitment sprint instead of reporting an unfunded 25% allocation."
        )
    allocation = 25 if sprint else 15
    commitment=(
        "DEFERRED" if sprint_blocked_reason or sprint_execution_failure
        else "RECOVERY_HIGH" if emergency and sprint
        else "SPRINT" if sprint
        else "ROUTINE"
    )
    execution_status="DEFERRED" if commitment=="DEFERRED" else "ACTIVE"
    expected_resources=sum(contributor_resources.values()) if sprint else 0
    reason = (
        f"{posture.value}: pursue {chosen.capability.name}; score={chosen_score:.1f}, "
        f"remaining={remaining}, cumulative cost={resource_cost:,} resources "
        f"({_cost_setting_phrase(state, chosen.capability.requirements)}), "
        f"budget≈{yearly_budget:,}/year, horizon~{estimated_turns} turn(s), "
        f"utilization={chosen.utilization:.2f}. "
        f"{chosen.explanation} Post-unlock: {chosen.capability.post_unlock_action}"
        f"{hysteresis_note}"
    )
    if chosen.production_follow_through:
        reason += f" Production follow-through: {chosen.production_follow_through}"
    if chosen.deadline_turns is not None:
        reason += (
            f" Verified threat deadline={chosen.deadline_turns} turn(s); "
            f"fieldable-before-deadline={'yes' if chosen.fieldable_before_deadline else 'no'}."
        )
    if opening_network_debt:
        reason += (
            f" OPENING NETWORK: radius={network.owned_radius_ly:.0f}/{network.target_radius_ly:.0f} ly; "
            f"outer hubs={list(network.outer_hub_ids)}; pop import backlog={network.population_import_backlog:,}; "
            f"bootstrap base={network.bootstrap_base_name}; ISB={'yes' if network.improved_starbases else 'no'}."
        )
    if opening_growth_lock:
        reason += (
            f" OPENING ECONOMY: turn {turn} keeps research at 15% until turn "
            f"{OPENING_GROWTH_TURNS}; mines, factories, and colony hulls have first claim on production."
        )
    if sprint_stalled:
        reason += " WARNING - prior sprint exceeded its horizon without measurable tech progress; 15% recovery posture selected."
    if execution_failure_reason:
        reason += f" EXECUTION FAILURE: {execution_failure_reason}"
    if sprint_blocked_reason:
        reason += f" SPRINT DEFERRED: {sprint_blocked_reason}"
    chosen_preferences = (state.native.get("preference_trace") or {}).get(chosen.capability.capability_id) or []
    if chosen_preferences:
        reason += " Preferences: " + ", ".join(
            f"{row['name']} {row['points']:+.0f}" for row in chosen_preferences
        ) + "."

    preference_trace = state.native.get("preference_trace") or {}
    state.native["research_goals"] = [
        {
            "capability_id": demand.capability.capability_id,
            "name": demand.capability.name,
            "tier": (
                "race_resource" if "race_resource" in demand.capability.tags
                else "growth" if "growth_unlock" in demand.capability.tags
                else "short_unlock" if "short_unlock" in demand.capability.tags
                else "fleet" if demand.capability.category in {"military", "logistics", "expansion", "combat"}
                else "general"
            ),
            "requirements": dict(demand.capability.requirements),
            "resource_cost": resources_to_requirements(
                state.race, state.tech, demand.capability.requirements,
            ),
            "field_costs": {
                field: field_cost_label(field_cost_percent(state.race, field))
                for field in demand.capability.requirements
            },
            "turns": research_turns(state, demand.capability.requirements)[0],
            "score": goal_score,
            "preferences": list(preference_trace.get(demand.capability.capability_id) or []),
        }
        for demand, goal_score in candidates[:6]
    ]
    decision = ResearchDecision(
        capability_id=chosen.capability.capability_id,
        capability_name=chosen.capability.name,
        category=chosen.capability.category,
        posture=posture.value,
        current_field=current_field,
        next_field=next_field,
        allocation_percent=allocation,
        score=chosen_score,
        estimated_turns=estimated_turns,
        estimate_confidence="low" if not getattr(memory, "research_history", []) else "medium",
        requirements=dict(chosen.capability.requirements),
        remaining_requirements=remaining,
        contributor_planet_ids=contributors,
        protected_production_planet_ids=protected,
        post_unlock_action=chosen.capability.post_unlock_action,
        reason=reason,
        candidate_scores=tuple({
            "capability_id": demand.capability.capability_id,
            "name": demand.capability.name,
            "score": score,
            "remaining": demand.capability.remaining(state.tech),
            "production_readiness": demand.production_readiness,
            "production_follow_through": demand.production_follow_through,
            "deadline_turns": demand.deadline_turns,
            "fieldable_before_deadline": demand.fieldable_before_deadline,
        } for demand, score in candidates[:8]),
        recently_unlocked=tuple(recently_unlocked),
        sprint_stalled=sprint_stalled,
        production_readiness=chosen.production_readiness,
        production_follow_through=chosen.production_follow_through,
        expected_completion_year=int(state.year) + estimated_turns,
        # A missed one-turn prediction is an execution failure just as surely
        # as an empty contributor set.  It must not remain marked executable
        # merely because the original selection did have a contributor.
        sprint_executable=not bool(sprint_blocked_reason or sprint_execution_failure),
        sprint_blocked_reason=sprint_blocked_reason,
        expected_contributor_resources=contributor_resources,
        commitment=commitment,
        execution_status=execution_status,
        expected_resources=expected_resources,
        sprint_execution_failure=sprint_execution_failure,
        execution_failure_reason=execution_failure_reason,
    )
    if memory is not None:
        selected_year = (
            int(previous.get("selected_year", state.year))
            if previous.get("capability_id") == decision.capability_id
            else int(state.year)
        )
        memory.research_state = {
            **decision.to_payload(),
            "selected_year": selected_year,
            "remaining_total": sum(remaining.values()),
        }
        memory.research_history.append({
            "year": int(state.year),
            "capability_id": decision.capability_id,
            "posture": decision.posture,
            "field": decision.current_field,
            "next_field": decision.next_field,
            "allocation_percent": decision.allocation_percent,
            "remaining": dict(decision.remaining_requirements),
            "recently_unlocked": list(decision.recently_unlocked),
        })
        memory.research_history[:] = memory.research_history[-200:]
    return decision
