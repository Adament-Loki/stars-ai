from __future__ import annotations

from ..models import GameState, OrderSet
from ..persona import StrategicPlan


def add_diplomacy_orders(state: GameState, orders: OrderSet, plan: StrategicPlan) -> None:
    for player_id, view in sorted(plan.diplomacy.items()):
        attitude = view.get("attitude")
        is_human = bool(view.get("is_human"))
        can_ally = bool(view.get("can_ally"))
        conflict = view.get("conflict", {})

        # Hard safety/integrity invariant: never emit an ally/friend intention for human players.
        if is_human:
            can_ally = False
            if attitude == "allied":
                attitude = "helpful"

        native_relation=int(view.get("native_relation",0))

        if is_human:
            if attitude == "hostile":
                orders.notes.append(
                    f"Player {player_id}: human opposition stays internal; no Enemy relation is written."
                )
        elif attitude == "allied" and can_ally and native_relation != 1:
            orders.add(
                "set_player_relation",
                {"player_id": player_id, "relation": "friend"},
                "AI ally candidate has high trust and low threat.",
                priority=62,
            )
        elif attitude == "hostile" and conflict.get("recommended_action") == "oppose" and native_relation != 2:
            orders.add(
                "set_player_relation",
                {"player_id": player_id, "relation": "enemy"},
                "Hostile AI opponent is marked Enemy so fleet battle plans that attack enemies will engage.",
                priority=64,
            )
        elif attitude in {"helpful", "neutral"} and native_relation == 2:
            orders.add(
                "set_player_relation",
                {"player_id": player_id, "relation": "neutral"},
                "Opposition has cooled; restore Neutral instead of leaving a stale Enemy relation.",
                priority=60,
            )
        elif attitude == "hostile" and conflict.get("recommended_action") == "oppose":
            orders.notes.append(
                f"Player {player_id}: opposition posture selected; native relation is already Enemy."
            )

        if is_human and attitude == "helpful":
            orders.notes.append(
                f"Player {player_id}: helpful human; cooperate/avoid conflict, but alliance is prohibited."
            )
