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
class TerritorialDefenseAssessment:
    """Serializable doctrine result consumed by military and production code."""

    claim_radius_ly: float
    violations: tuple[TerritorialViolation, ...]
    uncovered_minefield_anchor_ids: tuple[int, ...]
    desired_patrols: int
    desired_minelayers: int
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
        return TerritorialDefenseAssessment(radius, (), (), 0, 0, "No owned worlds define territory.", escalation)

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
            requires_minefield=severity >= 0.42,
        ))

    violations.sort(key=lambda violation: (-violation.severity, violation.distance_ly, violation.fleet_id))
    covered = {
        int(violation.anchor_planet_id)
        for violation in violations
        if _anchor_has_our_minefield(
            state,
            next(planet for planet in owned if int(planet.id) == int(violation.anchor_planet_id)),
        )
    }
    uncovered = tuple(dict.fromkeys(
        int(violation.anchor_planet_id)
        for violation in violations
        if violation.requires_minefield and int(violation.anchor_planet_id) not in covered
    ))
    patrols = sum(1 for violation in violations if violation.requires_patrol)
    desired_minelayers = min(2, max(1, math.ceil(len(uncovered) / 2))) if uncovered else 0
    reason = (
        f"{len(violations)} non-friendly armed/transport territorial violation(s) inside the "
        f"{radius:.0f}-ly claim zone; patrol objectives={patrols}, uncovered minefield anchors={len(uncovered)}."
    )
    escalation=_source_world_escalation(state, plan, violations)
    return TerritorialDefenseAssessment(
        claim_radius_ly=round(radius, 2),
        violations=tuple(violations),
        uncovered_minefield_anchor_ids=uncovered,
        desired_patrols=patrols,
        desired_minelayers=desired_minelayers,
        reason=reason,
        source_escalation=escalation,
    )
