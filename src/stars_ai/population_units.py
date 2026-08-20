from __future__ import annotations


# Stars! stores fleet cargo mass in kilotons. Population occupies one kT per
# 100 colonists. Planet surface population has a separate native encoding.
COLONISTS_PER_CARGO_KT = 100

# The empirically validated colony command fills the 25 kT cargo hold.
COLONY_LOAD_KT = 25
COLONY_LOAD_COLONISTS = COLONY_LOAD_KT * COLONISTS_PER_CARGO_KT


def colony_source_reserve_for_turn(turn: int) -> int:
    """Population retained while the opening expansion pipeline is active."""
    turn = max(0, int(turn))
    if turn <= 10:
        return 10_000
    if turn <= 25:
        return 25_000
    return 50_000


def colonists_from_cargo_kt(population_kt: int | None) -> int:
    """Convert native fleet population cargo (kT) to colonist headcount."""
    return max(0, int(population_kt or 0)) * COLONISTS_PER_CARGO_KT


def cargo_kt_from_colonists(colonists: int | None) -> int:
    """Return whole kT needed to carry a colonist headcount."""
    population = max(0, int(colonists or 0))
    return (population + COLONISTS_PER_CARGO_KT - 1) // COLONISTS_PER_CARGO_KT
