from __future__ import annotations
from ..models import GameState, OrderSet
from ..persona import StrategicPlan
from ..research_planner import ResearchDecision, plan_research



def _fuel_mizer_available(state:GameState)->bool:
    lrts=set((state.race.native or {}).get("lrts",[]))
    # Fuel Mizer stock requirement is Propulsion 2; it is the defining early IFE
    # engine. Existing designs using native engine id 2 are also direct evidence.
    existing=any(
        int(d.get("engine_id",-1))==2
        for d in state.native.get("design_profiles",[])
    )
    return existing or ("IFE" in lrts and int(state.tech.propulsion)>=2)


def add_research_orders(
    state: GameState,
    orders: OrderSet,
    plan: StrategicPlan | None = None,
    decision: ResearchDecision | None = None,
) -> ResearchDecision:
    decision = decision or plan_research(state, plan)
    fuel_mizer = _fuel_mizer_available(state)
    early_mizer_doctrine = bool(
        fuel_mizer
        and state.year <= 2435
        and min(state.tech.energy,state.tech.weapons,state.tech.construction) < 10
    )
    doctrine_note = (
        " Fuel Mizer is already available, so this is a named capability choice rather than blind Propulsion catch-up."
        if early_mizer_doctrine else ""
    )
    orders.add(
        "set_research",
        {
            **decision.to_payload(),
            "fuel_mizer_available": fuel_mizer,
            "early_mizer_doctrine": early_mizer_doctrine,
        },
        f"{plan.persona_name + ': ' if plan else ''}{decision.reason}{doctrine_note}",
        priority=145 if decision.posture in ("SPRINT", "MILITARY_EMERGENCY") else 80,
    )
    return decision
