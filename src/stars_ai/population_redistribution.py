"""Round-trip population logistics for the onion expansion strategy.

Opening doctrine:

* a population source retains its protected floor, while every available
  carrier takes the load its hold, the source's remaining surplus, and the
  selected child's remaining need permit; there is no population packet or
  same-turn departure cap;
* the homeworld exports only population above its protected breeder floor;
* a graduated child hub keeps at least ~25% of racial planet capacity before
  exporting onward;
* the homeworld keeps every colonist that still grows faster there than on a
  colony, and keeps enough people to run the factories it has already built.
  A colony is fed toward 25% of its current capacity. A 40% world that
  terraforming will raise to 70% is on that list. Pushing a world past 25%
  while another livable world is still under 25% is overfeeding.
* after a Layer-1 hub has a shipyard/refuel starbase AND ~25% population, the
  homeworld stops feeding it and the hub becomes an exporter to Layer 2;
* empty transports return only to an immediately loadable export hub so scarce
  early Privateer/Medium-Freighter-class hulls do not waste a flight waiting.

Loaded population never receives stargate range credit. The freight leg flies
normally; only an empty ship may later use a gate once native gate movement is
validated.

Population-only source loads use Type 1 through 127 kT and Type 2 from 128 kT.
Mixed population/mineral manifests use Type 2. Every experiment is carried in
the native decision trace with its exact block sequence, so a host rejection
can be isolated without disabling the capability.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from types import SimpleNamespace
from typing import Any

from .cargo_planner import derive_cargo_plan
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
    population_export_floor,
)
from .models import OrderSet
from .native.population_transport import MAX_SMALL_POPULATION_LOAD_KT
from .planet_economy import (
    breeder_hold,
    decode_race_economy,
    feed_habitability,
    growth_peak_population,
    may_receive_homeworld_population,
    projected_population_growth,
    snowball_population,
    ten_year_transfer_roi,
)
from .util import distance
from .warp_policy import mission_warp

COLONISTS_PER_KT = 100
MIN_POPULATION_CARRIER_KT = 1
HIGH_HAB_JUMPSTART = 70


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
    mineral_load: dict[str, int]
    native_experiment: dict[str, Any]
    reason: str

    def to_payload(self) -> dict:
        population_only_small=(
            1 <= int(self.population_kt) <= MAX_SMALL_POPULATION_LOAD_KT
            and not any(int(v or 0) for v in self.mineral_load.values())
        )
        return asdict(self) | {
            "mission": "transport",
            "unload": {
                "ironium": "all",
                "boranium": "all",
                "germanium": "all",
                "population": "all",
            },
            "fuel": "unchanged",
            "native_population_encoding": (
                "type1_source_planet_u16_12_08_u8"
                if population_only_small else "type2_source_planet_u16_12_mask_selected_le16"
            ),
            "gate_allowed_while_loaded": False,
            "round_trip_logistics": True,
            "population_dispatch_policy": "source_surplus_destination_need_and_hold_with_protected_floor",
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


def _feed_room(planet, race, owned) -> int:
    """Colonists this world should still receive.

    Fill every livable world to 25% of its current capacity before topping
    any of them toward the one-third growth peak. A world already past the
    peak is full.
    """
    pop = int(planet.population or 0)
    someone_under = any(
        int(other.id) != int(planet.id)
        and not bool((other.native or {}).get("is_homeworld"))
        and int(other.habitability or 0) >= 30
        and int(other.population or 0) < snowball_population(other, race)
        for other in owned
    )
    under_here = pop < snowball_population(planet, race)
    ceiling = snowball_population(planet, race) if someone_under or under_here else growth_peak_population(planet, race)
    return max(0, int(ceiling) - pop)


def _receiver_score(state, planet, hub) -> float:
    cap = max(1, int(getattr(hub, "capacity", 1) or 1))
    frac = int(planet.population or 0) / cap
    hab = int(planet.habitability if planet.habitability is not None else 0)
    strategic = float((planet.native or {}).get("strategic_value", 0.5) or 0.5)
    score = 0.0
    # A nearly-graduated hub remains valuable, but the planner never rounds a
    # packet up past its remaining need merely to make the arithmetic exact.
    score += 2.5 * min(1.0, max(0.0, (0.25 - frac) / 0.25))
    score += 0.012 * max(0, hab)
    score += 0.20 * min(6, int(getattr(hub, "frontier_worlds_160", 0) or 0))
    score += 0.34 * max(0, int(getattr(hub, "ring", 0) or 0))
    score += 0.40 * strategic
    if bool(getattr(hub, "designated_layer1", False)):
        score += 2.0
    feed = feed_habitability(planet, state)
    if feed >= HIGH_HAB_JUMPSTART:
        # A world that is 70% now, or 40% now and 70% after terraforming,
        # grows the people it is given. It jumps the queue ahead of a
        # mediocre hub that is merely closer to graduation.
        score += 6.0 + 0.08 * (feed - HIGH_HAB_JUMPSTART)
    return score


def _travel_years(distance_ly: float, warp: int) -> int:
    """Years a loaded freighter spends producing nothing.

    Stars! moves warp squared light-years in a year. A shipment that leaves
    this year does not arrive in the same year.
    """
    if distance_ly <= 0:
        return 0
    yearly = max(1, int(warp)) ** 2
    return max(1, int(math.ceil(float(distance_ly) / float(yearly))))


def _shipment_roi(state, row, planets, freighters, owned) -> int:
    """Ten-year resource gain of the next hold we could actually send."""
    _score, target, need, _hub, parent_id = row
    source = planets.get(int(parent_id))
    if source is None:
        return 0
    carriers = []
    for fleet in freighters:
        here = _planet_under_fleet(fleet, owned)
        if here is not None and int(here.id) == int(parent_id):
            carriers.append(fleet)
    if not carriers:
        return 0
    fleet = max(carriers, key=lambda item: int((item.native or {}).get("cargo_capacity", 0) or 0))
    cargo = int((fleet.native or {}).get("cargo_capacity", 0) or 0)
    surplus = max(0, int(source.population or 0) - breeder_hold(source, state.race))
    colonists = min(int(need), cargo * COLONISTS_PER_KT, surplus)
    colonists = (colonists // COLONISTS_PER_KT) * COLONISTS_PER_KT
    if colonists <= 0:
        return 0
    years = _travel_years(
        distance(source.position, target.position),
        mission_warp(fleet, target.position, "transport"),
    )
    energy = int(getattr(getattr(state, "tech", None), "energy", 0) or 0)
    roi = ten_year_transfer_roi(
        source, target, state.race, colonists, years, energy_tech=energy, state=state,
    )
    return int(roi["score"])


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
    orders=None,
    max_transfers: int = 4,
) -> list[PopulationTransferIntent]:
    """Plan phased parent->child population exports.

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

    # A child must still need one native-encodable 100-colonist (1 kT) packet.
    receivers = []
    for hub in network.hubs:
        parent_id = hub.parent_exporter_id
        p = planets.get(int(hub.planet_id))
        parent = None if parent_id is None else planets.get(int(parent_id))
        if p is None or parent is None or (p.habitability is not None and int(p.habitability) <= 0):
            continue
        need = min(int(hub.import_population_to_25 or 0), _feed_room(p, state.race, owned))
        if need < COLONISTS_PER_KT:
            continue
        if not may_receive_homeworld_population(parent, p, state.race, state):
            continue
        receivers.append((_receiver_score(state, p, hub), p, need, hub, int(parent_id)))
    receiver_ids = {int(row[1].id) for row in receivers}
    home = next((p for p in owned if bool((p.native or {}).get("is_homeworld"))), None)
    for planet in owned:
        feed = feed_habitability(planet, state)
        if feed < HIGH_HAB_JUMPSTART or int(planet.id) in receiver_ids:
            continue
        if bool((planet.native or {}).get("is_homeworld")):
            continue
        need = _feed_room(planet, state.race, owned)
        if need < COLONISTS_PER_KT or home is None:
            continue
        if not may_receive_homeworld_population(home, planet, state.race, state):
            continue
        parent_hub = hubs.get(int(home.id))
        if parent_hub is None:
            continue
        child_hub = hubs.get(int(planet.id))
        receivers.append((
            8.0 + feed / 10.0,
            planet,
            need,
            child_hub or parent_hub,
            int(home.id),
        ))
    if not receivers:
        return []

    freighters = _available_empty_population_freighters(state)
    if not freighters:
        return []
    receivers.sort(key=lambda row: (-_shipment_roi(state, row, planets, freighters, owned), -row[0], row[1].id))

    source_outbound: dict[int, int] = {}
    source_dispatches: dict[int, int] = {}
    used_fleets: set[int] = set()
    planned_to: dict[int, int] = {}
    intents: list[PopulationTransferIntent] = []

    for receiver_score, target, raw_need, target_hub, parent_id in receivers:
        if len(intents) >= int(max_transfers):
            break
        status = statuses.get(parent_id)
        source_hub = hubs.get(parent_id)
        source = planets.get(parent_id)
        target_feed = feed_habitability(target, state)
        if source_hub is None or source is None:
            continue
        protected = breeder_hold(source, state.race)
        if status is not None:
            protected = max(protected, int(status.protected_floor))
            if not status.ready_now and target_feed < HIGH_HAB_JUMPSTART:
                continue
        elif target_feed < HIGH_HAB_JUMPSTART:
            continue
        if int(source.population or 0) - protected < COLONISTS_PER_KT:
            continue
        remaining_need = int(raw_need) - int(planned_to.get(int(target.id), 0))
        already_outbound = int(source_outbound.get(parent_id, 0) or 0)
        source_available = max(0, int(source.population or 0) - protected - already_outbound)

        choices = []
        for fleet in freighters:
            if int(fleet.id) in used_fleets:
                continue
            here = _planet_under_fleet(fleet, owned)
            if here is None or int(here.id) != parent_id:
                continue
            cargo_cap = int((fleet.native or {}).get("cargo_capacity", 0) or 0)
            export_colonists = min(
                source_available,
                max(0, remaining_need),
                max(0, cargo_cap) * COLONISTS_PER_KT,
            )
            # The native population quantity is kT, i.e. an exact multiple of
            # 100 colonists. Do not round up beyond a source/target/fleet limit.
            export_colonists = (export_colonists // COLONISTS_PER_KT) * COLONISTS_PER_KT
            export_kt = export_colonists // COLONISTS_PER_KT
            if export_kt <= 0:
                continue
            if int(source.population or 0) - already_outbound - export_colonists < protected:
                continue
            if not mission_reachable_with_planned_cargo(
                fleet, target.position, "transport", {"population": export_kt}
            ):
                continue
            travel = distance(source.position, target.position)
            warp = int(mission_warp(fleet, target.position, "transport"))
            years = _travel_years(travel, warp)
            energy = int(getattr(getattr(state, "tech", None), "energy", 0) or 0)
            roi = ten_year_transfer_roi(
                source, target, state.race, export_colonists, years, energy_tech=energy, state=state,
            )
            # A trip that loses resources over ten years, transit included,
            # is not a grower. Leave the people where they are.
            if int(roi["score"]) <= 0:
                continue
            choices.append((
                int(roi["score"]),
                int(roi["population_delta"]),
                export_colonists,
                -int(fleet.id),
                fleet,
                cargo_cap,
                export_kt,
                years,
            ))

        if not choices:
            continue
        score, _population_delta, export_colonists, _, fleet, cargo_cap, export_kt, travel_years = max(
            choices, key=lambda row: row[:4]
        )

        # Use the otherwise-unused cargo hold for the recipient's real mineral
        # deficit. The shared reserve policy prevents stripping the source.
        # This combined population+mineral encoding is experimental, so it is
        # fully logged by the native writer.
        mineral_load = {"ironium": 0, "boranium": 0, "germanium": 0}
        residual_capacity = max(0, int(cargo_cap) - int(export_kt))
        planning_orders = orders or OrderSet(state.game_name, state.year, state.player_id)
        if residual_capacity:
            proxy_fleet = SimpleNamespace(cargo_capacity=residual_capacity, native={"cargo_capacity": residual_capacity})
            cargo_plan = derive_cargo_plan(
                source, target, proxy_fleet, decode_race_economy(state.race), planning_orders,
            )
            if cargo_plan is not None:
                mineral_load = cargo_plan.as_load()

        planned_cargo = {"population": export_kt, **mineral_load}
        # A mineral top-off must not invalidate the fuel-safe population route.
        if sum(mineral_load.values()) and not mission_reachable_with_planned_cargo(
            fleet, target.position, "transport", planned_cargo
        ):
            mineral_load = {"ironium": 0, "boranium": 0, "germanium": 0}
            planned_cargo = {"population": export_kt}

        fp = (fleet.native or {}).get("fuel_profile")
        if fp:
            flags = (fleet.native or {}).get("race_fuel_flags", {}) or {}
            planned = profile_with_planned_cargo(fp, planned_cargo)
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

        before = int(source.population or 0) - already_outbound
        after = before - export_colonists
        source_outbound[parent_id] = already_outbound + export_colonists
        source_dispatches[parent_id] = int(source_dispatches.get(parent_id, 0)) + 1
        used_fleets.add(int(fleet.id))
        planned_to[int(target.id)] = int(planned_to.get(int(target.id), 0)) + export_colonists

        mixed_load = sum(mineral_load.values()) > 0
        baseline_200kt = export_kt == POPULATION_PULSE_KT and not mixed_load
        baseline_80kt = export_kt == 80 and not mixed_load
        small_population_only = 1 <= export_kt <= MAX_SMALL_POPULATION_LOAD_KT and not mixed_load
        experiment_id = (
            "population-type2-200kt-baseline"
            if baseline_200kt
            else "population-type1-80kt-baseline"
            if baseline_80kt
            else f"population-{'type1' if small_population_only else 'type2'}-{export_kt}kt"
            + ("-with-minerals" if mixed_load else "")
        )
        native_experiment = {
            "enabled": True,
            "id": experiment_id,
            "trust_level": "VALIDATED" if baseline_80kt else "EXPERIMENTAL",
            "validated_baseline": (
                "80 kT Type-1 <source planet u16> 12 08 50 + 0x51 Transport unload-all I/B/G/population "
                "endpoint is host-validated end-to-end."
            ),
            "risk": (
                "None beyond the end-to-end validated 80 kT transport baseline."
                if baseline_80kt
                else "This quantity or mixed manifest remains trace-required until confirmed in a host turn."
            ),
            "mineral_capacity_kt": int(residual_capacity),
        }

        intents.append(PopulationTransferIntent(
            fleet_id=int(fleet.id),
            source_planet_id=parent_id,
            destination_planet_id=int(target.id),
            population_kt=export_kt,
            population_colonists=export_colonists,
            warp=selected_warp,
            score=round(float(score), 3),
            source_ring=int(source_hub.ring),
            destination_ring=int(target_hub.ring),
            source_population_before=before,
            source_population_after=after,
            source_protected_floor=int(protected),
            mineral_load=mineral_load,
            native_experiment=native_experiment,
            reason=(
                f"Strategic population export {source.name} (ring {source_hub.ring})->{target.name} "
                f"(ring {target_hub.ring}): load {export_colonists:,} colonists "
                f"({export_kt} kT) into {fleet.name} ({cargo_cap} kT hold)"
                f"; minerals I/B/G={mineral_load['ironium']}/{mineral_load['boranium']}/{mineral_load['germanium']} kT. "
                f"Projected source {before:,}->{after:,}; protected floor={protected:,}. "
                f"Transit {travel_years} year(s). Ten-year return {int(score):+,}. "
                f"against leaving these colonists on {source.name}. "
                f"Source dispatch #{source_dispatches[parent_id]} this turn; aggregate source load remains above "
                "the protected floor. Loaded cargo "
                "flies normally; after destination unload the empty hull returns/repositions for future work."
            ),
        ))
    return intents


def plan_empty_freighter_returns(
    state: Any,
    transfer_intents: list[PopulationTransferIntent] | tuple[PopulationTransferIntent, ...] = (),
    *,
    max_returns: int | None = None,
) -> list[EmptyFreighterReturnIntent]:
    """Route idle empty transports back to useful export hubs.

    Staging is deliberately broader than dispatch.  A freighter that has just
    unloaded at an outpost should return to a sound population source while
    that source replenishes, rather than remain stranded until a whole native
    1-kT packet is already available.  Actual loading remains subject to the
    protected-floor and destination-demand checks in ``plan_population_redistribution``.
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
    for pid, hub in hubs.items():
        status = statuses.get(pid)
        p = planets.get(pid)
        if p is None:
            continue
        protected_floor = (
            int(status.protected_floor)
            if status is not None
            else int(population_export_floor(state, hub, network))
        )
        exportable = max(0, int(p.population or 0) - protected_floor)
        growth = max(0, int(projected_population_growth(p, state.race)))
        downstream_backlog = int(status.downstream_backlog) if status is not None else 0
        immediate_packet = exportable >= COLONISTS_PER_KT
        # A source with a live onion lane, a network-advertised surplus, or a
        # completed hub facing the frontier is a useful future export location.
        # The latter admits a healthy HW/P1 between colony claims without
        # treating every owned world as a freighter parking site.
        export_role = bool(
            status is not None
            or int(hub.export_population or 0) > 0
            or (
                (int(hub.ring) == 0 or bool(hub.graduated))
                and int(hub.frontier_worlds_160 or 0) > 0
            )
        )
        if (
            not export_role
            or (not immediate_packet and growth <= 0)
        ):
            continue
        exporters.append((
            pid,
            p,
            hub,
            status,
            protected_floor,
            exportable,
            growth,
            downstream_backlog,
            immediate_packet,
        ))
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
        if (max_returns is not None and len(returns) >= int(max_returns)) or int(fleet.id) in used:
            continue
        current = _planet_under_fleet(fleet, owned)
        if current is None:
            continue
        if int(current.id) in staged:
            # Already waiting at a designated exporter. Phasing means it should
            # stay idle if another hull got this turn's one allowed pulse.
            continue

        choices = []
        for (
            pid,
            export_planet,
            hub,
            status,
            protected_floor,
            exportable,
            growth,
            downstream_backlog,
            immediate_packet,
        ) in exporters:
            warp = _safe_empty_warp(fleet, export_planet)
            if warp is None:
                continue
            travel = distance(current.position, export_planet.position)
            congestion = int(staged.get(pid, 0))
            # Sustainable rate, backlog, and current staging congestion all
            # matter; avoid accumulating an idle parking lot at one source.
            sustainable_rate = (
                float(status.sustainable_pulses_per_turn)
                if status is not None
                else min(1.0, float(growth) / float(POPULATION_PULSE_COLONISTS))
            )
            score = (
                2.2 * min(1.0, sustainable_rate + (0.35 if immediate_packet else 0.0))
                + min(2.5, downstream_backlog / 80_000.0)
                + 0.35 * int(hub.ring)
                - travel / 300.0
                - 1.05 * congestion
            )
            choices.append((
                score,
                pid,
                export_planet,
                hub,
                protected_floor,
                exportable,
                sustainable_rate,
                downstream_backlog,
                immediate_packet,
                warp,
            ))
        if not choices:
            continue
        (
            score,
            pid,
            export_planet,
            hub,
            protected_floor,
            exportable,
            sustainable_rate,
            downstream_backlog,
            immediate_packet,
            warp,
        ) = max(choices, key=lambda row: row[0])
        staged[pid] = staged.get(pid, 0) + 1
        returns.append(EmptyFreighterReturnIntent(
            fleet_id=int(fleet.id),
            current_planet_id=int(current.id),
            export_planet_id=int(pid),
            warp=int(warp),
            export_ring=int(hub.ring),
            downstream_population_backlog=int(downstream_backlog),
            score=round(float(score), 3),
            reason=(
                f"Round-trip onion logistics: {fleet.name} is empty at {current.name}; reposition to "
                f"ring-{hub.ring} exporter {export_planet.name}. Source currently has {int(export_planet.population or 0):,} "
                f"colonists, with {exportable:,} colonists above protected floor={protected_floor:,} "
                f"({'loadable now' if immediate_packet else 'replenishing for its next native packet'}), "
                f"sustainable planning rate~{sustainable_rate:.2f} base loads/turn, "
                f"downstream backlog={downstream_backlog:,}. Return now so this scarce hold is "
                "at an active exporter when current or newly discovered demand appears; loading still requires "
                "a legal source surplus above the protected floor."
            ),
        ))
    return returns


def add_population_redistribution_orders(state: Any, orders, plan=None) -> list[PopulationTransferIntent]:
    transfers = plan_population_redistribution(state, plan, orders=orders)
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
