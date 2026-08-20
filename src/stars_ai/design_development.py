
from __future__ import annotations

from dataclasses import dataclass, asdict
from functools import lru_cache
from pathlib import Path
import csv

from .fuel_planner import has_ife
from .colony_planner import colony_planet_is_eligible


TECH_FIELDS=("energy","weapons","propulsion","construction","electronics","biotechnology")


@dataclass
class HullOption:
    hull_id:int
    name:str
    requirements:tuple[int,int,int,int,int,int]
    mass:int
    cargo:int
    fuel:int
    armor:int
    is_starbase:bool


@dataclass
class DesignProposal:
    role:str
    name:str
    is_starbase:bool
    desired_hull_id:int
    desired_hull_name:str
    desired_engine:str|None
    objectives:list[str]
    reason:str
    priority:int
    native_status:str="PENDING_NATIVE_DESIGN_CREATION_VALIDATION"

    def to_payload(self):
        return asdict(self)


@lru_cache(maxsize=1)
def stock_hulls()->dict[int,HullOption]:
    path=Path(__file__).with_name("data_hulls.mod")
    out={}
    for parts in csv.reader(path.read_text(encoding="latin-1").splitlines()):
        if not parts or int(parts[0]) not in (15,16):
            continue
        nums=[int(x) if x else 0 for x in parts[3:]]
        hid=int(nums[0])
        # UNEDITED.MOD hull fields: item id, six tech requirements, mass,
        # resource/mineral costs..., cargo at index 13, fuel at 14, armor at 15.
        req=tuple(int(x) for x in nums[1:7])
        out[hid]=HullOption(
            hid,parts[2],req,int(nums[7]),int(nums[13]),int(nums[14]),
            int(nums[15]),int(parts[0])==16,
        )
    return out


def _unlocked(state,h:HullOption)->bool:
    levels=(
        state.tech.energy,state.tech.weapons,state.tech.propulsion,
        state.tech.construction,state.tech.electronics,state.tech.biotechnology,
    )
    return all(int(levels[i])>=int(h.requirements[i]) for i in range(6))


def _designs(state,role=None):
    ds=list(state.native.get("design_profiles",[]))
    if role is not None:
        ds=[d for d in ds if d.get("role")==role]
    return ds


def _best_unlocked_hull(state,ids):
    hs=[stock_hulls()[i] for i in ids if i in stock_hulls() and _unlocked(state,stock_hulls()[i])]
    return max(hs,key=lambda h:(h.cargo,h.fuel,h.armor,-h.mass),default=None)


def _has_engine(state,role,engine_id):
    return any(int(d.get("engine_id",-1))==int(engine_id) for d in _designs(state,role))


def plan_design_development(state,plan=None)->list[DesignProposal]:
    proposals=[]
    ife=has_ife(state.race)
    fuel_mizer=(ife and int(state.tech.propulsion)>=2) or any(
        int(d.get("engine_id",-1))==2 for d in state.native.get("design_profiles",[])
    )
    preferred_engine="Fuel Mizer" if fuel_mizer else None

    # Exploration design evolution.
    scouts=_designs(state,"scout")
    unknown=sum(1 for p in state.planets if not p.observed)
    if unknown>0 and scouts and fuel_mizer and not _has_engine(state,"scout",2):
        proposals.append(DesignProposal(
            role="scout",
            name="Long Range Scout Mk II",
            is_starbase=False,
            desired_hull_id=4,
            desired_hull_name="Scout",
            desired_engine="Fuel Mizer",
            objectives=[
                "maximize sustainable exploration range",
                "retain the best currently available scanner",
                "avoid unnecessary weapon mass on pure exploration hulls",
                "add fuel capacity when a legal slot/component permits",
            ],
            reason=(
                f"{unknown} worlds remain unexplored and Fuel Mizer is available, "
                "but no current scout design uses it."
            ),
            priority=125,
        ))

    # Logistics design evolution. Prefer the largest cargo hull currently unlocked.
    freighters=_designs(state,"freighter")
    unlocked_freighter=_best_unlocked_hull(state,[0,1,2,3,11,12,13])
    if freighters and unlocked_freighter is not None:
        best_current=max(
            freighters,
            key=lambda d:(int(d.get("cargo_capacity",0)),int(d.get("fuel_capacity",0))),
        )
        current_cargo=int(best_current.get("cargo_capacity",0))
        needs_hull=unlocked_freighter.cargo > current_cargo
        needs_engine=fuel_mizer and not any(int(d.get("engine_id",-1))==2 for d in freighters)
        if needs_hull or needs_engine:
            proposals.append(DesignProposal(
                role="freighter",
                name="Strategic Transport Mk II",
                is_starbase=False,
                desired_hull_id=unlocked_freighter.hull_id,
                desired_hull_name=unlocked_freighter.name,
                desired_engine=preferred_engine,
                objectives=[
                    "maximize useful cargo per fuel-safe trip",
                    "favor efficient engines over maximum nominal warp",
                    "retain adequate fuel reserve for round-trip logistics",
                    "support mineral redistribution, especially Germanium",
                ],
                reason=(
                    f"Unlocked transport hull {unlocked_freighter.name} offers "
                    f"{unlocked_freighter.cargo}kT base cargo versus current "
                    f"{current_cargo}kT; Fuel Mizer available={fuel_mizer}."
                ),
                priority=118,
            ))

    # Colony ship evolution.
    colonies=_designs(state,"colony")
    viable=sum(
        1 for p in state.planets
        if colony_planet_is_eligible(state,p,plan)
    )
    if colonies and viable and fuel_mizer and not _has_engine(state,"colony",2):
        proposals.append(DesignProposal(
            role="colony",
            name="Colonizer Mk II",
            is_starbase=False,
            desired_hull_id=15 if 15 in stock_hulls() and _unlocked(state,stock_hulls()[15]) else 14,
            desired_hull_name="Colony Ship" if 15 in stock_hulls() and _unlocked(state,stock_hulls()[15]) else "Mini-Colony Ship",
            desired_engine="Fuel Mizer",
            objectives=[
                "reach new colonies with a safe one-way fuel budget",
                "carry the required colonization module",
                "minimize nonessential mass",
            ],
            reason=f"{viable} viable colony targets are known and Fuel Mizer improves colonization range.",
            priority=116,
        ))

    # Starbase progression. Orbital Fort is explicitly not a refuel/shipyard base.
    owned=[p for p in state.planets if p.owner==state.player_id]
    operational=sum(
        1 for p in owned
        if bool(((p.native or {}).get("starbase_capabilities") or {}).get("can_build_ships"))
    )
    orbital_forts=sum(
        1 for p in owned
        if bool(((p.native or {}).get("starbase_capabilities") or {}).get("is_orbital_fort"))
    )
    if operational==0 and orbital_forts>0:
        dock=stock_hulls().get(33)
        if dock is not None and _unlocked(state,dock):
            proposals.append(DesignProposal(
                role="starbase",
                name="Fleet Support Space Dock",
                is_starbase=True,
                desired_hull_id=33,
                desired_hull_name="Space Dock",
                desired_engine=None,
                objectives=[
                    "provide real ship construction",
                    "provide orbital refueling",
                    "retain useful defenses without sacrificing support function",
                ],
                reason=(
                    f"Empire has {orbital_forts} Orbital Fort(s) but no operational "
                    "shipyard/refuel starbase; Construction tech unlocks Space Dock."
                ),
                priority=140,
            ))

    # Mid-game base upgrade recommendation.
    if operational>0 and int(state.tech.construction)>=12:
        if not any(int(x.get("hull_id",-1))==35 for x in state.native.get("starbase_profiles",[])):
            proposals.append(DesignProposal(
                role="starbase",
                name="Ultra Station Support Base",
                is_starbase=True,
                desired_hull_id=35,
                desired_hull_name="Ultra Station",
                desired_engine=None,
                objectives=[
                    "increase orbital capacity and defenses",
                    "serve as a mature fleet-construction/refuel hub",
                ],
                reason="Construction 12 makes an Ultra Station-class support design strategically relevant.",
                priority=95,
            ))

    # Combat designs should evolve as Weapons/Energy rise rather than remain frozen.
    combat=_designs(state,"combat")
    if combat and max(int(state.tech.weapons),int(state.tech.energy))>=6:
        newest_turn=max(
            (int(d.get("turn_designed",0) or 0) for d in state.native.get("designs",[]) if not d.get("is_starbase")),
            default=0,
        )
        if state.year-2400-newest_turn>=5:
            proposals.append(DesignProposal(
                role="combat",
                name="Fleet Escort Mk II",
                is_starbase=False,
                desired_hull_id=6 if 6 in stock_hulls() and _unlocked(state,stock_hulls()[6]) else 5,
                desired_hull_name="Destroyer" if 6 in stock_hulls() and _unlocked(state,stock_hulls()[6]) else "Frigate",
                desired_engine=preferred_engine,
                objectives=[
                    "use newly unlocked Weapons/Energy technology",
                    "retain fuel-safe strategic mobility",
                    "balance weapons, shields/armor, and mass",
                ],
                reason=(
                    f"Weapons={state.tech.weapons}, Energy={state.tech.energy}; "
                    "existing combat architecture is at least five turns old."
                ),
                priority=90,
            ))

    proposals.sort(key=lambda x:x.priority,reverse=True)
    return proposals[:4]


def add_design_development_orders(state,orders,plan=None):
    for proposal in plan_design_development(state,plan):
        orders.add(
            "create_design",
            proposal.to_payload(),
            proposal.reason,
            priority=proposal.priority,
        )
