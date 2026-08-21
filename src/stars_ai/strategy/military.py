"""Immediate defensive fleet assignment for visible, engageable threats."""

from __future__ import annotations
from ..models import GameState, OrderSet
from ..persona import StrategicPlan
from ..territorial_defense import assess_territorial_defense
from ..util import distance
from ..warp_policy import mission_warp


def add_military_orders(state: GameState, orders: OrderSet, plan: StrategicPlan | None = None) -> None:
    """Defend claimed territory with patrols, minefield deployment, and response fleets."""
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
        if (
            f.owner == state.player_id and f.role == "combat" and f.destination_planet_id is None
            and (f.native or {}).get("native_destination_fleet_id") is None
        )
    ]
    minelayer_fleets = [
        f for f in state.fleets
        if (
            f.owner == state.player_id and f.role == "minelayer" and f.destination_planet_id is None
            and (f.native or {}).get("native_destination_fleet_id") is None
        )
    ]

    defense_radius = plan.defense_radius if plan else 100.0
    required_ratio = plan.attack_strength_ratio if plan else 1.25
    # Higher risk tolerance permits engagement at a lower strength ratio.
    if plan:
        required_ratio *= max(0.55, 1.2 - plan.risk_tolerance * 0.65)

    # Territorial doctrine applies before the narrower hostile-response pass:
    # a neutral armed transport inside claimed space is a violation even when
    # the diplomacy model has not yet chosen an Enemy posture. Minefields make
    # the border visible; patrols are the force that can remove the intruder.
    territorial = assess_territorial_defense(state, plan)
    state.native["territorial_defense"] = territorial.to_dict()
    if territorial.needs_response:
        orders.notes.append(f"TERRITORIAL DEFENSE: {territorial.reason}")
    by_id = {int(planet.id): planet for planet in owned}
    available_patrols = list(combat_fleets)
    available_minelayers = list(minelayer_fleets)
    handled_intruder_ids: set[int] = set()
    assigned_minefield_anchors: set[int] = set()

    for violation in territorial.violations:
        anchor = by_id.get(int(violation.anchor_planet_id))
        if anchor is None:
            continue
        intruder = next((fleet for fleet in state.fleets if int(fleet.id) == violation.fleet_id), None)
        if intruder is None:
            continue
        if violation.requires_patrol and available_patrols:
            patrol = available_patrols.pop(0)
            enemy_power = max(float(intruder.combat_power or 0.0), 1.0)
            if float(patrol.combat_power or 0.0) >= enemy_power * required_ratio:
                # A patrol defends the claim by tracking the violating fleet
                # itself, not by guessing at the world it might visit next.
                # The target-type-2 waypoint gives ordinary Stars! battle
                # rules an opportunity to neutralize the intruder. Striking a
                # suspected source world is a separate invasion escalation and
                # is deliberately not implied by a defensive patrol.
                orders.add(
                    "move_fleet",
                    {
                        "fleet_id": patrol.id,
                        "destination_fleet_id": intruder.id,
                        "destination_fleet_owner": intruder.owner,
                        "warp": mission_warp(patrol, intruder.position, "territorial_intercept"),
                        "mission": "territorial_intercept",
                        "violator_fleet_id": violation.fleet_id,
                        "violator_owner": violation.owner,
                        "territorial_severity": violation.severity,
                    },
                    (
                        f"intercept {violation.classification} violator "
                        f"{violation.fleet_name} (P{violation.owner}) at {violation.distance_ly:.1f} ly; "
                        f"severity={violation.severity:.2f}."
                    ),
                    priority=int(130 + 25 * violation.severity),
                )
                handled_intruder_ids.add(int(violation.fleet_id))
            else:
                orders.notes.append(
                    f"TERRITORIAL PATROL: {patrol.name} cannot safely challenge {violation.fleet_name} "
                    f"at {required_ratio:.2f}x; prioritize minefield/production response at {anchor.name}."
                )

        if (
            violation.requires_minefield
            and int(anchor.id) in territorial.uncovered_minefield_anchor_ids
            and int(anchor.id) not in assigned_minefield_anchors
            and available_minelayers
        ):
            minelayer = available_minelayers.pop(0)
            at_anchor = (
                int((minelayer.native or {}).get("position_object_id", -1) or -1) == int(anchor.id)
                or (
                    abs(float(minelayer.position.x) - float(anchor.position.x)) <= 0.5
                    and abs(float(minelayer.position.y) - float(anchor.position.y)) <= 0.5
                )
            )
            payload = {
                "fleet_id": minelayer.id,
                "destination_planet_id": anchor.id,
                "minefield_type": "standard",
                "violator_fleet_id": violation.fleet_id,
                "violator_owner": violation.owner,
                "territorial_severity": violation.severity,
            }
            if at_anchor:
                orders.add(
                    "lay_minefield", payload,
                    (
                        f"claim and protect {anchor.name} with a minefield after {violation.classification} "
                        f"violator {violation.fleet_name} entered the territorial zone."
                    ),
                    priority=int(118 + 20 * violation.severity),
                )
            else:
                orders.add(
                    "move_fleet",
                    {
                        **payload,
                        "warp": mission_warp(minelayer, anchor.position, "minefield_deploy"),
                        "mission": "minefield_deploy",
                    },
                    (
                        f"position minelayer {minelayer.name} at {anchor.name} to establish a territorial "
                        f"minefield against P{violation.owner}'s {violation.classification} intrusion."
                    ),
                    priority=int(110 + 18 * violation.severity),
                )
            assigned_minefield_anchors.add(int(anchor.id))

    threats = []
    for enemy in hostile_fleets:
        if int(enemy.id) in handled_intruder_ids:
            continue
        if not owned:
            continue
        nearest = min(owned, key=lambda p: distance(enemy.position, p.position))
        d = distance(enemy.position, nearest.position)
        if d <= defense_radius:
            threats.append((enemy, nearest, d))
    threats.sort(key=lambda t: t[2])

    defend_w = plan.objective("defend") * plan.mission("defend") if plan else 1.0
    for fleet, threat in zip(available_patrols, threats):
        enemy, planet, d = threat
        enemy_power = max(enemy.combat_power, 1.0)
        if fleet.combat_power >= enemy_power * required_ratio:
            orders.add(
                "move_fleet",
                {"fleet_id": fleet.id, "destination_planet_id": planet.id, "warp": mission_warp(fleet,planet.position,"defend"), "mission": "defend"},
                f"{plan.persona_name + ': ' if plan else ''}respond to hostile {enemy.name} near {planet.name}; distance={d:.1f}.",
                priority=int(95 * defend_w),
            )
        else:
            orders.notes.append(
                f"{plan.persona_name + ': ' if plan else ''}threat near {planet.name}; {fleet.name} below engagement threshold ({required_ratio:.2f}x)."
            )
