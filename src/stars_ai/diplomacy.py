from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any

from .models import GameState
from .util import distance


class PlayerAttitude(str, Enum):
    HOSTILE = "hostile"
    NEUTRAL = "neutral"
    HELPFUL = "helpful"
    ALLIED = "allied"


class DiplomaticAction(str, Enum):
    OPPOSE = "oppose"
    AVOID = "avoid"
    COEXIST = "coexist"
    COOPERATE = "cooperate"
    ALLY = "ally"


@dataclass(frozen=True)
class ConflictAssessment:
    player_id: int
    our_strength: float
    their_strength: float
    strength_ratio: float
    border_pressure: float
    strategic_reward: float
    strategic_risk: float
    conflict_value: float
    recommended_action: DiplomaticAction
    reason: str

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["recommended_action"] = self.recommended_action.value
        return d


@dataclass(frozen=True)
class PlayerDiplomacyView:
    player_id: int
    is_human: bool
    attitude: PlayerAttitude
    trust: float
    threat: float
    usefulness: float
    can_ally: bool
    native_relation: int
    conflict: ConflictAssessment
    reason: str

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["attitude"] = self.attitude.value
        d["conflict"] = self.conflict.to_dict()
        return d


@dataclass
class DiplomacyPolicy:
    """Macro diplomacy rules.

    Human players may be treated as helpful/cooperative, but can never be allied.
    Human-vs-AI identity is supplied by the host configuration rather than inferred
    from undocumented Stars! bytes.
    """

    human_player_ids: frozenset[int] = field(default_factory=frozenset)
    alliance_trust_threshold: float = 0.72
    alliance_threat_ceiling: float = 0.38
    helpful_trust_threshold: float = 0.55
    hostile_threat_threshold: float = 0.62
    conflict_reward_weight: float = 1.0
    conflict_risk_weight: float = 1.0

    def is_human(self, player_id: int) -> bool:
        return player_id in self.human_player_ids

    def can_ally(self, player_id: int) -> bool:
        # HARD RULE: AI players cannot ally with human players.
        return not self.is_human(player_id)

    def evaluate_all(self, state: GameState) -> dict[int, PlayerDiplomacyView]:
        ids: set[int] = set()
        ids.update(f.owner for f in state.fleets if f.owner != state.player_id)
        ids.update(p.owner for p in state.planets if p.owner not in (None, state.player_id))
        relations = list(state.race.native.get("player_relations", []))
        ids.update(i + 1 for i in range(len(relations)) if (i + 1) != state.player_id)
        return {pid: self.evaluate_player(state, pid) for pid in sorted(ids)}

    def evaluate_player(self, state: GameState, player_id: int) -> PlayerDiplomacyView:
        native_relation = self._native_relation(state, player_id)
        our_strength = sum(max(0.0, f.combat_power) for f in state.fleets if f.owner == state.player_id)
        their_strength = sum(max(0.0, f.combat_power) for f in state.fleets if f.owner == player_id)

        owned = [p for p in state.planets if p.owner == state.player_id]
        their_fleets = [f for f in state.fleets if f.owner == player_id]
        close_count = 0
        if owned:
            for fleet in their_fleets:
                nearest = min(distance(fleet.position, p.position) for p in owned)
                if nearest <= 100.0:
                    close_count += 1
        border_pressure = min(1.0, close_count / 3.0)

        # Native Stars! relation is the strongest explicit signal we currently have:
        # 0 neutral, 1 friend, 2 enemy.
        trust = 0.50
        threat = 0.30
        if native_relation == 1:
            trust += 0.28
            threat -= 0.12
        elif native_relation == 2:
            trust -= 0.32
            threat += 0.40

        threat += 0.30 * border_pressure
        if our_strength > 0:
            threat += min(0.25, 0.12 * (their_strength / our_strength))
        elif their_strength > 0:
            threat += 0.25

        trust = max(0.0, min(1.0, trust))
        threat = max(0.0, min(1.0, threat))

        # Usefulness is intentionally strategic, not sentimental: another player is
        # more useful as a partner when they are non-hostile and have visible strength.
        relative_strength = their_strength / max(1.0, our_strength + their_strength)
        usefulness = max(0.0, min(1.0, 0.35 + 0.35 * relative_strength + 0.30 * trust - 0.35 * threat))

        conflict = self._assess_conflict(
            player_id=player_id,
            our_strength=our_strength,
            their_strength=their_strength,
            border_pressure=border_pressure,
            threat=threat,
            usefulness=usefulness,
        )

        ally_allowed = self.can_ally(player_id)
        if threat >= self.hostile_threat_threshold or native_relation == 2:
            attitude = PlayerAttitude.HOSTILE
            reason = "Enemy relation or strategic threat is high."
        elif trust >= self.alliance_trust_threshold and threat <= self.alliance_threat_ceiling and ally_allowed:
            attitude = PlayerAttitude.ALLIED
            reason = "High trust, low threat, and AI-to-AI alliance is permitted."
        elif trust >= self.helpful_trust_threshold and threat < self.hostile_threat_threshold:
            attitude = PlayerAttitude.HELPFUL
            if not ally_allowed:
                reason = "Useful/low-threat human player; cooperation allowed but alliance is prohibited."
            else:
                reason = "Useful, relatively trustworthy player; cooperation is favorable."
        else:
            attitude = PlayerAttitude.NEUTRAL
            reason = "Insufficient evidence for hostility or close cooperation."

        # Defensive invariant: no human can ever surface as ALLIED even if future
        # scoring code changes.
        if self.is_human(player_id) and attitude == PlayerAttitude.ALLIED:
            attitude = PlayerAttitude.HELPFUL
            reason = "Human-player alliance prohibited by policy; capped at helpful."

        return PlayerDiplomacyView(
            player_id=player_id,
            is_human=self.is_human(player_id),
            attitude=attitude,
            trust=round(trust, 4),
            threat=round(threat, 4),
            usefulness=round(usefulness, 4),
            can_ally=ally_allowed,
            native_relation=native_relation,
            conflict=conflict,
            reason=reason,
        )

    def _native_relation(self, state: GameState, player_id: int) -> int:
        relations = list(state.race.native.get("player_relations", []))
        idx = player_id - 1
        if 0 <= idx < len(relations):
            return int(relations[idx])
        return 0

    def _assess_conflict(
        self,
        *,
        player_id: int,
        our_strength: float,
        their_strength: float,
        border_pressure: float,
        threat: float,
        usefulness: float,
    ) -> ConflictAssessment:
        ratio = (our_strength + 1.0) / (their_strength + 1.0)
        # Reward rises when an opponent is dangerous/on our border and we have an
        # advantage. Risk rises with enemy strength and loss of a useful partner.
        advantage = max(0.0, min(2.0, ratio - 0.75)) / 2.0
        reward = min(1.0, 0.40 * threat + 0.35 * border_pressure + 0.25 * advantage)
        risk = min(1.0, 0.50 * min(1.0, 1.0 / max(0.01, ratio)) + 0.30 * usefulness + 0.20 * (1.0 - border_pressure))
        value = self.conflict_reward_weight * reward - self.conflict_risk_weight * risk

        if threat >= 0.72 and value > -0.10:
            action = DiplomaticAction.OPPOSE
            reason = "Threat is high enough to justify opposition despite conflict cost."
        elif value >= 0.18:
            action = DiplomaticAction.OPPOSE
            reason = "Expected strategic reward exceeds assessed conflict risk."
        elif risk >= 0.62:
            action = DiplomaticAction.AVOID
            reason = "Conflict risk is high relative to expected gain."
        elif usefulness >= 0.58 and threat < 0.55:
            action = DiplomaticAction.COOPERATE
            reason = "Cooperation has higher expected value than conflict."
        else:
            action = DiplomaticAction.COEXIST
            reason = "Neither conflict nor close cooperation has sufficient advantage."

        return ConflictAssessment(
            player_id=player_id,
            our_strength=round(our_strength, 3),
            their_strength=round(their_strength, 3),
            strength_ratio=round(ratio, 4),
            border_pressure=round(border_pressure, 4),
            strategic_reward=round(reward, 4),
            strategic_risk=round(risk, 4),
            conflict_value=round(value, 4),
            recommended_action=action,
            reason=reason,
        )
