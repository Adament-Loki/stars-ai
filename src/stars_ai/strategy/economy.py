
from __future__ import annotations
import math
from ..models import GameState, OrderSet
from ..persona import StrategicPlan
from ..util import distance
from ..colony_planner import (
    score_colony_candidates,
    reserved_colony_target_ids,
    colonization_policy,
    colony_support_distance,
    colony_transit_year_budget,
    colony_transit_years,
)
from ..warp_policy import mission_warp
from ..fuel_planner import (
    fastest_fuel_safe_warp,
    mission_reachable,
    mission_reachable_with_planned_cargo,
    profile_with_planned_cargo,
)
from ..objective_production import (
    BuildRequest,
    IMMEDIATE_SHIP_QUEUE_HORIZON,
    plan_objective_ship_builds,
)
from ..cargo_planner import derive_cargo_plan
from ..starbase_planner import (
    plan_support_base_builds,
    plan_support_base_material_demands,
    support_base_design_cost,
    STARBASE_QUEUE_SLOT_OFFSET,
)
from ..starbase_capabilities import can_starbase_build_ship
from ..expansion_network import evaluate_expansion_network
from ..planet_economy import (decode_race_economy, installation_status, estimated_operating_resources,
                              estimated_mineral_output, mineral_surface_stock,
                              working_mineral_reserve, factory_germanium_floor,
                              is_economic_core, theoretical_max_population,
                              planet_population_capacity, population_capacity_fraction,
                              projected_population_growth, projected_next_population)
from ..population_units import (
    COLONY_LOAD_COLONISTS,
    COLONY_LOAD_KT,
    cargo_kt_from_colonists,
    colony_source_reserve_for_turn,
)
from ..terraforming import evaluate_terraforming
from ..research_planner import ResearchDecision
from ..planetary_scanners import (
    PLANETARY_SCANNER_QUEUE_ITEM,
    best_penetrating_planetary_scanner,
    planetary_scanner_sites,
)
from ..shared_transport import schedule_shared_transport_orders
from ..population_invasion import population_capture_targets
from ..starsapi_items import stock_component_database

def _planet_under_fleet(fleet, owned):
    pid=int(fleet.native.get("position_object_id",-1))
    p=next((p for p in owned if p.id==pid),None)
    if p is not None:
        return p
    # Fallback to coordinates because position_object_id is not trustworthy
    # in every fleet-block variant. Stars! planet/fleet coordinates are integral.
    candidates=[
        p for p in owned
        if abs(float(p.position.x)-float(fleet.position.x)) <= 0.5
        and abs(float(p.position.y)-float(fleet.position.y)) <= 0.5
    ]
    return candidates[0] if candidates else None


def _colony_source_reserve(state: GameState) -> int:
    """Population retained before emitting validated 25 kT load orders."""
    turn = max(0, int(state.year) - 2400)
    return colony_source_reserve_for_turn(turn)


def _can_launch_colony_after_refuel(fleet, launch_world, target, population_kt: int) -> bool:
    """Test a colony leg from a particular refuel-capable launch world."""
    native=getattr(fleet,"native",{}) or {}
    profile=native.get("fuel_profile")
    if not profile:
        return True
    planned=profile_with_planned_cargo(profile,{"population":int(population_kt)})
    capacity=float(planned.get("fuel_capacity",0) or 0)
    if capacity<=0:
        return False
    planned["fuel"]=capacity
    planned["effective_fuel"]=capacity
    planned["at_starbase"]=True
    flags=native.get("race_fuel_flags",{}) or {}
    return fastest_fuel_safe_warp(
        planned,
        distance(launch_world.position,target.position),
        "colonize",
        ife=bool(flags.get("ife")),
        ce=bool(flags.get("ce")),
    ) is not None


def _shipyard_can_build_design(world, ship_mass_kt):
    """Apply a starbase's design-mass limit when native capability data exists.

    The no-capability fallback keeps this small planning helper usable with
    synthetic worlds in callers/tests.  Live shipyards are selected only from
    decoded ``starbase_capabilities`` and therefore always take the strict
    path below.
    """
    capabilities=(getattr(world,"native",None) or {}).get(
        "starbase_capabilities"
    )
    if not capabilities:
        return True
    return can_starbase_build_ship(capabilities,ship_mass_kt)


def _distribute_ship_builds(
    shipyards,
    requests,
    promotion_by_planet,
    *,
    design_masses_kt=None,
):
    """Spread strategic builds across operational yards.

    A ship build request is an empire requirement, not an instruction to make
    one world carry the whole queue.  Giving each ready yard a share produces
    colony, scout, freight, and defense hulls concurrently.  Core/promotion
    hubs are still assigned first, so the fastest and best supplied yard gets
    the first share without silently idling every other shipyard.
    """
    ordered=sorted(
        shipyards,
        key=lambda p:(
            -int(getattr(promotion_by_planet.get(int(p.id)),"promotion_tier",3) or 3),
            int(p.population or 0),int(p.factories or 0),int(p.mines or 0),-int(p.id),
        ),
        reverse=True,
    )
    assigned={int(p.id):[] for p in ordered}
    if not ordered:
        return assigned
    design_masses_kt=design_masses_kt or {}
    for request in requests:
        ship_mass_kt=design_masses_kt.get(int(request.design_slot))
        eligible=[
            world for world in ordered
            if _shipyard_can_build_design(world,ship_mass_kt)
        ]
        if not eligible:
            continue
        remaining=max(0,int(request.quantity))
        lane_index=0
        shares={int(world.id):0 for world in eligible}
        # One ship to each lane first gives the greatest immediate strategic
        # throughput.  Extra copies cycle from the strongest yard outward.
        while remaining:
            world=eligible[lane_index % len(eligible)]
            shares[int(world.id)]+=1
            lane_index+=1
            remaining-=1
        for world in eligible:
            quantity=shares[int(world.id)]
            if quantity:
                assigned[int(world.id)].append(BuildRequest(
                    request.role,request.design_slot,request.design_name,quantity,
                    request.priority,request.reason,
                ))
    return assigned


def _legacy_static_economic_priority(
    queue,
    *,
    opening_growth:bool=False,
    colony_launch_priority:bool=False,
    mineral_emergency:dict|None=None,
):
    """Apply short-horizon strategic production bands.

    A queue is a commitment to an order, not a claim that all of its mineral
    bills are already funded.  When the planner detects an I/B/G shortfall,
    allowing an unbounded ship queue to hide all infrastructure is just as
    harmful as letting a 25-factory batch hide a critical colony launch.  Keep
    up to three explicit urgent hulls at the front, then resume necessary mine
    work and routine development before the rest of the ship backlog.
    """
    mines=[item for item in queue if item.get("item")=="mine"]
    factories=[item for item in queue if item.get("item")=="factory"]
    other=[
        item for item in queue
        if item.get("item") not in {"mine","factory"}
    ]
    if opening_growth:
        # Early playtest evidence showed a preserved scout queue hiding a new
        # colony build behind fourteen probes.  Exploration now has enough
        # momentum; mines/factories first, then colony hulls and the freighters
        # that sustain them, is the necessary opening production sequence.
        project_order={
            "colony":0,
            "freighter":1,
            "miner":2,
            "scout":3,
            "combat":4,
        }
        other=[
            item
            for _,item in sorted(
                enumerate(other),
                key=lambda row:(
                    project_order.get(str(row[1].get("role") or ""), 5)
                    if row[1].get("item")=="ship_design" else 5,
                    row[0],
                ),
            )
        ]
    emergency_active=bool((mineral_emergency or {}).get("active"))

    def urgent_ship(item):
        if item.get("item") != "ship_design":
            return False
        role=str(item.get("role") or "")
        priority=int(item.get("strategic_priority",0) or 0)
        reason=str(item.get("strategic_reason","") or "").casefold()
        ordinary_urgent=bool(
            priority >= 180
            or (role == "combat" and ("territorial" in reason or "emergency" in reason))
            or (colony_launch_priority and role == "colony")
        )
        if not emergency_active:
            return ordinary_urgent
        # A zero-stock material runway is itself an emergency.  Only a real
        # defensive response or a colony hull whose launch is immediately
        # executable may pass its recovery tranche; a scout shortage is not
        # enough to consume the last usable mineral stock first.
        return bool(
            role == "combat" and ("territorial" in reason or "emergency" in reason)
            or role == "colony" and priority >= 200
        )

    # Band 1: immediate strategic capability.  A serial queue should expose a
    # small executable packet, not hide 6–25 future hulls ahead of the entire
    # economy.  Split an aggregate request so the tail resumes after growth.
    urgent=[]
    routine=[]
    horizon=3
    for item in sorted(other,key=lambda row:int(row.get("strategic_priority",0) or 0),reverse=True):
        if urgent_ship(item) and horizon>0:
            take=min(horizon,max(0,int(item.get("quantity",1) or 1)))
            if take:
                urgent.append(dict(item,quantity=take,queue_priority_band=1,
                                   queue_priority_reason="immediate strategic capability horizon"))
                horizon-=take
            tail=max(0,int(item.get("quantity",1) or 1)-take)
            if tail:
                routine.append(dict(item,quantity=tail,queue_priority_band=4,
                                    queue_priority_reason="strategic tail after protected horizon"))
        else:
            routine.append(dict(item,queue_priority_band=4,
                                queue_priority_reason="routine growth or preserved backlog"))

    # Band 3 is reserved for mines that have a documented current working
    # reserve deficit.  Factories and optional mine capacity remain Band 4.
    necessary_mines=[]
    routine_mines=[]
    for item in mines:
        deficits=item.get("mineral_deficits") or {}
        if emergency_active and bool(item.get("mineral_emergency")):
            necessary_mines.append(dict(
                item,
                queue_priority_band=2,
                queue_priority_reason="severe material runway recovery tranche",
            ))
        elif any(int(value or 0)>0 for value in deficits.values()):
            necessary_mines.append(dict(item,queue_priority_band=3,
                                        queue_priority_reason="near-term mineral reserve deficit"))
        else:
            routine_mines.append(dict(item,queue_priority_band=4,
                                      queue_priority_reason="routine mine capacity"))
    routine_factories=[dict(item,queue_priority_band=4,
                            queue_priority_reason="routine factory growth") for item in factories]
    if emergency_active:
        # Preserve only the truly immediate survival/colony packet above the
        # recovery work.  This replaces the old accidental "three scouts then
        # twenty-five mines" policy with a small, measurable mine tranche.
        return [*urgent,*necessary_mines,*routine_mines,*routine_factories,*routine]
    return [*urgent,*necessary_mines,*routine_mines,*routine_factories,*routine]


def _queue_mineral_costs(state: GameState, queue) -> dict[str, int]:
    """Estimate the committed I/B/G bill without treating unknown hulls as free.

    Native queue records do not expose a fully miniaturized ship bill in every
    variant. Explicit design bills are preferred; fitted components give a
    lower bound otherwise, while the working reserve protects unknown hull and
    queue overhead.
    """
    profiles={
        int(profile.get("design_number", -1)):profile
        for profile in ((state.native or {}).get("design_profiles", []) or [])
    }
    base_profiles={
        int(profile.get("design_number", -1)):profile
        for profile in ((state.native or {}).get("starbase_profiles", []) or [])
    }
    components=stock_component_database()
    total={mineral:0 for mineral in ("ironium", "boranium", "germanium")}

    def add(cost: dict, quantity: int) -> None:
        for mineral in total:
            total[mineral]+=max(0, int(cost.get(mineral, 0) or 0))*quantity

    for item in queue or ():
        quantity=max(0, int(item.get("quantity", item.get("count", 0)) or 0))
        if quantity <= 0:
            continue
        kind=str(item.get("item", "") or "")
        if kind == "factory":
            add({"germanium":int(item.get("germanium_cost_each", 4) or 4)}, quantity)
            continue
        if kind == "starbase_design":
            profile=base_profiles.get(int(item.get("design_slot", -1) or -1), {})
            if profile:
                add(support_base_design_cost(profile), quantity)
            continue
        if kind != "ship_design":
            continue
        profile=profiles.get(int(item.get("design_slot", -1) or -1), {})
        explicit=profile.get("mineral_cost") or profile.get("mineral_costs") or {}
        if explicit:
            add(explicit, quantity)
            continue
        component_cost={mineral:0 for mineral in total}
        for component in profile.get("components", profile.get("slots", [])) or []:
            if not isinstance(component, dict):
                continue
            spec=components.component(
                int(component.get("category", 0) or 0),
                int(component.get("item_id", 0) or 0),
            )
            count=max(0, int(component.get("count", 0) or 0))
            if spec is None or count <= 0:
                continue
            for mineral in component_cost:
                component_cost[mineral]+=count*int(getattr(spec, mineral, 0) or 0)
        add(component_cost, quantity)
    return total


def _resource_runway(
    state: GameState,
    planet,
    economy,
    queue,
    *,
    is_core: bool,
    mineral_output: dict | None = None,
) -> dict:
    """Calculate the material horizon that decides this world's queue order."""
    stock=mineral_surface_stock(planet)
    reserve=working_mineral_reserve(planet, economy, queue, is_core=is_core)
    income=(mineral_output or estimated_mineral_output(planet, economy)).get(
        "estimated_mineral_output", {}
    ) or {}
    committed=_queue_mineral_costs(state, queue)
    turns={}
    immediate_block=[]
    for mineral in ("ironium", "boranium", "germanium"):
        available=max(0, int(stock[mineral])-int(reserve[mineral]))
        spend=max(0, int(committed[mineral]))
        annual=max(0, int(income.get(mineral, 0) or 0))
        if available <= 0 or spend > available:
            turns[mineral]=0
            immediate_block.append(mineral)
        elif spend <= annual:
            turns[mineral]=None
        else:
            turns[mineral]=int(math.floor(available/max(1, spend-annual)))
    finite=[value for value in turns.values() if value is not None]
    return {
        "stock_by_mineral":{mineral:int(stock[mineral]) for mineral in stock},
        "working_reserve":{
            mineral:int(reserve[mineral]) for mineral in ("ironium", "boranium", "germanium")
        },
        "projected_income":{
            mineral:max(0, int(income.get(mineral, 0) or 0))
            for mineral in ("ironium", "boranium", "germanium")
        },
        "committed_queue_cost":dict(committed),
        "turns_to_material_block":turns,
        "immediate_block_minerals":immediate_block,
        "minimum_turns_to_material_block":min(finite) if finite else None,
        "immediately_executable":not immediate_block,
        "cost_confidence":(
            "exact for infrastructure and explicit design bills; "
            "component lower bound for other custom hulls"
        ),
    }


def _prioritize_economic_infrastructure(
    queue,
    *,
    opening_growth:bool=False,
    colony_launch_priority:bool=False,
    mineral_emergency:dict|None=None,
    resource_runway:dict|None=None,
    invasion_active:bool=False,
    funded_research_sprint:bool=False,
):
    """Order a serial queue from the current material runway.

    ``queue_priority_band`` is retained only as an audit-compatible label. Its
    value is now a contextual priority class rather than a static production
    band: survival, recovery, executable expansion, critical capability,
    funded research, then routine growth.
    """
    emergency_active=bool((mineral_emergency or {}).get("active"))
    runway=resource_runway or {}
    material_safe=bool(runway.get("immediately_executable", True))
    priority_names={
        1:"immediate survival or invasion defense",
        2:"severe mineral-runway recovery",
        3:"immediately executable expansion or capture",
        4:"critical scout or transport capability",
        5:"funded short research sprint",
        6:"routine factories, mines, or terraforming",
    }

    def classify(item: dict) -> int:
        kind=str(item.get("item", "") or "")
        role=str(item.get("role", "") or "")
        priority=int(item.get("strategic_priority", 0) or 0)
        reason=str(item.get("strategic_reason", item.get("reason", "")) or "").casefold()
        invasion=bool(invasion_active or any(token in reason for token in (
            "invasion", "territorial", "emergency", "defend",
        )))
        if (kind == "ship_design" and role == "combat" and invasion) or (
            kind == "defense" and invasion_active
        ) or (
            kind == "starbase_design" and invasion
        ) or (
            kind == "ship_design" and role == "minelayer" and invasion
        ):
            return 1
        if kind == "mine" and emergency_active and bool(item.get("mineral_emergency")):
            return 2
        expansion=bool(
            kind == "ship_design" and (
                # Objective production emits colony hulls only for a live,
                # source-backed claim.  Treat that request as executable
                # expansion even when ordinary pressure makes its numeric
                # priority lower than the emergency-queue threshold.
                (role == "colony" and (priority >= 180 or colony_launch_priority))
                or (role == "freighter" and "capture" in reason)
            )
        )
        if expansion and material_safe:
            return 3
        if kind == "ship_design" and role in {"scout", "freighter"} and priority >= 180:
            return 4
        # A funded sprint has no native queue item; this class is included so
        # callers can use the same contract when such an item becomes native.
        if funded_research_sprint and kind == "research":
            return 5
        return 6

    indexed=[]
    remaining={1:3, 3:2, 4:2}
    for index, item in enumerate(queue):
        cls=classify(item)
        quantity=max(0, int(item.get("quantity", 1) or 1))
        take=min(quantity, remaining[cls]) if cls in remaining else quantity
        if cls in remaining:
            remaining[cls]-=take
        if take:
            indexed.append((cls, index, dict(
                item, quantity=take, queue_priority_band=cls,
                queue_priority_reason=priority_names[cls],
            )))
        if take < quantity:
            indexed.append((6, index, dict(
                item, quantity=quantity-take, queue_priority_band=6,
                queue_priority_reason="deferred tail after executable priority horizon",
            )))
    routine_item_order={"mine":0, "factory":1}
    indexed.sort(key=lambda row:(
        row[0],
        routine_item_order.get(str(row[2].get("item", "")), 2) if row[0] == 6 else 0,
        row[1],
    ))
    return [item for _, _, item in indexed]


def _mineral_emergency_snapshot(
    mineral_stock:dict,
    mineral_reserve:dict,
    mineral_output:dict,
    *,
    mine_headroom:int,
    resource_runway:dict|None=None,
) -> dict:
    """Detect a short-run material runway failure and size one mine tranche.

    This deliberately uses the same working reserve as ordinary production.
    It is not a permanent installations-first rule: the condition has to be a
    near-empty stock *and* a recovery rate too slow to restore the reserve in
    roughly six turns.
    """
    annual=dict(mineral_output.get("estimated_mineral_output") or {})
    deficits={
        mineral:max(0,int(mineral_reserve.get(mineral,0) or 0)-int(mineral_stock.get(mineral,0) or 0))
        for mineral in ("ironium","boranium","germanium")
    }
    critical=max(deficits,key=lambda mineral:(deficits[mineral],-int(mineral_stock.get(mineral,0) or 0)))
    reserve=int(mineral_reserve.get(critical,0) or 0)
    stock=int(mineral_stock.get(critical,0) or 0)
    deficit=int(deficits[critical])
    annual_output=max(0,int(annual.get(critical,0) or 0))
    runway_turns=((resource_runway or {}).get("turns_to_material_block") or {}).get(critical)
    immediate_block=critical in set((resource_runway or {}).get("immediate_block_minerals") or ())
    near_empty=stock<=max(25,reserve//10) or immediate_block
    materially_short=deficit>=max(150,reserve//2)
    slow_recovery=annual_output<=max(10,reserve//25)
    active=bool(
        mine_headroom>0
        and near_empty
        and materially_short
        and (slow_recovery or runway_turns in {0,1})
    )
    recovery_turns=(
        int(math.ceil(deficit/max(1,annual_output)))
        if deficit else 0
    )
    operated=max(1,int(mineral_output.get("operated_mines",0) or 0))
    output_per_mine=max(1.0,float(annual_output)/operated)
    target_annual=max(1,int(math.ceil(deficit/6.0)))
    mines_needed=int(math.ceil(max(0,target_annual-annual_output)/output_per_mine))
    mines_needed=min(10,max(1,mines_needed),max(0,int(mine_headroom)))
    return {
        "active":active,
        "critical_mineral":critical,
        "stock":stock,
        "working_reserve":reserve,
        "annual_output":annual_output,
        "recovery_turns":recovery_turns,
        "mines_required_for_recovery":mines_needed if active else 0,
        "ship_work_deferred":bool(active),
        "mineral_deficits":deficits,
        "runway_turns_to_block":runway_turns,
    }


def _preserved_ship_queue_items(
    state,
    existing_queue,
    *,
    colony_launch_priority:bool,
    shipyard=None,
):
    """Keep only a short executable ship horizon from an old serial queue."""
    profiles={
        int(profile.get("design_number",-1)):profile
        for profile in (state.native or {}).get("design_profiles",[])
    }
    old=[]
    remaining=IMMEDIATE_SHIP_QUEUE_HORIZON
    for q in existing_queue:
        if remaining<=0:
            break
        if (
            int(q.get('item_type',0))!=4
            or not 0<=int(q.get('item_id',0))<STARBASE_QUEUE_SLOT_OFFSET
            or int(q.get('count',0))<=0
        ):
            continue
        slot=int(q.get('item_id',0))
        profile=profiles.get(slot) or {}
        if shipyard is not None and not _shipyard_can_build_design(
            shipyard,
            profile.get("dry_mass"),
        ):
            # A design queued before the base was demoted to a Space Dock (or
            # before this restriction was enforced) cannot be allowed to
            # block the serial queue indefinitely.
            continue
        role=str(profile.get("role") or "")
        if colony_launch_priority and role!="colony":
            continue
        quantity=min(remaining,int(q.get('count',0)))
        old.append({
            'item':'ship_design',
            'design_slot':slot,
            'design_name':profile.get('name',f'Design #{slot+1}'),
            'quantity':quantity,
            'role':role,
            'preserved_existing':True,
        })
        remaining-=quantity
    return old

def add_economic_orders(
    state: GameState,
    orders: OrderSet,
    plan: StrategicPlan | None = None,
    research_decision: ResearchDecision | None = None,
) -> None:
    # Persistent intelligence can retain a formerly owned world's last known
    # owner when a sparse M file omits it.  It remains useful strategic
    # information, but only the adapter's current-M allow-list can authorize
    # a local queue, cargo source, or starbase decision.
    current_owned=(state.native or {}).get("current_m_owned_planet_ids")
    authorized_ids=(
        {int(value) for value in current_owned}
        if current_owned is not None else None
    )
    owned=[
        p for p in state.planets
        if p.owner==state.player_id
        and (authorized_ids is None or int(p.id) in authorized_ids)
    ]
    develop=plan.objective("develop") if plan else 1.0
    defense_w=plan.planet("defenses") if plan else 1.0



    race_economy=decode_race_economy(state.race)
    promotion_network=evaluate_expansion_network(state)
    promotion_by_planet={int(h.planet_id):h for h in promotion_network.hubs}
    penetrating_scanner=best_penetrating_planetary_scanner(state)
    scanner_sites=set(planetary_scanner_sites(state,penetrating_scanner,limit=2))
    state.native["planetary_sensor_plan"]={
        "capability":penetrating_scanner,
        "target_planet_ids":sorted(scanner_sites),
        "policy":"Deploy up to two researched penetrating planetary scanners on mature core/frontier hubs; existing M-file scanners are retained.",
    }
    ship_builds=plan_objective_ship_builds(state,plan)
    # A strategic base is not allowed to block its world's mines/factories
    # until the exact remaining hull/component bill plus working reserve is on
    # the ground.  The blocked demand remains visible to freight routing below.
    support_base_material_demands={
        int(demand.planet_id): demand
        for demand in plan_support_base_material_demands(state,plan)
    }
    state.native["support_base_material_demands"]=[
        demand.to_dict() for demand in support_base_material_demands.values()
    ]
    support_base_builds={
        request.planet_id:request
        for request in plan_support_base_builds(state,plan)
        if bool(
            support_base_material_demands.get(int(request.planet_id))
            and support_base_material_demands[int(request.planet_id)].ready
        )
    }
    shipyards=[p for p in owned if bool(((p.native or {}).get('starbase_capabilities') or {}).get('can_build_ships'))]
    design_masses_kt={
        int(profile.get("design_number")) : int(profile.get("dry_mass"))
        for profile in (state.native or {}).get("design_profiles",[])
        if profile.get("design_number") is not None
        and profile.get("dry_mass") is not None
    }
    ship_builds_by_planet=_distribute_ship_builds(
        shipyards,
        ship_builds,
        promotion_by_planet,
        design_masses_kt=design_masses_kt,
    )
    # Keep a visible explanation for a request that no operational base can
    # construct, rather than silently assigning an impossible heavy hull to a
    # Space Dock.
    state.native["shipyard_mass_blocks"]=[
        {
            "design_slot":int(request.design_slot),
            "design_name":str(request.design_name),
            "ship_mass_kt":design_masses_kt.get(int(request.design_slot)),
            "quantity":int(request.quantity),
            "reason":"No operational shipyard can construct this design within its mass limit.",
        }
        for request in ship_builds
        if not any(
            _shipyard_can_build_design(
                yard,design_masses_kt.get(int(request.design_slot)),
            )
            for yard in shipyards
        )
    ]
    existing=state.native.get('production_by_planet',{})
    research_posture=str(
        getattr(research_decision,"posture","") or ""
    ).rsplit(".",1)[-1].upper()
    research_commitment=str(
        getattr(research_decision,"commitment","") or ""
    ).upper()
    selected_research_contributors=set(
        int(x) for x in (
            research_decision.contributor_planet_ids if research_decision is not None else ()
        )
    )
    protected_research=set(
        int(x) for x in (
            research_decision.protected_production_planet_ids if research_decision is not None else ()
        )
    )
    # Persisted/manual decisions can name a P1/P2.  Preserve its active
    # population-import/relay program just as the planner does for new plans.
    growth_hub_ids={
        int(hub.planet_id)
        for hub in promotion_network.hubs
        if int(getattr(hub,"promotion_tier",3) or 3) in (1,2)
        and not bool(getattr(hub,"graduated",False))
    }
    contributor_resources={
        int(planet.id):int(estimated_operating_resources(planet,race_economy)["estimated_resources"])
        for planet in owned
        if int(planet.id) in selected_research_contributors
    }
    # Backwards-compatible derivation for an in-flight/persisted decision made
    # before the explicit commitment field existed.
    if not research_commitment:
        research_commitment=(
            "RECOVERY_HIGH" if research_posture=="MILITARY_EMERGENCY"
            else "SPRINT" if research_posture=="SPRINT"
            else "ROUTINE"
        )
    high_research_commitment=research_commitment in {"SPRINT","RECOVERY_HIGH"}
    expected_resource_total=sum(contributor_resources.values())
    # New decisions explicitly attest to the capacity the planner expects
    # production to free.  Recheck that assertion against this turn's actual
    # operable worlds; an older persisted decision has no field and is safely
    # derived from the current contributor set for compatibility.
    declared_expected_resources=int(
        getattr(research_decision,"expected_resources",0) or 0
    )
    has_explicit_commitment=bool(
        str(getattr(research_decision,"commitment","") or "").strip()
    )
    requested_execution_status=str(
        getattr(research_decision,"execution_status","ACTIVE") or "ACTIVE"
    ).upper()
    planner_has_resources=(
        declared_expected_resources>0
        if has_explicit_commitment else expected_resource_total>0
    )
    contract_executable=bool(
        not high_research_commitment
        or (
            planner_has_resources
            and
            expected_resource_total>0
            and bool(getattr(research_decision,"sprint_executable",True))
        )
    )
    if requested_execution_status=="DEFERRED":
        contract_executable=False
    key_research_sprint=bool(
        research_decision is not None
        and high_research_commitment
        and int(research_decision.allocation_percent or 0)>=25
        and contract_executable
    )
    # Routine research never displaces baseline economic development.  Only a
    # named high-commitment plan with nonzero predicted capacity may do so.
    research_contributors=(
        selected_research_contributors if key_research_sprint else set()
    )
    research_sprint_execution={
        "capability_id":getattr(research_decision,"capability_id",None),
        "capability_name":getattr(research_decision,"capability_name",None),
        "requested_research_allocation":int(getattr(research_decision,"allocation_percent",0) or 0),
        "contributor_planet_ids":sorted(selected_research_contributors),
        "protected_production_planet_ids":sorted(protected_research),
        "expected_available_resources":contributor_resources,
        "expected_available_resource_total":expected_resource_total,
        "expected_resources":(
            declared_expected_resources
            if has_explicit_commitment else expected_resource_total
        ),
        "expected_contributor_resources":contributor_resources,
        "commitment":research_commitment,
        "execution_status":requested_execution_status,
        "key_sprint":key_research_sprint,
        "contributors":[],
        "factories_or_mines_deferred":[],
        "blocked_reason":None,
        # Stable, concise execution contract for playtest tooling.
        "requested_percent":int(getattr(research_decision,"allocation_percent",0) or 0),
        "expected_turns":int(getattr(research_decision,"estimated_turns",0) or 0),
        "deferred_installations":[],
        "protected_ship_work":[],
        "executable":bool(contract_executable),
    }
    if getattr(research_decision,"sprint_blocked_reason",None):
        research_sprint_execution["blocked_reason"]=research_decision.sprint_blocked_reason
    elif getattr(research_decision,"execution_failure_reason",None):
        # Carry a missed predicted sprint through the production diagnostic and
        # native report, rather than leaving a later reader to infer it from
        # a generic 15% recovery order.
        research_sprint_execution["blocked_reason"]=research_decision.execution_failure_reason
        research_sprint_execution["execution_failure"]=True
    elif high_research_commitment and not contract_executable:
        research_sprint_execution["blocked_reason"]=(
            "High-commitment research requires a cleared contributor with positive expected resources; "
            "deferred before production could displace routine work."
        )
        research_sprint_execution["execution_status"]="DEFERRED"
    state.native["research_sprint_execution"]=research_sprint_execution
    mineral_emergencies={}
    resource_runways={}

    # The empirically validated leftover-only flag protects critical shipyards
    # during a 25% sprint without clearing their colony/scout/defense work.
    if research_decision is not None and key_research_sprint:
        for planet_id in sorted(protected_research):
            planet=next((p for p in owned if int(p.id)==planet_id),None)
            if planet is None:
                continue
            queue=existing.get(str(planet_id),existing.get(planet_id,[])) or []
            shipyard=bool(((planet.native or {}).get('starbase_capabilities') or {}).get('can_build_ships'))
            has_custom=any(int(q.get('item_type',0) or 0)==4 and int(q.get('count',0) or 0)>0 for q in queue)
            if shipyard or has_custom:
                orders.add(
                    'set_planet_research_mode',
                    {'planet_id':planet_id,'leftover_only':True,'capability_id':research_decision.capability_id},
                    f"Protect {planet.name} production during the {research_decision.capability_name} sprint; contribute leftover resources only.",
                    priority=146,
                )

    for p in owned:
        queue=[]
        reasons=[]
        strategic_priority=0
        status=installation_status(p,race_economy)
        resource_est=estimated_operating_resources(p,race_economy)
        promotion=promotion_by_planet.get(int(p.id))
        promotion_tier=int(getattr(promotion,"promotion_tier",3) or 3)
        promotion_rank=getattr(promotion,"promotion_rank",None)
        promotion_label={0:"HW",1:"P1",2:"P2"}.get(promotion_tier,"LOCAL")
        promotion_priority={0:132,1:126,2:120}.get(promotion_tier,110)

        base_demand=support_base_material_demands.get(int(p.id))
        base_request=support_base_builds.get(p.id)
        if base_request is not None:
            base_item=base_request.queue_item()
            # Defensive refits are material survival work, not a generic
            # fuel-hub item that can disappear behind a factory batch.
            base_item["strategic_priority"]=int(base_request.priority)
            base_item["strategic_reason"]=base_request.reason
            queue.append(base_item)
            reasons.append(base_request.reason)
            strategic_priority=max(strategic_priority,base_request.priority)
        elif base_demand is not None:
            # Keep the normal development queue active while minerals are
            # concentrated.  Leaving the stalled starbase at queue position 1
            # was the direct cause of idle, under-mined hub worlds.
            reasons.append(base_demand.reason)

        # Existing custom ships are strategic commitments. Preserve them at
        # every operational shipyard so parallel production never erases work
        # that was previously assigned to a secondary yard.  A queue is serial,
        # though, so keep only its near executable horizon.  When a colony
        # launch is required, obsolete probe/defense backlogs yield to it.
        if int(p.id) in ship_builds_by_planet:
            requested=ship_builds_by_planet[int(p.id)]
            colony_launch_priority=any(request.role=="colony" for request in requested)
            old=_preserved_ship_queue_items(
                state,
                existing.get(str(p.id),existing.get(p.id,[])),
                colony_launch_priority=colony_launch_priority,
                shipyard=p,
            )
            queue.extend(old)
            preserved={int(x['design_slot']):x for x in old}
            for req in requested:
                quantity=int(req.quantity)
                if req.role=="colony":
                    queued_colonies=sum(
                        int(item.get("quantity",0))
                        for item in old if item.get("role")=="colony"
                    )
                    quantity=min(
                        quantity,
                        max(0,IMMEDIATE_SHIP_QUEUE_HORIZON-queued_colonies),
                    )
                if quantity<=0:
                    continue
                if req.design_slot in preserved:
                    item=preserved[req.design_slot]
                    item['quantity']=int(item.get('quantity',0))+quantity
                    item['role']=req.role
                    item['objective_top_up']=quantity
                    reasons.append(
                        f"{req.reason} Add {quantity} to the preserved "
                        f"{req.design_name} queue."
                    )
                    strategic_priority=max(strategic_priority,req.priority)
                else:
                    item=req.queue_item()
                    item['quantity']=quantity
                    queue.append(item)
                    reasons.append(req.reason)
                    strategic_priority=max(strategic_priority,req.priority)

        # HARD RULE: never intentionally build more installations than the CURRENT
        # population can operate. Growth can open more headroom next year.
        infrastructure=[]
        # Promotion has a deliberate resource sequence. The homeworld remains
        # the primary core; selected P1 worlds receive the same reserve and
        # installation-first treatment so they can become true relay cores.
        # P2s receive accelerated development only after their P1 parent is
        # operational (the expansion network withholds their active lane until
        # then), preventing the AI from overextending into the third tier.
        is_core=is_economic_core(p) or promotion_tier <= 1
        promoted_p2=promotion_tier == 2 and bool(getattr(promotion,"parent_exporter_id",None))
        mineral_stock=mineral_surface_stock(p)
        mineral_output=estimated_mineral_output(p,race_economy)
        mineral_emergency={"active":False}
        if not race_economy.alternate_reality:
            factory_headroom=int(status["factory_headroom"])
            mine_headroom=int(status["mine_headroom"])
            germanium_per_factory=3 if race_economy.factory_germanium_discount else 4
            reserve_before_infrastructure=working_mineral_reserve(
                p,race_economy,queue,is_core=is_core,
            )
            runway_before_queue=_resource_runway(
                state,p,race_economy,queue,is_core=is_core,
                mineral_output=mineral_output,
            )
            mineral_deficits={
                mineral:max(0,int(reserve_before_infrastructure[mineral])-mineral_stock[mineral])
                for mineral in ("ironium","boranium","germanium")
            }
            germanium_reserve=factory_germanium_floor(
                p,race_economy,is_core=is_core,
            )
            germanium_for_factories=max(0,int(p.germanium)-germanium_reserve)
            germanium_factory_cap=germanium_for_factories//germanium_per_factory
            if promotion_tier == 0:
                factory_batch=min(factory_headroom,25); mine_batch=min(mine_headroom,25)
            elif promotion_tier == 1:
                factory_batch=min(factory_headroom,20); mine_batch=min(mine_headroom,20)
            elif is_core:
                factory_batch=min(factory_headroom,25); mine_batch=min(mine_headroom,25)
            elif promoted_p2:
                factory_batch=min(factory_headroom,14); mine_batch=min(mine_headroom,12)
            else:
                factory_batch=min(factory_headroom,10); mine_batch=min(mine_headroom,8)
            # A mine field begins producing in groups of ten.  Avoid issuing a
            # token mine queue that cannot produce any minerals next year when
            # the planet has the population/resources to complete the group.
            operated_mines=int(mineral_output["operated_mines"])
            if operated_mines<10 and mine_headroom>0:
                mine_batch=min(
                    mine_headroom,
                    max(mine_batch,10-operated_mines),
                )
            mineral_emergency=_mineral_emergency_snapshot(
                mineral_stock,
                reserve_before_infrastructure,
                mineral_output,
                mine_headroom=mine_headroom,
                resource_runway=runway_before_queue,
            )
            if mineral_emergency["active"]:
                mine_batch=min(
                    mine_batch,
                    int(mineral_emergency["mines_required_for_recovery"]),
                )
            germanium_short=(
                factory_headroom>0
                and germanium_factory_cap < factory_batch
            )
            output_shortfall=any(
                amount is not None and int(amount)<(12 if is_core else 8 if promoted_p2 else 4)
                for amount in mineral_output["estimated_mineral_output"].values()
            )
            # A core's status alone is not a reason to keep adding mines.  A
            # mature homeworld can have a deep mineral reserve and enough
            # operating mines to support the next factory batch, while its
            # factory base is still far below the population-operable limit.
            # In that state another mine delays both resources and ships for
            # no strategic return.  Compare installed capacity with the
            # factory expansion this turn rather than relying on a fixed mine
            # count.
            mineral_base_secure=(
                not germanium_short
                and not any(mineral_deficits.values())
                and not output_shortfall
            )
            mine_capacity_ahead_of_factory_growth=(
                operated_mines >= int(p.factories) + max(1,factory_batch)
            )
            defer_core_mines_for_factory_growth=bool(
                is_core
                and factory_headroom>0
                and factory_batch>0
                and mineral_base_secure
                and mine_capacity_ahead_of_factory_growth
            )
            needs_mining=bool(
                mine_headroom>0
                and (
                    (is_core and not defer_core_mines_for_factory_growth)
                    or promoted_p2
                    or germanium_short
                    or any(mineral_deficits.values())
                    or output_shortfall
                )
            )

            # Mine capacity comes first whenever a core needs stock or
            # production is constrained.  This grows all three minerals rather
            # than reacting only after Germanium has already run out.
            if needs_mining and mine_batch>0:
                mine_reason=(
                    "core_mineral_reserve"
                    if is_core and any(mineral_deficits.values())
                    else "core_mine_capacity" if is_core
                    else "mineral_reserve_or_output"
                )
                infrastructure.append({
                    'item':'mine',
                    'quantity':mine_batch,
                    'reason':(
                        'severe_mineral_recovery'
                        if mineral_emergency["active"] else mine_reason
                    ),
                    'mineral_deficits':mineral_deficits,
                    'mineral_emergency':dict(mineral_emergency),
                })
                reasons.append(
                    f"{'severe material-runway recovery' if mineral_emergency['active'] else 'baseline mineral capacity'}: "
                    f"{mine_batch} mines for I/B/G reserve "
                    f"deficits {mineral_deficits['ironium']}/"
                    f"{mineral_deficits['boranium']}/"
                    f"{mineral_deficits['germanium']}kT"
                )

            # Never queue more factories than current surface Germanium can pay
            # for after the appropriate working floor.  Core worlds keep a
            # larger stock so they can continue ship/base production.
            if factory_headroom>0 and germanium_factory_cap>0:
                qty=min(factory_batch,germanium_factory_cap)
                if qty>0:
                    infrastructure.append({
                        'item':'factory',
                        'quantity':qty,
                        'germanium_cost_each':germanium_per_factory,
                        'germanium_available':int(p.germanium),
                        'germanium_floor':germanium_reserve,
                    })
                    reasons.append(
                        f"baseline factory capacity: {qty} factories while retaining "
                        f"{germanium_reserve}kT Germanium"
                    )

            # Non-core colonies still develop toward their current operating
            # cap even when their stock report is incomplete.
            if mine_headroom>0 and not needs_mining and not defer_core_mines_for_factory_growth:
                infrastructure.append({
                    'item':'mine','quantity':mine_batch,'reason':'mine_capacity',
                    'mineral_emergency':dict(mineral_emergency),
                })
                reasons.append(f"baseline mine capacity: {mine_batch} mines")
            elif defer_core_mines_for_factory_growth:
                reasons.append(
                    "factory-first core growth: mineral reserve and operating mine capacity already cover "
                    "the next factory batch"
                )

            # Defenses are not a default sink for excess resources. Only a
            # persona with an explicitly elevated defense posture spends on them;
            # otherwise capped planets prefer objective ships or research.
            if (
                defense_w>1.15
                and p.defenses<max(5,round(5*defense_w))
                and p.population>=70000
            ):
                infrastructure.append({'item':'defense','quantity':max(1,round(defense_w))})

        # Mines and factories are the universal economic foundation: optional
        # ships, bases, and terraforming never jump ahead of currently operable
        # infrastructure.  Research allocation changes whether a world is
        # cleared for research; it never reverses an under-mined world's
        # production order.
        if infrastructure:
            queue[0:0]=infrastructure
            if promotion_tier <= 1:
                reasons.insert(
                    0,
                    f"{promotion_label} promotion rank {promotion_rank or 1}: "
                    "installations precede optional local projects",
                )
            strategic_priority=max(strategic_priority,promotion_priority)

        # Terraforming improves a mature world, but it is never the reason an
        # immature world postpones the mines and factories that pay for it.
        terraforming=evaluate_terraforming(state,p)
        if terraforming.tech_steps>0 and terraforming.tech_gain>0:
            queue.append({
                'item':'max_terraform',
                'quantity':1,
                'current_habitability':terraforming.current_habitability,
                'target_habitability':terraforming.tech_habitability,
                'terraform_steps':terraforming.tech_steps,
                'resource_cost_per_step':terraforming.resource_cost_per_step,
            })
            reasons.append(
                f"terraform {p.name} from {terraforming.current_habitability}% toward "
                f"{terraforming.tech_habitability}% ({terraforming.tech_steps} available steps) after economic infrastructure"
            )
            strategic_priority=max(strategic_priority,108)

        current_queue=existing.get(str(p.id),existing.get(p.id,[])) or []
        current_has_standard=any(
            int(q.get('item_type',0))==2 and int(q.get('count',0))>0
            for q in current_queue
        )
        current_has_custom=any(
            int(q.get('item_type',0))==4 and int(q.get('count',0))>0
            for q in current_queue
        )

        is_research_contributor=int(p.id) in research_contributors
        generated_critical=any(
            q.get('item') in ('ship_design','starbase_design')
            and (
                (
                    q.get('item')=='ship_design'
                    and str(q.get('role') or '')=='combat'
                    and any(token in str(q.get('strategic_reason') or '').casefold()
                            for token in ('emergency','territorial'))
                )
                or (
                    q.get('item')=='ship_design'
                    and str(q.get('role') or '')=='colony'
                    and int(q.get('strategic_priority',0) or 0)>=200
                )
                or q.get('item')=='starbase_design'
            )
            for q in queue
        )
        emergency_defense=bool(
            research_posture=="MILITARY_EMERGENCY"
            and any(q.get("item")=="defense" for q in queue)
        )
        if is_research_contributor and int(p.id) in growth_hub_ids:
            is_research_contributor=False
            research_sprint_execution["contributors"].append({
                "planet_id":int(p.id),"status":"protected_colony_support",
                "reason":"ungraduated P1/P2 population-import and relay program is protected",
            })
            research_sprint_execution["protected_ship_work"].append({
                "planet_id":int(p.id),
                "reason":"ungraduated P1/P2 population-import and relay program is protected",
            })
        elif is_research_contributor and (
            current_has_custom
            or generated_critical
            or emergency_defense
            or mineral_emergency["active"]
        ):
            is_research_contributor=False
            blocked=(
                "mineral-emergency recovery is protected"
                if mineral_emergency["active"] else
                "emergency planetary defense is protected"
                if emergency_defense else
                "critical production is protected"
                if int(p.id) in protected_research else
                "this turn assigned an immediate strategic hull"
            )
            research_sprint_execution["contributors"].append({
                "planet_id":int(p.id),"status":"protected_production","reason":blocked,
            })
            orders.notes.append(
                f"RESEARCH PROTECTION: {p.name} was not cleared for the "
                f"{research_decision.capability_name if research_decision else 'research'} sprint "
                "because a critical ship/starbase build is present."
            )
        elif is_research_contributor:
            deferred=[item.get("item") for item in queue if item.get("item") in {"mine","factory"}]
            queue=[]
            reasons=[
                f"selected mature contributor for the {research_decision.capability_name} "
                f"{research_decision.posture.lower()}"
            ]
            strategic_priority=max(strategic_priority,145)
            research_sprint_execution["contributors"].append({
                "planet_id":int(p.id),"status":"contributing","reason":"queue cleared for named sprint",
            })
            if deferred:
                research_sprint_execution["factories_or_mines_deferred"].append({
                    "planet_id":int(p.id),"items":deferred,
                })
                research_sprint_execution["deferred_installations"].append({
                    "planet_id":int(p.id),"items":deferred,
                })

        if int(p.id) in scanner_sites:
            scanner_name=str((penetrating_scanner or {}).get("name", "penetrating planetary scanner"))
            scanner_range=int((penetrating_scanner or {}).get("range", 0) or 0)
            queue.append({
                "item":PLANETARY_SCANNER_QUEUE_ITEM,
                "quantity":1,
                "scanner_name":scanner_name,
                "scanner_range":scanner_range,
                "penetrating":True,
                "standard_item_id":27,
                "native_status":"ENABLED_UNTRUSTED_STANDARD_QUEUE_ITEM",
            })
            reasons.append(
                f"deploy researched {scanner_name} planetary sensor ({scanner_range} ly normal / "
                f"{scanner_range // 2} ly penetrating range) for border and planet intelligence"
            )
            strategic_priority=max(strategic_priority,118)

        # Keep the precedence explicit at the final queue assembly point.  It
        # protects against future queue additions above (or a preserved custom
        # ship build) accidentally placing an unaffordable ship ahead of the
        # mine/factory work that restores its mineral supply.
        colony_launch_priority=(
            max(0,int(state.year)-2400)<20
            and any(
                item.get("item")=="ship_design" and item.get("role")=="colony"
                for item in queue
            )
        )
        if colony_launch_priority:
            reasons.append(
                "opening colony launch priority: keep the next two colony hulls ahead of routine infrastructure"
            )
        queue=_prioritize_economic_infrastructure(
            queue,
            opening_growth=max(0,int(state.year)-2400)<10,
            colony_launch_priority=colony_launch_priority,
            mineral_emergency=mineral_emergency,
            resource_runway=_resource_runway(
                state,p,race_economy,queue,is_core=is_core,
                mineral_output=mineral_output,
            ),
            invasion_active=bool(
                ((state.native or {}).get("territorial_defense") or {}).get("needs_response")
                or research_posture=="MILITARY_EMERGENCY"
            ),
            funded_research_sprint=key_research_sprint,
        )

        mineral_reserve=working_mineral_reserve(
            p,race_economy,queue,is_core=is_core,
        )
        mineral_deficits={
            mineral:max(0,int(mineral_reserve[mineral])-mineral_stock[mineral])
            for mineral in ("ironium","boranium","germanium")
        }
        resource_runway=_resource_runway(
            state,p,race_economy,queue,is_core=is_core,
            mineral_output=mineral_output,
        )
        mineral_emergencies[int(p.id)]=dict(mineral_emergency)
        resource_runways[int(p.id)]=resource_runway
        payload={
            'planet_id':p.id,
            'queue':queue,
            'economy':{
                **status,
                'estimated_resources':resource_est['estimated_resources'],
                'operable_factories_per_10k':race_economy.operable_factories_per_10k,
                'operable_mines_per_10k':race_economy.operable_mines_per_10k,
                'factory_output_per_10':race_economy.factory_output_per_10,
                'mine_output_per_10':race_economy.mine_output_per_10,
                'economic_core':is_core,
                'promotion':{
                    'tier':promotion_tier,
                    'label':promotion_label,
                    'rank':promotion_rank,
                    'parent_id':getattr(promotion,"promotion_parent_id",None),
                    'parent_operational':bool(getattr(promotion,"parent_exporter_id",None)),
                    'economic_value':getattr(promotion,"economic_value",None),
                    'strategic_value':getattr(promotion,"strategic_value",None),
                    'overall_value':getattr(promotion,"overall_value",None),
                    'development_priority':promotion_priority,
                },
                'queue_priority_bands':[
                    {
                        'item':str(item.get('item','?')),
                        'role':item.get('role'),
                        'band':int(item.get('queue_priority_band',4) or 4),
                        'reason':item.get('queue_priority_reason'),
                    }
                    for item in queue
                ],
                'research_sprint':{
                    'requested_research_allocation':research_sprint_execution['requested_research_allocation'],
                    'contributor':int(p.id) in selected_research_contributors,
                    'deferred_factories_or_mines':[
                        row for row in research_sprint_execution['factories_or_mines_deferred']
                        if int(row['planet_id'])==int(p.id)
                    ],
                },
                'mineral_surface_stock':mineral_stock,
                'mineral_reserve':mineral_reserve,
                'mineral_reserve_deficits':mineral_deficits,
                'mineral_emergency':dict(mineral_emergency),
                'resource_runway':resource_runway,
                'support_base_material_demand':(
                    base_demand.to_dict() if base_demand is not None else None
                ),
                'estimated_mineral_output':mineral_output['estimated_mineral_output'],
                'mineral_concentrations':mineral_output['concentrations'],
                'germanium_surface':int(p.germanium),
                'germanium_per_factory':3 if race_economy.factory_germanium_discount else 4,
                'germanium_concentration':(p.native or {}).get('mineral_concentrations',[None,None,None])[2],
                'population_raw_hundreds':(p.native or {}).get('population_raw_hundreds'),
                'population_source_year':(p.native or {}).get('population_source_year',state.year),
                'theoretical_race_max_population':theoretical_max_population(state.race),
                'planet_population_capacity':planet_population_capacity(p,state.race),
                'population_capacity_percent':round(population_capacity_fraction(p,state.race)*100,1),
                'projected_growth':projected_population_growth(p,state.race),
                'projected_next_population':projected_next_population(p,state.race),
            },
        }

        if queue:
            if reasons:
                reason=(
                    f"{plan.persona_name + ': ' if plan else ''}objective production first: "
                    + ' | '.join(reasons)
                )
                priority=max(promotion_priority,110,strategic_priority)
            else:
                reason=(
                    f"{plan.persona_name + ': ' if plan else ''}build only currently operable infrastructure: "
                    f"factories {p.factories}/{status['factory_cap']}, mines {p.mines}/{status['mine_cap']} "
                    f"at population {p.population:,}."
                )
                priority=int(60*develop)
            orders.add('set_planet_queue',payload,reason,priority=priority)
        else:
            # Emit an explicit native queue decision even for a previously empty
            # world.  A deliberate empty/research queue is not an accidental
            # idle planet and makes every planet's turn intent auditable.
            payload['clear_queue']=True
            payload['research_when_idle']=True
            orders.add(
                'set_planet_queue',
                payload,
                (
                    f"{plan.persona_name + ': ' if plan else ''}"
                    + (
                        f"selected contributor to the {research_decision.capability_name} sprint; "
                        "hold an explicit empty queue and direct capacity to the named unlock."
                        if is_research_contributor and research_decision is not None
                        else (
                            f"population currently supports only {status['factory_cap']} factories and "
                            f"{status['mine_cap']} mines; existing {p.factories}/{p.mines}. No higher-priority "
                            "operable production exists, so hold an explicit research queue."
                        )
                    )
                ),
                priority=145 if is_research_contributor else 92,
            )
    state.native["mineral_emergency"]=mineral_emergencies
    state.native["resource_runway"]={
        str(planet_id):runway for planet_id,runway in resource_runways.items()
    }
    if key_research_sprint and not any(
        row.get("status")=="contributing"
        for row in research_sprint_execution["contributors"]
    ):
        research_sprint_execution["blocked_reason"]=(
            "No selected mature contributor could be cleared without displacing protected colony support, mineral-emergency recovery, or immediate strategic production."
        )
        research_sprint_execution["executable"]=False
        research_sprint_execution["execution_status"]="DEFERRED"
        orders.notes.append(
            "RESEARCH SPRINT BLOCKED: no eligible contributor was cleared; protected colony support or production was retained."
        )

    colony_fleets=[
        f for f in state.fleets
        if f.owner==state.player_id and f.role=="colony" and f.destination_planet_id is None
    ]
    expand=plan.objective("expand")*plan.mission("colonize") if plan else 1.0
    watchdog=(state.native or {}).get("strategic_watchdog") or {}
    expand*=float(watchdog.get("colonization_pressure",1.0))
    # An active colony waypoint and a semantic colony order already queued by
    # an earlier coordinator pass reserve the same claim.  Both are removed
    # before scoring, so each launch-ready hull evaluates the next-best valid
    # world instead of treating the first reserved candidate as a terminal
    # hold.
    assigned=reserved_colony_target_ids(state)
    assigned.update(
        int(order.payload["destination_planet_id"])
        for order in orders.orders
        if order.kind == "colony_operation"
        and (order.payload or {}).get("destination_planet_id") is not None
    )
    source_reserve=_colony_source_reserve(state)
    planned_population_loads={}
    minimum_launch_population=COLONY_LOAD_COLONISTS+source_reserve
    loading_worlds=[
        p for p in owned
        if int(p.population or 0)>=minimum_launch_population
    ]
    refuel_worlds=[
        p for p in owned
        if bool((((p.native or {}).get("starbase_capabilities") or {}).get("can_refuel")))
    ]
    launch_worlds=[p for p in loading_worlds if p in refuel_worlds]
    colony_range_blocks=[]
    colony_target_diagnostics=[]
    colony_capture_conversions=[]
    colony_repurpose_intents=[]
    # A normal colony threshold is a preference, not a reason to strand a
    # launch-ready hull permanently.  Tests and alternate host integrations may
    # tune this, but a two-turn wait is the safe default: it lets a new scan or
    # an in-flight claimant settle before relaxing the preference.
    idle_fallback_turns=max(1,int((state.native or {}).get("idle_colony_fallback_turns",2) or 2))
    capture_by_planet={
        int(target.planet_id):target for target in population_capture_targets(state)
    }

    def staging_world(fleet):
        choices=loading_worlds or refuel_worlds
        if not choices:
            return None
        # Population is the primary launch constraint; an operational base is
        # the tie-breaker because it supplies a full, predictable fuel tank.
        return max(
            choices,
            key=lambda p:(
                int(p.population or 0),
                bool((((p.native or {}).get("starbase_capabilities") or {}).get("can_refuel"))),
                -distance(fleet.position,p.position),
            ),
        )

    for fleet in colony_fleets:
        source=_planet_under_fleet(fleet,owned)
        aboard=int(fleet.cargo_population or 0)
        population_to_load=max(0,COLONY_LOAD_COLONISTS-aboard)
        population_to_load_kt=cargo_kt_from_colonists(population_to_load)
        # Candidate scoring includes safe-transit time.  At a verified refuel
        # base, score the pending colony leg from the host-provided full tank,
        # not the stale fuel amount serialized before the base refuel occurs.
        if source is not None and source in refuel_worlds:
            profile=(fleet.native or {}).get("fuel_profile")
            if profile:
                capacity=float(profile.get("fuel_capacity",0) or 0)
                if capacity>0:
                    profile["fuel"]=max(float(profile.get("fuel",0) or 0),capacity)
                    profile["effective_fuel"]=capacity
                    profile["at_starbase"]=True
        raw_ranked=[
            c for c in score_colony_candidates(state,fleet,plan)
            if c.planet_id not in assigned
        ]
        policy=colonization_policy(state,plan)
        idle_turns=max(0,int((fleet.native or {}).get(
            "idle_colony_turns",(fleet.native or {}).get("idle_turns",0)
        ) or 0))
        fallback_used=False
        # Preserve the policy's normal floor by default.  A hull that has
        # demonstrably waited may take one close, *currently positive* bridge
        # world only when terraforming makes it a sound eventual investment.
        # This deliberately applies after every phase-specific preference,
        # rather than letting a later-phase colony wait forever for an ideal
        # scan.  The current-habitability guard still excludes red worlds.
        if policy.normal_habitability_floor is not None:
            normal=[
                candidate for candidate in raw_ranked
                # Planning habitability may include future terraforming.  The
                # normal tier is deliberately current-world quality; otherwise
                # a 30% / eventual-70% candidate silently passes a 50% normal
                # threshold and we never record its controlled fallback.
                if int(
                    candidate.current_habitability
                    if candidate.current_habitability is not None
                    else candidate.habitability
                )>=int(policy.normal_habitability_floor)
                or candidate.selection_basis in {"exceptional_resources","compact_frontier_bridge"}
            ]
            fallback=[
                candidate for candidate in raw_ranked
                if int(candidate.current_habitability or 0)>=25
                and max(
                    int(candidate.tech_terraform_habitability or candidate.habitability),
                    int(candidate.eventual_terraform_habitability or candidate.habitability),
                )>=50
                and float(candidate.distance_from_nearest_owned)<=120.0
            ]
            if normal:
                raw_ranked=normal
            elif idle_turns>=idle_fallback_turns and fallback:
                raw_ranked=fallback
                fallback_used=True
            else:
                raw_ranked=[]

        candidate_by_id={int(candidate.planet_id):candidate for candidate in raw_ranked}
        source_population_available=bool(
            source is not None
            and int(source.population or 0)-int(planned_population_loads.get(int(source.id),0))
            >=source_reserve+population_to_load
        )
        for target in state.planets:
            if not bool(target.observed):
                continue
            target_id=int(target.id)
            candidate=candidate_by_id.get(target_id)
            potential=evaluate_terraforming(state,target)
            fleet_distance=distance(fleet.position,target.position)
            owner=target.owner
            capture=capture_by_planet.get(target_id)
            if owner is not None and int(owner)!=int(state.player_id):
                capacity=max(
                    (
                        int(getattr(other,"cargo_capacity",0) or (other.native or {}).get("cargo_capacity",0) or 0)
                        for other in state.fleets
                        if other.owner==state.player_id and other.role=="freighter"
                    ),
                    default=0,
                )
                hard_reason=(
                    "foreign_owned_population_capture_not_colony"
                    if capture is not None else "foreign_owned_capture_population_unknown_or_unviable"
                )
                blocked=(
                    "transport_capacity" if capture is not None and capacity<int(capture.required_kt)
                    else "diplomacy_or_population_scheduler" if capture is not None
                    else "population_estimate"
                )
                colony_target_diagnostics.append({
                    "fleet_id":int(fleet.id),"planet_id":target_id,"name":target.name,
                    "owner":owner,"distance_from_owned":min((distance(target.position,p.position) for p in owned),default=None),
                    "distance_from_colony_fleet":round(fleet_distance,2),
                    "current_racial_hab":potential.current_habitability,
                    "current_terraform_hab":potential.tech_habitability,
                    "eventual_hab":potential.eventual_habitability,
                    "reachable":False,"fuel_safe":False,"population_available":source_population_available,
                    "hard_rejection_reason":hard_reason,"score":None,
                    "capture_desirable":capture is not None,
                    "required_population":None if capture is None else int(capture.required_colonists),
                    "available_capacity":capacity,"blocked_by":blocked,
                })
                # A foreign world can never be a colony operation.  Put every
                # such discovery on the capture/war evaluation trace; eligible
                # targets are then consumed by shared transport, whose range,
                # source-population, and diplomacy gates decide whether a war
                # action is actually legal this turn.
                colony_capture_conversions.append({
                    "fleet_id":int(fleet.id),
                    "planet_id":target_id,
                    "planet_name":target.name,
                    "owner_id":int(owner),
                    "action":"POPULATION_CAPTURE_EVALUATION",
                    "required_population":(
                        None if capture is None else int(capture.required_colonists)
                    ),
                    "required_transport_kt":(
                        None if capture is None else int(capture.required_kt)
                    ),
                    "available_transport_kt":capacity,
                    "status":(
                        "NOT_CURRENTLY_CAPTURABLE"
                        if capture is None
                        else "READY_FOR_SHARED_TRANSPORT_EVALUATION"
                        if capacity>=int(capture.required_kt)
                        else "NEEDS_CAPTURE_TRANSPORT"
                    ),
                    "policy":"foreign worlds are excluded from ordinary colony orders",
                })
                continue
            if owner is not None:
                hard_reason="already_owned"
            elif candidate is not None:
                hard_reason=None
            elif fleet_distance>float(colony_support_distance(state)):
                hard_reason="outside_practical_expansion_radius"
            elif colony_transit_years(fleet,target.position)>colony_transit_year_budget(state):
                hard_reason="transit_budget"
            elif int(potential.current_habitability or 0)<=0:
                hard_reason="currently_uninhabitable"
            elif (
                policy.normal_habitability_floor is not None
                and int(potential.tech_habitability or 0)<int(policy.normal_habitability_floor)
            ):
                hard_reason="below_normal_and_controlled_fallback_floor"
            elif policy.normal_habitability_floor is not None and idle_turns<idle_fallback_turns:
                hard_reason="awaiting_idle_colony_fallback"
            else:
                hard_reason="not_selected_by_colony_score"
            reachable=bool(
                candidate is not None
                and (
                    mission_reachable(fleet,target.position,"colonize")
                    if aboard>=COLONY_LOAD_COLONISTS else
                    mission_reachable_with_planned_cargo(
                        fleet,target.position,"colonize",{"population":population_to_load_kt}
                    )
                )
            )
            colony_target_diagnostics.append({
                "fleet_id":int(fleet.id),"planet_id":target_id,"name":target.name,
                "owner":owner,"distance_from_owned":min((distance(target.position,p.position) for p in owned),default=None),
                "distance_from_colony_fleet":round(fleet_distance,2),
                "current_racial_hab":potential.current_habitability,
                "current_terraform_hab":potential.tech_habitability,
                "eventual_hab":potential.eventual_habitability,
                "reachable":reachable,"fuel_safe":reachable,"population_available":source_population_available,
                "hard_rejection_reason":hard_reason,
                "score":None if candidate is None else round(float(candidate.score),3),
                "fallback_used":fallback_used,
                "idle_turns":idle_turns,
            })
        ranked=[]
        refuel_launches={}
        for candidate in raw_ranked:
            target=next(p for p in state.planets if p.id==candidate.planet_id)
            reachable=(
                mission_reachable(fleet,target.position,'colonize')
                if aboard>=COLONY_LOAD_COLONISTS
                else mission_reachable_with_planned_cargo(
                    fleet,target.position,'colonize',{"population":population_to_load_kt}
                )
            )
            if reachable:
                ranked.append(candidate)
                continue
            if aboard<COLONY_LOAD_COLONISTS:
                supported=[
                    world for world in launch_worlds
                    if _can_launch_colony_after_refuel(
                        fleet,world,target,population_to_load_kt
                    )
                ]
                if supported:
                    refuel_launches[int(candidate.planet_id)]=supported

        if not ranked:
            # Do not create a movement order just to keep a colony ship busy.
            # If useful colony intelligence exists, however, stage an empty
            # hull at the best population/refuel world so it is launch-ready.
            launch_candidate=next(
                (candidate for candidate in raw_ranked if int(candidate.planet_id) in refuel_launches),
                None,
            )
            launch_options=(
                refuel_launches.get(int(launch_candidate.planet_id),[])
                if launch_candidate is not None else []
            )
            home=(
                max(
                    launch_options,
                    key=lambda p:(int(p.population or 0),-distance(fleet.position,p.position)),
                )
                if launch_options else
                (staging_world(fleet) if raw_ranked and aboard<COLONY_LOAD_COLONISTS else None)
            )
            if (
                launch_candidate is not None
                and home is not None
                and source is not None
                and int(source.id)==int(home.id)
            ):
                profile=(fleet.native or {}).get("fuel_profile")
                if profile:
                    capacity=float(profile.get("fuel_capacity",0) or 0)
                    if capacity>0:
                        profile["fuel"]=max(float(profile.get("fuel",0) or 0),capacity)
                        profile["effective_fuel"]=capacity
                        profile["at_starbase"]=True
                ranked.append(launch_candidate)
            elif raw_ranked and not launch_options:
                colony_range_blocks.append({
                    "fleet_id":int(fleet.id),
                    "candidate_planet_ids":[int(candidate.planet_id) for candidate in raw_ranked[:4]],
                    "reason":"No owned population/refuel launch world can make a fuel-safe loaded colony leg with this design.",
                })
            if not ranked:
                if home is not None and (source is None or source.id!=home.id):
                    orders.add(
                        "move_fleet",
                        {
                            "fleet_id":fleet.id,
                            "destination_planet_id":home.id,
                            "warp":mission_warp(fleet,home.position,"return_for_colonists"),
                            "mission":"return_for_colonists",
                            "cargo_population_before":aboard,
                        },
                        f"{plan.persona_name + ': ' if plan else ''}stage empty colony ship at {home.name}; "
                        f"known viable claims exist, but none is fuel-safe with the planned "
                        f"{COLONY_LOAD_KT} kT / {COLONY_LOAD_COLONISTS:,}-colonist load. "
                        f"Prepare at the best population/refuel hub while support expands.",
                        priority=int(118*expand),
                    )
                elif not raw_ranked:
                    # No valid unreserved colony claim remains.  Return an
                    # otherwise idle hull to a population/refuel hub when
                    # possible; at that hub explicitly reserve it for the
                    # next claim or capture-capable conversion instead of
                    # reporting an indefinite, unexplained hold.
                    repurpose_home=staging_world(fleet)
                    repurpose={
                        "fleet_id":int(fleet.id),
                        "action":"COLONY_RESERVE_REPURPOSE",
                        "idle_turns":idle_turns,
                        "destination_planet_id":(
                            None if repurpose_home is None else int(repurpose_home.id)
                        ),
                        "reason":(
                            "No unreserved, currently viable colony claim remains; "
                            "reserve this hull for the next valid claim or a separately "
                            "approved population-capture transport program."
                        ),
                    }
                    colony_repurpose_intents.append(repurpose)
                    if (
                        repurpose_home is not None
                        and (source is None or int(source.id)!=int(repurpose_home.id))
                    ):
                        orders.add(
                            "move_fleet",
                            {
                                "fleet_id":fleet.id,
                                "destination_planet_id":repurpose_home.id,
                                "warp":mission_warp(fleet,repurpose_home.position,"return_for_colony_repurpose"),
                                "mission":"return_for_colony_repurpose",
                                "colony_repurpose":True,
                            },
                            f"Return colony ship {fleet.name} to {repurpose_home.name}: no valid unreserved "
                            "colony target remains, so retain the hull for the next claim or approved capture program.",
                            priority=int(116*expand),
                        )
                continue

        best=ranked[0]
        loaded=aboard>=COLONY_LOAD_COLONISTS
        can_load=(
            source is not None
            and (
                int(source.population or 0)
                -int(planned_population_loads.get(int(source.id),0))
            )>=source_reserve+population_to_load
            and aboard<COLONY_LOAD_COLONISTS
        )

        if loaded or can_load:
            assigned.add(best.planet_id)
            already_committed=(
                int(planned_population_loads.get(int(source.id),0))
                if source is not None else 0
            )
            if can_load and source is not None:
                planned_population_loads[int(source.id)]=already_committed+population_to_load
            source_after=(
                int(source.population)-already_committed-population_to_load
                if can_load and source is not None else None
            )
            alts="; ".join(f"{c.planet_name}={c.score:.1f}" for c in ranked[1:4]) or "none"
            orders.add(
                "colony_operation",
                {
                    "fleet_id":fleet.id,
                    "source_planet_id":source.id if can_load else None,
                    "destination_planet_id":best.planet_id,
                    "target_habitability":best.habitability,
                    "target_current_habitability":best.current_habitability,
                    "target_current_tech_terraform_habitability":best.tech_terraform_habitability,
                    "target_eventual_terraform_habitability":best.eventual_terraform_habitability,
                    "target_terraform_steps":best.terraform_steps,
                    "target_distance_from_homeworld":round(best.distance_from_homeworld,2),
                    "colonization_stage":best.colonization_stage,
                    "habitability_floor":best.habitability_floor,
                    "selection_basis":best.selection_basis,
                    "warp":mission_warp(
                        fleet,
                        next(p.position for p in state.planets if p.id==best.planet_id),
                        "colonize",
                    ),
                    "load_25kt_population":bool(can_load),
                    "native_load_order_kt":COLONY_LOAD_KT if can_load else 0,
                    "load_population_kt":population_to_load_kt if can_load else 0,
                    "population_loaded":population_to_load if can_load else 0,
                    "population":aboard+population_to_load if can_load else aboard,
                    "mission":"colonize",
                    "cargo_population_before":aboard,
                    "source_population":source.population if source else None,
                    "source_population_after_load":(
                        source_after
                    ),
                    "source_population_reserve":source_reserve if can_load else None,
                    "minimum_launch_population":(
                        source_reserve+population_to_load if can_load else None
                    ),
                    "load_decision":"emit_25kt_load" if can_load else "already_loaded",
                },
                (
                    f"{plan.persona_name + ': ' if plan else ''}colonize {best.planet_name}; "
                    f"score={best.score:.1f}; {best.explanation}. Alternatives: {alts}. "
                    +(
                        f"Load {COLONY_LOAD_KT} kT ({COLONY_LOAD_COLONISTS:,} colonists) first; "
                        f"expect {population_to_load:,} colonists to transfer and "
                        f"{source_after:,} to remain on {source.name} after this turn's planned loads."
                        if can_load and source is not None else "Use colonists already aboard."
                    )
                ),
                priority=int(125*expand),
            )
            continue

        # A viable target exists, but the ship cannot safely execute it yet.
        # Empty ships away from population return home. Ships already at owned
        # population simply hold; fleet_intent records that purposeful wait.
        home=staging_world(fleet) if aboard<COLONY_LOAD_COLONISTS else None
        if home is not None and (source is None or source.id!=home.id):
            orders.add(
                "move_fleet",
                {
                    "fleet_id":fleet.id,
                    "destination_planet_id":home.id,
                    "warp":mission_warp(fleet,home.position,"return_for_colonists"),
                    "mission":"return_for_colonists",
                    "cargo_population_before":aboard,
                    "desired_colony_planet_id":best.planet_id,
                    "desired_colony_score":best.score,
                },
                f"{plan.persona_name + ': ' if plan else ''}best colony is {best.planet_name}, "
                f"but the ship is empty or its current world cannot supply "
                f"{COLONY_LOAD_KT} kT ({COLONY_LOAD_COLONISTS:,} colonists) while retaining "
                f"the {source_reserve:,}-colonist reserve; stage at {home.name} for population first.",
                priority=int(120*expand),
            )


    state.native["colony_range_blocked_fleet_ids"]=sorted({
        int(block["fleet_id"]) for block in colony_range_blocks
    })
    state.native["colony_range_blocks"]=colony_range_blocks
    state.native["colony_target_diagnostics"]=colony_target_diagnostics
    state.native["colony_capture_conversions"]=colony_capture_conversions
    state.native["colony_repurpose_intents"]=colony_repurpose_intents

    # Population and mineral freight share one fleet-level ranked schedule.
    # The scheduler consumes the just-created production queues so a selected
    # P1/P2/tactical destination gets one full manifest, not competing orders
    # from independent population and mineral passes.
    schedule_shared_transport_orders(
        state,
        orders,
        plan,
        support_base_material_demands=support_base_material_demands,
    )
