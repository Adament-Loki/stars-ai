
from __future__ import annotations
from dataclasses import dataclass, asdict
from .models import GameState
from .util import distance


@dataclass
class ColonyCandidate:
    planet_id:int
    planet_name:str
    habitability:int
    distance_from_fleet:float
    distance_from_nearest_owned:float
    population:int
    resources:int
    score:float
    explanation:str

    def to_dict(self):
        return asdict(self)



def score_colony_candidates(state:GameState, fleet) -> list[ColonyCandidate]:
    owned=[p for p in state.planets if p.owner==state.player_id]
    universal=bool((state.race.native or {}).get("universal_hab",False))
    candidates=[]

    for p in state.planets:
        if p.owner is not None or not p.observed:
            continue

        if not universal:
            if p.habitability is None or p.habitability < 25:
                continue

        travel=distance(fleet.position,p.position)
        nearest_owned=min(
            (distance(p.position,q.position) for q in owned),
            default=travel,
        )
        conc=(p.native or {}).get("mineral_concentrations") or [None,None,None]
        mineral_known=len(conc)>=3 and all(v is not None for v in conc[:3])
        mineral_sum=sum(max(0,int(v)) for v in conc[:3]) if mineral_known else 0
        mineral_avg=mineral_sum/3.0 if mineral_known else 0.0

        # Strategic value: a world that opens a new local cluster is more useful
        # than an isolated world at the same mineral quality.
        nearby_frontier=sum(
            1 for q in state.planets
            if q.owner is None and q.id!=p.id
            and distance(p.position,q.position)<=60.0
        )
        frontier_bonus=min(nearest_owned,120.0)*0.08
        cluster_bonus=min(nearby_frontier,12)*1.5
        travel_penalty=0.32*travel
        population_penalty=0.0 if p.population<=0 else 5.0

        if universal:
            # Triple-immune/universal-hab races grow equivalently everywhere.
            # Habitability is therefore deliberately NOT part of the rank.
            mineral_bonus=mineral_avg*1.45
            known_bonus=8.0 if mineral_known else 0.0
            score=(
                mineral_bonus+known_bonus+frontier_bonus+cluster_bonus
                -travel_penalty-population_penalty
            )
            explanation=(
                "universal-hab race: habitability ignored; "
                f"mineral concentrations={conc[:3] if mineral_known else 'unknown'} "
                f"add {mineral_bonus+known_bonus:.1f}; "
                f"fleet distance {travel:.1f} costs {travel_penalty:.1f}; "
                f"frontier {nearest_owned:.1f} adds {frontier_bonus:.1f}; "
                f"nearby expansion cluster adds {cluster_bonus:.1f}"
            )
            hab=100
        else:
            hab=float(p.habitability)
            mineral_bonus=mineral_avg*0.22
            score=(
                hab+mineral_bonus+frontier_bonus+cluster_bonus
                -travel_penalty-population_penalty
            )
            explanation=(
                f"hab={p.habitability} contributes {hab:.1f}; "
                f"mineral concentrations={conc[:3] if mineral_known else 'unknown'} "
                f"add {mineral_bonus:.1f}; "
                f"fleet distance {travel:.1f} costs {travel_penalty:.1f}; "
                f"frontier distance {nearest_owned:.1f} adds {frontier_bonus:.1f}; "
                f"strategic cluster adds {cluster_bonus:.1f}"
            )
            hab=int(p.habitability)

        candidates.append(
            ColonyCandidate(
                planet_id=p.id,
                planet_name=p.name,
                habitability=hab,
                distance_from_fleet=travel,
                distance_from_nearest_owned=nearest_owned,
                population=p.population,
                resources=mineral_sum,
                score=score,
                explanation=explanation,
            )
        )

    candidates.sort(key=lambda c:c.score,reverse=True)
    return candidates

