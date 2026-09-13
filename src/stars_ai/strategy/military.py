"""Immediate defensive fleet assignment for visible, engageable threats."""

from __future__ import annotations
from ..models import GameState, OrderSet
from ..persona import StrategicPlan
from ..territorial_defense import assess_territorial_defense
from ..util import distance
from ..warp_policy import mission_warp


def _is_explicitly_armed(fleet) -> bool:
    """Whether an owned fleet is a usable combat responder.

    The native adapter normally labels weapon-bearing designs ``combat``.  A
    mixed fleet, an older saved design, or an external adapter can instead
    retain another role while still providing a real armed screen.  Do not
    leave that force in a generic hold merely because its high-level role is
    less specific than the component/readiness evidence.
    """
    native = fleet.native or {}
    role = str(getattr(fleet, "role", "") or "").casefold()
    return (
        role in {"combat", "bomber", "escort", "warship"}
        or bool(native.get("armed") or native.get("has_weapons"))
        or int(native.get("weapon_count", 0) or 0) > 0
        or float(getattr(fleet, "combat_power", 0.0) or 0.0) > 0.0
    )


def _defensive_power(fleet) -> float:
    """Return a conservative, non-zero readiness value for armed responders.

    Some native fleet records have no legacy mass value.  The adapter recovers
    that for standard combat fleets, but retained mixed-role fleets can still
    have armament evidence with a zero public ``combat_power``.  Their decoded
    dry mass is a conservative readiness proxy; when it is unavailable, the
    value 1 keeps the fleet in the fallback *defend the world* response rather
    than falsely treating it as unarmed or suitable for a strong intercept.
    """
    direct = float(getattr(fleet, "combat_power", 0.0) or 0.0)
    if direct > 0.0:
        return direct
    native = fleet.native or {}
    for field in ("combat_readiness", "combat_power", "mass"):
        try:
            value = float(native.get(field, 0.0) or 0.0)
        except (TypeError, ValueError):
            continue
        if value > 0.0:
            return value
    profile = native.get("fuel_profile") or {}
    try:
        dry_mass = float(profile.get("dry_mass", 0.0) or 0.0)
    except (TypeError, ValueError):
        dry_mass = 0.0
    if dry_mass > 0.0:
        return dry_mass
    return 1.0 if _is_explicitly_armed(fleet) else 0.0


def _available_defensive_fleet(fleet, player_id: int) -> bool:
    """Keep only idle armed fleets that can be safely borrowed for defense."""
    native = fleet.native or {}
    if int(getattr(fleet, "owner", -1)) != int(player_id):
        return False
    # A working minelayer is a strategic defense mission in its own right and
    # is handled separately below.  It must not be stripped from a field just
    # because its hull happens to carry a weapon.
    if str(getattr(fleet, "role", "") or "").casefold() == "minelayer":
        return False
    if getattr(fleet, "destination_planet_id", None) is not None:
        return False
    if native.get("native_destination_fleet_id") is not None:
        return False
    return _is_explicitly_armed(fleet)


def _active_minefield_anchor(state: GameState, fleet, owned_by_id: dict[int, object]):
    """Return the owned anchor for a validated, already-active Lay Mines task.

    A dedicated mine layer earns its value by adding mine mass at the same
    point over successive turns.  Do not treat a native task-6 waypoint as an
    idle fleet merely because the first visible minefield has already formed.
    Keep this check as strict as the native writer: one stationary waypoint at
    an owned planet.  Other waypoint shapes are left untouched rather than
    guessing at an experimental minefield transition.
    """
    native = fleet.native or {}
    waypoints = list(native.get("waypoints") or [])
    if int(native.get("waypoint_count", len(waypoints)) or 0) != 1 or not waypoints:
        return None
    current = waypoints[0]
    if int(current.get("task", 0) or 0) != 6:
        return None
    raw_target = current.get("position_object")
    target_id = int(-1 if raw_target is None else raw_target) & 0x7FF
    anchor = owned_by_id.get(target_id)
    object_type = int(current.get("position_object_type", 0) or 0)
    at_anchor = (
        int(native.get("position_object_id", -1) or -1) == target_id
        or (
            abs(float(fleet.position.x) - float(anchor.position.x)) <= 0.5
            and abs(float(fleet.position.y) - float(anchor.position.y)) <= 0.5
        )
    ) if anchor is not None else False
    if anchor is None or (object_type & 0x0F) != 1 or not at_anchor:
        return None
    return anchor


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
        if _available_defensive_fleet(f, state.player_id)
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

    # A task-6 minelayer is an anchored strategic asset, not an idle reserve.
    # Reaffirm it every turn so the turn report tells the player why it is not
    # moving, and so a later planner cannot borrow it for a new border alert.
    # Space Demolition may have enough dedicated layers to establish more than
    # one field, but each already-active layer remains at its selected anchor
    # until an explicit reassignment workflow is implemented and validated.
    active_minefield_anchors = {
        int(fleet.id): anchor
        for fleet in state.fleets
        if fleet.owner == state.player_id and fleet.role == "minelayer"
        for anchor in [_active_minefield_anchor(state, fleet, by_id)]
        if anchor is not None
    }
    for fleet_id, anchor in active_minefield_anchors.items():
        orders.add(
            "lay_minefield",
            {
                "fleet_id": fleet_id,
                "destination_planet_id": int(anchor.id),
                "minefield_type": "standard",
                "mission": "minefield_hold",
                "minefield_phase": "continuous",
            },
            (
                f"keep the dedicated minelayer stationary at {anchor.name} and continue laying mines; "
                "successive turns increase this field's density and radius."
            ),
            priority=155,
        )

    minelayer_fleets = [
        f for f in state.fleets
        if (
            f.owner == state.player_id and f.role == "minelayer"
            and int(f.id) not in active_minefield_anchors
            and f.destination_planet_id is None
            and (f.native or {}).get("native_destination_fleet_id") is None
        )
    ]
    # Commit the strongest available escorts to the highest-severity alerts
    # first.  File order is not tactical order: native fleet records are
    # numbered by creation time and otherwise made the oldest light escort the
    # first (and frequently inadequate) responder.
    available_patrols = sorted(
        combat_fleets,
        key=lambda fleet: (-_defensive_power(fleet), int(fleet.id)),
    )
    available_minelayers = list(minelayer_fleets)
    handled_intruder_ids: set[int] = set()
    assigned_minefield_anchors: set[int] = {
        int(anchor.id) for anchor in active_minefield_anchors.values()
    }

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
            patrol_power = _defensive_power(patrol)
            if patrol_power >= enemy_power * required_ratio:
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
                # A one-ship comparison is too pessimistic for an escort
                # screen.  When several idle combat fleets collectively meet
                # the defensive threshold, send them to the threatened world
                # rather than leaving all of them on generic HOLD.  Planet
                # defense uses the established planet waypoint form; it does
                # not claim an unvalidated multi-fleet intercept transaction.
                group=[patrol]
                group_power=patrol_power
                while available_patrols and group_power < enemy_power * required_ratio:
                    reinforcement=available_patrols.pop(0)
                    group.append(reinforcement)
                    group_power+=_defensive_power(reinforcement)
                if group_power >= enemy_power * required_ratio:
                    for defender in group:
                        orders.add(
                            "move_fleet",
                            {
                                "fleet_id":defender.id,
                                "destination_planet_id":anchor.id,
                                "warp":mission_warp(defender,anchor.position,"defend"),
                                "mission":"defend",
                                "territorial_group_defense":True,
                                "violator_fleet_id":violation.fleet_id,
                            },
                            (
                                f"concentrate defensive patrol at {anchor.name} against "
                                f"{violation.fleet_name}; combined readiness={group_power:.1f} "
                                f"meets {required_ratio:.2f}x the visible threat."
                            ),
                            priority=int(124 + 20 * violation.severity),
                        )
                    handled_intruder_ids.add(int(violation.fleet_id))
                else:
                    # Even an outmatched force is more useful at the exposed
                    # world than scattered across generic HOLD orders.  It
                    # reinforces the starbase and planetary defenses, can
                    # combine with arrivals next turn, and denies the enemy a
                    # sequence of undefended captures.  This is deliberately
                    # a planet-defense waypoint, not an unsafe attempt to
                    # intercept a visibly superior fleet.
                    for defender in group:
                        orders.add(
                            "move_fleet",
                            {
                                "fleet_id": defender.id,
                                "destination_planet_id": anchor.id,
                                "warp": mission_warp(defender, anchor.position, "defend"),
                                "mission": "defend",
                                "territorial_group_defense": True,
                                "defensive_fallback": True,
                                "violator_fleet_id": violation.fleet_id,
                                "violator_owner": violation.owner,
                            },
                            (
                                f"outmatched but active defense: concentrate at {anchor.name} against "
                                f"{violation.fleet_name}; combined readiness={group_power:.1f} is below "
                                f"the {required_ratio:.2f}x intercept threshold, so hold the world rather "
                                "than leave the fleet on generic reserve."
                            ),
                            priority=int(120 + 20 * violation.severity),
                        )
                    handled_intruder_ids.add(int(violation.fleet_id))
                    orders.notes.append(
                        f"TERRITORIAL PATROL: available group cannot safely challenge {violation.fleet_name} "
                        f"at {required_ratio:.2f}x; staged {len(group)} fleet(s) at {anchor.name} for "
                        "planetary defense and reinforce/minefield response."
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
                "minefield_mode": "planet_destination",
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
                    "lay_minefield",
                    {
                        **payload,
                        "warp": mission_warp(minelayer, anchor.position, "minefield_deploy"),
                        "mission": "minefield_deploy_and_lay",
                    },
                    (
                        f"send minelayer {minelayer.name} to {anchor.name}, then establish a territorial "
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
        fleet_power = _defensive_power(fleet)
        if fleet_power >= enemy_power * required_ratio:
            orders.add(
                "move_fleet",
                {"fleet_id": fleet.id, "destination_planet_id": planet.id, "warp": mission_warp(fleet,planet.position,"defend"), "mission": "defend"},
                f"{plan.persona_name + ': ' if plan else ''}respond to hostile {enemy.name} near {planet.name}; distance={d:.1f}.",
                priority=int(95 * defend_w),
            )
        else:
            # A weaker individual escort still has a concrete defensive job:
            # reinforce the threatened planet.  The previous behaviour only
            # wrote a warning and let it fall through to HOLD / DEFEND.
            orders.add(
                "move_fleet",
                {
                    "fleet_id": fleet.id,
                    "destination_planet_id": planet.id,
                    "warp": mission_warp(fleet, planet.position, "defend"),
                    "mission": "defend",
                    "defensive_fallback": True,
                    "hostile_fleet_id": enemy.id,
                    "hostile_owner": enemy.owner,
                },
                (
                    f"{plan.persona_name + ': ' if plan else ''}outmatched by {enemy.name} near "
                    f"{planet.name}; reinforce the world and its defenses instead of remaining on generic hold."
                ),
                priority=int(90 * defend_w),
            )
            orders.notes.append(
                f"{plan.persona_name + ': ' if plan else ''}threat near {planet.name}; {fleet.name} below "
                f"the {required_ratio:.2f}x intercept threshold and is reinforcing the planet instead."
            )
