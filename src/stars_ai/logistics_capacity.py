"""Shared logistics-capacity model for onion expansion.

Population logistics and industrial bulk logistics are deliberately separate:

* Opening population movement uses frequent 20,000-colonist / 200-kT pulses on
  Privateer/Medium-Freighter-class ships. A source may dispatch at most one
  population transport per turn and must remain above its protected breeder
  floor.
* Large Freighters are valued primarily for bulk mineral concentration at
  shipyards / major base-construction hubs, not because a child world wants
  more population.

This module contains no native serialization. It supplies strategy/production,
research, design-development, and diagnostics with one shared definition of
transport demand.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from typing import Any

from .expansion_network import evaluate_expansion_network
from .planet_economy import planet_population_capacity, projected_population_growth
from .util import distance

POPULATION_PULSE_COLONISTS = 20_000
POPULATION_PULSE_KT = 200
POPULATION_EXPORT_TRIGGER = 100_000
HOMEWORLD_POST_EXPORT_FLOOR = 80_000
OPENING_HUB_HOLD_FRACTION = 0.25
LATE_HUB_HOLD_FRACTION = 0.33
POPULATION_PLANNING_WARP = 7

# Production deliberately keeps the opening population fleet compact. Aggressive
# round-trip micro is preferred to solving every backlog with more hulls.
OPENING_POPULATION_FREIGHTER_CAP = 4
T30_POPULATION_FREIGHTER_CAP = 5
LATE_POPULATION_FREIGHTER_CAP = 8

# Large-freighter industrial trigger. This is intentionally much larger than a
# normal 20k population pulse or a single small hub bootstrap shipment.
BULK_LARGE_FREIGHTER_TRIGGER_KT = 600


@dataclass(frozen=True)
class ExportSourceStatus:
    planet_id: int
    ring: int
    population: int
    protected_floor: int
    dispatch_trigger: int
    downstream_backlog: int
    projected_growth: int
    exportable_population: int
    stored_pulses: int
    sustainable_pulses_per_turn: float
    ready_now: bool


@dataclass(frozen=True)
class LogisticsCapacitySnapshot:
    turn: int
    population_pulse_colonists: int
    population_pulse_kt: int
    population_lane_count: int
    active_population_exporter_ids: tuple[int, ...]
    ready_population_exporter_ids: tuple[int, ...]
    sustainable_population_pulses_per_turn: float
    average_population_round_trip_turns: float
    desired_population_freighters: int
    bulk_shipyard_deficit_kt: int
    bulk_donor_surplus_kt: int
    bulk_transferable_kt: int
    active_shipyard_build_count: int
    desired_bulk_freighters: int
    large_freighter_valuable: bool
    exporter_status: tuple[ExportSourceStatus, ...]

    def to_dict(self) -> dict:
        return asdict(self)


def population_export_floor(state: Any, hub, network=None) -> int:
    """Protected population after an economic population-freight departure.

    Opening homeworld doctrine is the explicit pulse rule requested for this AI:
    at ~100k population, one 20k packet may leave and ~80k remains. This is
    intentionally more aggressive than a literal 25%-of-HW-capacity floor.

    A graduated child hub is different: it may export only while remaining at
    or above its onion graduation/breeder floor (~25% capacity through T30).
    """
    network = network or evaluate_expansion_network(state)
    planet = next((p for p in state.planets if int(p.id) == int(hub.planet_id)), None)
    if planet is None:
        return HOMEWORLD_POST_EXPORT_FLOOR
    if network.homeworld_id is not None and int(hub.planet_id) == int(network.homeworld_id):
        return HOMEWORLD_POST_EXPORT_FLOOR
    cap = max(1, int(planet_population_capacity(planet, state.race)))
    fraction = OPENING_HUB_HOLD_FRACTION if int(network.turn) <= 30 else LATE_HUB_HOLD_FRACTION
    late_absolute = 100_000 if int(network.turn) > 30 else HOMEWORLD_POST_EXPORT_FLOOR
    return max(late_absolute, int(math.ceil(cap * fraction)))


def _children_by_parent(network) -> dict[int, list]:
    out: dict[int, list] = {}
    for hub in network.hubs:
        parent = hub.parent_exporter_id
        need = int(hub.import_population_to_25 or 0)
        if parent is None or need <= 0:
            continue
        out.setdefault(int(parent), []).append(hub)
    return out


def export_source_statuses(state: Any, network=None) -> list[ExportSourceStatus]:
    network = network or evaluate_expansion_network(state)
    children = _children_by_parent(network)
    planets = {int(p.id): p for p in state.planets if p.owner == state.player_id}
    hubs = {int(h.planet_id): h for h in network.hubs}
    out: list[ExportSourceStatus] = []
    for parent_id, child_hubs in children.items():
        planet = planets.get(int(parent_id))
        hub = hubs.get(int(parent_id))
        if planet is None or hub is None:
            continue
        backlog = sum(int(x.import_population_to_25 or 0) for x in child_hubs)
        floor = population_export_floor(state, hub, network)
        trigger = max(POPULATION_EXPORT_TRIGGER, floor + POPULATION_PULSE_COLONISTS)
        pop = int(planet.population or 0)
        exportable = max(0, pop - floor)
        stored = max(0, exportable // POPULATION_PULSE_COLONISTS)
        growth = max(0, int(projected_population_growth(planet, state.race)))
        # Long-run source replenishment is the primary rate. Existing stored
        # surplus is allowed to raise the near-term rate, but never above the
        # one-departure-per-source-per-turn phasing invariant.
        growth_rate = growth / float(POPULATION_PULSE_COLONISTS)
        stored_rate = min(1.0, stored / 5.0)
        sustainable = min(1.0, max(growth_rate, stored_rate))
        ready = bool(pop >= trigger and backlog >= 10_000)
        out.append(ExportSourceStatus(
            planet_id=int(parent_id),
            ring=int(hub.ring),
            population=pop,
            protected_floor=int(floor),
            dispatch_trigger=int(trigger),
            downstream_backlog=int(backlog),
            projected_growth=int(growth),
            exportable_population=int(exportable),
            stored_pulses=int(stored),
            sustainable_pulses_per_turn=round(float(sustainable), 4),
            ready_now=ready,
        ))
    return sorted(out, key=lambda x: (x.ring, x.planet_id))


def _round_trip_turns(state: Any, network, statuses: list[ExportSourceStatus]) -> float:
    if not statuses:
        return 0.0
    planets = {int(p.id): p for p in state.planets if p.owner == state.player_id}
    children = _children_by_parent(network)
    weighted = []
    for status in statuses:
        source = planets.get(status.planet_id)
        child_hubs = children.get(status.planet_id, [])
        if source is None or not child_hubs:
            continue
        ds = []
        for child in child_hubs:
            p = planets.get(int(child.planet_id))
            if p is not None:
                ds.append(distance(source.position, p.position))
        if not ds:
            continue
        avg_d = sum(ds) / len(ds)
        one_way = max(1, int(math.ceil(avg_d / float(POPULATION_PLANNING_WARP ** 2))))
        # Gates are not credited because native gate orders are not yet emitted;
        # loaded outbound and empty return are both conservatively flown here.
        cycle = max(2, 2 * one_way)
        weighted.append((cycle, max(0.05, status.sustainable_pulses_per_turn)))
    if not weighted:
        return 2.0
    total_w = sum(w for _, w in weighted)
    return sum(c * w for c, w in weighted) / max(0.001, total_w)


def _production_queue(state: Any, planet_id: int) -> list[dict]:
    raw = (state.native or {}).get("production_by_planet", {}) or {}
    return list(raw.get(str(int(planet_id)), raw.get(int(planet_id), [])) or [])


def _bulk_industrial_demand(state: Any, network) -> tuple[int, int, int, int]:
    """Return (deficit, donor surplus, transferable, active shipyard builds) in kT.

    Exact per-design mineral bills are not yet modeled in objective production,
    so this is intentionally a stockpile/queue pressure estimate rather than a
    fake precise cost calculation. Large Freighter value only appears at a high
    threshold where bulk concentration is clearly useful.
    """
    owned = [p for p in state.planets if p.owner == state.player_id]
    deficit = 0
    active_builds = 0
    shipyard_ids = set()
    for p in owned:
        caps = (p.native or {}).get("starbase_capabilities") or {}
        if not bool(caps.get("can_build_ships")):
            continue
        shipyard_ids.add(int(p.id))
        queue = _production_queue(state, int(p.id))
        custom_ship = any(
            int(q.get("item_type", 0) or 0) == 4
            and 0 <= int(q.get("item_id", -1) or -1) < 16
            and int(q.get("count", 0) or 0) > 0
            for q in queue
        )
        custom_base = any(
            int(q.get("item_type", 0) or 0) == 4
            and int(q.get("item_id", -1) or -1) >= 16
            and int(q.get("count", 0) or 0) > 0
            for q in queue
        )
        if custom_ship:
            active_builds += 1
        # Active fleet construction deserves a substantial forward stockpile;
        # a mature shipyard without an active custom queue gets only a modest
        # reserve and therefore will not by itself trigger Large-Freighter tech.
        if custom_ship:
            targets = (500, 350, 300)
        elif custom_base:
            targets = (350, 200, 220)
        elif int(network.turn) >= 25 and int(p.population or 0) >= 150_000:
            targets = (250, 150, 160)
        else:
            continue
        deficit += max(0, targets[0] - int(p.ironium or 0))
        deficit += max(0, targets[1] - int(p.boranium or 0))
        deficit += max(0, targets[2] - int(p.germanium or 0))

    # Hub/base bootstrap shortages contribute, but alone are normally too small
    # to cross the Large-Freighter threshold.
    deficit += int(network.bootstrap_ironium_deficit or 0)
    deficit += int(network.bootstrap_boranium_deficit or 0)
    deficit += int(network.bootstrap_germanium_deficit or 0)

    donor_surplus = 0
    for p in owned:
        # Keep healthy local reserves; concentrate only genuine excess.
        donor_surplus += max(0, int(p.ironium or 0) - 300)
        donor_surplus += max(0, int(p.boranium or 0) - 220)
        donor_surplus += max(0, int(p.germanium or 0) - 220)
    transferable = min(deficit, donor_surplus)
    return int(deficit), int(donor_surplus), int(transferable), int(active_builds)


def evaluate_logistics_capacity(state: Any) -> LogisticsCapacitySnapshot:
    network = evaluate_expansion_network(state)
    statuses = export_source_statuses(state, network)
    lanes = sum(1 for h in network.hubs if h.parent_exporter_id is not None and int(h.import_population_to_25 or 0) > 0)
    pulse_rate = sum(float(x.sustainable_pulses_per_turn) for x in statuses)
    cycle = _round_trip_turns(state, network, statuses)
    desired_pop = int(math.ceil(pulse_rate * cycle - 1e-9)) if pulse_rate > 0 else 0
    # If there is actual downstream work and a source is ready/near-ready, keep
    # at least one suitable transport in circulation.
    if lanes and statuses and desired_pop == 0:
        desired_pop = 1
    turn = int(network.turn)
    cap = OPENING_POPULATION_FREIGHTER_CAP if turn <= 20 else T30_POPULATION_FREIGHTER_CAP if turn <= 30 else LATE_POPULATION_FREIGHTER_CAP
    desired_pop = min(cap, max(0, desired_pop))

    bulk_deficit, donor_surplus, transferable, active_builds = _bulk_industrial_demand(state, network)
    large_value = bool(
        transferable >= BULK_LARGE_FREIGHTER_TRIGGER_KT
        or (active_builds >= 2 and transferable >= 400)
    )
    desired_bulk = 0
    if large_value:
        desired_bulk = 1
        if transferable >= 1_500:
            desired_bulk = 2
        if transferable >= 3_000:
            desired_bulk = 3

    return LogisticsCapacitySnapshot(
        turn=turn,
        population_pulse_colonists=POPULATION_PULSE_COLONISTS,
        population_pulse_kt=POPULATION_PULSE_KT,
        population_lane_count=int(lanes),
        active_population_exporter_ids=tuple(x.planet_id for x in statuses),
        ready_population_exporter_ids=tuple(x.planet_id for x in statuses if x.ready_now),
        sustainable_population_pulses_per_turn=round(pulse_rate, 4),
        average_population_round_trip_turns=round(cycle, 3),
        desired_population_freighters=int(desired_pop),
        bulk_shipyard_deficit_kt=int(bulk_deficit),
        bulk_donor_surplus_kt=int(donor_surplus),
        bulk_transferable_kt=int(transferable),
        active_shipyard_build_count=int(active_builds),
        desired_bulk_freighters=int(desired_bulk),
        large_freighter_valuable=bool(large_value),
        exporter_status=tuple(statuses),
    )
