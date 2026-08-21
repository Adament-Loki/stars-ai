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

        if attitude == "allied" and can_ally and native_relation != 1:
            orders.add(
                "set_player_relation",
                {"player_id": player_id, "relation": "friend"},
                "AI ally candidate has high trust and low threat.",
                priority=62,
            )
        elif attitude == "hostile" and conflict.get("recommended_action") == "oppose":
            # Stars! does not require an Enemy relation write before a player
            # may be treated as a threat.  Keep hostility as an internal
            # strategic posture: it can drive defensive positioning, fields,
            # force composition, and later validated attack orders without
            # wasting a turn on an unneeded/unsupported relation mutation.
            orders.notes.append(
                f"Player {player_id}: opposition posture selected; preserve native relation and act on observed threat."
            )

        if is_human and attitude == "helpful":
            orders.notes.append(
                f"Player {player_id}: helpful human; cooperate/avoid conflict, but alliance is prohibited."
            )
