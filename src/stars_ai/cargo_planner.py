
from __future__ import annotations

from dataclasses import dataclass, asdict
from .planet_economy import installation_status

SMALL_LOAD_MAX_PER_MINERAL = 255


@dataclass
class CargoPlan:
    ironium: int
    boranium: int
    germanium: int
    capacity: int
    source_surplus: dict
    destination_deficit: dict
    rationale: list[str]

    @property
    def total(self) -> int:
        return self.ironium + self.boranium + self.germanium

    def as_load(self) -> dict:
        return {
            "ironium": self.ironium,
            "boranium": self.boranium,
            "germanium": self.germanium,
        }

    def to_dict(self):
        return asdict(self)


def _planned_queue_for(orders, planet_id: int) -> list[dict]:
    for o in orders.orders:
        if o.kind == "set_planet_queue" and int(o.payload.get("planet_id", -1)) == int(planet_id):
            return list(o.payload.get("queue", []) or [])
    return []


def _working_stock(planet, economy, orders) -> dict:
    """
    Estimate minerals that should remain on the planet for near-term work.

    Germanium is tied directly to factory demand. Ship mineral costs are not yet
    reconstructed design-by-design, so ship construction gets a conservative
    mineral reserve rather than a fake exact cost.
    """
    queue = _planned_queue_for(orders, planet.id)
    factories_queued = sum(
        int(q.get("quantity", 0) or 0)
        for q in queue
        if q.get("item") == "factory"
    )
    defenses_queued = sum(
        int(q.get("quantity", 0) or 0)
        for q in queue
        if q.get("item") == "defense"
    )
    ships_queued = sum(
        int(q.get("quantity", 0) or 0)
        for q in queue
        if q.get("item") == "ship_design"
    )

    status = installation_status(planet, economy)
    g_per_factory = 3 if economy.factory_germanium_discount else 4

    # Even when factories could not be queued this year because Germanium was
    # missing, carry enough target stock to unlock a useful near-term batch.
    near_term_factory_demand = max(
        factories_queued,
        min(10, int(status["factory_headroom"])),
    )
    factory_germanium = near_term_factory_demand * g_per_factory

    defense_minerals = defenses_queued * 5

    # A design-aware exact ship bill will replace this later. This is only a
    # reserve to prevent logistics from draining a shipyard before a build.
    ship_reserve = ships_queued * 30

    pop_band = max(1, int(planet.population or 0) // 100000)
    baseline = min(60, 15 + 5 * pop_band)

    return {
        "ironium": baseline + defense_minerals + ship_reserve,
        "boranium": baseline + defense_minerals + ship_reserve,
        "germanium": baseline + factory_germanium + defense_minerals + ship_reserve,
        "factory_germanium": factory_germanium,
        "queued_factories": factories_queued,
        "queued_ships": ships_queued,
    }


def _allocate(deficit: dict, surplus: dict, capacity: int) -> dict:
    """
    Derive exact I/B/G quantities.

    Germanium gets first call on finite capacity because it directly gates
    factory growth. Ironium/Boranium then share the remainder according to their
    useful deficits.
    """
    cap = max(0, int(capacity))
    out = {"ironium": 0, "boranium": 0, "germanium": 0}

    g = min(
        cap,
        SMALL_LOAD_MAX_PER_MINERAL,
        max(0, int(deficit["germanium"])),
        max(0, int(surplus["germanium"])),
    )
    out["germanium"] = g
    cap -= g

    if cap <= 0:
        return out

    wants = {
        k: min(
            SMALL_LOAD_MAX_PER_MINERAL,
            max(0, int(deficit[k])),
            max(0, int(surplus[k])),
        )
        for k in ("ironium", "boranium")
    }

    total = wants["ironium"] + wants["boranium"]
    if total <= 0:
        return out

    # Proportional first pass, then give any residual space to remaining demand.
    i = min(wants["ironium"], int(round(cap * wants["ironium"] / total)))
    out["ironium"] = i
    cap -= i

    b = min(wants["boranium"], cap)
    out["boranium"] = b
    cap -= b

    if cap > 0 and out["ironium"] < wants["ironium"]:
        extra = min(cap, wants["ironium"] - out["ironium"])
        out["ironium"] += extra

    return out


def derive_cargo_plan(source, target, fleet, economy, orders) -> CargoPlan | None:
    capacity = int(
        getattr(fleet, "cargo_capacity", 0)
        or (getattr(fleet, "native", {}) or {}).get("cargo_capacity", 0)
        or 0
    )
    if capacity <= 0:
        return None

    source_need = _working_stock(source, economy, orders)
    target_need = _working_stock(target, economy, orders)

    source_surface = {
        "ironium": int(source.ironium),
        "boranium": int(source.boranium),
        "germanium": int(source.germanium),
    }
    target_surface = {
        "ironium": int(target.ironium),
        "boranium": int(target.boranium),
        "germanium": int(target.germanium),
    }

    surplus = {
        k: max(0, source_surface[k] - int(source_need[k]))
        for k in ("ironium", "boranium", "germanium")
    }
    deficit = {
        k: max(0, int(target_need[k]) - target_surface[k])
        for k in ("ironium", "boranium", "germanium")
    }

    load = _allocate(deficit, surplus, capacity)
    if sum(load.values()) <= 0:
        return None

    rationale = []
    if load["germanium"]:
        rationale.append(
            f"Germanium {load['germanium']}kT supports factory/working-stock demand "
            f"(near-term factory Germanium={target_need['factory_germanium']}kT)."
        )
    if load["ironium"] or load["boranium"]:
        rationale.append(
            f"Ironium/Boranium {load['ironium']}/{load['boranium']}kT replenish "
            "ship/defense working stock."
        )
    rationale.append(
        f"capacity={capacity}kT; source surplus I/B/G="
        f"{surplus['ironium']}/{surplus['boranium']}/{surplus['germanium']}; "
        f"target deficit={deficit['ironium']}/{deficit['boranium']}/{deficit['germanium']}."
    )

    return CargoPlan(
        ironium=load["ironium"],
        boranium=load["boranium"],
        germanium=load["germanium"],
        capacity=capacity,
        source_surplus=surplus,
        destination_deficit=deficit,
        rationale=rationale,
    )
