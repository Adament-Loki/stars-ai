
from __future__ import annotations

STARBASE_HULLS = {
    32: "Orbital Fort",
    33: "Space Dock",
    34: "Space Station",
    35: "Ultra Station",
    36: "Death Star",
}

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
        "is_orbital_fort":hid==32,
    }
