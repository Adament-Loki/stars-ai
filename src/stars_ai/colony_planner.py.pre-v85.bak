
from __future__ import annotations
from dataclasses import dataclass, asdict
from .models import GameState
from .util import distance
from .empire_geometry import distance_from_homeworld
from .terraforming import evaluate_terraforming


MAX_COLONY_SUPPORT_DISTANCE=300.0
MIN_COLONY_SCORE=0.0


def reserved_colony_target_ids(state:GameState) -> set[int]:
    """Claims already committed to an in-flight colony fleet."""
    return {
        int(f.destination_planet_id)
        for f in state.fleets
        if f.owner==state.player_id
        and f.role=="colony"
        and f.destination_planet_id is not None
    }


@dataclass(frozen=True)
class ColonizationPolicy:
    stage:str
    normal_habitability_floor:int | None
    resource_habitability_floor:int | None
    exceptional_mineral_average:int
    mineral_weight:float
    travel_penalty_per_ly:float
    support_penalty_per_ly:float


def colonization_policy(state:GameState, plan=None) -> ColonizationPolicy:
    """Return the race-adjusted quality policy for the current game age.

    ``Planet.habitability`` is already calculated from the active race's hab
    ranges, immunities, and environmental tolerances. The percentages here are
    therefore racial values rather than universal planet ratings.
    """
    universal=bool((state.race.native or {}).get("universal_hab",False))
    if universal:
        return ColonizationPolicy(
            "universal_hab",None,None,0,1.45,.28,.10,
        )

    turn=max(0,int(state.year)-2400)
    if turn<=15:
        values=("opening_quality",60,50,85,.20,.28,.10)
    elif turn<=25:
        values=("opening_broadening",50,40,80,.35,.26,.09)
    elif turn<=40:
        values=("midgame_expansion",35,25,70,.55,.24,.08)
    elif turn<=55:
        values=("resource_expansion",20,10,60,.80,.22,.07)
    else:
        values=("late_resource_expansion",1,1,0,1.05,.20,.06)

    stage,floor,resource_floor,mineral,weight,travel,support=values
    # Persona selectivity remains a small modifier after the shared selective
    # opening. It cannot make an opening race settle below the 60% doctrine.
    if turn>15 and plan is not None:
        persona_floor=int(getattr(plan,"colonize_min_habitability",40) or 40)
        adjustment=round((persona_floor-40)*.20)
        floor=max(1,int(floor)+adjustment)
        resource_floor=max(1,min(floor,int(resource_floor)+adjustment))

    return ColonizationPolicy(
        str(stage),int(floor),int(resource_floor),int(mineral),float(weight),
        float(travel),float(support),
    )


def _mineral_context(planet):
    conc=(planet.native or {}).get("mineral_concentrations") or [None,None,None]
    known=len(conc)>=3 and all(v is not None for v in conc[:3])
    total=sum(max(0,int(v)) for v in conc[:3]) if known else 0
    return conc,known,total,(total/3.0 if known else 0.0)


def _eligibility(
    state:GameState,
    planet,
    policy:ColonizationPolicy,
    *,
    evaluated_habitability:int | None=None,
    nearest_owned:float,
    nearby_frontier:int,
) -> tuple[bool,str]:
    if policy.normal_habitability_floor is None:
        return True,"universal_hab"
    if evaluated_habitability is None:
        return False,"habitability_unknown"

    hab=int(evaluated_habitability)
    if hab>=int(policy.normal_habitability_floor):
        return True,"racial_habitability"

    _,known,_,average=_mineral_context(planet)
    resource_exception=bool(
        hab>=int(policy.resource_habitability_floor or 1)
        and known
        and average>=float(policy.exceptional_mineral_average)
    )
    if resource_exception:
        return True,"exceptional_resources"

    # A compact bridge world may be worth accepting below the normal quality
    # floor, but never below the phase's resource floor. This is intentionally
    # demanding during the opening so ordinary marginal worlds do not qualify.
    frontier_exception=bool(
        hab>=int(policy.resource_habitability_floor or 1)
        and nearby_frontier>=8
        and nearest_owned<=80.0
    )
    if frontier_exception:
        return True,"compact_frontier_bridge"
    return False,"below_phase_habitability_floor"


def colony_planet_is_eligible(state:GameState, planet, plan=None) -> bool:
    """Shared target/build-demand eligibility without fleet-specific range."""
    if planet.owner is not None or not planet.observed:
        return False
    policy=colonization_policy(state,plan)
    owned=[p for p in state.planets if p.owner==state.player_id]
    nearest=min((distance(planet.position,q.position) for q in owned),default=0.0)
    if nearest>MAX_COLONY_SUPPORT_DISTANCE:
        return False
    nearby=sum(
        1 for q in state.planets
        if q.owner is None and q.id!=planet.id
        and distance(planet.position,q.position)<=60.0
    )
    potential=evaluate_terraforming(state,planet)
    return _eligibility(
        state,planet,policy,
        evaluated_habitability=potential.planning_habitability,
        nearest_owned=nearest,nearby_frontier=nearby,
    )[0]


@dataclass
class ColonyCandidate:
    planet_id:int
    planet_name:str
    habitability:int
    distance_from_fleet:float
    distance_from_nearest_owned:float
    distance_from_homeworld:float
    population:int
    resources:int
    score:float
    explanation:str
    colonization_stage:str="unknown"
    habitability_floor:int | None=None
    selection_basis:str="unknown"
    current_habitability:int | None=None
    tech_terraform_habitability:int | None=None
    eventual_terraform_habitability:int | None=None
    terraform_steps:int=0
    eventual_terraform_steps:int=0

    def to_dict(self):
        return asdict(self)



def score_colony_candidates(state:GameState, fleet, plan=None) -> list[ColonyCandidate]:
    owned=[p for p in state.planets if p.owner==state.player_id]
    universal=bool((state.race.native or {}).get("universal_hab",False))
    policy=colonization_policy(state,plan)
    candidates=[]

    for p in state.planets:
        if p.owner is not None or not p.observed:
            continue
        native=p.native or {}

        travel=distance(fleet.position,p.position)
        nearest_owned=min(
            (distance(p.position,q.position) for q in owned),
            default=travel,
        )
        if nearest_owned>MAX_COLONY_SUPPORT_DISTANCE:
            continue
        home_distance=distance_from_homeworld(state,p.position)
        potential=evaluate_terraforming(state,p)
        conc,mineral_known,mineral_sum,mineral_avg=_mineral_context(p)

        # Strategic value: a world that opens a new local cluster is more useful
        # than an isolated world at the same mineral quality.
        nearby_frontier=sum(
            1 for q in state.planets
            if q.owner is None and q.id!=p.id
            and distance(p.position,q.position)<=60.0
        )
        eligible,selection_basis=_eligibility(
            state,p,policy,
            evaluated_habitability=potential.planning_habitability,
            nearest_owned=nearest_owned,
            nearby_frontier=nearby_frontier,
        )
        if not eligible:
            continue
        cluster_bonus=min(nearby_frontier,12)*1.5
        travel_penalty=policy.travel_penalty_per_ly*travel
        support_penalty=policy.support_penalty_per_ly*nearest_owned
        population_penalty=0.0 if p.population<=0 else 5.0
        intel_age=(
            int(native.get("intel_age_years",0) or 0)
            if native.get("intel_source")=="persistent_memory" else 0
        )
        # Environmental observations remain valid strategic knowledge. Do not
        # age them out and force a second scout visit; merely expose age in logs.
        stale_penalty=0.0
        turn=max(0,int(state.year)-2400)
        if turn<=25:
            home_penalty=home_distance*.14
            home_bonus=max(0.0,200.0-home_distance)*.10
        else:
            home_penalty=home_distance*.05
            home_bonus=max(0.0,120.0-home_distance)*.04

        if universal:
            # Triple-immune/universal-hab races grow equivalently everywhere.
            # Habitability is therefore deliberately NOT part of the rank.
            mineral_bonus=mineral_avg*1.45
            known_bonus=8.0 if mineral_known else 0.0
            score=(
                mineral_bonus+known_bonus+cluster_bonus+home_bonus
                -travel_penalty-support_penalty-home_penalty-population_penalty-stale_penalty
            )
            explanation=(
                "universal-hab race: habitability ignored; "
                f"mineral concentrations={conc[:3] if mineral_known else 'unknown'} "
                f"add {mineral_bonus+known_bonus:.1f}; "
                f"fleet distance {travel:.1f} costs {travel_penalty:.1f}; "
                f"support distance {nearest_owned:.1f} costs {support_penalty:.1f}; "
                f"nearby expansion cluster adds {cluster_bonus:.1f}; "
                f"home distance {home_distance:.1f} adds {home_bonus:.1f} and costs {home_penalty:.1f}; "
                f"remembered intel age={intel_age}"
            )
            hab=100
        else:
            hab=float(potential.planning_habitability)
            tech_hab=int(potential.tech_habitability if potential.tech_habitability is not None else hab)
            current_hab=int(potential.current_habitability if potential.current_habitability is not None else hab)
            eventual_hab=int(potential.eventual_habitability if potential.eventual_habitability is not None else hab)
            quality_bonus=(
                12.0+max(0.0,hab-float(policy.normal_habitability_floor or 0))*.75
                if selection_basis=="racial_habitability" else 0.0
            )
            mineral_bonus=mineral_avg*policy.mineral_weight
            speculation_penalty=(
                max(0,hab-tech_hab)*.25
                +float(potential.tech_steps)*.35
            )
            score=(
                hab+quality_bonus+mineral_bonus+cluster_bonus+home_bonus
                -travel_penalty-support_penalty-home_penalty-population_penalty
                -speculation_penalty-stale_penalty
            )
            explanation=(
                f"race-adjusted hab current={current_hab}%, current-tech terraform={tech_hab}%, "
                f"eventual terraform={eventual_hab}%, planning value={hab:.1f}; "
                f"terraform steps now={potential.tech_steps}, speculation cost={speculation_penalty:.1f}; "
                f"{policy.stage} normal floor={policy.normal_habitability_floor}% "
                f"(selection={selection_basis}, quality bonus={quality_bonus:.1f}); "
                f"mineral concentrations={conc[:3] if mineral_known else 'unknown'} "
                f"add {mineral_bonus:.1f}; "
                f"fleet distance {travel:.1f} costs {travel_penalty:.1f}; "
                f"support distance {nearest_owned:.1f} costs {support_penalty:.1f}; "
                f"strategic cluster adds {cluster_bonus:.1f}; "
                f"home distance {home_distance:.1f} adds {home_bonus:.1f} and costs {home_penalty:.1f}; "
                f"remembered intel age={intel_age}"
            )
            hab=int(potential.planning_habitability)

        if score<MIN_COLONY_SCORE:
            continue

        candidates.append(
            ColonyCandidate(
                planet_id=p.id,
                planet_name=p.name,
                habitability=hab,
                distance_from_fleet=travel,
                distance_from_nearest_owned=nearest_owned,
                distance_from_homeworld=home_distance,
                population=p.population,
                resources=mineral_sum,
                score=score,
                explanation=explanation,
                colonization_stage=policy.stage,
                habitability_floor=policy.normal_habitability_floor,
                selection_basis=selection_basis,
                current_habitability=potential.current_habitability,
                tech_terraform_habitability=potential.tech_habitability,
                eventual_terraform_habitability=potential.eventual_habitability,
                terraform_steps=potential.tech_steps,
                eventual_terraform_steps=potential.eventual_steps,
            )
        )

    candidates.sort(key=lambda c:c.score,reverse=True)
    return candidates
