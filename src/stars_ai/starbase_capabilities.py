
from __future__ import annotations

STARBASE_HULLS = {
    32: "Orbital Fort",
    33: "Space Dock",
    34: "Space Station",
    35: "Ultra Station",
    36: "Death Star",
}

# In Stars!, the Space Dock is a working shipyard but its construction bay is
# limited to designs of at most 200 kT.  The larger operational bases have no
# corresponding size cap.
SPACE_DOCK_MAX_BUILDABLE_SHIP_MASS_KT = 200

def starbase_capabilities(hull_id:int|None, *, armed:bool|None=None) -> dict:
    """
    Operational facilities are not equivalent to "some starbase exists".

    An empty base does not refuel. An Orbital Fort with a couple of weapons
    does, and so does any larger starbase hull that is actually armed. An
    Orbital Fort is never a shipyard.

    When ``armed`` is omitted, the historical hull-only rule remains: Space
    Dock, Space Station, Ultra Station, and Death Star can refuel, and an
    Orbital Fort cannot. Callers that have the fitted design pass ``armed``.

    Space Dock can construct designs up to 200 kT.  Space Station, Ultra
    Station, and Death Star may construct designs of any mass.

    Unknown/custom hulls are conservative: no refuel/shipyard privilege until
    their capability is decoded.
    """
    hid=None if hull_id is None else int(hull_id)
    name=STARBASE_HULLS.get(hid, f"Hull#{hid}" if hid is not None else "None")
    operational = hid in (33,34,35,36)
    fort = hid==32
    if armed is None:
        can_refuel = operational
    else:
        can_refuel = bool(armed) and (operational or fort)
    return {
        "hull_id":hid,
        "name":name,
        "can_refuel":can_refuel,
        "can_build_ships":operational,
        "max_buildable_ship_mass_kt":(
            SPACE_DOCK_MAX_BUILDABLE_SHIP_MASS_KT if hid==33 else None
        ),
        "is_orbital_fort":hid==32,
    }


def design_is_armed(slots) -> bool:
    """True when the fitted design carries at least one beam, torpedo, or bomb."""
    for slot in slots or []:
        if isinstance(slot, dict):
            category = slot.get("category", 0)
            count = slot.get("count", 0)
        else:
            category = getattr(slot, "category", 0)
            count = getattr(slot, "count", 0)
        try:
            if int(category or 0) in (16, 32, 64) and int(count or 0) > 0:
                return True
        except (TypeError, ValueError):
            continue
    return False


def can_starbase_build_ship(
    capabilities:dict|None,
    ship_mass_kt:int|None,
) -> bool:
    """Whether a known starbase can construct a ship of this design mass.

    A capped bay with an undecoded design mass is deliberately treated as
    ineligible.  Sending such a build to a Space Dock would otherwise create
    a queue the game cannot execute.
    """
    caps=capabilities or {}
    if not bool(caps.get("can_build_ships")):
        return False
    limit=caps.get("max_buildable_ship_mass_kt")
    if limit is None:
        return True
    if ship_mass_kt is None:
        return False
    return int(ship_mass_kt)<=int(limit)
