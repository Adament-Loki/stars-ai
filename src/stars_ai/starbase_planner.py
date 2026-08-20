from __future__ import annotations

from dataclasses import dataclass

from .util import distance
from .colony_planner import colony_planet_is_eligible


STARBASE_QUEUE_SLOT_OFFSET = 16
STARBASE_DESIGN_LIMIT = 10


@dataclass(frozen=True)
class SupportBaseBuild:
    planet_id: int
    design_slot: int
    design_name: str
    hull_name: str
    priority: int
    reason: str
    complete_percent: int = 0

    def queue_item(self) -> dict:
        item = {
            "item": "starbase_design",
            "design_slot": self.design_slot,
            "design_name": self.design_name,
            "hull_name": self.hull_name,
            "quantity": 1,
            "role": "fuel_hub",
        }
        if self.complete_percent:
            item["complete_percent"] = self.complete_percent
        return item


def _production_for(state, planet_id: int) -> list[dict]:
    raw = (state.native or {}).get("production_by_planet", {}) or {}
    return list(raw.get(str(int(planet_id)), raw.get(int(planet_id), [])) or [])


def _queued_starbase_item(state, planet_id: int) -> dict | None:
    for item in _production_for(state, planet_id):
        if int(item.get("item_type", 0) or 0) != 4:
            continue
        item_id = int(item.get("item_id", -1) or -1)
        if STARBASE_QUEUE_SLOT_OFFSET <= item_id < STARBASE_QUEUE_SLOT_OFFSET + STARBASE_DESIGN_LIMIT:
            return item
    return None


def _support_designs(state) -> dict[int, dict]:
    return {
        int(profile["design_number"]): profile
        for profile in (state.native or {}).get("starbase_profiles", []) or []
        if bool((profile.get("capabilities") or {}).get("can_refuel"))
        and bool((profile.get("capabilities") or {}).get("can_build_ships"))
    }


def _preferred_support_design(state) -> dict | None:
    designs = list(_support_designs(state).values())
    if not designs:
        return None
    # Space Dock is the cheapest true support hull when it exists. Otherwise
    # reuse the lightest already-defined operational base design. Reusing an
    # existing design avoids the unsafe native create-design path.
    return min(
        designs,
        key=lambda d: (
            0 if int(d.get("hull_id", -1)) == 33 else 1,
            int(d.get("hull_id", 999)),
            int(d.get("design_number", 999)),
        ),
    )


def plan_support_base_builds(state,plan=None) -> list[SupportBaseBuild]:
    """Select at most one active refuel/shipyard hub project per empire.

    Existing starbase work is continued first. New projects favor Orbital Fort
    worlds that are far from a real refueling base or sit beside a useful
    frontier. A no-base world is considered only after it has meaningful
    population, while upgrading an existing fort can begin earlier and receive
    Stars!' normal upgrade credit.
    """
    support_designs = _support_designs(state)
    preferred = _preferred_support_design(state)
    if not preferred:
        return []

    owned = [p for p in state.planets if p.owner == state.player_id]

    # Do not restart or replace a base already under construction. Preserve its
    # native completion percentage and make it the empire's sole active project.
    for planet in owned:
        queued = _queued_starbase_item(state, planet.id)
        if queued is None:
            continue
        slot = int(queued["item_id"]) - STARBASE_QUEUE_SLOT_OFFSET
        profile = support_designs.get(slot)
        if profile is None:
            continue
        return [SupportBaseBuild(
            planet_id=planet.id,
            design_slot=slot,
            design_name=str(profile.get("name") or profile.get("hull_name") or f"Starbase #{slot + 1}"),
            hull_name=str(profile.get("hull_name") or "Support starbase"),
            priority=146,
            complete_percent=int(queued.get("complete_percent", 0) or 0),
            reason=(
                f"continue the in-progress {profile.get('name') or profile.get('hull_name')} "
                f"fuel-hub project at {planet.name}; native completion is "
                f"{int(queued.get('complete_percent', 0) or 0)}%."
            ),
        )]

    operational = [
        p for p in owned
        if bool((((p.native or {}).get("starbase_capabilities") or {}).get("can_refuel")))
    ]
    candidates = []
    for planet in owned:
        native = planet.native or {}
        caps = native.get("starbase_capabilities") or {}
        if bool(caps.get("can_refuel")):
            continue

        has_base = bool(native.get("has_starbase", native.get("starbase", False)))
        is_fort = bool(caps.get("is_orbital_fort"))
        if not has_base and int(planet.population or 0) < 100_000:
            continue

        nearest_hub = min(
            (distance(planet.position, hub.position) for hub in operational),
            default=999.0,
        )
        viable_frontier = [
            other for other in state.planets
            if other.owner is None
            and other.observed
            and colony_planet_is_eligible(state,other,plan)
            and distance(planet.position, other.position) <= 160.0
        ]
        unknown_frontier = sum(
            1 for other in state.planets
            if not other.observed and distance(planet.position, other.position) <= 120.0
        )

        network_gap = not operational or nearest_hub >= 120.0
        frontier_need = bool(viable_frontier) or unknown_frontier >= 3
        if not (network_gap or frontier_need):
            continue

        score = (
            min(1.5, nearest_hub / 240.0)
            + 0.38 * min(3, len(viable_frontier))
            + 0.06 * min(8, unknown_frontier)
            + (0.25 if is_fort else 0.0)
            + min(0.35, int(planet.population or 0) / 500_000.0)
        )
        candidates.append((score, planet, nearest_hub, viable_frontier, unknown_frontier, is_fort))

    if not candidates:
        return []

    score, planet, nearest_hub, viable, unknown, is_fort = max(candidates, key=lambda row: row[0])
    slot = int(preferred["design_number"])
    design_name = str(preferred.get("name") or preferred.get("hull_name") or f"Starbase #{slot + 1}")
    hull_name = str(preferred.get("hull_name") or "Support starbase")
    distance_text = "no operational hub exists" if not operational else f"nearest refuel hub is {nearest_hub:.1f} ly away"
    return [SupportBaseBuild(
        planet_id=planet.id,
        design_slot=slot,
        design_name=design_name,
        hull_name=hull_name,
        priority=144,
        reason=(
            f"upgrade {'Orbital Fort' if is_fort else 'orbital infrastructure'} at {planet.name} "
            f"to {design_name} ({hull_name}) for ship construction and refueling: "
            f"{distance_text}; {len(viable)} viable and {unknown} unknown frontier worlds "
            f"lie nearby; hub score={score:.2f}."
        ),
    )]
