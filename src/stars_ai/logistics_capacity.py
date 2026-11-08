"""Shared logistics-capacity model for onion expansion.

Population logistics and industrial bulk logistics share the same carriers:

* A source retains its protected breeder floor, while every available
  freighter may take the population that its hold, the source's surplus, and
  the selected destination's remaining need allow.  There is no fixed
  population packet size or same-turn departure count.
* Large Freighters remain useful for mineral concentration at shipyards / major
  base-construction hubs, and now also accelerate high-priority population
  development when they are available.

This module contains no native serialization. It supplies strategy/production,
research, design-development, and diagnostics with one shared definition of
transport demand.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from typing import Any

from .expansion_network import evaluate_expansion_network
from .planet_economy import breeder_export_floor, projected_population_growth
from .starbase_planner import plan_support_base_material_demands
from .util import distance

POPULATION_PULSE_COLONISTS = 20_000
POPULATION_PULSE_KT = 200
# ``POPULATION_PULSE_*`` remains a capacity-planning unit.  It is not a
# transport manifest, readiness, or same-turn departure cap.
HOMEWORLD_POST_EXPORT_FLOOR = 80_000
OPENING_HUB_HOLD_FRACTION = 0.25
LATE_HUB_HOLD_FRACTION = 0.33
POPULATION_PLANNING_WARP = 7
POPULATION_DELIVERY_HORIZON_COLONISTS = 200_000

# Production deliberately keeps the opening population fleet compact. Aggressive
# round-trip micro is preferred to solving every backlog with more hulls.
OPENING_POPULATION_FREIGHTER_CAP = 4
T30_POPULATION_FREIGHTER_CAP = 5
LATE_POPULATION_FREIGHTER_CAP = 8

# Large-freighter industrial trigger. This is intentionally much larger than a
# capacity-planning population pulse or a single small hub bootstrap shipment.
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
    max_export_colonists: int
    max_export_kt: int
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
    live_freighter_count: int
    population_committed_freighters: int
    downstream_population_backlog: int
    exportable_population: int
    protected_population_floor: int
    available_freighter_capacity_kt: int
    active_population_transport_capacity_kt: int
    desired_population_transport_capacity_kt: int
    industrial_freighters_available: int
    bulk_shipyard_deficit_kt: int
    bulk_donor_surplus_kt: int
    bulk_transferable_kt: int
    active_shipyard_build_count: int
    desired_bulk_freighters: int
    large_freighter_valuable: bool
    exporter_status: tuple[ExportSourceStatus, ...]

    def to_dict(self) -> dict:
        return asdict(self)


def _freighter_capacity(fleet: Any) -> int:
    native = getattr(fleet, "native", {}) or {}
    return max(0, int(
        getattr(fleet, "cargo_capacity", 0)
        or native.get("cargo_capacity", 0)
        or 0
    ))


def _freighter_population_cargo(fleet: Any) -> int:
    cargo = (getattr(fleet, "native", {}) or {}).get("cargo", {}) or {}
    return max(
        int(getattr(fleet, "cargo_population", 0) or 0),
        int(cargo.get("population", 0) or 0) * 100,
    )


def _freighter_cargo_mass(fleet: Any) -> int:
    cargo = (getattr(fleet, "native", {}) or {}).get("cargo", {}) or {}
    return sum(max(0, int(cargo.get(kind, 0) or 0)) for kind in (
        "ironium", "boranium", "germanium", "population",
    ))


def population_transport_throughput(
    state: Any,
    snapshot: LogisticsCapacitySnapshot | None = None,
    *,
    assigned_population_fleet_ids: tuple[int, ...] | list[int] | set[int] = (),
) -> dict[str, Any]:
    """Return the explicit capacity contract for population transport.

    ``active`` means a hold is carrying population, is remembered as being on
    a population leg, or was assigned to the population shuttle this planning
    pass.  Empty holds at owned worlds are separately *staged* capacity: they
    can be reserved from mineral work immediately, but must not be counted a
    second time after the scheduler assigns them.  The shortfall is therefore
    the missing capacity after both active and staged usable hulls are counted.
    """
    snapshot = snapshot or evaluate_logistics_capacity(state)
    assigned = {int(fleet_id) for fleet_id in assigned_population_fleet_ids}
    active_ids: set[int] = set()
    staged_ids: set[int] = set()
    active_kt = 0
    staged_kt = 0
    for fleet in getattr(state, "fleets", []) or []:
        if fleet.owner != state.player_id or str(fleet.role) != "freighter":
            continue
        capacity = _freighter_capacity(fleet)
        if capacity <= 0:
            continue
        fleet_id = int(fleet.id)
        native = getattr(fleet, "native", {}) or {}
        mission = str(
            native.get("transport_mission_class", native.get("mission_class", ""))
            or ""
        ).lower()
        active = bool(
            fleet_id in assigned
            or _freighter_population_cargo(fleet) > 0
            or mission in {"population", "population_export", "return_for_population_export"}
        )
        if active:
            active_ids.add(fleet_id)
            active_kt += capacity
        elif fleet.destination_planet_id is None and _freighter_cargo_mass(fleet) == 0:
            staged_ids.add(fleet_id)
            staged_kt += capacity

    desired_kt = int(snapshot.desired_population_transport_capacity_kt)
    deployable_kt = active_kt + staged_kt
    return {
        "backlog_colonists": int(snapshot.downstream_population_backlog),
        "exportable_population": int(snapshot.exportable_population),
        "protected_source_floor": int(snapshot.protected_population_floor),
        "active_population_transport_kt": int(active_kt),
        "round_trip_turns": float(snapshot.average_population_round_trip_turns),
        "desired_transport_kt": desired_kt,
        "transport_shortfall": max(0, desired_kt - deployable_kt),
        "staged_population_transport_kt": int(staged_kt),
        "deployable_population_transport_kt": int(deployable_kt),
        "active_freighter_ids": sorted(active_ids),
        "staged_freighter_ids": sorted(staged_ids),
        "policy": (
            "exporter -> colony -> unload -> return -> reload; preserve the "
            "source floor and reserve staged freight before mineral work when "
            "population throughput is short."
        ),
    }


def population_export_floor(state: Any, hub, network=None) -> int:
    """Protected population after an economic population-freight departure.

    Opening homeworld doctrine keeps an 80k protected breeder floor.  Export
    size is then chosen from the receiving world's need and the transport's
    hold, never by an 8k or 20k artificial packet cap.

    A graduated child hub is different: it may export only while remaining at
    or above its onion graduation/breeder floor (~25% capacity through T30).
    """
    network = network or evaluate_expansion_network(state)
    planet = next((p for p in state.planets if int(p.id) == int(hub.planet_id)), None)
    if planet is None:
        return HOMEWORLD_POST_EXPORT_FLOOR
    return breeder_export_floor(planet, state.race)


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
        pop = int(planet.population or 0)
        # This status field is a snapshot of total currently exportable
        # population, not a fixed per-ship allowance.  The scheduler spends it
        # across however many available freighters are useful this turn while
        # maintaining the protected floor.
        max_export = max(0, pop - floor)
        max_export_kt = max_export // 100
        exportable = max(0, pop - floor)
        stored = max(0, exportable // POPULATION_PULSE_COLONISTS)
        growth = max(0, int(projected_population_growth(planet, state.race)))
        # Long-run source replenishment is the primary rate. Existing stored
        # surplus raises the near-term capacity estimate.  This is a fleet
        # sizing signal only; it does not restrict actual departures.
        growth_rate = growth / float(POPULATION_PULSE_COLONISTS)
        stored_rate = min(1.0, stored / 5.0)
        sustainable = min(1.0, max(growth_rate, stored_rate))
        # The native cargo field is expressed in kT (100 colonists).  Beyond
        # that protocol granularity, readiness is entirely source-surplus and
        # destination-demand driven.
        trigger = floor
        ready = bool(exportable >= 100 and backlog >= 100)
        out.append(ExportSourceStatus(
            planet_id=int(parent_id),
            ring=int(hub.ring),
            population=pop,
            protected_floor=int(floor),
            dispatch_trigger=int(trigger),
            downstream_backlog=int(backlog),
            projected_growth=int(growth),
            exportable_population=int(exportable),
            max_export_colonists=int(max_export),
            max_export_kt=int(max_export_kt),
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


def _live_freighter_commitment(state: Any) -> tuple[int,int]:
    """Return (live freighters, hulls currently carrying colonists).

    A loaded population carrier cannot also haul the minerals required by an
    outer onion-layer base.  We use current native cargo, rather than a
    persona's preferred playstyle, as the evidence that a hull is committed.
    """
    live=0
    population_committed=0
    for fleet in getattr(state,"fleets",[]) or []:
        if fleet.owner != state.player_id or str(fleet.role) != "freighter":
            continue
        ship_count=sum(
            max(0,int(n or 0))
            for n in ((getattr(fleet,"native",{}) or {}).get("ship_count",[]) or [])
        )
        hulls=max(1,ship_count)
        live += hulls
        cargo=(getattr(fleet,"native",{}) or {}).get("cargo",{}) or {}
        population=max(
            int(getattr(fleet,"cargo_population",0) or 0),
            int(cargo.get("population",0) or 0)*100,
        )
        if population>0:
            population_committed += hulls
    return int(live),int(population_committed)


def _population_transport_capacity(state: Any) -> tuple[int,int]:
    """Return (available empty hold, currently active population hold) in kT.

    A hull carrying colonists or flying a remembered population leg is active
    throughput.  Empty capacity is reported separately so production can see
    whether it should stage existing ships or actually commission another.
    """
    available=0
    active=0
    for fleet in getattr(state,"fleets",[]) or []:
        if fleet.owner != state.player_id or str(fleet.role) != "freighter":
            continue
        native=getattr(fleet,"native",{}) or {}
        capacity=_freighter_capacity(fleet)
        population=_freighter_population_cargo(fleet)
        mission=str(native.get("transport_mission_class",native.get("mission_class","")) or "").lower()
        if population>0 or mission in {"population","population_export","return_for_population_export"}:
            active+=capacity
        elif fleet.destination_planet_id is None and _freighter_cargo_mass(fleet) == 0:
            available+=capacity
    return int(available),int(active)


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

    # Exact remaining support-base bills are more meaningful than the old
    # generic bootstrap allowance.  A stalled Space Station now contributes its
    # actual I/B/G deficit, which is precisely the use case for a Large
    # Freighter once compact population carriers are occupied.
    material_demands=plan_support_base_material_demands(state)
    deficit += sum(
        sum(int(amount) for amount in demand.mineral_deficit.values())
        for demand in material_demands
        if not demand.ready
    )

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
    population_backlog=sum(int(status.downstream_backlog or 0) for status in statuses)
    # A huge accumulated import debt needs a concrete throughput objective,
    # not merely a source-growth estimate.  One compact/medium hold should
    # remove roughly 200k colonists over the planning horizon; the existing
    # turn-based fleet cap still keeps this an incremental program.
    backlog_freighters=int(math.ceil(
        population_backlog / float(POPULATION_DELIVERY_HORIZON_COLONISTS)
    )) if population_backlog else 0
    desired_pop=max(desired_pop,backlog_freighters)
    # If there is actual downstream work and a source is ready/near-ready, keep
    # at least one suitable transport in circulation.
    if lanes and statuses and desired_pop == 0:
        desired_pop = 1
    turn = int(network.turn)
    cap = OPENING_POPULATION_FREIGHTER_CAP if turn <= 20 else T30_POPULATION_FREIGHTER_CAP if turn <= 30 else LATE_POPULATION_FREIGHTER_CAP
    desired_pop = min(cap, max(0, desired_pop))
    live_freighters,population_committed=_live_freighter_commitment(state)
    available_capacity,active_population_capacity=_population_transport_capacity(state)
    desired_population_capacity=max(
        0,
        int(math.ceil(population_backlog / float(POPULATION_DELIVERY_HORIZON_COLONISTS)))
        * POPULATION_PULSE_KT,
    )

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
        live_freighter_count=int(live_freighters),
        population_committed_freighters=int(population_committed),
        downstream_population_backlog=int(population_backlog),
        exportable_population=sum(int(status.exportable_population) for status in statuses),
        protected_population_floor=sum(int(status.protected_floor) for status in statuses),
        available_freighter_capacity_kt=int(available_capacity),
        active_population_transport_capacity_kt=int(active_population_capacity),
        desired_population_transport_capacity_kt=int(desired_population_capacity),
        industrial_freighters_available=max(0,int(live_freighters)-int(population_committed)),
        bulk_shipyard_deficit_kt=int(bulk_deficit),
        bulk_donor_surplus_kt=int(donor_surplus),
        bulk_transferable_kt=int(transferable),
        active_shipyard_build_count=int(active_builds),
        desired_bulk_freighters=int(desired_bulk),
        large_freighter_valuable=bool(large_value),
        exporter_status=tuple(statuses),
    )
