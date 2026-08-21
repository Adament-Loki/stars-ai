from __future__ import annotations

from dataclasses import dataclass, asdict
from functools import lru_cache
from pathlib import Path
import csv
import math

from .colony_planner import colony_planet_is_eligible
from .empire_geometry import distance_from_homeworld
from .expansion_network import evaluate_expansion_network
from .planet_economy import (
    decode_race_economy,
    estimated_operating_resources,
    mineral_surface_stock,
    working_mineral_reserve,
)
from .starsapi_items import stock_component_database
from .util import distance

STARBASE_QUEUE_SLOT_OFFSET = 16
STARBASE_DESIGN_LIMIT = 10
MAX_NEW_SUPPORT_BASES_PER_TURN = 2


@dataclass(frozen=True)
class SupportBaseBuild:
    planet_id: int
    design_slot: int
    design_name: str
    hull_name: str
    priority: int
    reason: str
    complete_percent: int = 0

    def queue_item(self) -> dict:
        item = {
            "item": "starbase_design",
            "design_slot": self.design_slot,
            "design_name": self.design_name,
            "hull_name": self.hull_name,
            "quantity": 1,
            "role": "fuel_hub",
        }
        if self.complete_percent:
            item["complete_percent"] = self.complete_percent
        return item


@dataclass(frozen=True)
class SupportBaseMaterialDemand:
    """The real remaining bill for a proposed or stalled support base.

    A starbase must not reserve the front of a world queue merely because it is
    strategically desirable.  This record makes the bill visible to the queue
    planner and to freight routing, including partial construction already
    paid for by the host.
    """
    planet_id: int
    design_slot: int
    design_name: str
    complete_percent: int
    resource_remaining: int
    mineral_remaining: dict[str, int]
    mineral_required: dict[str, int]
    mineral_deficit: dict[str, int]
    ready: bool
    reason: str

    def to_dict(self) -> dict:
        return asdict(self)


def _production_for(state, planet_id: int) -> list[dict]:
    raw = (state.native or {}).get("production_by_planet", {}) or {}
    return list(raw.get(str(int(planet_id)), raw.get(int(planet_id), [])) or [])


def _queued_starbase_item(state, planet_id: int) -> dict | None:
    for item in _production_for(state, planet_id):
        if int(item.get("item_type", 0) or 0) != 4:
            continue
        item_id = int(item.get("item_id", -1) or -1)
        if STARBASE_QUEUE_SLOT_OFFSET <= item_id < STARBASE_QUEUE_SLOT_OFFSET + STARBASE_DESIGN_LIMIT:
            return item
    return None


def _support_designs(state) -> dict[int, dict]:
    """Only already-defined race-legal designs with real shipyard+refuel capability."""
    return {
        int(profile["design_number"]): profile
        for profile in (state.native or {}).get("starbase_profiles", []) or []
        if bool((profile.get("capabilities") or {}).get("can_refuel"))
        and bool((profile.get("capabilities") or {}).get("can_build_ships"))
    }


def _preferred_support_design(state) -> dict | None:
    designs = list(_support_designs(state).values())
    if not designs:
        return None
    lrts = {str(x).upper() for x in ((state.race.native or {}).get("lrts", []) or [])}
    has_isb = "ISB" in lrts
    # A Space Dock can only exist for an ISB race. If corrupted/synthetic input
    # claims otherwise, refuse to prefer it. Normal races reuse Space Station or
    # another already-defined legal shipyard/refuel design.
    legal = [d for d in designs if has_isb or int(d.get("hull_id", -1)) not in (33, 35)]
    if not legal:
        return None
    return min(
        legal,
        key=lambda d: (
            0 if has_isb and int(d.get("hull_id", -1)) == 33 else 1,
            0 if int(d.get("hull_id", -1)) == 34 else 1,
            int(d.get("hull_id", 999)),
            int(d.get("design_number", 999)),
        ),
    )


def desired_support_base_count(state) -> int:
    """Competitive milestone: roughly 3-5 useful support bases by T25-30.

    The target is bounded by empire size so a tiny/bad-start empire does not
    bankrupt itself chasing an arbitrary count.
    """
    owned = [p for p in state.planets if p.owner == state.player_id]
    n = len(owned)
    if n <= 1:
        return 1 if n else 0
    turn = max(0, int(state.year) - 2400)
    if turn < 10:
        milestone = 1
    elif turn < 18:
        milestone = 2
    elif turn < 25:
        milestone = 3
    elif turn < 30:
        milestone = 4
    else:
        milestone = 5
    if turn >= 40:
        milestone = max(milestone, min(8, int(math.ceil(n / 4.0))))
    # About one hub for every two owned worlds is an aggressive but bounded
    # opening network. This makes 3-5 bases feasible when the empire has 6-10+
    # worlds, while small empires get a smaller target.
    empire_bound = max(1, int(math.ceil(n / 2.0)))
    return min(milestone, empire_bound)


def _operational_support_worlds(state):
    return [
        p for p in state.planets
        if p.owner == state.player_id
        and bool(((p.native or {}).get("starbase_capabilities") or {}).get("can_refuel"))
        and bool(((p.native or {}).get("starbase_capabilities") or {}).get("can_build_ships"))
    ]


def _candidate_score(state, planet, operational, plan, network) -> tuple[float, dict]:
    native = planet.native or {}
    caps = native.get("starbase_capabilities") or {}
    has_base = bool(native.get("has_starbase", native.get("starbase", False)))
    is_fort = bool(caps.get("is_orbital_fort"))
    nearest_hub = min(
        (distance(planet.position, hub.position) for hub in operational if int(hub.id) != int(planet.id)),
        default=999.0,
    )
    viable = [
        other for other in state.planets
        if other.owner is None and other.observed
        and colony_planet_is_eligible(state, other, plan)
        and distance(planet.position, other.position) <= 160.0
    ]
    unknown = sum(
        1 for other in state.planets
        if not other.observed and distance(planet.position, other.position) <= 120.0
    )
    economy = decode_race_economy(state.race)
    resources = int(estimated_operating_resources(planet, economy)["estimated_resources"])
    industry = int(planet.factories or 0) + int(planet.mines or 0)
    home_distance = float(distance_from_homeworld(state, planet.position))
    hub = next((h for h in network.hubs if int(h.planet_id) == int(planet.id)), None)
    ring = int(hub.ring) if hub is not None else 0
    pop = int(planet.population or 0)
    mineral_ready = min(1.0, (
        max(0, int(planet.ironium or 0))
        + max(0, int(planet.boranium or 0))
        + 1.5 * max(0, int(planet.germanium or 0))
    ) / 300.0)
    network_gap = not operational or nearest_hub >= 120.0
    outer_bonus = 1.0 if int(planet.id) in set(network.outer_hub_ids) else 0.0
    layer1_pending = bool(hub and hub.designated_layer1 and not hub.graduated)
    promotion_tier = int(getattr(hub,"promotion_tier",3) or 3) if hub is not None else 3
    promotion_rank = getattr(hub,"promotion_rank",None) if hub is not None else None
    promotion_value = float(getattr(hub,"overall_value",0.0) or 0.0) if hub is not None else 0.0
    # A P2 is deliberately not an active base project until its P1 has
    # graduated. The classification remains visible, but the spending order is
    # HW -> P1 -> active P2.
    next_layer_child = bool(
        hub and promotion_tier == 2 and hub.parent_exporter_id is not None
    )

    score = (
        0.60 * min(1.5, pop / 250_000.0)
        + 0.42 * min(1.5, industry / 220.0)
        + 0.35 * min(1.5, resources / 500.0)
        + 0.46 * min(1.5, nearest_hub / 220.0)
        + 0.22 * min(4, len(viable))
        + 0.045 * min(10, unknown)
        + 0.18 * min(3, ring)
        + 0.22 * min(2.0, home_distance / 180.0)
        + 0.36 * outer_bonus
        + 0.28 * mineral_ready
        + (0.38 if is_fort else 0.0)
        + (0.22 if has_base else 0.0)
        + (0.35 if network_gap else 0.0)
        + (2.20 if layer1_pending else 0.0)
        + (0.95 if next_layer_child else 0.0)
        + 0.70 * promotion_value
    )
    detail = {
        "nearest_hub": nearest_hub,
        "viable": len(viable),
        "unknown": unknown,
        "resources": resources,
        "industry": industry,
        "home_distance": home_distance,
        "ring": ring,
        "is_fort": is_fort,
        "has_base": has_base,
        "network_gap": network_gap,
        "outer": bool(outer_bonus),
        "layer1_pending": layer1_pending,
        "next_layer_child": next_layer_child,
        "promotion_tier": promotion_tier,
        "promotion_rank": promotion_rank,
        "promotion_value": promotion_value,
    }
    return score, detail


def plan_support_base_builds(state, plan=None) -> list[SupportBaseBuild]:
    """Maintain a growing network of high-value shipyard/refuel hubs.

    Existing projects are always continued. If the empire is below its base
    milestone, start up to two additional projects this turn on mature/high-value
    worlds. A newly proposed support design is deliberately not queued in this
    same turn: it must first appear in the next M-file read-back, after which
    this planner treats it exactly like any player-authored starbase design.
    """
    support_designs = _support_designs(state)
    preferred = _preferred_support_design(state)
    if preferred is None:
        return []

    owned = [p for p in state.planets if p.owner == state.player_id]
    operational = _operational_support_worlds(state)
    network = evaluate_expansion_network(state)
    target = desired_support_base_count(state)
    turn = max(0, int(state.year) - 2400)
    # Onion milestone: by T25-30, build real support bases on roughly four of
    # the designated Layer-1 worlds, then push toward five when the economy can
    # support it. The homeworld/support core is counted separately in the total.
    if turn >= 30:
        layer1_goal = min(5, len(network.layer1_hub_ids))
    elif turn >= 25:
        layer1_goal = min(4, len(network.layer1_hub_ids))
    elif turn >= 18:
        layer1_goal = min(3, len(network.layer1_hub_ids))
    else:
        layer1_goal = min(2, len(network.layer1_hub_ids))
    target = max(target, min(len(owned), 1 + layer1_goal))
    out: list[SupportBaseBuild] = []
    queued_planets: set[int] = set()

    # Continue all recognized support-base projects, not just the first one.
    for planet in owned:
        queued = _queued_starbase_item(state, planet.id)
        if queued is None:
            continue
        slot = int(queued["item_id"]) - STARBASE_QUEUE_SLOT_OFFSET
        profile = support_designs.get(slot)
        if profile is None:
            continue
        queued_planets.add(int(planet.id))
        out.append(SupportBaseBuild(
            planet_id=int(planet.id),
            design_slot=slot,
            design_name=str(profile.get("name") or profile.get("hull_name") or f"Starbase #{slot + 1}"),
            hull_name=str(profile.get("hull_name") or "Support starbase"),
            priority=148,
            complete_percent=int(queued.get("complete_percent", 0) or 0),
            reason=(
                f"continue in-progress support-base project at {planet.name}; native completion="
                f"{int(queued.get('complete_percent', 0) or 0)}%. Empire target is {target} useful "
                "shipyard/refuel bases."
            ),
        ))

    deficit = max(0, int(target) - len(operational) - len(queued_planets))
    # A distant Orbital Fort is not a shipyard/refuel hub.  Permit one upgrade
    # project even when the numerical support-base milestone is met, while the
    # material gate still prevents it from starving local development.
    remote_fort_needs_upgrade=any(
        bool(((p.native or {}).get("starbase_capabilities") or {}).get("is_orbital_fort"))
        and not bool(((p.native or {}).get("starbase_capabilities") or {}).get("can_refuel"))
        and distance_from_homeworld(state,p.position) >= 180.0
        for p in owned
    )
    if deficit <= 0 and remote_fort_needs_upgrade:
        deficit=1
    if deficit <= 0:
        return out

    candidates = []
    for planet in owned:
        if int(planet.id) in queued_planets:
            continue
        caps = (planet.native or {}).get("starbase_capabilities") or {}
        if bool(caps.get("can_refuel")) and bool(caps.get("can_build_ships")):
            continue
        has_base = bool((planet.native or {}).get("has_starbase", False))
        hub = next((h for h in network.hubs if int(h.planet_id) == int(planet.id)), None)
        # Designated Layer-1 hubs are intentionally based before they reach the
        # 25% graduation threshold so population and orbital development can
        # converge. Deeper children get a smaller early-base allowance.
        if hub is not None and hub.designated_layer1:
            minimum_population = 40_000 if has_base else 55_000
        elif hub is not None and hub.parent_exporter_id is not None:
            minimum_population = 50_000 if has_base else 70_000
        elif has_base:
            # An existing remote fort is already an orbital investment.  It is
            # a valid support-base *candidate* at a smaller population; the
            # separate material gate below/economy planner decides whether it
            # may occupy the production queue this turn.
            minimum_population = 20_000
        else:
            minimum_population = 55_000 if has_base else 85_000
        if int(planet.population or 0) < minimum_population:
            continue
        score, detail = _candidate_score(state, planet, operational, plan, network)
        # Below target we may build an inner economic hub too, but outer/frontier
        # worlds get the score needed to win naturally.
        if score < 1.20:
            continue
        candidates.append((score, planet, detail))

    candidates.sort(key=lambda row: row[0], reverse=True)
    starts = min(MAX_NEW_SUPPORT_BASES_PER_TURN, deficit, len(candidates))
    slot = int(preferred["design_number"])
    design_name = str(preferred.get("name") or preferred.get("hull_name") or f"Starbase #{slot + 1}")
    hull_name = str(preferred.get("hull_name") or "Support starbase")

    for score, planet, detail in candidates[:starts]:
        nearest_text = (
            "no operational support hub exists"
            if detail["nearest_hub"] >= 900
            else f"nearest support hub={detail['nearest_hub']:.1f} ly"
        )
        out.append(SupportBaseBuild(
            planet_id=int(planet.id),
            design_slot=slot,
            design_name=design_name,
            hull_name=hull_name,
            priority=(
                152 if detail["layer1_pending"]
                else 147 if detail["next_layer_child"]
                else 145 if detail["outer"] or detail["network_gap"]
                else 138
            ),
            reason=(
                f"promote high-value ring hub {planet.name} to {design_name} ({hull_name}): "
                + ("restore remote refueling and ship-construction support; " if detail["is_fort"] else "")
                + f"{nearest_text}; ring={detail['ring']}; home distance={detail['home_distance']:.1f} ly; "
                f"nearby viable/unknown frontier={detail['viable']}/{detail['unknown']}; "
                f"population={int(planet.population or 0):,}; industry={detail['industry']}; "
                f"estimated resources={detail['resources']}; hub score={score:.2f}; "
                f"promotion=P{detail['promotion_tier']} rank={detail['promotion_rank']} "
                f"value={detail['promotion_value']:.2f}; layer1_pending={detail['layer1_pending']}; "
                f"next_layer_child={detail['next_layer_child']}. "
                f"Support-base milestone={len(operational)} operational + {len(queued_planets)} queued / target {target}."
            ),
        ))
    return out


@lru_cache(maxsize=1)
def _stock_starbase_hull_costs() -> dict[int, dict[str, int]]:
    """Read stock hull costs from the same MOD data used for native designs."""
    path = Path(__file__).with_name("data_hulls.mod")
    costs: dict[int, dict[str, int]] = {}
    for row in csv.reader(path.read_text(encoding="latin-1").splitlines()):
        if not row or int(row[0]) != 16:
            continue
        numbers = [int(value) if value else 0 for value in row[3:]]
        if len(numbers) < 12:
            continue
        hull_id = int(numbers[0])
        costs[hull_id] = {
            "resources": int(numbers[8]),
            "ironium": int(numbers[9]),
            "boranium": int(numbers[10]),
            "germanium": int(numbers[11]),
        }
    return costs


def _raw_slot_value(slot, key: str, default: int = 0) -> int:
    if isinstance(slot, dict):
        value = slot.get(key, default)
    else:
        value = getattr(slot, key, default)
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return int(default)


def support_base_design_cost(profile: dict) -> dict[str, int]:
    """Return the exact stock hull + fitted-component bill where slots exist.

    Old/hand-built test states may not carry slot data.  In that case the bare
    hull bill is still a safe lower bound, so the planner never invents a free
    Space Station.
    """
    hull_id = int(profile.get("hull_id", -1) or -1)
    total = dict(_stock_starbase_hull_costs().get(hull_id, {
        "resources": 0, "ironium": 0, "boranium": 0, "germanium": 0,
    }))
    slots = list(profile.get("slots", []) or [])
    database = stock_component_database()
    for slot in slots:
        category = _raw_slot_value(slot, "category")
        item_id = _raw_slot_value(slot, "item_id")
        count = max(0, _raw_slot_value(slot, "count"))
        component = database.component(category, item_id)
        if component is None or count <= 0:
            continue
        total["resources"] += count * int(component.resource_cost)
        total["ironium"] += count * int(component.ironium)
        total["boranium"] += count * int(component.boranium)
        total["germanium"] += count * int(component.germanium)
    return total


def plan_support_base_material_demands(state, plan=None) -> list[SupportBaseMaterialDemand]:
    """Expose ready/blocked base work for production and transport decisions."""
    requests = plan_support_base_builds(state, plan)
    profiles = _support_designs(state)
    economy = decode_race_economy(state.race)
    planets = {int(p.id): p for p in state.planets if p.owner == state.player_id}
    out: list[SupportBaseMaterialDemand] = []
    for request in requests:
        planet = planets.get(int(request.planet_id))
        profile = profiles.get(int(request.design_slot))
        if planet is None or profile is None:
            continue
        cost = support_base_design_cost(profile)
        completion = min(100, max(0, int(request.complete_percent or 0)))
        # The native queue reports whole-percent completion.  Round remaining
        # material up so a 99% record cannot be treated as already funded.
        remaining_fraction = max(0.0, (100 - completion) / 100.0)
        mineral_remaining = {
            mineral: int(math.ceil(int(cost[mineral]) * remaining_fraction))
            for mineral in ("ironium", "boranium", "germanium")
        }
        working = working_mineral_reserve(planet, economy, queue=())
        mineral_required = {
            mineral: mineral_remaining[mineral] + int(working[mineral])
            for mineral in mineral_remaining
        }
        stock = mineral_surface_stock(planet)
        deficit = {
            mineral: max(0, mineral_required[mineral] - int(stock[mineral]))
            for mineral in mineral_required
        }
        ready = not any(deficit.values())
        shortage = "/".join(
            f"{mineral[0].upper()}={deficit[mineral]}"
            for mineral in ("ironium", "boranium", "germanium") if deficit[mineral]
        ) or "none"
        out.append(SupportBaseMaterialDemand(
            planet_id=int(planet.id),
            design_slot=int(request.design_slot),
            design_name=str(request.design_name),
            complete_percent=completion,
            resource_remaining=int(math.ceil(int(cost["resources"]) * remaining_fraction)),
            mineral_remaining=mineral_remaining,
            mineral_required=mineral_required,
            mineral_deficit=deficit,
            ready=ready,
            reason=(
                f"{request.design_name} material gate at {planet.name}: remaining I/B/G="
                f"{mineral_remaining['ironium']}/{mineral_remaining['boranium']}/{mineral_remaining['germanium']} kT; "
                f"working-stock target I/B/G={mineral_required['ironium']}/{mineral_required['boranium']}/{mineral_required['germanium']} kT; "
                f"shortage {shortage}. "
                + ("Queue may proceed." if ready else "Hold the base queue and route minerals before construction resumes.")
            ),
        ))
    return out
