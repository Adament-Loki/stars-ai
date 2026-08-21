
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any

from .empire_optimizer import optimize_population_transfers, classify_planet_role
from .base_network import evaluate_base_network
from .race_doctrine import doctrine_for
from .native_capabilities import capability
from .strategic_lookahead import StrategicOption, choose_strategy

@dataclass
class V4Assessment:
    race_doctrine: dict
    planet_roles: dict[int,str]
    population_transfers: list[dict]
    base_recommendations: list[dict]
    strategic_choice: str | None
    native_warnings: list[str]=field(default_factory=list)

def assess_turn_v4(state: Any, plan: Any | None=None) -> V4Assessment:
    doctrine=doctrine_for(state.race.primary_trait)
    roles={p.id:classify_planet_role(p).role for p in state.planets if p.owner==state.player_id}
    transfers=optimize_population_transfers(state)
    bases=evaluate_base_network(state)

    owned=len([p for p in state.planets if p.owner==state.player_id])
    hostiles=len([f for f in state.fleets if f.owner!=state.player_id])
    risk=getattr(plan,"risk_tolerance",0.5) if plan else 0.5
    options=[
      StrategicOption("CONSOLIDATE_AND_GROW",4,0.3,0.8,0.05,0.05,0.15,0.8,0.25),
      StrategicOption("TECH_AND_MODERNIZE",4,0.1,0.95,0.15,0.05,0.9,0.25,0.35),
      StrategicOption("PRESS_ADVANTAGE",3,0.8,0.55,0.20,0.30,0.10,0.35,0.55),
    ]
    if hostiles==0:
        options[2].future_value*=0.4
    if owned<4:
        options[0].future_value+=0.25
    choice=choose_strategy(options,risk_tolerance=risk).selected.name

    warnings=[]
    for action in ("transport_population","research_change","player_relation_change","set_battle_plan","replace_ship_design","packet_order"):
        c=capability(action)
        if c.status!="VALIDATED":
            warnings.append(f"{action}: {c.status} — {c.reason}")
    return V4Assessment(
      race_doctrine={"prt":doctrine.prt,"objective_modifiers":doctrine.objective_modifiers,"tactical_rules":doctrine.tactical_rules},
      planet_roles=roles,
      population_transfers=[t.__dict__ for t in transfers],
      base_recommendations=[b.__dict__ for b in bases[:8]],
      strategic_choice=choice,
      native_warnings=warnings,
    )

def augment_orders_v4(state: Any, orders: Any, plan: Any | None=None) -> V4Assessment:
    a=assess_turn_v4(state,plan)
    orders.notes.append(f"v4 strategic lookahead: {a.strategic_choice}")
    orders.notes.append(f"v4 race doctrine: {a.race_doctrine['prt']}")
    # Execution belongs to the shared fleet-level transport scheduler in the
    # economy pass.  Keeping this assessment advisory prevents a late,
    # independent population pass from claiming a fleet already selected for a
    # higher-ranked P1/P2/tactical need.
    for b in a.base_recommendations[:3]:
        orders.notes.append(f"v4 base-network: planet {b['planet_id']} -> {b['role']} priority={b['priority']:.2f}")
    for t in a.population_transfers[:3]:
        orders.notes.append(
            f"v4 pop optimizer advisory: {t['source_planet_id']} -> {t['destination_planet_id']} "
            f"{t['population']} colonists, score={t['score']:.2f}"
        )
    for w in a.native_warnings[:4]:
        orders.notes.append(f"native safety: {w}")
    return a
