"""Round-trip population logistics for the onion expansion strategy.

Opening doctrine:

* economic population moves in 20,000-colonist / 200-kT pulses;
* a source normally dispatches at most ONE population transport per turn;
* the opening homeworld starts pulsing at about 100,000 population and keeps
  about 80,000 behind after a departure;
* a graduated child hub keeps at least ~25% of racial planet capacity before
  exporting onward;
* the homeworld feeds only designated, not-yet-graduated Layer-1 hubs;
* after a Layer-1 hub has a shipyard/refuel starbase AND ~25% population, the
  homeworld stops feeding it and the hub becomes an exporter to Layer 2;
* empty transports return/reposition to active export hubs so scarce early
  Privateer/Medium-Freighter-class hulls perform repeated round trips.

Loaded population never receives stargate range credit. The freight leg flies
normally; only an empty ship may later use a gate once native gate movement is
validated.

The source-load quantity byte is still an EXPERIMENTAL generalization of the
controlled 25-kT sample. This planner intentionally requests only 200 kT for
opening economic freight, which fits a stock Medium Freighter or Privateer.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .expansion_network import evaluate_expansion_network
from .fuel_planner import (
    fastest_fuel_safe_warp,
    mission_reachable_with_planned_cargo,
    profile_with_planned_cargo,
)
from .logistics_capacity import (
    POPULATION_PULSE_COLONISTS,
    POPULATION_PULSE_KT,
    evaluate_logistics_capacity,
    export_source_statuses,
)
from .util import distance
from .warp_policy import mission_warp

COLONISTS_PER_KT = 100
MIN_POPULATION_CARRIER_KT = POPULATION_PULSE_KT


@dataclass(frozen=True)
class PopulationTransferIntent:
    fleet_id: int
    source_planet_id: int
    destination_planet_id: int
    population_kt: int
    population_colonists: int
    warp: int
    score: float
    source_ring: int
    destination_ring: int
    source_population_before: int
    source_population_after: int
    source_protected_floor: int
    reason: str

    def to_payload(self) -> dict:
        return asdict(self) | {
            "mission": "transport",
            "unload": {
                "ironium": "all",
                "boranium": "all",
                "germanium": "all",
                "population": "all",
            },
            "fuel": "load_optimal",
            "native_population_encoding": "experimental_one_byte_kt",
            "gate_allowed_while_loaded": False,
            "round_trip_logistics": True,
            "population_dispatch_policy": "one_20k_pulse_per_source_per_turn",
        }


@dataclass(frozen=True)
class EmptyFreighterReturnIntent:
    fleet_id: int
    current_planet_id: int
    export_planet_id: int
    warp: int
    export_ring: int
    downstream_population_backlog: int
    score: float
    reason: str

    def to_payload(self) -> dict:
        return {
            "fleet_id": self.fleet_id,
            "destination_planet_id": self.export_planet_id,
            "warp": self.warp,
            "mission": "return_for_population_export",
            "round_trip_logistics": True,
            "population_export_hub_id": self.export_planet_id,
            "downstream_population_backlog": self.downstream_population_backlog,
            "gate_preferred_if_legal": True,
            "gate_native_status": "flight_fallback_until_gate_order_validated",
        }


def _planet_under_fleet(fleet, owned):
    pid = int((fleet.native or {}).get("position_object_id", -1))
    direct = next((p for p in owned if int(p.id) == pid), None)
    if direct is not None:
        return direct
    return next((
        p for p in owned
        if abs(float(p.position.x) - float(fleet.position.x)) <= 0.5
        and abs(float(p.position.y) - float(fleet.position.y)) <= 0.5
    ), None)


def _cargo_mass(fleet) -> int:
    cargo = (fleet.native or {}).get("cargo", {}) or {}
    return sum(int(cargo.get(k, 0) or 0) for k in (
        "ironium", "boranium", "germanium", "population"
    ))


def _receiver_score(state, planet, hub) -> float:
    cap = max(1, int(getattr(hub, "capacity", 1) or 1))
    frac = int(planet.population or 0) / cap
    hab = int(planet.habitability if planet.habitability is not None else 0)
    strategic = float((planet.native or {}).get("strategic_value", 0.5) or 0.5)
    score = 0.0
    # The last full 20k packet before graduation is strategically valuable, but
    # we do not send a partial packet merely to make the arithmetic exact.
    score += 2.5 * min(1.0, max(0.0, (0.25 - frac) / 0.25))
    score += 0.012 * max(0, hab)
    score += 0.20 * min(6, int(getattr(hub, "frontier_worlds_160", 0) or 0))
    score += 0.34 * max(0, int(getattr(hub, "ring", 0) or 0))
    score += 0.40 * strategic
    if bool(getattr(hub, "designated_layer1", False)):
        score += 2.0
    return score


def _available_empty_population_freighters(state):
    """Small/medium population carriers; bulk freighters are not preferred here."""
    out = []
    for f in state.fleets:
        if f.owner != state.player_id or f.role != "freighter" or f.destination_planet_id is not None:
            continue
        if _cargo_mass(f) != 0:
            continue
        cap = int((f.native or {}).get("cargo_capacity", 0) or 0)
        if cap < MIN_POPULATION_CARRIER_KT:
            continue
        out.append(f)
    return out


def _safe_empty_warp(fleet, target) -> int | None:
    fp = (fleet.native or {}).get("fuel_profile")
    if fp:
        flags = (fleet.native or {}).get("race_fuel_flags", {}) or {}
        safe = fastest_fuel_safe_warp(
            fp,
            distance(fleet.position, target.position),
            "return_for_population_export",
            ife=bool(flags.get("ife")),
            ce=bool(flags.get("ce")),
        )
        return None if safe is None else int(safe)
    return int(mission_warp(fleet, target.position, "return_for_population_export"))


def plan_population_redistribution(
    state: Any,
    plan=None,
    *,
    max_transfers: int = 4,
) -> list[PopulationTransferIntent]:
    """Plan phased parent->child 20k population pulses.

    The dispatch budget is centralized per source. Two transports sitting on the
    same homeworld cannot both independently decide to load population in the
    same turn. That phasing protects the breeder and lets it replenish between
    departures.
    """
    owned = [p for p in state.planets if p.owner == state.player_id]
    if len(owned) < 2:
        return []

    network = evaluate_expansion_network(state)
    hubs = {int(h.planet_id): h for h in network.hubs}
    planets = {int(p.id): p for p in owned}
    statuses = {x.planet_id: x for x in export_source_statuses(state, network)}

    # Only children that still need at least one complete 20k packet qualify.
    receivers = []
    for hub in network.hubs:
        parent_id = hub.parent_exporter_id
        need = int(hub.import_population_to_25 or 0)
        if parent_id is None or need < POPULATION_PULSE_COLONISTS:
            continue
        p = planets.get(int(hub.planet_id))
        if p is None or (p.habitability is not None and int(p.habitability) <= 0):
            continue
        receivers.append((_receiver_score(state, p, hub), p, need, hub, int(parent_id)))
    receivers.sort(key=lambda row: (-row[0], row[3].ring, row[1].id))
    if not receivers:
        return []

    freighters = _available_empty_population_freighters(state)
    if not freighters:
        return []

    dispatched_sources: set[int] = set()
    used_fleets: set[int] = set()
    planned_to: dict[int, int] = {}
    intents: list[PopulationTransferIntent] = []

    for receiver_score, target, raw_need, target_hub, parent_id in receivers:
        if len(intents) >= int(max_transfers):
            break
        if parent_id in dispatched_sources:
            # Hard opening micro invariant: no 2+ population loads from one
            # export planet in the same turn.
            continue
        status = statuses.get(parent_id)
        source_hub = hubs.get(parent_id)
        source = planets.get(parent_id)
        if status is None or source_hub is None or source is None or not status.ready_now:
            continue
        remaining_need = int(raw_need) - int(planned_to.get(int(target.id), 0))
        if remaining_need < POPULATION_PULSE_COLONISTS:
            continue

        choices = []
        for fleet in freighters:
            if int(fleet.id) in used_fleets:
                continue
            here = _planet_under_fleet(fleet, owned)
            if here is None or int(here.id) != parent_id:
                continue
            cargo_cap = int((fleet.native or {}).get("cargo_capacity", 0) or 0)
            if cargo_cap < POPULATION_PULSE_KT:
                continue
            if int(source.population or 0) - POPULATION_PULSE_COLONISTS < int(status.protected_floor):
                continue
            if not mission_reachable_with_planned_cargo(
                fleet, target.position, "transport", {"population": POPULATION_PULSE_KT}
            ):
                continue
            travel = distance(source.position, target.position)
            # Prefer long-range compact population carriers over an oversized
            # bulk freighter when both are otherwise usable.
            oversize_penalty = max(0, cargo_cap - 300) / 600.0
            choices.append((receiver_score - travel / 500.0 - oversize_penalty, fleet, cargo_cap))

        if not choices:
            continue
        score, fleet, cargo_cap = max(choices, key=lambda row: row[0])

        fp = (fleet.native or {}).get("fuel_profile")
        if fp:
            flags = (fleet.native or {}).get("race_fuel_flags", {}) or {}
            planned = profile_with_planned_cargo(fp, {"population": POPULATION_PULSE_KT})
            safe_warp = fastest_fuel_safe_warp(
                planned,
                distance(fleet.position, target.position),
                "transport",
                ife=bool(flags.get("ife")),
                ce=bool(flags.get("ce")),
            )
            if safe_warp is None:
                continue
            selected_warp = int(safe_warp)
        else:
            selected_warp = int(mission_warp(fleet, target.position, "transport"))

        before = int(source.population or 0)
        after = before - POPULATION_PULSE_COLONISTS
        dispatched_sources.add(parent_id)
        used_fleets.add(int(fleet.id))
        planned_to[int(target.id)] = int(planned_to.get(int(target.id), 0)) + POPULATION_PULSE_COLONISTS

        intents.append(PopulationTransferIntent(
            fleet_id=int(fleet.id),
            source_planet_id=parent_id,
            destination_planet_id=int(target.id),
            population_kt=POPULATION_PULSE_KT,
            population_colonists=POPULATION_PULSE_COLONISTS,
            warp=selected_warp,
            score=round(float(score), 3),
            source_ring=int(source_hub.ring),
            destination_ring=int(target_hub.ring),
            source_population_before=before,
            source_population_after=after,
            source_protected_floor=int(status.protected_floor),
            reason=(
                f"Onion population pulse {source.name} (ring {source_hub.ring})->{target.name} "
                f"(ring {target_hub.ring}): load exactly {POPULATION_PULSE_COLONISTS:,} colonists "
                f"({POPULATION_PULSE_KT} kT) into {fleet.name} ({cargo_cap} kT hold). "
                f"Projected source {before:,}->{after:,}; protected floor={status.protected_floor:,}. "
                "This is the only population departure allowed from this source this turn. Loaded cargo "
                "flies normally; after destination unload the empty hull returns/repositions for another pulse."
            ),
        ))
    return intents


def plan_empty_freighter_returns(
    state: Any,
    transfer_intents: list[PopulationTransferIntent] | tuple[PopulationTransferIntent, ...] = (),
    *,
    max_returns: int = 5,
) -> list[EmptyFreighterReturnIntent]:
    """Route idle empty transports back to useful export hubs.

    Exporters are based on downstream demand and sustainable pulse generation,
    not merely whether they can dispatch *this* turn. This allows an empty ship
    to return while the breeder is replenishing, then wait at source for the
    next 100k/20k pulse trigger.
    """
    owned = [p for p in state.planets if p.owner == state.player_id]
    if not owned:
        return []
    network = evaluate_expansion_network(state)
    logistics = evaluate_logistics_capacity(state)
    hubs = {int(h.planet_id): h for h in network.hubs}
    planets = {int(p.id): p for p in owned}
    statuses = {x.planet_id: x for x in logistics.exporter_status}
    used = {int(x.fleet_id) for x in transfer_intents}

    exporters = []
    for pid, status in statuses.items():
        hub = hubs.get(pid)
        p = planets.get(pid)
        if hub is None or p is None or int(status.downstream_backlog) < POPULATION_PULSE_COLONISTS:
            continue
        exporters.append((pid, p, hub, status))
    if not exporters:
        return []

    # Count empty hulls already staged at each exporter so return traffic spreads
    # across active sources rather than creating a parking lot at the homeworld.
    staged = {pid: 0 for pid, *_ in exporters}
    empty_freighters = _available_empty_population_freighters(state)
    for fleet in empty_freighters:
        here = _planet_under_fleet(fleet, owned)
        if here is not None and int(here.id) in staged:
            staged[int(here.id)] += 1

    returns: list[EmptyFreighterReturnIntent] = []
    for fleet in empty_freighters:
        if len(returns) >= int(max_returns) or int(fleet.id) in used:
            continue
        current = _planet_under_fleet(fleet, owned)
        if current is None:
            continue
        if int(current.id) in staged:
            # Already waiting at a designated exporter. Phasing means it should
            # stay idle if another hull got this turn's one allowed pulse.
            continue

        choices = []
        for pid, export_planet, hub, status in exporters:
            warp = _safe_empty_warp(fleet, export_planet)
            if warp is None:
                continue
            travel = distance(current.position, export_planet.position)
            congestion = int(staged.get(pid, 0))
            # A source that can sustain only ~0.2 pulse/turn does not need three
            # waiting freighters. Sustainable rate and backlog both matter.
            score = (
                2.2 * min(1.0, float(status.sustainable_pulses_per_turn) + (0.35 if status.ready_now else 0.0))
                + min(2.5, int(status.downstream_backlog) / 80_000.0)
                + 0.35 * int(hub.ring)
                - travel / 300.0
                - 1.05 * congestion
            )
            choices.append((score, pid, export_planet, hub, status, warp))
        if not choices:
            continue
        score, pid, export_planet, hub, status, warp = max(choices, key=lambda row: row[0])
        staged[pid] = staged.get(pid, 0) + 1
        returns.append(EmptyFreighterReturnIntent(
            fleet_id=int(fleet.id),
            current_planet_id=int(current.id),
            export_planet_id=int(pid),
            warp=int(warp),
            export_ring=int(hub.ring),
            downstream_population_backlog=int(status.downstream_backlog),
            score=round(float(score), 3),
            reason=(
                f"Round-trip onion logistics: {fleet.name} is empty at {current.name}; reposition to "
                f"ring-{hub.ring} exporter {export_planet.name}. Source currently has {status.population:,} "
                f"colonists, trigger={status.dispatch_trigger:,}, protected floor={status.protected_floor:,}, "
                f"sustainable rate~{status.sustainable_pulses_per_turn:.2f} 20k pulses/turn, downstream "
                f"backlog={status.downstream_backlog:,}. Wait there if another hull already used this turn's "
                "single source dispatch. Empty-leg gating may be added later; safe flight is used now."
            ),
        ))
    return returns


def add_population_redistribution_orders(state: Any, orders, plan=None) -> list[PopulationTransferIntent]:
    transfers = plan_population_redistribution(state, plan)
    for intent in transfers:
        orders.add(
            "transport_population",
            intent.to_payload(),
            intent.reason,
            priority=138 if int(state.year) <= 2430 else 124,
        )

    for ret in plan_empty_freighter_returns(state, transfers):
        orders.add(
            "move_fleet",
            ret.to_payload(),
            ret.reason,
            priority=129 if int(state.year) <= 2430 else 115,
        )
    return transfers
