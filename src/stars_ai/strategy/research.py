"""Translate the strategic research decision into one semantic native order."""

from __future__ import annotations
from ..models import GameState, OrderSet
from ..persona import StrategicPlan
from ..research_planner import ResearchDecision, plan_research
from ..turn_state import publish as publish_turn_state



def _fuel_mizer_available(state:GameState)->bool:
    """Determine availability from researched IFE or an already fitted design."""
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
    """Record the selected research posture, field, and any Fuel Mizer context."""
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
    execution=dict((state.native or {}).get("research_sprint_execution") or {})
    # Production is the final authority on whether a named sprint has a real
    # cleared contributor.  This protects against a late emergency build that
    # legitimately retained every selected world after strategic planning ran.
    # Never serialize or report a 25% SPRINT with an empty execution plan.
    commitment=str(getattr(decision,"commitment","") or "").upper()
    if not commitment:
        commitment=(
            "RECOVERY_HIGH" if decision.posture=="MILITARY_EMERGENCY"
            else "SPRINT" if decision.posture=="SPRINT"
            else "ROUTINE"
        )
    high_commitment=commitment in {"SPRINT","RECOVERY_HIGH"}
    execution_failed=bool(
        high_commitment
        and not bool(execution.get("executable",decision.sprint_executable))
    )
    payload=decision.to_payload()
    reason=decision.reason
    priority=145 if high_commitment else 80
    if execution_failed:
        blocked=str(execution.get("blocked_reason") or decision.sprint_blocked_reason or "no executable contributor")
        payload.update({
            "posture":"RECOVERY",
            "allocation_percent":15,
            "contributor_planet_ids":[],
            "sprint_executable":False,
            "sprint_blocked_reason":blocked,
            "commitment":"DEFERRED",
            "execution_status":"DEFERRED",
            "expected_resources":0,
        })
        reason=(
            f"{reason} EXECUTION DOWNGRADE: the selected sprint has no cleared contributor; "
            f"emit normal 15% recovery research instead. {blocked}"
        )
        priority=80
        publish_turn_state(
            state,
            "research_strategy",
            {**payload, "execution_downgrade": True},
        )
    orders.add(
        "set_research",
        {
            **payload,
            "fuel_mizer_available": fuel_mizer,
            "early_mizer_doctrine": early_mizer_doctrine,
        },
        f"{plan.persona_name + ': ' if plan else ''}{reason}{doctrine_note}",
        priority=priority,
    )
    return decision
