
from __future__ import annotations

from typing import Any

from .decision_trace import CandidateScore, DecisionTrace, ScoreFactor


def trace_phase_decision(trace: DecisionTrace, phase_decision: Any) -> None:
    a = phase_decision.assessment
    p = phase_decision.policy
    trace.record(
        "strategy",
        f"Enter phase {phase_decision.phase.value}",
        phase_decision.reason,
        selected=phase_decision.phase.value,
        context={
            "explored_fraction": round(a.explored_fraction, 4),
            "owned_planets": a.owned_planets,
            "known_enemy_players": a.known_enemy_players,
            "open_frontier_fraction": round(a.open_frontier_fraction, 4),
            "contested_frontier_fraction": round(a.contested_frontier_fraction, 4),
            "frontier_pressure": round(a.frontier_pressure, 4),
            "expansion_saturation": round(a.expansion_saturation, 4),
            "policy_weights": {
                "explore": p.explore_weight,
                "expand": p.expand_weight,
                "develop": p.develop_weight,
                "fortify": p.fortify_weight,
                "attack": p.attack_weight,
                "research": p.research_weight,
            },
        },
    )


def trace_planet_defense(
    trace: DecisionTrace,
    planet_label: str,
    posture: Any,
    *,
    attacker: str | None = None,
) -> None:
    inv = posture.investment
    trace.record(
        "planet-defense",
        posture.recommended_response,
        posture.reason,
        selected=planet_label,
        context={
            "attacker": attacker,
            "total_value": round(inv.total_value, 4),
            "population_value": round(inv.population_value, 4),
            "infrastructure_value": round(inv.infrastructure_value, 4),
            "mineral_value": round(inv.mineral_value, 4),
            "strategic_value": round(inv.strategic_value, 4),
            "core_proximity_value": round(inv.core_proximity_value, 4),
            "logistics_value": round(inv.logistics_value, 4),
            "irreplaceability_value": round(inv.irreplaceability_value, 4),
            "abandonability": round(posture.abandonability, 4),
            "defense_priority": round(posture.defense_priority, 4),
            "escalation_priority": round(posture.escalation_priority, 4),
        },
    )


def trace_ranked_choice(
    trace: DecisionTrace,
    *,
    category: str,
    decision: str,
    selected: str,
    reason: str,
    ranked: list[tuple[str, list[tuple[str, float, float, str]]]],
    goals: list[str] | None = None,
    rules: list[str] | None = None,
    context: dict[str, Any] | None = None,
) -> None:
    candidates: list[CandidateScore] = []
    for label, raw_factors in ranked:
        factors = [
            ScoreFactor(name=n, value=v, weight=w, reason=r)
            for n, v, w, r in raw_factors
        ]
        candidates.append(trace.score_candidate(label, factors))
    trace.record(
        category,
        decision,
        reason,
        selected=selected,
        candidates=candidates,
        goals=goals or [],
        rules=rules or [],
        context=context or {},
    )


def trace_combat_modernization(trace, assessment, plan):
    trace.record(
        "combat-doctrine",
        f"Modernization decision: {plan.decision.value}",
        plan.reason,
        selected=plan.decision.value,
        context={
            "our_combat_value": round(assessment.our_value, 3),
            "enemy_combat_value": round(assessment.enemy_value, 3),
            "relative_strength": round(assessment.relative_strength, 3),
            "expected_trade_ratio": round(assessment.expected_trade_ratio, 3),
            "our_modern_fraction": round(assessment.our_modern_fraction, 3),
            "enemy_modern_fraction": round(assessment.enemy_modern_fraction, 3),
            "tech_gap": round(assessment.tech_gap, 3),
            "current_design_efficiency": round(plan.current_design_efficiency, 3),
            "prospective_design_efficiency": round(plan.prospective_design_efficiency, 3),
            "turns_to_upgrade": plan.expected_turns_to_upgrade,
            "territorial_risk": round(plan.territorial_risk, 3),
            "sacrifice_budget": round(plan.sacrifice_budget, 3),
            "production_guidance": plan.production_guidance,
            "engagement_guidance": plan.engagement_guidance,
        },
        goals=[f"Research priorities: {', '.join(plan.research_priorities)}"],
    )
