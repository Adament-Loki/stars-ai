"""Expansion-network doctrine for breeder/hub/ring development.

Early Stars! expansion is treated as a relay network rather than a single
homeworld-to-frontier range problem.  Mature breeder/shipyard/refuel worlds
seed the next group of worlds, which in turn become the parents for another
ring.  Research can then score capabilities that remove the current network
bottleneck (transport hull, fuel/range, legal base hull, or gate) instead of
researching fields in isolation.

The population doctrine follows the StarsFAQ population-management guidance:
keep the homeworld near 25% capacity early when good alternate habitats exist,
allow child hubs to mature before draining them, and move toward a higher
resource-production hold later.  This module emits no native population order;
it only measures strategic backlog for research/logistics planning.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from functools import lru_cache
import csv
import math
from pathlib import Path
from typing import Any

from .planet_economy import planet_population_capacity
from .standard_mod import ComponentCategory, parse_mod_file
from .util import distance


DEFAULT_RING_HOP_LY = 160.0
OPENING_300_RADIUS_TURN = 15
OPENING_500_RADIUS_TURN = 30

HUB_SEED_FRACTION = 0.10
HUB_PARENT_READY_FRACTION = 0.25
HUB_GROWTH_FRACTION = 0.33
HUB_PRODUCTION_FRACTION = 0.50
LAYER1_MIN_HUBS = 4
LAYER1_TARGET_HUBS = 5

PRT_HE = 0


@dataclass(frozen=True)
class ExpansionHub:
    planet_id: int
    name: str
    ring: int
    distance_from_home: float
    population: int
    capacity: int
    capacity_fraction: float
    can_build_ships: bool
    can_refuel: bool
    has_starbase: bool
    frontier_worlds_160: int
    frontier_worlds_300: int
    stage: str
    parent_ready: bool
    support_ready: bool
    graduated: bool
    designated_layer1: bool
    parent_exporter_id: int | None
    import_population_to_25: int
    export_population: int
    bootstrap_mineral_deficit: dict[str, int]


@dataclass(frozen=True)
class ExpansionNetworkSnapshot:
    turn: int
    homeworld_id: int | None
    owned_radius_ly: float
    target_radius_ly: float
    radius_gap_ly: float
    ring_hop_ly: float
    deepest_owned_ring: int
    hubs: tuple[ExpansionHub, ...]
    outer_hub_ids: tuple[int, ...]
    parent_ready_hub_ids: tuple[int, ...]
    layer1_minimum_count: int
    layer1_target_count: int
    layer1_hub_ids: tuple[int, ...]
    layer1_graduated_ids: tuple[int, ...]
    layer1_pending_ids: tuple[int, ...]
    active_export_hub_ids: tuple[int, ...]
    population_export_backlog: int
    population_import_backlog: int
    bootstrap_ironium_deficit: int
    bootstrap_boranium_deficit: int
    bootstrap_germanium_deficit: int
    hubs_missing_shipyard: int
    hubs_missing_refuel: int
    frontier_worlds_inside_target: int
    frontier_worlds_beyond_current_ring: int
    frontier_worlds_beyond_ungated_hop: int
    gate_pair_opportunities: int
    improved_starbases: bool
    gates_available: bool
    gate_cargo_allowed: bool
    gate_logistics_mode: str
    bootstrap_base_name: str
    bootstrap_gate_name: str | None
    bootstrap_resources: int
    bootstrap_ironium: int
    bootstrap_boranium: int
    bootstrap_germanium: int
    gate_resources: int
    gate_ironium: int
    gate_boranium: int
    gate_germanium: int

    @property
    def expansion_network_debt(self) -> bool:
        return bool(
            self.radius_gap_ly > 40.0
            or self.population_import_backlog > 0
            or self.hubs_missing_shipyard > 0
            or self.frontier_worlds_beyond_ungated_hop > 0
        )

    @property
    def needs_heavy_transport(self) -> bool:
        # Medium Freighter base cargo is 210 kT.  A substantial population
        # backlog plus mineral bootstrap demand is a direct Large-Freighter use
        # case.  Population counts here are strategic headcount, not native
        # cargo bytes.
        return bool(
            self.population_import_backlog >= 90_000
            or self.population_export_backlog >= 90_000
            or self.bootstrap_germanium_deficit >= 90
        )

    @property
    def needs_range_infrastructure(self) -> bool:
        return bool(
            self.frontier_worlds_beyond_ungated_hop > 0
            or (self.radius_gap_ly > 80.0 and self.deepest_owned_ring >= 1)
        )

    @property
    def needs_gate_network(self) -> bool:
        return bool(
            self.gates_available
            and self.gate_pair_opportunities > 0
            and self.deepest_owned_ring >= 1
        )

    def to_dict(self) -> dict:
        return asdict(self) | {
            "expansion_network_debt": self.expansion_network_debt,
            "needs_heavy_transport": self.needs_heavy_transport,
            "needs_range_infrastructure": self.needs_range_infrastructure,
            "needs_gate_network": self.needs_gate_network,
            "gate_rule": "cargo must be unloaded before gating; gates reposition empty ships only",
            "layer1_program": {
                "minimum_hubs": self.layer1_minimum_count,
                "target_hubs": self.layer1_target_count,
                "designated": list(self.layer1_hub_ids),
                "graduated": list(self.layer1_graduated_ids),
                "pending": list(self.layer1_pending_ids),
                "graduation_rule": "population >= 25% capacity and shipyard+refuel starbase operational",
            },
        }


def _lrts(state: Any) -> set[str]:
    return {
        str(x).upper()
        for x in ((getattr(state.race, "native", {}) or {}).get("lrts", []) or [])
    }


def _prt_id(state: Any) -> int | None:
    native = getattr(state.race, "native", {}) or {}
    value = native.get("prt_id")
    if value is not None:
        return int(value)
    name = str(getattr(state.race, "primary_trait", "") or "").casefold()
    if "hyper expansion" in name:
        return PRT_HE
    return None


@lru_cache(maxsize=4)
def _stock_hub_bootstrap_cost(
    improved_starbases: bool,
    gates_available: bool,
) -> dict[str, int | str | None]:
    """Return race-legal base and gate costs as separate investments.

    Improved Starbases (ISB) is the only LRT that grants Space Dock and Ultra
    Station. Non-ISB races use the normal Space Station as the shipyard/refuel
    bootstrap target.

    Stargates are deliberately NOT folded into every hub bootstrap cost. A gate
    is a separate network investment and Stars! unloads cargo before a ship
    gates. Therefore a gate accelerates empty-ship return/reposition legs; it
    does not extend loaded population/mineral transport range.
    """
    mod_path = Path(__file__).with_name("data_hulls.mod")
    db = parse_mod_file(mod_path)
    base_id = 33 if improved_starbases else 34

    base_row = None
    for parts in csv.reader(mod_path.read_text(encoding="latin-1").splitlines()):
        if len(parts) < 15 or int(parts[0]) != 16:
            continue
        nums = [int(x) if x else 0 for x in parts[3:]]
        if nums and int(nums[0]) == base_id:
            base_row = (str(parts[2]), nums)
            break
    if base_row is None:
        base_name = "Space Dock" if improved_starbases else "Space Station"
        base_resources = base_ironium = base_boranium = base_germanium = 0
    else:
        base_name, base_nums = base_row
        base_resources = int(base_nums[8])
        base_ironium = int(base_nums[9])
        base_boranium = int(base_nums[10])
        base_germanium = int(base_nums[11])

    gate = None
    if gates_available:
        gate = next(
            (
                c for c in db.components.values()
                if int(c.category) == int(ComponentCategory.ORBITAL)
                and c.name.casefold() == "stargate 100/250"
            ),
            None,
        )

    # The bundled project MOD may omit orbital component rows. StarsAPI's
    # canonical UNEDITED.MOD defines Stargate 100/250 as 400 resources and
    # 100/40/40 I/B/G, requiring P5/C5.
    if gates_available:
        gate_resources = int(gate.resource_cost) if gate is not None else 400
        gate_ironium = int(gate.ironium) if gate is not None else 100
        gate_boranium = int(gate.boranium) if gate is not None else 40
        gate_germanium = int(gate.germanium) if gate is not None else 40
        gate_name = str(gate.name) if gate is not None else "Stargate 100/250"
    else:
        gate_resources = gate_ironium = gate_boranium = gate_germanium = 0
        gate_name = None

    return {
        "base_name": base_name,
        "base_resources": base_resources,
        "base_ironium": base_ironium,
        "base_boranium": base_boranium,
        "base_germanium": base_germanium,
        "gate_name": gate_name,
        "gate_resources": gate_resources,
        "gate_ironium": gate_ironium,
        "gate_boranium": gate_boranium,
        "gate_germanium": gate_germanium,
        # Backward-compatible totals for older diagnostics/tests. New strategy
        # must not interpret these as the cost of every hub: base and gate are
        # separate investments and cargo cannot travel through a gate.
        "resources": base_resources + gate_resources,
        "ironium": base_ironium + gate_ironium,
        "boranium": base_boranium + gate_boranium,
        "germanium": base_germanium + gate_germanium,
    }


def opening_target_radius(turn: int) -> float:
    """Strategic opening reach target: ~300 ly first, ~500 ly by turn 30."""
    turn = max(0, int(turn))
    if turn <= OPENING_300_RADIUS_TURN:
        return 300.0
    if turn <= OPENING_500_RADIUS_TURN:
        progress = (turn - OPENING_300_RADIUS_TURN) / (
            OPENING_500_RADIUS_TURN - OPENING_300_RADIUS_TURN
        )
        return 300.0 + 200.0 * progress
    return 500.0


def _homeworld(state: Any):
    owned = [p for p in state.planets if p.owner == state.player_id]
    if not owned:
        return None
    marked = [p for p in owned if bool((p.native or {}).get("is_homeworld", False))]
    if marked:
        return max(marked, key=lambda p: (int(p.population or 0), -int(p.id)))
    return max(owned, key=lambda p: (int(p.population or 0), -int(p.id)))


def _capacity_fraction(planet, race) -> tuple[int, float]:
    capacity = max(1, int(planet_population_capacity(planet, race)))
    return capacity, max(0.0, float(int(planet.population or 0)) / capacity)


def _stage(fraction: float, *, can_refuel: bool, can_build_ships: bool) -> str:
    if fraction < HUB_SEED_FRACTION:
        return "SEED"
    if fraction < HUB_PARENT_READY_FRACTION:
        return "DEVELOPING"
    if fraction < HUB_GROWTH_FRACTION:
        return "BREEDER"
    if fraction < HUB_PRODUCTION_FRACTION:
        return "MATURE_BREEDER"
    if can_refuel or can_build_ships:
        return "PRODUCTION_HUB"
    return "MATURE_WORLD"


def _source_hold_fraction(*, turn: int, is_home: bool, good_alternate_habitat: bool) -> float:
    if turn <= OPENING_500_RADIUS_TURN:
        if is_home:
            return 0.25 if good_alternate_habitat else 0.33
        return 0.33
    return 0.50


def evaluate_expansion_network(
    state: Any,
    *,
    ring_hop_ly: float = DEFAULT_RING_HOP_LY,
) -> ExpansionNetworkSnapshot:
    turn = max(0, int(state.year) - 2400)
    home = _homeworld(state)
    target_radius = opening_target_radius(turn)
    lrts = _lrts(state)
    improved_starbases = "ISB" in lrts
    gates_available = _prt_id(state) != PRT_HE
    bootstrap = _stock_hub_bootstrap_cost(improved_starbases, gates_available)

    if home is None:
        return ExpansionNetworkSnapshot(
            turn=turn,
            homeworld_id=None,
            owned_radius_ly=0.0,
            target_radius_ly=target_radius,
            radius_gap_ly=target_radius,
            ring_hop_ly=float(ring_hop_ly),
            deepest_owned_ring=0,
            hubs=(),
            outer_hub_ids=(),
            parent_ready_hub_ids=(),
            layer1_minimum_count=LAYER1_MIN_HUBS,
            layer1_target_count=LAYER1_TARGET_HUBS,
            layer1_hub_ids=(),
            layer1_graduated_ids=(),
            layer1_pending_ids=(),
            active_export_hub_ids=(),
            population_export_backlog=0,
            population_import_backlog=0,
            bootstrap_ironium_deficit=0,
            bootstrap_boranium_deficit=0,
            bootstrap_germanium_deficit=0,
            hubs_missing_shipyard=0,
            hubs_missing_refuel=0,
            frontier_worlds_inside_target=0,
            frontier_worlds_beyond_current_ring=0,
            frontier_worlds_beyond_ungated_hop=0,
            gate_pair_opportunities=0,
            improved_starbases=improved_starbases,
            gates_available=gates_available,
            gate_cargo_allowed=False,
            gate_logistics_mode="empty_ship_reposition_only",
            bootstrap_base_name=str(bootstrap["base_name"]),
            bootstrap_gate_name=bootstrap["gate_name"],
            bootstrap_resources=int(bootstrap["base_resources"]),
            bootstrap_ironium=int(bootstrap["base_ironium"]),
            bootstrap_boranium=int(bootstrap["base_boranium"]),
            bootstrap_germanium=int(bootstrap["base_germanium"]),
            gate_resources=int(bootstrap["gate_resources"]),
            gate_ironium=int(bootstrap["gate_ironium"]),
            gate_boranium=int(bootstrap["gate_boranium"]),
            gate_germanium=int(bootstrap["gate_germanium"]),
        )

    owned = [p for p in state.planets if p.owner == state.player_id]
    frontier = [p for p in state.planets if p.owner is None]
    good_alternates = [
        p for p in owned
        if p.id != home.id and (p.habitability is None or int(p.habitability) >= 33)
    ]
    good_alternate_habitat = bool(good_alternates)

    home_dist = {int(p.id): float(distance(home.position, p.position)) for p in owned}
    owned_radius = max(home_dist.values(), default=0.0)
    deepest = max(
        (int(math.ceil(d / max(1.0, ring_hop_ly))) for d in home_dist.values()),
        default=0,
    )

    prelim = []
    for p in owned:
        cap, frac = _capacity_fraction(p, state.race)
        caps = (p.native or {}).get("starbase_capabilities") or {}
        can_build = bool(caps.get("can_build_ships"))
        can_refuel = bool(caps.get("can_refuel"))
        ring = (
            0 if int(p.id) == int(home.id)
            else max(1, int(math.ceil(home_dist[int(p.id)] / max(1.0, ring_hop_ly))))
        )
        parent_ready = bool(frac >= HUB_PARENT_READY_FRACTION)
        support_ready = bool(can_build and can_refuel)
        prelim.append((p, cap, frac, can_build, can_refuel, ring, parent_ready, support_ready))

    # Formal opening program: designate the best 4-5 owned Ring-1 worlds as the
    # homeworld's children. Existing support bases and nearly mature breeders are
    # sticky by score so the designation does not thrash as new colonies appear.
    layer1_ranked = []
    for p, cap, frac, can_build, can_refuel, ring, parent_ready, support_ready in prelim:
        if int(p.id) == int(home.id) or ring != 1:
            continue
        if p.habitability is not None and int(p.habitability) <= 0:
            continue
        f160 = sum(1 for q in frontier if distance(p.position, q.position) <= ring_hop_ly)
        strategic = float((p.native or {}).get("strategic_value", 0.5) or 0.5)
        hab = float(p.habitability if p.habitability is not None else 35)
        radial = max(0.0, 1.0 - abs(home_dist[int(p.id)] - 130.0) / 130.0)
        score = (
            (3.0 if support_ready else 0.0)
            + 2.2 * min(1.0, frac / HUB_PARENT_READY_FRACTION)
            + 0.018 * max(0.0, hab)
            + 0.32 * min(6, f160)
            + 0.45 * strategic
            + 0.55 * radial
            + 0.25 * min(2.0, (int(p.factories or 0) + int(p.mines or 0)) / 120.0)
        )
        layer1_ranked.append((score, int(p.id)))
    layer1_ranked.sort(key=lambda row: (-row[0], row[1]))
    layer1_ids = tuple(pid for _, pid in layer1_ranked[:LAYER1_TARGET_HUBS])
    layer1_set = set(layer1_ids)

    prelim_by_id = {int(row[0].id): row for row in prelim}
    layer1_graduated_ids = tuple(sorted(
        pid for pid in layer1_ids
        if prelim_by_id[pid][6] and prelim_by_id[pid][7]
    ))
    layer1_graduated_set = set(layer1_graduated_ids)
    layer1_pending_ids = tuple(pid for pid in layer1_ids if pid not in layer1_graduated_set)

    # A ring can only parent the next ring after it reaches ~25% population AND
    # has a real shipyard/refuel starbase. The homeworld remains the parent for
    # pending Layer-1 hubs only; it never skips directly to Layer 2.
    graduated_by_ring: dict[int, list] = {}
    for row in prelim:
        p, cap, frac, can_build, can_refuel, ring, parent_ready, support_ready = row
        if int(p.id) == int(home.id):
            continue
        if parent_ready and support_ready and (ring != 1 or int(p.id) in layer1_graduated_set):
            graduated_by_ring.setdefault(int(ring), []).append(p)

    parent_positions = [home]
    for worlds in graduated_by_ring.values():
        parent_positions.extend(worlds)

    hubs: list[ExpansionHub] = []
    active_export_ids: set[int] = set()
    for p, cap, frac, can_build, can_refuel, ring, parent_ready, support_ready in prelim:
        f160 = sum(1 for q in frontier if distance(p.position, q.position) <= ring_hop_ly)
        f300 = sum(1 for q in frontier if distance(p.position, q.position) <= 300.0)

        is_home = int(p.id) == int(home.id)
        designated_layer1 = int(p.id) in layer1_set
        graduated = bool(parent_ready and support_ready and (ring != 1 or designated_layer1))

        parent_exporter_id = None
        if designated_layer1:
            parent_exporter_id = int(home.id)
        elif ring >= 2:
            parents = graduated_by_ring.get(ring - 1, [])
            if parents:
                parent_exporter_id = int(min(parents, key=lambda q: distance(p.position, q.position)).id)

        # The HW exports only to still-pending designated Layer-1 hubs. A child
        # becomes an exporter as soon as it is a ~25% support hub, and retains
        # roughly 25% during the opening so subsequent growth feeds the next ring.
        if is_home:
            hold = _source_hold_fraction(
                turn=turn, is_home=True, good_alternate_habitat=bool(layer1_pending_ids)
            )
            export_pop = (
                max(0, int(p.population or 0) - int(round(cap * hold)))
                if layer1_pending_ids else 0
            )
            if export_pop > 0:
                active_export_ids.add(int(p.id))
            import_pop = 0
        else:
            hold = 0.25 if turn <= OPENING_500_RADIUS_TURN else 0.33
            export_pop = (
                max(0, int(p.population or 0) - int(round(cap * hold)))
                if graduated else 0
            )
            if export_pop > 0:
                active_export_ids.add(int(p.id))
            import_pop = (
                max(0, int(round(cap * HUB_PARENT_READY_FRACTION)) - int(p.population or 0))
                if parent_exporter_id is not None and not graduated else 0
            )

        strategic_hub = bool(
            designated_layer1
            or parent_exporter_id is not None
            or (not is_home and ring >= max(1, deepest - 1) and f160 >= 2)
        )
        mineral_deficit = {"ironium": 0, "boranium": 0, "germanium": 0}
        if strategic_hub and not (can_build and can_refuel):
            for key in mineral_deficit:
                mineral_deficit[key] = max(
                    0,
                    int(bootstrap[{
                        "ironium": "base_ironium",
                        "boranium": "base_boranium",
                        "germanium": "base_germanium",
                    }[key]]) - int(getattr(p, key, 0) or 0),
                )

        hubs.append(ExpansionHub(
            planet_id=int(p.id),
            name=str(p.name),
            ring=ring,
            distance_from_home=round(home_dist[int(p.id)], 3),
            population=int(p.population or 0),
            capacity=cap,
            capacity_fraction=frac,
            can_build_ships=can_build,
            can_refuel=can_refuel,
            has_starbase=bool((p.native or {}).get("has_starbase", False)),
            frontier_worlds_160=f160,
            frontier_worlds_300=f300,
            stage=_stage(frac, can_refuel=can_refuel, can_build_ships=can_build),
            parent_ready=parent_ready,
            support_ready=support_ready,
            graduated=graduated,
            designated_layer1=designated_layer1,
            parent_exporter_id=parent_exporter_id,
            import_population_to_25=import_pop,
            export_population=export_pop,
            bootstrap_mineral_deficit=mineral_deficit,
        ))

    outer_threshold = max(1, deepest)
    outer_hubs = [
        h for h in hubs
        if h.ring >= outer_threshold and h.planet_id != int(home.id)
    ]
    if not outer_hubs:
        outer_hubs = [
            h for h in hubs
            if h.ring >= max(1, deepest - 1) and h.planet_id != int(home.id)
        ]

    frontier_inside_target = 0
    frontier_beyond_ring = 0
    frontier_beyond_hop = 0
    for p in frontier:
        d_home = distance(home.position, p.position)
        if d_home > target_radius:
            continue
        frontier_inside_target += 1
        nearest_parent = min(
            (distance(p.position, q.position) for q in parent_positions),
            default=9999.0,
        )
        if d_home > owned_radius + 0.5 * ring_hop_ly:
            frontier_beyond_ring += 1
        if nearest_parent > ring_hop_ly:
            frontier_beyond_hop += 1

    # Gate pairs measure EMPTY-ship return/reposition opportunities only.
    # Loaded cargo is unloaded before gating and must fly its freight leg normally.
    gate_pairs = 0
    if gates_available:
        gate_nodes = [
            p for p, cap, frac, can_build, can_refuel, ring, ready, support_ready in prelim
            if support_ready and ready
        ]
        for i, a in enumerate(gate_nodes):
            for b in gate_nodes[i + 1:]:
                d = distance(a.position, b.position)
                if 80.0 <= d <= 250.0:
                    gate_pairs += 1

    pipeline_hubs = [h for h in hubs if h.designated_layer1 or h.parent_exporter_id is not None]
    pop_export = sum(h.export_population for h in hubs)
    pop_import = sum(h.import_population_to_25 for h in pipeline_hubs)
    i_def = sum(h.bootstrap_mineral_deficit["ironium"] for h in pipeline_hubs)
    b_def = sum(h.bootstrap_mineral_deficit["boranium"] for h in pipeline_hubs)
    g_def = sum(h.bootstrap_mineral_deficit["germanium"] for h in pipeline_hubs)
    missing_shipyard = sum(
        1 for h in pipeline_hubs
        if h.population >= 55_000 and not h.can_build_ships
    )
    missing_refuel = sum(
        1 for h in pipeline_hubs
        if h.population >= 55_000 and not h.can_refuel
    )

    return ExpansionNetworkSnapshot(
        turn=turn,
        homeworld_id=int(home.id),
        owned_radius_ly=round(owned_radius, 3),
        target_radius_ly=round(target_radius, 3),
        radius_gap_ly=round(max(0.0, target_radius - owned_radius), 3),
        ring_hop_ly=float(ring_hop_ly),
        deepest_owned_ring=deepest,
        hubs=tuple(sorted(hubs, key=lambda h: (h.ring, h.distance_from_home, h.planet_id))),
        outer_hub_ids=tuple(sorted(h.planet_id for h in outer_hubs)),
        parent_ready_hub_ids=tuple(sorted(h.planet_id for h in hubs if h.parent_ready)),
        layer1_minimum_count=LAYER1_MIN_HUBS,
        layer1_target_count=LAYER1_TARGET_HUBS,
        layer1_hub_ids=tuple(sorted(layer1_ids)),
        layer1_graduated_ids=tuple(sorted(layer1_graduated_ids)),
        layer1_pending_ids=tuple(sorted(layer1_pending_ids)),
        active_export_hub_ids=tuple(sorted(active_export_ids)),
        population_export_backlog=pop_export,
        population_import_backlog=pop_import,
        bootstrap_ironium_deficit=i_def,
        bootstrap_boranium_deficit=b_def,
        bootstrap_germanium_deficit=g_def,
        hubs_missing_shipyard=missing_shipyard,
        hubs_missing_refuel=missing_refuel,
        frontier_worlds_inside_target=frontier_inside_target,
        frontier_worlds_beyond_current_ring=frontier_beyond_ring,
        frontier_worlds_beyond_ungated_hop=frontier_beyond_hop,
        gate_pair_opportunities=gate_pairs,
        improved_starbases=improved_starbases,
        gates_available=gates_available,
        gate_cargo_allowed=False,
        gate_logistics_mode="empty_ship_reposition_only",
        bootstrap_base_name=str(bootstrap["base_name"]),
        bootstrap_gate_name=bootstrap["gate_name"],
        bootstrap_resources=int(bootstrap["base_resources"]),
        bootstrap_ironium=int(bootstrap["base_ironium"]),
        bootstrap_boranium=int(bootstrap["base_boranium"]),
        bootstrap_germanium=int(bootstrap["base_germanium"]),
        gate_resources=int(bootstrap["gate_resources"]),
        gate_ironium=int(bootstrap["gate_ironium"]),
        gate_boranium=int(bootstrap["gate_boranium"]),
        gate_germanium=int(bootstrap["gate_germanium"]),
    )
