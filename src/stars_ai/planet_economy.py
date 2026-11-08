
from __future__ import annotations

from dataclasses import dataclass, asdict
import math


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


def may_receive_homeworld_population(source, target, race) -> bool:
    """The homeworld feeds an 80%+ world once it is past 25% capacity.

    Lesser worlds wait until the homeworld has reached the 33% growth peak.
    Other breeders may still feed a world of at least 30% habitability.
    """
    hab=int(getattr(target, "habitability", 0) or 0)
    if hab < 30:
        return False
    if not bool((getattr(source, "native", None) or {}).get("is_homeworld")):
        return True
    cap=max(1, planet_population_capacity(source, race))
    at_peak=int(getattr(source, "population", 0) or 0) >= int(math.ceil(cap * 0.33))
    return hab >= 80 or at_peak


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
