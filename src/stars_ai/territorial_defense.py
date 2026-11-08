"""Territorial-violation assessment for minefield and patrol doctrine.

Stars! has no diplomatic treaty mechanic that reserves map coordinates.  This
module therefore treats minefields as an explicit *claim* and reacts only when
a non-friendly fleet with military or transport significance enters the
defensible space around an owned world.  Scouts and harmless unknown contacts
remain intelligence events, not automatic escalations.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from typing import Any

from .util import distance
from .turn_state import mapping as turn_mapping


# A captured border world remains an emergency after the one turn in which the
# ownership change first appears in an M file.  The loss shock decays across
# five planning cycles so the empire can complete defensive work already put
# into serial queues instead of whipsawing back to factories on the next turn.
RECENT_LOSS_EMERGENCY_TURNS = 5
EXPOSURE_PRESSURE_DECAY = 0.72


@dataclass(frozen=True)
class TerritorialViolation:
    """One non-friendly armed or transport fleet inside a claimed zone."""

    fleet_id: int
    fleet_name: str
    owner: int
    classification: str
    anchor_planet_id: int
    anchor_planet_name: str
    distance_ly: float
    claim_radius_ly: float
    severity: float
    requires_patrol: bool
    requires_minefield: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SourceWorldEscalation:
    """A guarded source-world campaign assessment, never an automatic attack."""

    status: str
    enemy_player_id: int | None
    source_planet_id: int | None
    source_planet_name: str | None
    source_confidence: str
    can_hold_current_territory: bool
    desperate_to_neutralize_host: bool
    invasion_authorized: bool
    own_visible_combat_power: float
    defensive_reserve_power: float
    required_invasion_force: float
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class EmpireExposure:
    """Empire-wide defensive demand, including a deliberately decaying shock.

    Individual violations still drive immediate intercept orders.  This
    snapshot answers the separate production question: how much reserve must
    the whole empire retain while several border failures compound.
    """

    border_worlds: int
    enemy_contacts_near_border: int
    uncovered_minefield_anchors: int
    enemy_power_inside_claim: float
    recent_planet_losses: int
    owned_planet_count: int
    raw_pressure: float
    sustained_pressure: float
    pressure_multiplier: float
    loss_emergency_multiplier: float
    loss_emergency_turns_remaining: int
    combat_reserve_target: int
    minelayer_force_target: int
    defensive_base_priority: int
    counter_capture_transport_target: int
    counter_capture_priority: int
    border_world_ids: tuple[int, ...]

    @property
    def emergency_active(self) -> bool:
        return bool(
            self.recent_planet_losses
            or self.enemy_contacts_near_border >= 6
            or self.uncovered_minefield_anchors >= 2
            or self.sustained_pressure >= 2.5
        )

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["emergency_active"] = self.emergency_active
        return out


@dataclass(frozen=True)
class TerritorialDefenseAssessment:
    """Serializable doctrine result consumed by military and production code."""

    claim_radius_ly: float
    violations: tuple[TerritorialViolation, ...]
    uncovered_minefield_anchor_ids: tuple[int, ...]
    desired_patrols: int
    desired_minelayers: int
    exposure: EmpireExposure
    reason: str
    source_escalation: SourceWorldEscalation

    @property
    def needs_response(self) -> bool:
        return bool(self.violations)

    @property
    def needs_minefield(self) -> bool:
        return bool(self.uncovered_minefield_anchor_ids)

    def to_dict(self) -> dict[str, Any]:
        return {
            "claim_radius_ly": self.claim_radius_ly,
            "violations": [violation.to_dict() for violation in self.violations],
            "uncovered_minefield_anchor_ids": list(self.uncovered_minefield_anchor_ids),
            "desired_patrols": self.desired_patrols,
            "desired_minelayers": self.desired_minelayers,
            "exposure": self.exposure.to_dict(),
            "reason": self.reason,
            "source_escalation": self.source_escalation.to_dict(),
        }


def _friendly_owner(state: Any, plan: Any, owner: int) -> bool:
    """Only actual friend/alliance evidence exempts a fleet from territory rules."""
    if int(owner) == int(state.player_id):
        return True
    view = (getattr(plan, "diplomacy", {}) or {}).get(int(owner), {}) if plan else {}
    if str(view.get("attitude", "")).lower() in {"helpful", "allied"}:
        return True
    if int(view.get("native_relation", 0) or 0) == 1:
        return True
    relations = list((state.race.native or {}).get("player_relations", []) or [])
    index = int(owner) - 1
    return 0 <= index < len(relations) and int(relations[index] or 0) == 1


def _cargo_kt(fleet: Any) -> int:
    native = getattr(fleet, "native", {}) or {}
    cargo = native.get("cargo") or {}
    return sum(max(0, int(cargo.get(key, 0) or 0)) for key in (
        "ironium", "boranium", "germanium", "population",
    ))


def _fleet_classification(fleet: Any) -> str | None:
    """Classify visible foreign traffic without pretending all mass is a gun.

    A native M file often does not reveal a foreign design's components.  Cargo
    is direct evidence of transport work.  For armament, retain explicit roles
    when the source supplies them and use a deliberately high mass threshold
    only for non-cargo fleets.  The latter is a defensive suspicion, recorded
    in the trace rather than presented as certainty.
    """
    role = str(getattr(fleet, "role", "unknown") or "unknown").casefold()
    native = getattr(fleet, "native", {}) or {}
    cargo = _cargo_kt(fleet)
    transport = role in {"freighter", "colony", "transport"} or cargo > 0
    explicit_armed = role in {"combat", "bomber", "minelayer"} or bool(
        native.get("armed") or native.get("has_weapons") or native.get("weapon_count")
    )
    inferred_armed = (
        not transport
        and float(getattr(fleet, "combat_power", 0.0) or 0.0) >= 100.0
    )
    if transport and (explicit_armed or inferred_armed):
        return "armed_transport"
    if transport:
        return "transport"
    if explicit_armed:
        return "armed"
    if inferred_armed:
        return "suspected_armed"
    return None


def _claim_radius(plan: Any) -> float:
    """Use a stable, conservative zone rather than an unbounded expansion halo."""
    defense = float(getattr(plan, "defense_radius", 100.0) or 100.0)
    return max(110.0, min(180.0, defense * 1.35))


def _anchor_has_our_minefield(state: Any, anchor: Any) -> bool:
    """Recognize a visible own field already protecting an anchor world."""
    for obj in ((state.native or {}).get("objects", []) or []):
        kind = str(obj.get("object_kind", obj.get("kind", "")))
        if kind.casefold() != "minefield":
            continue
        if int(obj.get("owner", -1) or -1) != int(state.player_id):
            continue
        fields = obj.get("fields") or {}
        if int(fields.get("mine_count", 0) or 0) <= 0:
            continue
        x = obj.get("x")
        y = obj.get("y")
        if x is None or y is None:
            continue
        if distance(anchor.position, type("Point", (), {"x": float(x), "y": float(y)})()) <= 80.0:
            return True
    return False


def _source_world_escalation(
    state: Any,
    plan: Any,
    violations: list[TerritorialViolation],
) -> SourceWorldEscalation:
    """Assess, but do not automatically launch, a source-world campaign.

    Following a violating fleet is defensive. Sending a force to a known enemy
    world is a material escalation from *hold our territory* to *neutralize or
    take theirs*, so it receives a materially higher threshold. A source is an
    inference from the nearest visible enemy world, never asserted as fact.
    """
    own_power=sum(
        max(0.0, float(getattr(fleet, "combat_power", 0.0) or 0.0))
        for fleet in state.fleets if int(getattr(fleet, "owner", -1)) == int(state.player_id)
    )
    ratio=float(getattr(plan, "attack_strength_ratio", 1.25) or 1.25)
    if plan is not None:
        ratio *= max(0.75, 1.25 - float(getattr(plan, "risk_tolerance", 0.5) or 0.5) * 0.35)
    violating_by_id={int(v.fleet_id): v for v in violations}
    violator_power=sum(
        max(1.0, float(getattr(fleet, "combat_power", 0.0) or 0.0))
        for fleet in state.fleets if int(getattr(fleet, "id", -1)) in violating_by_id
        and int(getattr(fleet, "owner", -1)) != int(state.player_id)
    )
    defensive_reserve=max(200.0, violator_power * ratio * 1.25)
    can_hold=own_power >= defensive_reserve * 1.5
    highest=max((violation.severity for violation in violations), default=0.0)
    desperate=bool(highest >= 0.85 and own_power >= defensive_reserve)

    if not violations:
        return SourceWorldEscalation(
            "NO_ESCALATION", None, None, None, "none", False, False, False,
            round(own_power, 1), round(defensive_reserve, 1), 0.0,
            "No territorial violation exists; no source-world campaign is considered.",
        )

    violator=violations[0]
    visible_fleet=next(
        (
            fleet for fleet in state.fleets
            if int(getattr(fleet, "id", -1)) == int(violator.fleet_id)
            and int(getattr(fleet, "owner", -1)) == int(violator.owner)
        ),
        None,
    )
    candidate_worlds=[
        planet for planet in state.planets
        if int(getattr(planet, "owner", -1) or -1) == int(violator.owner) and bool(getattr(planet, "observed", True))
    ]
    source=min(
        candidate_worlds,
        key=lambda planet: distance(visible_fleet.position, planet.position),
        default=None,
    ) if visible_fleet is not None else None
    required_invasion=max(400.0, defensive_reserve * 2.25)
    authorized=source is not None and (can_hold or desperate)
    if authorized:
        status="DESPERATE_NEUTRALIZATION_AUTHORIZED" if desperate and not can_hold else "PREPARE_SOURCE_INVASION"
        reason=(
            "A source-world campaign may be prepared, not auto-launched: current territory has sufficient visible "
            "defensive reserve. A later invasion planner must still verify transport, local enemy force, and holding value."
            if can_hold else
            "An exceptional territorial emergency permits preparation to neutralize the likely host; a later invasion planner "
            "must still verify transport and local strength before issuing an assault route."
        )
    else:
        status="DEFEND_TERRITORY"
        reason=(
            "Keep this conflict defensive: the visible force cannot yet retain the required territorial reserve after a "
            "source-world commitment."
            if source is not None else
            "Keep this conflict defensive: no visible enemy world can be treated as a source-world target."
        )
    return SourceWorldEscalation(
        status=status,
        enemy_player_id=int(violator.owner),
        source_planet_id=(int(source.id) if source is not None else None),
        source_planet_name=(str(source.name) if source is not None else None),
        source_confidence="inferred_nearest_visible_enemy_world" if source is not None else "no_visible_source_world",
        can_hold_current_territory=can_hold,
        desperate_to_neutralize_host=desperate,
        invasion_authorized=authorized,
        own_visible_combat_power=round(own_power, 1),
        defensive_reserve_power=round(defensive_reserve, 1),
        required_invasion_force=round(required_invasion, 1),
        reason=reason,
    )


def hostile_colonies_inside_claimed_area(state: Any, plan: Any = None) -> tuple[Any, ...]:
    # Capability signal only; this does not authorize an attack.
    owned=[planet for planet in state.planets if planet.owner == state.player_id]
    if not owned:
        return ()
    radius=_claim_radius(plan)
    out=[]
    for planet in state.planets:
        owner=getattr(planet,"owner",None)
        if owner is None or int(owner)==int(state.player_id):
            continue
        if not bool(getattr(planet,"observed",False)):
            continue
        if _friendly_owner(state,plan,int(owner)):
            continue
        native=getattr(planet,"native",{}) or {}
        recently_lost=bool(native.get("recently_lost_by_player"))
        view=(getattr(plan,"diplomacy",{}) or {}).get(int(owner),{}) if plan else {}
        attitude=str(view.get("attitude","neutral") or "neutral").casefold()
        action=str((view.get("conflict",{}) or {}).get("recommended_action","coexist") or "coexist").casefold()
        hostile=recently_lost or attitude=="hostile" or action=="oppose"
        if not hostile:
            continue
        separation=min(distance(planet.position,anchor.position) for anchor in owned)
        if separation <= radius:
            out.append(planet)
    return tuple(sorted(out,key=lambda planet:int(planet.id)))


def _recent_loss_events(state: Any) -> list[dict[str, int]]:
    """Collect current and persisted ownership losses without double-counting.

    The agent seeds ``territorial_exposure_history`` from durable memory.  The
    direct state inputs below keep this function useful for diagnostics and
    unit callers that have not gone through the full agent lifecycle.
    """
    native = getattr(state, "native", {}) or {}
    year = int(getattr(state, "year", 0) or 0)
    events: dict[int, int] = {}

    def add(planet_id: Any, lost_year: Any = None) -> None:
        try:
            planet_id = int(planet_id)
        except (TypeError, ValueError):
            return
        try:
            event_year = int(lost_year) if lost_year is not None else year
        except (TypeError, ValueError):
            event_year = year
        if 0 <= year - event_year < RECENT_LOSS_EMERGENCY_TURNS:
            events[planet_id] = max(event_year, events.get(planet_id, event_year))

    history = native.get("territorial_exposure_history", {}) or {}
    for event in history.get("loss_events", []) or []:
        if isinstance(event, dict):
            add(event.get("planet_id"), event.get("lost_year"))

    for key in ("recent_planet_losses", "lost_owned_planet_ids"):
        raw = native.get(key, []) or []
        if isinstance(raw, int):
            # A count-only integration lacks identities; retain it with stable
            # synthetic ids so it still creates the documented emergency.
            for index in range(max(0, raw)):
                add(-(index + 1), year)
        else:
            for item in raw:
                if isinstance(item, dict):
                    add(item.get("planet_id", item.get("id")), item.get("lost_year", item.get("year")))
                else:
                    add(item, year)

    persistent = turn_mapping(state, "persistent_intel")
    for planet_id in persistent.get("lost_owned_planet_ids", []) or []:
        add(planet_id, year)
    for planet in getattr(state, "planets", []) or []:
        if bool((getattr(planet, "native", {}) or {}).get("recently_lost_by_player")):
            add(getattr(planet, "id", None), year)
    return [
        {"planet_id": planet_id, "lost_year": lost_year}
        for planet_id, lost_year in sorted(events.items())
    ]


def _empire_exposure(
    state: Any,
    plan: Any,
    *,
    owned: list[Any],
    radius: float,
    violations: list[TerritorialViolation],
    edge_anchor_ids: list[int],
    uncovered_anchor_ids: tuple[int, ...],
    base_minelayer_target: int,
) -> EmpireExposure:
    """Turn local observations into a durable, bounded force requirement."""
    warning_radius = radius * 1.35
    border_ids = {
        int(violation.anchor_planet_id) for violation in violations
    } | {int(anchor_id) for anchor_id in edge_anchor_ids} | {
        int(anchor_id) for anchor_id in uncovered_anchor_ids
    }
    contact_ids: set[int] = set()
    for fleet in getattr(state, "fleets", []) or []:
        owner = int(getattr(fleet, "owner", -1) or -1)
        if owner <= 0 or owner == int(getattr(state, "player_id", -1)):
            continue
        if _friendly_owner(state, plan, owner) or _fleet_classification(fleet) is None:
            continue
        anchor = min(owned, key=lambda planet: distance(fleet.position, planet.position))
        if float(distance(fleet.position, anchor.position)) <= warning_radius:
            contact_ids.add(int(getattr(fleet, "id", -1)))
            border_ids.add(int(anchor.id))
    for planet in getattr(state, "planets", []) or []:
        owner = getattr(planet, "owner", None)
        if owner is None or int(owner) == int(getattr(state, "player_id", -1)):
            continue
        if not bool(getattr(planet, "observed", False)) or _friendly_owner(state, plan, int(owner)):
            continue
        anchor = min(owned, key=lambda world: distance(planet.position, world.position))
        if float(distance(planet.position, anchor.position)) <= warning_radius:
            border_ids.add(int(anchor.id))

    violating_ids = {int(violation.fleet_id) for violation in violations}
    enemy_power = sum(
        max(0.0, float(getattr(fleet, "combat_power", 0.0) or 0.0))
        for fleet in getattr(state, "fleets", []) or []
        if int(getattr(fleet, "id", -1)) in violating_ids
    )
    loss_events = _recent_loss_events(state)
    year = int(getattr(state, "year", 0) or 0)
    loss_turns_remaining = max(
        (max(0, RECENT_LOSS_EMERGENCY_TURNS - (year - int(event["lost_year"]))) for event in loss_events),
        default=0,
    )
    recent_losses = len(loss_events)
    raw_pressure = (
        0.13 * len(border_ids)
        + 0.20 * len(contact_ids)
        + 0.40 * len(uncovered_anchor_ids)
        + 0.30 * min(10.0, enemy_power / 100.0)
        + 1.25 * recent_losses
    )
    history = (getattr(state, "native", {}) or {}).get("territorial_exposure_history", {}) or {}
    try:
        prior_pressure = max(0.0, float(history.get("pressure_reserve", 0.0) or 0.0))
        prior_year = int(history.get("year", year) or year)
    except (TypeError, ValueError):
        prior_pressure, prior_year = 0.0, year
    elapsed = max(0, year - prior_year)
    decayed_prior = prior_pressure * (EXPOSURE_PRESSURE_DECAY ** elapsed)
    sustained = max(raw_pressure, decayed_prior)
    pressure_multiplier = 1.0 + min(1.0, sustained * 0.12)
    loss_multiplier = 1.0 + (0.80 * loss_turns_remaining / RECENT_LOSS_EMERGENCY_TURNS)
    if raw_pressure <= 0.0 and sustained < 0.20 and not recent_losses:
        combat_target = 0
        base_priority = 0
    else:
        reserve = (
            max(4, math.ceil(len(owned) * 0.35))
            + math.ceil(len(contact_ids) * 1.5)
            + 2 * len(uncovered_anchor_ids)
            + math.ceil(enemy_power / 125.0)
            + 4 * recent_losses
            # Persisted pressure reserves a tapering screen while completed
            # hulls/mines arrive; it is not allowed to hold the full crisis
            # target once contacts and losses are gone.
            + math.ceil(sustained * 2.0)
        )
        combat_target = min(36, int(math.ceil(reserve * loss_multiplier)))
        base_priority = min(
            240,
            int(165 + 12 * sustained + 22 * recent_losses + 8 * len(uncovered_anchor_ids)),
        )
    minelayer_target = base_minelayer_target
    if uncovered_anchor_ids:
        minelayer_target = max(
            base_minelayer_target,
            min(
                8,
                int(math.ceil(len(uncovered_anchor_ids) * 1.25))
                + int(math.ceil(len(contact_ids) / 6.0))
                + recent_losses,
            ),
        )
    counter_capture_target = min(
        3,
        max(recent_losses, 1 if len(contact_ids) >= 8 or sustained >= 2.5 else 0),
    )
    counter_capture_priority = min(
        235,
        int(178 + 15 * recent_losses + 8 * min(4.0, sustained)),
    ) if counter_capture_target else 0
    return EmpireExposure(
        border_worlds=len(border_ids),
        enemy_contacts_near_border=len(contact_ids),
        uncovered_minefield_anchors=len(uncovered_anchor_ids),
        enemy_power_inside_claim=round(enemy_power, 1),
        recent_planet_losses=recent_losses,
        owned_planet_count=len(owned),
        raw_pressure=round(raw_pressure, 3),
        sustained_pressure=round(sustained, 3),
        pressure_multiplier=round(pressure_multiplier, 3),
        loss_emergency_multiplier=round(loss_multiplier, 3),
        loss_emergency_turns_remaining=loss_turns_remaining,
        combat_reserve_target=combat_target,
        minelayer_force_target=minelayer_target,
        defensive_base_priority=base_priority,
        counter_capture_transport_target=counter_capture_target,
        counter_capture_priority=counter_capture_priority,
        border_world_ids=tuple(sorted(border_ids)),
    )



def assess_territorial_defense(state: Any, plan: Any = None) -> TerritorialDefenseAssessment:
    """Return patrol/minefield needs caused by meaningful border violations.

    The zone is the AI's perceived territory, not a claim that other players
    must acknowledge. A fleet is considered a violation only when it is not
    friendly and is either armed/suspected armed or currently carrying cargo.
    """
    owned = [planet for planet in state.planets if planet.owner == state.player_id]
    radius = _claim_radius(plan)
    if not owned:
        escalation=_source_world_escalation(state, plan, [])
        exposure=EmpireExposure(
            0, 0, 0, 0.0, 0, 0, 0.0, 0.0, 1.0, 1.0, 0,
            0, 0, 0, 0, 0, (),
        )
        return TerritorialDefenseAssessment(
            radius, (), (), 0, 0, exposure,
            "No owned worlds define territory.", escalation,
        )

    violations: list[TerritorialViolation] = []
    for fleet in state.fleets:
        owner = int(getattr(fleet, "owner", -1) or -1)
        if owner <= 0 or owner == int(state.player_id) or _friendly_owner(state, plan, owner):
            continue
        classification = _fleet_classification(fleet)
        if classification is None:
            continue
        anchor = min(owned, key=lambda planet: distance(fleet.position, planet.position))
        separation = float(distance(fleet.position, anchor.position))
        if separation > radius:
            continue
        proximity = max(0.0, 1.0 - separation / radius)
        kind_weight = {
            "armed": 0.74,
            "armed_transport": 0.82,
            "transport": 0.58,
            "suspected_armed": 0.48,
        }[classification]
        anchor_value = min(0.35, float(anchor.population or 0) / 500_000.0)
        severity = min(1.0, 0.30 * kind_weight + 0.45 * proximity + anchor_value)
        violations.append(TerritorialViolation(
            fleet_id=int(fleet.id),
            fleet_name=str(fleet.name),
            owner=owner,
            classification=classification,
            anchor_planet_id=int(anchor.id),
            anchor_planet_name=str(anchor.name),
            distance_ly=round(separation, 2),
            claim_radius_ly=round(radius, 2),
            severity=round(severity, 3),
            requires_patrol=severity >= 0.30,
            requires_minefield=severity >= (0.32 if classification in {"transport", "armed_transport"} else 0.42),
        ))

    violations.sort(key=lambda violation: (-violation.severity, violation.distance_ly, violation.fleet_id))

    # Pre-emptive edge deterrence. Significant hostile traffic just outside the
    # formal claim radius is where minefields have the greatest deterrent value.
    edge_anchor_ids=[]
    warning_radius=radius*1.35
    for fleet in state.fleets:
        owner=int(getattr(fleet,"owner",-1) or -1)
        if owner <= 0 or owner == int(state.player_id) or _friendly_owner(state,plan,owner):
            continue
        if _fleet_classification(fleet) is None:
            continue
        anchor=min(owned,key=lambda planet:distance(fleet.position,planet.position))
        separation=float(distance(fleet.position,anchor.position))
        if radius < separation <= warning_radius:
            edge_anchor_ids.append(int(anchor.id))
    for colony in hostile_colonies_inside_claimed_area(state,plan):
        anchor=min(owned,key=lambda planet:distance(colony.position,planet.position))
        edge_anchor_ids.append(int(anchor.id))

    candidate_anchor_ids=list(dict.fromkeys(
        [
            int(violation.anchor_planet_id)
            for violation in violations
            if violation.requires_minefield
        ] + edge_anchor_ids
    ))
    covered={
        planet_id
        for planet_id in candidate_anchor_ids
        if _anchor_has_our_minefield(
            state,
            next(planet for planet in owned if int(planet.id)==int(planet_id)),
        )
    }
    uncovered=tuple(
        planet_id for planet_id in candidate_anchor_ids
        if planet_id not in covered
    )
    patrols=sum(1 for violation in violations if violation.requires_patrol)
    desired_minelayers=(
        min(4,max(1,math.ceil(len(uncovered)*0.75)))
        if uncovered else 0
    )
    exposure=_empire_exposure(
        state,
        plan,
        owned=owned,
        radius=radius,
        violations=violations,
        edge_anchor_ids=edge_anchor_ids,
        uncovered_anchor_ids=uncovered,
        base_minelayer_target=desired_minelayers,
    )
    desired_minelayers=max(desired_minelayers, exposure.minelayer_force_target)
    reason=(
        f"{len(violations)} non-friendly armed/transport territorial violation(s) inside the "
        f"{radius:.0f}-ly claim zone; patrol objectives={patrols}, "
        f"proactive edge-pressure anchors={len(set(edge_anchor_ids))}, "
        f"uncovered minefield anchors={len(uncovered)}; empire exposure border worlds/contacts/"
        f"inside-claim power/recent losses={exposure.border_worlds}/{exposure.enemy_contacts_near_border}/"
        f"{exposure.enemy_power_inside_claim:.0f}/{exposure.recent_planet_losses}, "
        f"sustained pressure={exposure.sustained_pressure:.2f}."
    )
    escalation=_source_world_escalation(state, plan, violations)
    assessment=TerritorialDefenseAssessment(
        claim_radius_ly=round(radius, 2),
        violations=tuple(violations),
        uncovered_minefield_anchor_ids=uncovered,
        desired_patrols=patrols,
        desired_minelayers=desired_minelayers,
        exposure=exposure,
        reason=reason,
        source_escalation=escalation,
    )
    # All consumers see the same snapshot even if they invoke this assessment
    # independently during the planning pass.
    state.native["empire_exposure"]=exposure.to_dict()
    return assessment
