from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from enum import Enum
import math
import re
from typing import Any

from .expansion_network import ExpansionNetworkSnapshot, evaluate_expansion_network
from .expansion_research import expansion_research_demands
from .models import GameState
from .persona import StrategicPlan
from .planet_economy import decode_race_economy, estimated_operating_resources
from .research_capabilities import ResearchCapability, stock_capability_catalog
from .starsapi_items import MINING_ROBOT, component_spec, stock_component_database
from .terraforming import evaluate_terraforming
from .util import distance


FIELDS = ("energy", "weapons", "propulsion", "construction", "electronics", "biotechnology")
EXPANSION_CATEGORIES = {"expansion", "logistics", "terraforming"}
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

    @property
    def field(self) -> str:
        return self.current_field

    def to_payload(self) -> dict[str, Any]:
        return asdict(self) | {"field": self.current_field}


def _watchdog(state: GameState) -> dict[str, Any]:
    return dict((state.native or {}).get("strategic_watchdog") or {})


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
        if not planet.observed:
            continue
        before = evaluate_terraforming(state, planet).tech_habitability
        after = evaluate_terraforming(future, planet).tech_habitability
        if before is None or after is None or int(after) <= int(before):
            continue
        gain += int(after) - int(before)
        improved += 1
    return min(3.0, gain / 35.0), improved


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
            demands.append(ResearchDemand(
                capability,
                max(1.0, colonization_pressure),
                1.25 if expansion_debt else 1.0,
                1.0,
                value,
                False,
                f"This exact breakpoint improves {improved} observed world(s); aggregate habitability gain={round(value * 35)}.",
            ))

    demands.extend(expansion_demands)

    threats = _nearby_threats(state, plan)
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
    score -= 5.0 * sum(remaining.values())

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
        elif demand.capability.category == "terraforming":
            score *= 0.55
        elif demand.capability.category in EXPANSION_CATEGORIES:
            score *= 1.20
        else:
            score *= 0.12
    elif expansion_debt:
        score *= 1.35 if demand.capability.category in EXPANSION_CATEGORIES else 0.25

    if not demand.capability.executable:
        score *= 0.78
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
) -> tuple[tuple[int, ...], tuple[int, ...]]:
    owned = [p for p in state.planets if p.owner == state.player_id]
    existing = (state.native or {}).get("production_by_planet", {}) or {}
    economy = decode_race_economy(state.race)
    protected: set[int] = set()
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
        if custom or fragile or (shipyard and (expansion_debt or military_emergency)):
            protected.add(int(planet.id))
            continue
        resources = int(estimated_operating_resources(planet, economy)["estimated_resources"])
        candidates.append((resources, int(planet.population or 0), int(planet.id)))
    if not sprint or not candidates:
        return (), tuple(sorted(protected))
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
    if not demands:
        demands = [_fallback_demand(state, network)]

    watchdog = _watchdog(state)
    expansion_debt = bool(
        network.expansion_network_debt
        or watchdog.get("colonization_below_minimum")
        or watchdog.get("exploration_below_minimum")
        or expansion_pressure(state) > 1.15
    )
    opening_network_debt = bool(network.turn <= 30 and network.expansion_network_debt)
    turn = max(0, int(state.year) - 2400)
    # The opening must establish its mineral economy and colony pipeline
    # before it sacrifices one quarter of empire resources to a technology
    # breakpoint.  Research continues at the normal background allocation,
    # but no sprint (including a military-selected one) is allowed until turn
    # 10.  This is shared economic doctrine, not a persona preference.
    opening_growth_lock = turn < OPENING_GROWTH_TURNS

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
    estimated_turns = max(1, sum(remaining.values()))
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
    if (
        previous.get("capability_id") == chosen.capability.capability_id
        and previous.get("posture") == ResearchPosture.SPRINT.value
    ):
        start_year = int(previous.get("selected_year", state.year))
        expected = int(previous.get("estimated_turns", estimated_turns))
        start_remaining = int(previous.get("remaining_total", sum(remaining.values())))
        if int(state.year) - start_year > expected + 2 and sum(remaining.values()) >= start_remaining:
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
    contributors, protected = _contributors(state, sprint, expansion_debt, emergency)
    allocation = 25 if sprint else 15
    reason = (
        f"{posture.value}: pursue {chosen.capability.name}; score={chosen_score:.1f}, "
        f"remaining={remaining}, horizon~{estimated_turns} turn(s), utilization={chosen.utilization:.2f}. "
        f"{chosen.explanation} Post-unlock: {chosen.capability.post_unlock_action}"
        f"{hysteresis_note}"
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
        } for demand, score in candidates[:8]),
        recently_unlocked=tuple(recently_unlocked),
        sprint_stalled=sprint_stalled,
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
