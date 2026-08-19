from __future__ import annotations
from ..models import GameState, OrderSet
from ..persona import StrategicPlan
from ..util import distance


def add_military_orders(state: GameState, orders: OrderSet, plan: StrategicPlan | None = None) -> None:
    owned = [p for p in state.planets if p.owner == state.player_id]
    def _is_engageable(owner: int) -> bool:
        if owner == state.player_id:
            return False
        if not plan:
            return True
        view = plan.diplomacy.get(owner, {})
        attitude = view.get("attitude", "neutral")
        action = view.get("conflict", {}).get("recommended_action", "coexist")
        # Helpful/allied players are never treated as military targets. Neutral
        # players are engaged only when the conflict model explicitly recommends it.
        if attitude in ("helpful", "allied"):
            return False
        return attitude == "hostile" or action == "oppose"

    hostile_fleets = [f for f in state.fleets if _is_engageable(f.owner)]
    combat_fleets = [
        f for f in state.fleets
        if f.owner == state.player_id and f.role == "combat" and f.destination_planet_id is None
    ]

    defense_radius = plan.defense_radius if plan else 100.0
    required_ratio = plan.attack_strength_ratio if plan else 1.25
    # Higher risk tolerance permits engagement at a lower strength ratio.
    if plan:
        required_ratio *= max(0.55, 1.2 - plan.risk_tolerance * 0.65)

    threats = []
    for enemy in hostile_fleets:
        if not owned:
            continue
        nearest = min(owned, key=lambda p: distance(enemy.position, p.position))
        d = distance(enemy.position, nearest.position)
        if d <= defense_radius:
            threats.append((enemy, nearest, d))
    threats.sort(key=lambda t: t[2])

    defend_w = plan.objective("defend") * plan.mission("defend") if plan else 1.0
    for fleet, threat in zip(combat_fleets, threats):
        enemy, planet, d = threat
        enemy_power = max(enemy.combat_power, 1.0)
        if fleet.combat_power >= enemy_power * required_ratio:
            orders.add(
                "move_fleet",
                {"fleet_id": fleet.id, "destination_planet_id": planet.id, "warp": min(fleet.speed, 9), "mission": "defend"},
                f"{plan.persona_name + ': ' if plan else ''}respond to hostile {enemy.name} near {planet.name}; distance={d:.1f}.",
                priority=int(95 * defend_w),
            )
        else:
            orders.notes.append(
                f"{plan.persona_name + ': ' if plan else ''}threat near {planet.name}; {fleet.name} below engagement threshold ({required_ratio:.2f}x)."
            )
