
from __future__ import annotations

from dataclasses import dataclass, asdict
from .planet_economy import working_mineral_reserve

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
    """Shared planet-reserve policy used by both production and freight."""
    return working_mineral_reserve(
        planet,
        economy,
        _planned_queue_for(orders,planet.id),
    )


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


def derive_cargo_plan(
    source,
    target,
    fleet,
    economy,
    orders,
    *,
    destination_minimum_stock: dict | None = None,
    source_committed: dict | None = None,
    target_inbound: dict | None = None,
) -> CargoPlan | None:
    """Plan one safe shipment without double-spending a planet's minerals.

    ``destination_minimum_stock`` is used by high-value projects such as a
    support starbase.  It is a stock target, not a guessed native order: the
    normal working reserve remains the floor for every other destination.
    Callers can pass per-turn source and inbound commitments while assigning
    several freighters so two ships never claim the same surface minerals.
    """
    capacity = int(
        getattr(fleet, "cargo_capacity", 0)
        or (getattr(fleet, "native", {}) or {}).get("cargo_capacity", 0)
        or 0
    )
    if capacity <= 0:
        return None

    source_need = _working_stock(source, economy, orders)
    target_need = _working_stock(target, economy, orders)
    for mineral, required in (destination_minimum_stock or {}).items():
        if mineral in target_need:
            target_need[mineral] = max(int(target_need[mineral]), max(0, int(required or 0)))

    source_surface = {
        "ironium": max(0, int(source.ironium) - int((source_committed or {}).get("ironium", 0) or 0)),
        "boranium": max(0, int(source.boranium) - int((source_committed or {}).get("boranium", 0) or 0)),
        "germanium": max(0, int(source.germanium) - int((source_committed or {}).get("germanium", 0) or 0)),
    }
    target_surface = {
        "ironium": int(target.ironium) + int((target_inbound or {}).get("ironium", 0) or 0),
        "boranium": int(target.boranium) + int((target_inbound or {}).get("boranium", 0) or 0),
        "germanium": int(target.germanium) + int((target_inbound or {}).get("germanium", 0) or 0),
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
