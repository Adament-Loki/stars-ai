
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

def starbase_capabilities(hull_id:int|None) -> dict:
    """
    Operational facilities are not equivalent to "some starbase exists".

    Orbital Fort:
      - may carry orbital components such as a gate
      - does NOT count as a shipyard
      - does NOT refuel orbiting fleets

    Space Dock / Space Station / Ultra Station / Death Star:
      - count as ship-production facilities
      - count as refuel facilities

    Space Dock can construct designs up to 200 kT.  Space Station, Ultra
    Station, and Death Star may construct designs of any mass.

    Unknown/custom hulls are conservative: no refuel/shipyard privilege until
    their capability is decoded.
    """
    hid=None if hull_id is None else int(hull_id)
    name=STARBASE_HULLS.get(hid, f"Hull#{hid}" if hid is not None else "None")
    operational = hid in (33,34,35,36)
    return {
        "hull_id":hid,
        "name":name,
        "can_refuel":operational,
        "can_build_ships":operational,
        "max_buildable_ship_mass_kt":(
            SPACE_DOCK_MAX_BUILDABLE_SHIP_MASS_KT if hid==33 else None
        ),
        "is_orbital_fort":hid==32,
    }


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
