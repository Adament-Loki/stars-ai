from __future__ import annotations
from ..models import GameState, OrderSet
from ..persona import StrategicPlan



def _fuel_mizer_available(state:GameState)->bool:
    lrts=set((state.race.native or {}).get("lrts",[]))
    # Fuel Mizer stock requirement is Propulsion 2; it is the defining early IFE
    # engine. Existing designs using native engine id 2 are also direct evidence.
    existing=any(
        int(d.get("engine_id",-1))==2
        for d in state.native.get("design_profiles",[])
    )
    return existing or ("IFE" in lrts and int(state.tech.propulsion)>=2)


def add_research_orders(state: GameState, orders: OrderSet, plan: StrategicPlan | None = None) -> None:
    tech=state.tech
    fields={
        "energy":tech.energy,
        "weapons":tech.weapons,
        "propulsion":tech.propulsion,
        "construction":tech.construction,
        "electronics":tech.electronics,
        "biotechnology":tech.biotechnology,
    }
    bias=state.race.research_bias or {}
    persona_weight=plan.research_weights if plan else {}

    fuel_mizer=_fuel_mizer_available(state)
    early_mizer_doctrine=(
        fuel_mizer
        and state.year <= 2435
        and min(tech.energy,tech.weapons,tech.construction) < 10
    )

    allowed=set(fields)
    doctrine_note=""
    if early_mizer_doctrine:
        # Once the economical Fuel Mizer is unlocked, early marginal returns in
        # Propulsion/Biotech are intentionally deferred. Concentrate on the tech
        # fields that unlock stronger hulls, defenses, beams/shields and weapons.
        allowed={"construction","energy","weapons"}
        doctrine_note=(
            " Fuel Mizer is available; early IFE doctrine defers Propulsion/"
            "Biotechnology and concentrates on Construction, Energy, and Weapons."
        )

    scored={}
    for field,level in fields.items():
        if field not in allowed:
            scored[field]=-1e9
            continue
        strategic_bonus={
            "weapons":1.25,
            "propulsion":1.15,
            "construction":1.30,
            "electronics":1.05,
            "energy":1.20,
            "biotechnology":0.95,
        }[field]
        score=(10-min(level,10))*strategic_bonus
        score*=float(bias.get(field,1.0))
        score*=float(persona_weight.get(field,1.0))
        if plan:
            score*=plan.objective("research")
        scored[field]=score

    target=max(scored,key=scored.get)
    orders.add(
        "set_research",
        {
            "field":target,
            "allocation_percent":100,
            "fuel_mizer_available":fuel_mizer,
            "early_mizer_doctrine":early_mizer_doctrine,
        },
        f"{plan.persona_name + ': ' if plan else ''}research {target}; "
        f"macro score={scored[target]:.2f}.{doctrine_note}",
        priority=int(65*(plan.objective("research") if plan else 1.0)),
    )

