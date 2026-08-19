
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Any
import math

class WeaponKind(str, Enum):
    BEAM = "beam"
    TORPEDO = "torpedo"
    CAPITAL_MISSILE = "capital_missile"
    SAPPER = "sapper"

@dataclass
class Weapon:
    kind: WeaponKind
    damage: float
    range: int
    initiative: float = 0.0
    accuracy: float = 1.0
    count: int = 1

@dataclass
class BattleDesign:
    name: str
    armor: float
    shields: float
    movement: float
    resource_cost: float
    boranium_cost: float = 0.0
    jammer: float = 0.0
    computer: float = 0.0
    deflector: float = 0.0
    weapons: list[Weapon] = field(default_factory=list)

@dataclass
class BattleStack:
    design: BattleDesign
    count: int
    x: int = 0
    y: int = 0
    armor_remaining_per_ship: float | None = None
    shields_remaining: float | None = None

    def __post_init__(self):
        if self.armor_remaining_per_ship is None:
            self.armor_remaining_per_ship = self.design.armor
        if self.shields_remaining is None:
            self.shields_remaining = self.design.shields * self.count

    @property
    def alive(self) -> bool:
        return self.count > 0

@dataclass
class BattleSide:
    name: str
    stacks: list[BattleStack]
    starbase: bool = False
    tactic: str = "maximize_net_damage"

@dataclass
class BattleOutcome:
    winner: str | None
    rounds: int
    attacker_survivors: int
    defender_survivors: int
    attacker_loss_cost: float
    defender_loss_cost: float
    expected_trade_ratio: float
    notes: list[str] = field(default_factory=list)

def attractiveness(stack: BattleStack, weapon_kind: WeaponKind) -> float:
    d = stack.design
    defense = max(1.0, d.armor + d.shields)
    if weapon_kind in (WeaponKind.TORPEDO, WeaponKind.CAPITAL_MISSILE):
        defense *= max(0.35, 1.0 + d.jammer)
    elif weapon_kind == WeaponKind.BEAM:
        defense *= max(0.35, 1.0 + d.deflector)
    # Historical targeting strongly keys on resource + boranium cost / defense.
    return (d.resource_cost + d.boranium_cost) / defense

def _range_damage_modifier(w: Weapon, distance: int, target: BattleStack) -> float:
    if distance > w.range:
        return 0.0
    if w.kind in (WeaponKind.BEAM, WeaponKind.SAPPER) and w.range > 0:
        # StarsFAQ: 10% total dissipation from range 0 to max range.
        return 1.0 - 0.10 * (distance / w.range)
    return 1.0

def _hit_probability(w: Weapon, shooter: BattleDesign, target: BattleDesign) -> float:
    if w.kind not in (WeaponKind.TORPEDO, WeaponKind.CAPITAL_MISSILE):
        return 1.0
    # Transparent approximation until exact comp/jammer table is ported.
    p = w.accuracy + 0.04 * shooter.computer - 0.04 * target.jammer
    return max(0.05, min(0.99, p))

def _apply_damage(target: BattleStack, damage: float, kind: WeaponKind, hit_probability: float) -> float:
    if damage <= 0 or not target.alive:
        return 0.0

    # Expected-value missile model. Misses still splash shields for 1/8 damage.
    if kind in (WeaponKind.TORPEDO, WeaponKind.CAPITAL_MISSILE):
        effective = damage * hit_probability + damage * (1.0-hit_probability) * 0.125
    else:
        effective = damage

    shield_before = target.shields_remaining or 0.0
    shield_absorb = min(shield_before, effective)
    target.shields_remaining = shield_before - shield_absorb
    armor_damage = effective - shield_absorb

    if kind == WeaponKind.CAPITAL_MISSILE and target.shields_remaining <= 0:
        armor_damage *= 2.0

    if armor_damage <= 0:
        return effective

    armor_per_ship = max(1.0, target.armor_remaining_per_ship or target.design.armor)
    kills = min(target.count, int(armor_damage // armor_per_ship))
    target.count -= kills
    residual = armor_damage - kills * armor_per_ship

    if target.count > 0 and residual > 0:
        # Shared token damage approximation.
        target.armor_remaining_per_ship = max(0.2, armor_per_ship - residual / target.count)
    return effective

def _distance(a: BattleStack, b: BattleStack) -> int:
    return abs(a.x-b.x) + abs(a.y-b.y)

def _move_toward(stack: BattleStack, target: BattleStack):
    steps = max(0, min(3, int(stack.design.movement)))
    for _ in range(steps):
        if stack.x < target.x: stack.x += 1
        elif stack.x > target.x: stack.x -= 1
        elif stack.y < target.y: stack.y += 1
        elif stack.y > target.y: stack.y -= 1

def _move_away(stack: BattleStack, target: BattleStack):
    steps = max(0, min(3, int(stack.design.movement)))
    for _ in range(steps):
        if stack.x <= target.x: stack.x = max(0, stack.x-1)
        else: stack.x = min(9, stack.x+1)

def _target_for(shooter: BattleStack, enemies: list[BattleStack]) -> BattleStack | None:
    living = [e for e in enemies if e.alive]
    if not living:
        return None
    kinds = [w.kind for w in shooter.design.weapons] or [WeaponKind.BEAM]
    kind = kinds[0]
    return max(living, key=lambda s: attractiveness(s, kind))

def _side_cost(side: BattleSide) -> float:
    return sum(s.design.resource_cost * s.count for s in side.stacks)

def simulate_battle(attacker: BattleSide, defender: BattleSide, max_rounds: int = 16) -> BattleOutcome:
    # Clone mutable stacks.
    import copy
    atk = copy.deepcopy(attacker)
    dfn = copy.deepcopy(defender)
    original_atk = _side_cost(atk)
    original_dfn = _side_cost(dfn)

    # Start opposite edges of 10x10 board.
    for s in atk.stacks:
        s.x, s.y = 0, 5
    for s in dfn.stacks:
        s.x, s.y = 9, 5

    notes = []
    rounds = 0
    for rnd in range(1, max_rounds+1):
        rounds = rnd
        if not any(s.alive for s in atk.stacks) or not any(s.alive for s in dfn.stacks):
            break

        # Movement; heavier/token subtleties abstracted, but board/range behavior retained.
        for side, enemy, is_base in ((atk, dfn, False), (dfn, atk, dfn.starbase)):
            for s in [x for x in side.stacks if x.alive]:
                if side.starbase:
                    continue
                t = _target_for(s, enemy.stacks)
                if not t: continue
                if side.tactic.startswith("disengage"):
                    _move_away(s, t)
                else:
                    longest = max((w.range for w in s.design.weapons), default=0)
                    if _distance(s,t) > longest:
                        _move_toward(s,t)

        # Initiative across all weapon slots.
        shots = []
        for side, enemy in ((atk, dfn), (dfn, atk)):
            for s in [x for x in side.stacks if x.alive]:
                for w in s.design.weapons:
                    shots.append((w.initiative, side, enemy, s, w))
        shots.sort(key=lambda x: x[0], reverse=True)

        for _, side, enemy, shooter, w in shots:
            if not shooter.alive:
                continue
            target = _target_for(shooter, enemy.stacks)
            if target is None:
                continue
            effective_range = w.range + (1 if side.starbase else 0)
            dist = _distance(shooter, target)
            if dist > effective_range:
                continue
            # For starbase bonus use original weapon dissipation profile, just extended range.
            mod = _range_damage_modifier(w, min(dist, w.range), target) if w.range > 0 else 1.0
            damage = shooter.count * w.count * w.damage * mod
            hitp = _hit_probability(w, shooter.design, target.design)
            _apply_damage(target, damage, w.kind, hitp)

    atk_survivors = sum(s.count for s in atk.stacks if s.alive)
    def_survivors = sum(s.count for s in dfn.stacks if s.alive)
    atk_remaining = _side_cost(atk)
    def_remaining = _side_cost(dfn)
    atk_loss = max(0.0, original_atk-atk_remaining)
    def_loss = max(0.0, original_dfn-def_remaining)
    if atk_survivors and not def_survivors:
        winner = attacker.name
    elif def_survivors and not atk_survivors:
        winner = defender.name
    else:
        winner = None
    trade = def_loss / max(1.0, atk_loss)
    if defender.starbase:
        notes.append("Defender received Stars! starbase +1 weapon-range bonus.")
    return BattleOutcome(winner, rounds, atk_survivors, def_survivors, atk_loss, def_loss, trade, notes)
