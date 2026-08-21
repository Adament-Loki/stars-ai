"""Research-aware deployment policy for Stars! planetary X-series scanners."""
from __future__ import annotations

import re
from dataclasses import asdict
from typing import Any

from .design_legality import ComponentCategory
from .starsapi_items import proven_available_components, stock_component_database
from .util import distance


PLANETARY_SCANNER_QUEUE_ITEM = "planetary_scanner"
PLANETARY_SCANNER_STANDARD_ITEM_ID = 27


def best_penetrating_planetary_scanner(state: Any) -> dict[str, Any] | None:
    """Return the best race-legal researched Snooper X scanner, if any.

    UNEDITED.MOD contains the planetary scanner research ladder.  The X-series
    has a negative range entry in the MOD and is the stock penetrating family.
    ``proven_available_components`` applies the current research level and the
    NAS restriction before this policy sees an item.
    """
    candidates = [
        spec
        for spec in proven_available_components(state).values()
        if int(spec.category) == int(ComponentCategory.PLANETARY)
        and spec.name.startswith("Snooper ")
        and spec.name.endswith("X")
    ]
    if not candidates:
        return None

    def scanner_range(spec: Any) -> int:
        match = re.search(r"(\d+)X$", str(spec.name))
        return int(match.group(1)) if match else 0

    best = max(candidates, key=lambda spec: (scanner_range(spec), int(spec.item_id)))
    mod_spec=stock_component_database().component(int(best.category),int(best.item_id))
    return {
        "name": str(best.name),
        "range": scanner_range(best),
        "tech_required": list(best.tech_required),
        "resource_cost":int(getattr(mod_spec,"resource_cost",0) or 0),
        "ironium":int(getattr(mod_spec,"ironium",0) or 0),
        "boranium":int(getattr(mod_spec,"boranium",0) or 0),
        "germanium":int(getattr(mod_spec,"germanium",0) or 0),
        "component": asdict(best),
        "queue_item": PLANETARY_SCANNER_QUEUE_ITEM,
        "standard_item_id": PLANETARY_SCANNER_STANDARD_ITEM_ID,
    }


def planetary_scanner_sites(
    state: Any,
    capability: dict[str, Any] | None,
    *,
    limit: int = 2,
) -> list[int]:
    """Choose a small, useful sensor network instead of blanketing worlds.

    A planetary scanner is most valuable on developed core and frontier hubs,
    particularly near observed foreign territory.  Worlds which already report
    a scanner are considered current: the M-file exposes scanner presence but
    not the installed scanner model, so repeatedly queuing blind upgrades would
    waste every later turn.
    """
    if capability is None:
        return []
    owned = [p for p in state.planets if p.owner == state.player_id]
    foreign = [
        item
        for item in [*state.planets, *state.fleets]
        if getattr(item, "owner", None) not in (None, state.player_id)
    ]
    candidates = []
    for planet in owned:
        if bool((planet.native or {}).get("has_scanner", False)):
            continue
        # Do not ask a newborn outpost to spend its scarce first resources on
        # intelligence hardware.  Mature core/frontier hubs make the network.
        if int(planet.population or 0) < 30_000:
            continue
        native = planet.native or {}
        promotion = native.get("promotion") or {}
        tier = int(promotion.get("tier", 3) or 3)
        strategic = float(native.get("strategic_value", 0.5) or 0.5)
        nearest_foreign = min(
            (distance(planet.position, item.position) for item in foreign),
            default=9999.0,
        )
        # The outer edge is the network's most valuable location.  A mature
        # border world should beat the homeworld when it is materially nearer
        # to observed opposition, because that one standing sensor observes a
        # whole contested cluster every year.
        border_value = max(0.0, 450.0 - nearest_foreign)
        score = (
            border_value
            + (75.0 if bool(native.get("is_homeworld", False)) else 0.0)
            + ({0: 60.0, 1: 50.0, 2: 25.0}.get(tier, 0.0))
            + min(25.0, int(planet.population or 0) / 10_000.0)
            + 20.0 * strategic
        )
        candidates.append((score, int(planet.id)))
    candidates.sort(reverse=True)
    return [planet_id for _, planet_id in candidates[:max(0, int(limit))]]


def deployed_planetary_sensor_network(
    state: Any,
    capability: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Describe the live multi-system intelligence supplied by our scanners.

    Stars! performs the actual scan during host processing.  This is therefore
    a planning/diagnostic coverage map, based only on planets whose current M
    record already says ``has_scanner``.  Queued installations are deliberately
    excluded until a later M-file confirms them.
    """
    capability = capability or best_penetrating_planetary_scanner(state)
    if capability is None:
        return {
            "penetrating": False,
            "normal_range": 0,
            "penetrating_range": 0,
            "site_planet_ids": [],
            "normal_covered_planet_ids": [],
            "penetrating_covered_planet_ids": [],
        }
    normal_range=int(capability["range"])
    penetrating_range=normal_range // 2
    sites=[
        planet for planet in state.planets
        if planet.owner == state.player_id
        and bool((planet.native or {}).get("has_scanner", False))
    ]
    normal_covered=set()
    penetrating_covered=set()
    for site in sites:
        for planet in state.planets:
            d=distance(site.position,planet.position)
            if d<=normal_range:
                normal_covered.add(int(planet.id))
            if d<=penetrating_range:
                penetrating_covered.add(int(planet.id))
    return {
        "penetrating": True,
        "scanner_name":capability["name"],
        "normal_range":normal_range,
        "penetrating_range":penetrating_range,
        "site_planet_ids":sorted(int(site.id) for site in sites),
        "normal_covered_planet_ids":sorted(normal_covered),
        "penetrating_covered_planet_ids":sorted(penetrating_covered),
    }
