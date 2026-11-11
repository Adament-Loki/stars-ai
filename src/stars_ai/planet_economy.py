
from __future__ import annotations

from dataclasses import dataclass, asdict
import math
from types import SimpleNamespace


MINERAL_TYPES = ("ironium", "boranium", "germanium")


@dataclass(frozen=True)
class RaceEconomy:
    colonists_per_resource:int
    factory_output_per_10:int
    factory_build_cost:int
    operable_factories_per_10k:int
    factory_germanium_discount:bool
    mine_output_per_10:int
    mine_build_cost:int
    operable_mines_per_10k:int
    alternate_reality:bool=False

    def to_dict(self):
        return asdict(self)


def decode_race_economy(race) -> RaceEconomy:
    """
    StarsAPI PlayerBlock fullData mapping:

      [54] population efficiency, in hundreds of colonists/resource
      [55] resources produced by 10 factories
      [56] factory resource build cost
      [57] factories operable per 10,000 colonists
      [58] minerals produced by 10 mines
      [59] mine resource build cost
      [60] mines operable per 10,000 colonists
      [73] bit 7 = factory costs 1kT less Germanium

    StarsAPI's own "beefy" race writes 7, 15/5/25, 25/2/25.
    """
    native=getattr(race,"native",{}) or {}
    raw=list(native.get("economy_raw",[]) or [])
    pop_raw=int(native.get("population_efficiency_raw",10) or 10)
    flags=int(native.get("flags_73",0) or 0)

    # Conservative standard-race defaults if full race data is unavailable.
    while len(raw)<6:
        raw.append((10,10,10,10,5,10)[len(raw)])

    prt=str(getattr(race,"primary_trait","") or "")
    ar=("Alternate Reality" in prt) or int(native.get("prt_id",-1))==8

    return RaceEconomy(
        colonists_per_resource=max(100,pop_raw*100),
        factory_output_per_10=max(0,int(raw[0])),
        factory_build_cost=max(1,int(raw[1])),
        operable_factories_per_10k=max(0,int(raw[2])),
        factory_germanium_discount=bool(flags & 0x80),
        mine_output_per_10=max(0,int(raw[3])),
        mine_build_cost=max(1,int(raw[4])),
        operable_mines_per_10k=max(0,int(raw[5])),
        alternate_reality=ar,
    )


def installation_caps(population:int,economy:RaceEconomy) -> tuple[int,int]:
    """
    Current-population operating ceiling.

    Stars! defines the setting as "Every 10,000 colonists may operate up to N".
    Infrastructure above this ceiling provides no current operating benefit.
    """
    if economy.alternate_reality:
        return 0,0
    pop=max(0,int(population))
    factories=(pop*economy.operable_factories_per_10k)//10000
    mines=(pop*economy.operable_mines_per_10k)//10000
    return int(factories),int(mines)


def installation_status(planet,economy:RaceEconomy) -> dict:
    fcap,mcap=installation_caps(int(planet.population or 0),economy)
    factories=int(planet.factories or 0)
    mines=int(planet.mines or 0)
    return {
        "population":int(planet.population or 0),
        "factory_cap":fcap,
        "mine_cap":mcap,
        "factories":factories,
        "mines":mines,
        "factory_headroom":max(0,fcap-factories),
        "mine_headroom":max(0,mcap-mines),
        "factory_overbuilt":max(0,factories-fcap),
        "mine_overbuilt":max(0,mines-mcap),
        "factories_fully_utilized":factories>=fcap,
        "mines_fully_utilized":mines>=mcap,
    }


RESOURCE_TARGET = 50_000
FACTORY_OVERBUILD_FRACTION = 0.05


def factory_build_ceiling(factory_cap: int) -> int:
    """Most factories a world may own: the operable cap plus five percent.

    Population can operate `factory_cap` factories. A small buffer covers the
    next year's growth. Anything above that ceiling is idle industry.
    """

    cap = max(0, int(factory_cap))
    return int(math.ceil(cap * (1.0 + FACTORY_OVERBUILD_FRACTION)))


def population_resource_value(economy: RaceEconomy, habitability: float) -> float:
    """Yearly resources from 10,000 colonists on a world of this habitability.

    The population term is one resource per `colonists_per_resource` people.
    Those people also raise the factory cap, so a fully factored world adds
    the factory term as well. Habitability scales the value because a red
    world does not grow the population that produces either term.
    """

    hab = max(0.0, min(100.0, float(habitability or 0)))
    per_10k = (
        10000 / max(1, int(economy.colonists_per_resource))
        + int(economy.operable_factories_per_10k) * int(economy.factory_output_per_10) / 10.0
    )
    return (hab / 100.0) * per_10k


def measure_empire_resources(state) -> dict:
    """Resources this race's planets produce from population and operable factories."""

    economy = decode_race_economy(getattr(state, "race", None))
    owned = [
        planet for planet in getattr(state, "planets", []) or []
        if getattr(planet, "owner", None) == getattr(state, "player_id", None)
    ]
    population = factories = factory_cap = 0
    population_resources = factory_resources = 0
    worlds = []
    for planet in owned:
        estimate = estimated_operating_resources(
            planet, economy, energy_tech=int(getattr(getattr(state, "tech", None), "energy", 0) or 0),
        )
        population += int(estimate["population"])
        factories += int(estimate["factories"])
        factory_cap += int(estimate["factory_cap"])
        population_resources += int(estimate["population_resources"])
        factory_resources += int(estimate["factory_resources"])
        worlds.append({
            "planet_id": int(planet.id),
            "name": str(getattr(planet, "name", "") or planet.id),
            "population": int(estimate["population"]),
            "factories": int(estimate["factories"]),
            "factory_cap": int(estimate["factory_cap"]),
            "population_resources": int(estimate["population_resources"]),
            "factory_resources": int(estimate["factory_resources"]),
            "resources": int(estimate["estimated_resources"]),
        })
    at_cap = (
        population // max(1, economy.colonists_per_resource)
        + (factory_cap * economy.factory_output_per_10) // 10
    )
    if economy.alternate_reality:
        energy = int(getattr(getattr(state, "tech", None), "energy", 0) or 0)
        at_cap = (population // max(1, economy.colonists_per_resource)) * (energy + 1) // 10
    resources = population_resources + factory_resources
    worlds.sort(key=lambda row: (-row["resources"], row["planet_id"]))
    return {
        "year": int(getattr(state, "year", 0) or 0),
        "colonists_per_resource": int(economy.colonists_per_resource),
        "factory_output_per_10": int(economy.factory_output_per_10),
        "operable_factories_per_10k": int(economy.operable_factories_per_10k),
        "worlds": len(owned),
        "population": population,
        "factories": factories,
        "factory_cap": factory_cap,
        "population_resources": population_resources,
        "factory_resources": factory_resources,
        "resources": resources,
        "resources_at_factory_cap": at_cap,
        "factory_gap_resources": max(0, at_cap - resources),
        "resource_target": RESOURCE_TARGET,
        "planets": worlds,
    }


def estimated_operating_resources(planet,economy:RaceEconomy, *, energy_tech:int|None=None) -> dict:
    """
    Estimate the resources currently generated by population + OPERABLE factories.

    Alternate Reality builds no factories. Its population term is multiplied by
    (Energy + 1) / 10, so Energy research is that race's resource engine.
    Energy 9 matches a normal race's population term. This is for planning and
    reporting, not native accounting.
    """
    status=installation_status(planet,economy)
    operated_factories=min(status["factories"],status["factory_cap"])
    pop_resources=int(planet.population or 0)//max(1,economy.colonists_per_resource)
    factory_resources=(operated_factories*economy.factory_output_per_10)//10
    if economy.alternate_reality:
        level=0 if energy_tech is None else max(0, int(energy_tech))
        pop_resources=pop_resources * (level + 1) // 10
        factory_resources=0
        operated_factories=0
    return {
        **status,
        "operated_factories":operated_factories,
        "population_resources":pop_resources,
        "factory_resources":factory_resources,
        "estimated_resources":pop_resources+factory_resources,
    }


def mineral_surface_stock(planet) -> dict[str,int]:
    """Return the observed I/B/G stock on a planet in kT."""
    return {
        mineral:max(0,int(getattr(planet,mineral,0) or 0))
        for mineral in MINERAL_TYPES
    }


def mineral_concentrations(planet) -> dict[str,int|None]:
    """Return observed I/B/G concentrations without inventing missing intel."""
    raw=list((getattr(planet,"native",{}) or {}).get("mineral_concentrations",[]) or [])
    values=[]
    for index in range(3):
        value=raw[index] if index<len(raw) else None
        try:
            values.append(max(0,int(value)) if value is not None else None)
        except (TypeError,ValueError):
            values.append(None)
    return dict(zip(MINERAL_TYPES,values,strict=True))


def estimated_mineral_output(planet,economy:RaceEconomy) -> dict:
    """
    Estimate this year's extractable kT for each mineral.

    This deliberately follows the StarsAPI/Nova implementation: only complete
    groups of ten operable mines count, then each group produces the race's
    ``mine_output_per_10`` scaled by the observed concentration.  The result
    is planning/reporting data; it never changes native mineral accounting.
    """
    status=installation_status(planet,economy)
    operated_mines=min(int(status["mines"]),int(status["mine_cap"]))
    mine_groups=max(0,operated_mines//10)
    concentrations=mineral_concentrations(planet)
    output={
        mineral:(
            (mine_groups*int(economy.mine_output_per_10)*int(concentration))//100
            if concentration is not None else None
        )
        for mineral,concentration in concentrations.items()
    }
    return {
        **status,
        "operated_mines":operated_mines,
        "mine_groups_of_ten":mine_groups,
        "concentrations":concentrations,
        "estimated_mineral_output":output,
    }


def is_economic_core(planet) -> bool:
    """True for a homeworld or an operational refuel/shipbuilding hub."""
    native=getattr(planet,"native",{}) or {}
    base=native.get("starbase_capabilities") or {}
    return bool(
        native.get("is_homeworld")
        or base.get("can_build_ships")
        or base.get("can_refuel")
    )


def working_mineral_reserve(
    planet,
    economy:RaceEconomy,
    queue=(),
    *,
    is_core:bool|None=None,
) -> dict:
    """
    Mineral stock that should remain available for normal planetary work.

    This is a conservative planning reserve, not a guessed design bill.  It
    protects core worlds and active shipyards from being drained by transports,
    reserves Germanium for queued factories exactly, and gives every colony a
    modest working stock.  Core classification is derived from native world
    state, never from a persona or playstyle.
    """
    queue=list(queue or [])
    core=is_economic_core(planet) if is_core is None else bool(is_core)
    status=installation_status(planet,economy)
    factories_queued=sum(
        max(0,int(item.get("quantity",0) or 0))
        for item in queue if item.get("item")=="factory"
    )
    defenses_queued=sum(
        max(0,int(item.get("quantity",0) or 0))
        for item in queue if item.get("item")=="defense"
    )
    ships_queued=sum(
        max(0,int(item.get("quantity",0) or 0))
        for item in queue if item.get("item")=="ship_design"
    )
    bases_queued=sum(
        max(0,int(item.get("quantity",0) or 0))
        for item in queue if item.get("item")=="starbase_design"
    )

    # Keep the historic small-colony baseline, but retain a genuine working
    # reserve at cores so a freighter cannot strip an active production hub.
    pop_band=max(1,int(getattr(planet,"population",0) or 0)//100000)
    baseline=min(60,15+5*pop_band)
    if core:
        base={"ironium":300,"boranium":220,"germanium":220}
    else:
        base={mineral:baseline for mineral in MINERAL_TYPES}

    g_per_factory=3 if economy.factory_germanium_discount else 4
    factory_germanium=factories_queued*g_per_factory
    defense_minerals=defenses_queued*5

    # Exact ship/starbase mineral bills are not present in all normalized game
    # states.  These are intentionally labelled reserve allowances rather than
    # claims about a particular design's actual cost.  A serial queue cannot
    # spend the bill for every distant hull this turn, so reserve only a short
    # executable horizon instead of fabricating a multi-thousand-kT deficit.
    reserved_ship_horizon=min(3,ships_queued)
    ship_reserve=reserved_ship_horizon*(90 if core else 30)
    starbase_reserve=bases_queued*(180 if core else 80)
    return {
        "ironium":base["ironium"]+defense_minerals+ship_reserve+starbase_reserve,
        "boranium":base["boranium"]+defense_minerals+ship_reserve+starbase_reserve,
        "germanium":base["germanium"]+factory_germanium+defense_minerals+ship_reserve+starbase_reserve,
        "factory_germanium":factory_germanium,
        "queued_factories":factories_queued,
        "queued_ships":ships_queued,
        "reserved_ship_horizon":reserved_ship_horizon,
        "queued_starbases":bases_queued,
        "core":core,
        "factory_headroom":int(status["factory_headroom"]),
        "mine_headroom":int(status["mine_headroom"]),
    }


def factory_germanium_floor(planet, economy:RaceEconomy, *, is_core:bool|None=None) -> int:
    """Surface Germanium the factory planner must not spend this turn."""
    core=is_economic_core(planet) if is_core is None else bool(is_core)
    # Small colonies retain the historical eight-kT floor.  A core protects a
    # useful Germanium reserve before converting minerals into factories.
    # Keep one small factory batch of Germanium. A 220 kT core reserve was
    # large enough to stall factory growth while mines were still queued.
    cost=3 if economy.factory_germanium_discount else 4
    return cost * 4 if core else max(cost, 2 * cost)


# --- Population carrying capacity / growth ---

def theoretical_max_population(race) -> int:
    """
    Stars! nominal carrying capacity is 1,000,000 colonists, modified by race:
      HE   x 0.5
      JOAT x 1.2
      OBRM x 1.1

    These modifiers stack the same way StarsAPI's Race.getMaxPopulation() does.
    """
    native=getattr(race,"native",{}) or {}
    prt_id=int(native.get("prt_id",-1))
    prt=str(getattr(race,"primary_trait","") or "")
    lrts=set(native.get("lrts",[]) or [])

    max_pop=1_000_000.0
    if prt_id==0 or "Hyper Expansion" in prt:
        max_pop *= 0.5
    if prt_id==9 or "Jack of All Trades" in prt:
        max_pop *= 1.2
    if "OBRM" in lrts:
        max_pop *= 1.1
    return int(max_pop)


def planet_population_capacity(planet,race) -> int:
    """
    A world's ceiling is the race maximum times its habitability.

    A 60% world is full at 60% of the race max, so its crowding band starts
    much earlier than the homeworld's. A red world keeps Stars!' 25,000 ceiling.
    """
    hab=getattr(planet,"habitability",None)
    if hab is not None and int(hab) < 0:
        return 25_000
    nominal=theoretical_max_population(race)
    if hab is None:
        return nominal
    return max(1, int(nominal * max(0, int(hab)) / 100))


def breeder_export_floor(planet, race) -> int:
    """Population a breeder keeps before it may export.

    Absolute growth peaks at about 33% of capacity. Until the factories that
    25% of capacity can operate are built, the world holds 25%. After that it
    holds 33% and only the surplus leaves.
    """
    cap=max(1, planet_population_capacity(planet, race))
    quarter=int(math.ceil(cap * 0.25))
    economy=decode_race_economy(race)
    factories_needed,_mines=installation_caps(quarter, economy)
    fraction=0.33 if int(getattr(planet, "factories", 0) or 0) >= max(1, factories_needed) else 0.25
    return int(math.ceil(cap * fraction))


def racial_growth_rate(race) -> float:
    rate=float(getattr(race, "growth_rate", 0.15) or 0.15)
    native=getattr(race, "native", {}) or {}
    prt_id=int(native.get("prt_id", -1))
    prt=str(getattr(race, "primary_trait", "") or "")
    if prt_id==0 or "Hyper Expansion" in prt:
        rate *= 2.0
    return rate


def marginal_colonist_growth(population:int, capacity:int, habitability:int, rate:float) -> float:
    """Extra colonists per year from one more colonist on this world.

    At or below 25% of capacity the marginal is ``rate * habitability``.
    Above that, Stars! crowding multiplies it by ``(16/9) * (1-c) * (1-3c)``.
    That term is about one third of the uncrowded rate just past 25%, zero at
    one third of capacity, and negative after the peak.
    """
    cap=max(1, int(capacity))
    hab=int(habitability)
    if hab<=0 or rate<=0:
        return 0.0
    fraction=max(0.0, int(population))/cap
    habit=hab/100.0
    if fraction<=0.25:
        return rate*habit
    if fraction>=1.0:
        return 0.0
    return rate*habit*(16.0/9.0)*(1.0-fraction)*(1.0-3.0*fraction)


def snowball_population(planet, race) -> int:
    """Population at which another colonist stops being the best use of a person.

    Below 25% of this world's current capacity, each colonist grows at the
    full habitability rate. The next colonist past 25% produces about a third
    of that, so it belongs on a world that is still under 25%.
    """
    cap=max(1, planet_population_capacity(planet, race))
    return int(math.ceil(cap*0.25))


def growth_peak_population(planet, race) -> int:
    """Population where this world adds the most new colonists per year."""
    cap=max(1, planet_population_capacity(planet, race))
    return int(math.ceil(cap/3.0))


def starvation_population(planet, race) -> int:
    """Smallest population that grows a full 100 colonists and can build industry.

    Stars! rounds growth to 100 colonists, so fewer people than
    ``100 / (rate * habitability)`` add nobody. A non-AR world also needs
    enough people to produce one factory's resource cost by themselves.
    The result never exceeds the snowball point.
    """
    hab=int(getattr(planet, "habitability", 0) or 0)
    if hab<=0:
        return 0
    rate=racial_growth_rate(race)
    grow=int(math.ceil(100.0/max(rate*(hab/100.0), 1e-9)))
    economy=decode_race_economy(race)
    if economy.alternate_reality or economy.operable_factories_per_10k<=0:
        factory=0
    else:
        factory=int(economy.factory_build_cost)*int(economy.colonists_per_resource)
    return min(snowball_population(planet, race), max(100, grow, factory))


def population_to_operate_factories(planet, race) -> int:
    """People required to run the factories already built on this world."""
    factories=int(getattr(planet, "factories", 0) or 0)
    economy=decode_race_economy(race)
    per=int(economy.operable_factories_per_10k)
    if economy.alternate_reality or per<=0 or factories<=0:
        return 0
    return int(math.ceil(factories*10000/per))


def breeder_hold(planet, race) -> int:
    """People who stay. Shipping them idles a factory or cuts the growth peak."""
    return max(
        breeder_export_floor(planet, race),
        population_to_operate_factories(planet, race),
    )


def _advance_world(pop:int, hab:int, factories:int, race, economy:RaceEconomy, energy:int) -> tuple[int,int,int]:
    """One year of resources, population growth, and factory construction."""
    planet=SimpleNamespace(
        population=max(0, int(pop)),
        habitability=int(hab),
        factories=max(0, int(factories)),
        mines=0,
        native={},
    )
    resources=int(estimated_operating_resources(planet, economy, energy_tech=energy)["estimated_resources"])
    if economy.alternate_reality:
        built=0
    else:
        cap_f,_mines=installation_caps(int(planet.population), economy)
        room=max(0, int(cap_f)-int(planet.factories))
        built=min(room, resources//max(1, int(economy.factory_build_cost)))
    growth=projected_population_growth(planet, race)
    return resources, int(planet.population)+int(growth), int(planet.factories)+int(built)


def _step_toward(environment:tuple, goal:tuple) -> tuple | None:
    """Move one environment point toward the terraformed goal."""
    best=None
    for axis in range(3):
        gap=int(goal[axis])-int(environment[axis])
        if gap==0:
            continue
        distance=abs(gap)
        if best is None or distance>best[0]:
            best=(distance, axis, 1 if gap>0 else -1)
    if best is None:
        return None
    updated=list(environment)
    updated[best[1]]+=best[2]
    return tuple(updated)


def _buy_terraform(environment:tuple, goal:tuple, resources:int, cost:int, race) -> tuple:
    """Spend this year's resources on steps. The new habitability applies next year."""
    from .terraforming import habitability_for_environment
    env=tuple(int(v) for v in environment)
    goal=tuple(int(v) for v in goal)
    left=max(0, int(resources))
    price=max(1, int(cost))
    while left>=price:
        nxt=_step_toward(env, goal)
        if nxt is None:
            break
        env=nxt
        left-=price
    hab=habitability_for_environment(race, env)
    return env, left, (None if hab is None else int(hab))


def ten_year_transfer_roi(
    source, target, race, colonists:int, travel_years:int, *,
    horizon:int=10, energy_tech:int=0, state=None,
) -> dict:
    """Resources and people gained over ten years by shipping colonists.

    The shipment produces nothing while it is in transit. The alternative is
    leaving those people on the source. Each year the destination spends
    resources on terraforming steps it can already perform. A 40% world grows
    at 40% until those steps are paid for, and at the higher habitability
    only in the years after it gets there. A goal the colony cannot afford
    inside the ten years is not credited.
    """
    moved=max(0, int(colonists))
    years=max(0, int(horizon))
    transit=max(0, int(travel_years))
    economy=decode_race_economy(race)
    energy=max(0, int(energy_tech))
    start_hab=int(getattr(target, "habitability", 0) or 0)
    goal_env=None
    start_env=None
    step_cost=100
    if state is not None:
        from .terraforming import evaluate_terraforming
        potential=evaluate_terraforming(state, target)
        if (
            int(potential.tech_steps or 0)>0
            and potential.current_environment is not None
            and potential.tech_environment is not None
        ):
            start_env=tuple(potential.current_environment)
            goal_env=tuple(potential.tech_environment)
            step_cost=max(1, int(potential.resource_cost_per_step or 100))
            if potential.current_habitability is not None:
                start_hab=int(potential.current_habitability)

    def run(ship:bool) -> tuple[int,int,int]:
        source_pop=int(getattr(source, "population", 0) or 0)
        target_pop=int(getattr(target, "population", 0) or 0)
        source_factories=int(getattr(source, "factories", 0) or 0)
        target_factories=int(getattr(target, "factories", 0) or 0)
        source_hab=int(getattr(source, "habitability", 0) or 0)
        target_hab=start_hab
        target_env=None if start_env is None else tuple(start_env)
        if ship:
            source_pop=max(0, source_pop-moved)
        total=0
        delivered=not ship
        for year in range(years):
            if ship and not delivered and year>=transit:
                target_pop+=moved
                delivered=True
            growth_hab=target_hab
            source_resources, source_pop, source_factories=_advance_world(
                source_pop, source_hab, source_factories, race, economy, energy,
            )
            pop_now=target_pop
            fac_now=target_factories
            world=SimpleNamespace(
                population=max(0, pop_now), habitability=int(growth_hab),
                factories=max(0, fac_now), mines=0, native={},
            )
            target_resources=int(estimated_operating_resources(
                world, economy, energy_tech=energy,
            )["estimated_resources"])
            budget=target_resources
            if target_env is not None and goal_env is not None:
                target_env, budget, bought_hab=_buy_terraform(
                    target_env, goal_env, budget, step_cost, race,
                )
                if bought_hab is not None:
                    target_hab=bought_hab
            if economy.alternate_reality:
                target_factories=fac_now
            else:
                cap_f,_mines=installation_caps(max(0, pop_now), economy)
                room=max(0, int(cap_f)-fac_now)
                built=min(room, max(0, budget)//max(1, int(economy.factory_build_cost)))
                target_factories=fac_now+built
            target_pop=max(0, pop_now+int(projected_population_growth(world, race)))
            total+=source_resources+target_resources
        return total, source_pop+target_pop, int(target_hab)

    stayed_resources, stayed_population, _stayed_hab=run(False)
    shipped_resources, shipped_population, shipped_hab=run(True)
    resource_delta=int(shipped_resources-stayed_resources)
    population_delta=int(shipped_population-stayed_population)
    # The resource ledger is only the years inside the window. The extra
    # people at year 10 are the asset that produces the next decade, priced
    # at one colonist's population resources for that same ten years.
    per_year=1.0/max(1, int(economy.colonists_per_resource))
    if economy.alternate_reality:
        per_year *= (energy+1)/10.0
    terminal=int(population_delta*per_year*years)
    return {
        "horizon":years,
        "travel_years":transit,
        "colonists":moved,
        "resource_delta":resource_delta,
        "population_delta":population_delta,
        "habitability_now":int(start_hab),
        "habitability_year10":int(shipped_hab),
        "score":int(resource_delta+terminal),
    }


def feed_habitability(planet, state=None) -> int:
    """Habitability used when deciding whether to send colonists now.

    A world already at 70% counts as itself. A world at 40% or better counts
    as the habitability it reaches once the race finishes terraforming, so a
    40% world that becomes 70% is fed while the terraform is still running.
    """
    hab=int(getattr(planet, "habitability", 0) or 0)
    if state is None or hab >= 70 or hab < 40:
        return hab
    from .terraforming import evaluate_terraforming
    eventual=evaluate_terraforming(state, planet).eventual_habitability
    if eventual is None:
        return hab
    return max(hab, int(eventual))


def may_receive_homeworld_population(source, target, race, state=None) -> bool:
    """True when one colonist grows the empire faster on the target than on the source.

    A colonist on a world under 25% of capacity grows at the full habitability
    rate. On the usual homeworld that rate is the best in the empire, so the
    homeworld does not export until it has crossed 25% and crowding has cut
    its marginal. A world already at 70%, or at 40% now and 70% after
    terraforming, is fed once that happens. A lesser world waits until the
    homeworld is at its one-third growth peak, where another colonist adds
    nothing. Other breeders may still feed a world of at least 30%.
    """
    hab=int(getattr(target, "habitability", 0) or 0)
    if hab < 30:
        return False
    if not bool((getattr(source, "native", None) or {}).get("is_homeworld")):
        return True
    rate=racial_growth_rate(race)
    source_cap=max(1, planet_population_capacity(source, race))
    target_cap=max(1, planet_population_capacity(target, race))
    source_marginal=marginal_colonist_growth(
        int(getattr(source, "population", 0) or 0),
        source_cap,
        int(getattr(source, "habitability", 0) or 0),
        rate,
    )
    target_marginal=marginal_colonist_growth(
        int(getattr(target, "population", 0) or 0),
        target_cap,
        hab,
        rate,
    )
    if target_marginal<=source_marginal:
        return False
    if int(getattr(target, "population", 0) or 0) >= growth_peak_population(target, race):
        return False
    if feed_habitability(target, state) >= 70:
        return True
    return int(getattr(source, "population", 0) or 0) >= growth_peak_population(source, race)


def population_capacity_fraction(planet,race) -> float:
    cap=max(1,planet_population_capacity(planet,race))
    # StarsAPI/Technical FAQ behavior uses CEIL to an integer capacity percent.
    pop=max(0,int(getattr(planet,"population",0) or 0))
    pct=math.ceil((pop/cap)*100.0)
    return pct/100.0


def projected_population_growth(planet,race) -> int:
    """
    Stars! crowding-aware annual population growth.

    Source model follows StarsAPI Star.CalculateGrowth:
      - <=25% capacity: nominal growth
      - 25-100%: crowding factor 16/9 * (1-capacity)^2
      - 100%: zero
      - over capacity: crowding deaths
      - growth/death is rounded to 100-colonist units

    Habitability is represented in our normalized model as -15..100%.
    """
    pop=max(0,int(getattr(planet,"population",0) or 0))
    if pop<=0:
        return 0

    hab_raw=getattr(planet,"habitability",None)
    if hab_raw is None:
        return 0
    hab=max(-1.0,min(1.0,float(hab_raw)/100.0))

    growth_rate=float(getattr(race,"growth_rate",0.15) or 0.15)
    native=getattr(race,"native",{}) or {}
    prt_id=int(native.get("prt_id",-1))
    prt=str(getattr(race,"primary_trait","") or "")
    if prt_id==0 or "Hyper Expansion" in prt:
        growth_rate *= 2.0

    capacity=population_capacity_fraction(planet,race)

    if hab < 0:
        growth=0.1*pop*hab
    elif capacity <= 0.25:
        growth=pop*growth_rate*hab
    elif capacity < 1.0:
        growth=pop*growth_rate*hab
        growth *= (16.0/9.0)*(1.0-capacity)*(1.0-capacity)
    elif capacity == 1.0:
        growth=0.0
    elif capacity < 4.0:
        growth=pop*(capacity-1.0)*-4.0/100.0
    else:
        growth=pop*-0.12

    # Vanilla Stars! tracks population in 100-colonist increments.
    final=int(growth)
    final=int(final/100)*100
    return final


def projected_next_population(planet,race) -> int:
    return max(0,int(getattr(planet,"population",0) or 0)+projected_population_growth(planet,race))
