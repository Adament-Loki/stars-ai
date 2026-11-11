"""Immediate defensive fleet assignment for visible, engageable threats."""

from __future__ import annotations
from ..models import GameState, OrderSet
from ..persona import StrategicPlan
from ..invasion import (
    empire_ready_to_press_border,
    paper_tiger_colony,
    plan_invasion,
    weak_undefended_colony,
    worth_securing,
)
from ..territorial_defense import _minefield_base, assess_territorial_defense
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
                    # Stacking an outmatched fleet on the threatened world
                    # destroyed Player 4's combat force three times in the
                    # second 100-turn run. Planetary defenses hold that world.
                    # The ships fall back to another colony.
                    owned_worlds=[
                        planet for planet in state.planets
                        if planet.owner==state.player_id and int(planet.id)!=int(anchor.id)
                    ]
                    refuge=min(
                        owned_worlds,
                        key=lambda planet: distance(anchor.position, planet.position),
                    ) if owned_worlds else None
                    if refuge is not None:
                        for defender in group:
                            orders.add(
                                "move_fleet",
                                {
                                    "fleet_id": defender.id,
                                    "destination_planet_id": refuge.id,
                                    "warp": mission_warp(defender, refuge.position, "defend"),
                                    "mission": "defend",
                                    "territorial_group_defense": True,
                                    "defensive_fallback": True,
                                    "violator_fleet_id": violation.fleet_id,
                                    "violator_owner": violation.owner,
                                },
                                (
                                    f"outmatched; fall back to {refuge.name} instead of stacking at "
                                    f"{anchor.name} against {violation.fleet_name}. Combined readiness="
                                    f"{group_power:.1f} is below the {required_ratio:.2f}x intercept threshold."
                                ),
                                priority=int(120 + 20 * violation.severity),
                            )
                    handled_intruder_ids.add(int(violation.fleet_id))
                    orders.notes.append(
                        f"TERRITORIAL PATROL: available group cannot safely challenge {violation.fleet_name} "
                        f"at {required_ratio:.2f}x; fell back from {anchor.name} rather than losing the stack."
                    )

        field_base = _minefield_base(owned, anchor) if violation.requires_minefield else None
        if (
            field_base is not None
            and int(field_base.id) in territorial.uncovered_minefield_anchor_ids
            and int(field_base.id) not in assigned_minefield_anchors
            and available_minelayers
        ):
            minelayer = available_minelayers.pop(0)
            at_anchor = (
                int((minelayer.native or {}).get("position_object_id", -1) or -1) == int(field_base.id)
                or (
                    abs(float(minelayer.position.x) - float(field_base.position.x)) <= 0.5
                    and abs(float(minelayer.position.y) - float(field_base.position.y)) <= 0.5
                )
            )
            payload = {
                "fleet_id": minelayer.id,
                "destination_planet_id": field_base.id,
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
                        f"claim and protect {field_base.name} with a minefield after {violation.classification} "
                        f"violator {violation.fleet_name} entered the territorial zone. "
                        "Keep laying until the field is at least 100 ly across."
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
                        f"send minelayer {minelayer.name} to {field_base.name}, then lay until the field "
                        f"is at least 100 ly across against P{violation.owner}'s {violation.classification} intrusion."
                    ),
                    priority=int(110 + 18 * violation.severity),
                )
            assigned_minefield_anchors.add(int(field_base.id))

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

    _add_paper_tiger_push(state, orders, plan, available_patrols)
    _add_weak_colony_assault(state, orders, plan, available_patrols, required_ratio)
    _add_border_pressure(state, orders, plan, available_patrols, required_ratio)


# Two light armed ships are enough to slow an Alternate Reality colony drive.
# The battle line stays on defense.
PAPER_TIGER_SHIP_CAP = 2


def _light_armed_ship(fleet, player_id: int) -> bool:
    """A scout or other small armed hull, not a warship pulled off the line."""
    if not _available_defensive_fleet(fleet, player_id) or not _is_explicitly_armed(fleet):
        return False
    role = str(getattr(fleet, "role", "") or "").casefold()
    if role in {"combat", "warship", "bomber", "escort"} and _defensive_power(fleet) > 40.0:
        return False
    return True


def _add_paper_tiger_push(state, orders, plan, available_patrols) -> None:
    """Send at most two light armed ships at nearby high-value empty forts.

    Alternate Reality territory looks large because every world has a fort.
    Those forts have no ground defense. A couple of scouts with a weapon
    remove the ones on good worlds. A colonizer then claims the world.
    """

    owned = [planet for planet in state.planets if planet.owner == state.player_id]
    if not owned:
        return
    planets_by_id = {int(planet.id): planet for planet in state.planets}
    committed = []
    for fleet in state.fleets:
        if int(getattr(fleet, "owner", -1) or -1) != int(state.player_id):
            continue
        destination = getattr(fleet, "destination_planet_id", None)
        if destination is None:
            continue
        planet = planets_by_id.get(int(destination))
        if planet is None or not paper_tiger_colony(state, planet):
            continue
        committed.append(int(planet.id))
    slots = PAPER_TIGER_SHIP_CAP - len(committed)
    claims = list(committed)
    if slots > 0:
        lights = [
            fleet for fleet in state.fleets
            if _light_armed_ship(fleet, state.player_id)
        ]
        lights.sort(key=lambda fleet: (_defensive_power(fleet), int(fleet.id)))
        candidates = []
        for planet in state.planets:
            owner = getattr(planet, "owner", None)
            if owner is None or int(owner) == int(state.player_id) or not bool(getattr(planet, "observed", False)):
                continue
            if plan is not None:
                view = (plan.diplomacy or {}).get(int(owner), {})
                if str(view.get("attitude", "neutral") or "neutral").lower() in {"helpful", "allied"}:
                    continue
            if not paper_tiger_colony(state, planet) or not worth_securing(planet):
                continue
            nearest = min(distance(planet.position, world.position) for world in owned)
            if nearest > 180.0:
                continue
            candidates.append((nearest, int(planet.habitability or 0), int(planet.id), planet))
        candidates.sort(key=lambda row: (row[0], -row[1], row[2]))
        for _nearest, _habitability, _planet_id, planet in candidates:
            if slots <= 0 or not lights:
                break
            if int(planet.id) in claims:
                continue
            scout = lights.pop(0)
            slots -= 1
            claims.append(int(planet.id))
            if scout in available_patrols:
                available_patrols.remove(scout)
            scout.destination_planet_id = int(planet.id)
            orders.add(
                "move_fleet",
                {
                    "fleet_id": scout.id,
                    "destination_planet_id": int(planet.id),
                    "warp": mission_warp(scout, planet.position, "attack"),
                    "mission": "attack",
                    "invasion_action": "CAPTURE",
                    "target_owner": int(planet.owner),
                    "secure_after_attack": True,
                    "paper_tiger": True,
                },
                (
                    f"push into {planet.name}: the colony is an empty orbital fort with no ground defense. "
                    f"One light armed ship removes it, and habitability={int(planet.habitability or 0)}% "
                    "is worth settling. Two such ships are enough."
                ),
                priority=92,
            )
    state.native["paper_tiger_claims"] = list(dict.fromkeys(claims))[:PAPER_TIGER_SHIP_CAP]


def _add_weak_colony_assault(state, orders, plan, available_patrols, required_ratio: float) -> None:
    """Send one idle combat fleet to a small undefended non-allied colony.

    Defense assignments consume patrols first. Allies and helpful partners are
    never targets. At most one world is approached per turn.
    """

    if not available_patrols:
        return
    candidates = []
    for planet in state.planets:
        owner = getattr(planet, "owner", None)
        if owner is None or int(owner) == int(state.player_id) or not bool(getattr(planet, "observed", False)):
            continue
        if plan is not None:
            view = (plan.diplomacy or {}).get(int(owner), {})
            attitude = str(view.get("attitude", "neutral") or "neutral").lower()
            if attitude in {"helpful", "allied"}:
                continue
        if not worth_securing(planet) or not weak_undefended_colony(state, planet):
            continue
        nearest = min(
            (distance(planet.position, owned.position) for owned in state.planets if owned.owner == state.player_id),
            default=9999.0,
        )
        if nearest > 180.0:
            continue
        local_enemy = sum(
            max(1.0, float(fleet.combat_power or 0.0))
            for fleet in state.fleets
            if int(fleet.owner) == int(owner)
            and abs(float(fleet.position.x) - float(planet.position.x)) <= 1.0
            and abs(float(fleet.position.y) - float(planet.position.y)) <= 1.0
        )
        candidates.append((nearest, local_enemy, planet))
    if not candidates:
        return
    candidates.sort(key=lambda row: (row[0], row[1], int(row[2].id)))
    _, local_enemy, planet = candidates[0]
    patrol = available_patrols[0]
    patrol_power = _defensive_power(patrol)
    if local_enemy > 0.0 and patrol_power < local_enemy * required_ratio:
        return
    assessed = plan_invasion(
        planet,
        our_local_strength=patrol_power,
        enemy_local_strength=max(1.0, local_enemy),
        transport_capacity=0,
    )
    if assessed.action not in {"CAPTURE", "NEUTRALIZE"}:
        return
    available_patrols.pop(0)
    orders.add(
        "move_fleet",
        {
            "fleet_id": patrol.id,
            "destination_planet_id": int(planet.id),
            "warp": mission_warp(patrol, planet.position, "attack"),
            "mission": "attack",
            "invasion_action": assessed.action,
            "target_owner": int(planet.owner),
        },
        (
            f"approach undefended colony {planet.name} (P{planet.owner}) for {assessed.action.lower()}; "
            f"population={int(planet.population or 0):,}, defenses={int(planet.defenses or 0)}."
        ),
        priority=88,
    )
    state.native["invasion_targets"] = [{
        "planet_id": int(planet.id),
        "owner": int(planet.owner),
        "action": assessed.action,
        "fleet_id": int(patrol.id),
    }]


def _add_border_pressure(state, orders, plan, available_patrols, required_ratio: float) -> None:
    """After saturation, take a high-value border world or break a lesser one.

    Securing a world costs colonists and a garrison, so only a high-value
    colony is captured. Any other enemy world on the border is attacked and
    then left; the fleet is free to move on.
    """

    if not empire_ready_to_press_border(state, plan) or not available_patrols:
        return
    owned = [planet for planet in state.planets if planet.owner == state.player_id]
    if not owned:
        return
    raids = [int(planet_id) for planet_id in (state.native.get("raid_only_planet_ids") or [])]
    captures = []
    ranked = []
    for planet in state.planets:
        owner = getattr(planet, "owner", None)
        if owner is None or int(owner) == int(state.player_id) or not bool(getattr(planet, "observed", False)):
            continue
        if plan is not None:
            view = (plan.diplomacy or {}).get(int(owner), {})
            if str(view.get("attitude", "neutral") or "neutral").lower() in {"helpful", "allied"}:
                continue
        nearest = min(distance(planet.position, world.position) for world in owned)
        if nearest > 180.0:
            continue
        ranked.append((0 if worth_securing(planet) else 1, nearest, int(planet.id), planet))
    ranked.sort()
    for _priority, _nearest, _planet_id, planet in ranked:
        if not available_patrols:
            break
        owner = int(planet.owner)
        local_enemy = sum(
            max(1.0, float(fleet.combat_power or 0.0))
            for fleet in state.fleets
            if int(fleet.owner) == owner
            and abs(float(fleet.position.x) - float(planet.position.x)) <= 1.0
            and abs(float(fleet.position.y) - float(planet.position.y)) <= 1.0
        )
        patrol = available_patrols[0]
        patrol_power = _defensive_power(patrol)
        if local_enemy > 0.0 and patrol_power < local_enemy * required_ratio:
            continue
        secure = worth_securing(planet)
        if int(planet.defenses or 0) > 30 and not secure:
            continue
        available_patrols.pop(0)
        action = "CAPTURE" if secure else "RAID"
        orders.add(
            "move_fleet",
            {
                "fleet_id": patrol.id,
                "destination_planet_id": int(planet.id),
                "warp": mission_warp(patrol, planet.position, "attack"),
                "mission": "attack",
                "invasion_action": action,
                "target_owner": owner,
                "secure_after_attack": secure,
            },
            (
                f"capture high-value border world {planet.name}; habitability={int(planet.habitability or 0)}%."
                if secure else
                f"raid {planet.name} and move on; destroy the enemy hold without securing a low-value world."
            ),
            priority=86 if secure else 80,
        )
        if secure:
            captures.append(int(planet.id))
        else:
            raids.append(int(planet.id))
        if len(captures) + sum(1 for item in raids if item == int(planet.id)) >= 1 and not secure:
            break
        if secure:
            break
    state.native["raid_only_planet_ids"] = list(dict.fromkeys(raids))
    if captures:
        state.native["capture_planet_ids"] = captures
