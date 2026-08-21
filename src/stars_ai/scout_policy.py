"""Shared policy for the transition from broad scouting to targeted recon."""
from __future__ import annotations

from typing import Any

from .util import distance
from .planetary_scanners import deployed_planetary_sensor_network


def enemy_contact_summary(state: Any) -> dict[str, Any]:
    """Return visible foreign fleet/planet contact that ends classic scouting.

    Stars! intelligence is incomplete, so a foreign owner is treated as a
    strategic contact until diplomacy has enough evidence to classify it as a
    friendly ally. This avoids continuing to build unarmed wide-area scouts
    after the empire has reached another player's space.
    """
    player_id=int(getattr(state,"player_id",0) or 0)
    foreign_fleets=[
        fleet for fleet in (getattr(state,"fleets",[]) or [])
        if getattr(fleet,"owner",None) not in (None,player_id)
    ]
    foreign_planets=[
        planet for planet in (getattr(state,"planets",[]) or [])
        if getattr(planet,"owner",None) not in (None,player_id)
    ]
    owners=sorted({
        int(getattr(item,"owner"))
        for item in [*foreign_fleets,*foreign_planets]
        if getattr(item,"owner",None) is not None
    })
    return {
        "enemy_contact":bool(foreign_fleets or foreign_planets),
        "foreign_owner_ids":owners,
        "foreign_fleet_count":len(foreign_fleets),
        "foreign_planet_count":len(foreign_planets),
    }


def custom_scout_missions(state: Any, *, support_distance: float|None=None) -> list[dict]:
    """Return explicit or contact-derived missions that justify a new scout.

    A border-recon mission is created for an unobserved world near a visible
    foreign fleet/planet.  There is no territorial-radius veto: route fuel
    safety decides whether a scout can get there.  A world already covered by a
    live penetrating planetary scanner does not consume a scout assignment.
    Callers may additionally provide native ``custom_scout_missions`` records
    with a stable ``id`` and a stated purpose.
    """
    native=getattr(state,"native",{}) or {}
    missions=[]
    for index, raw in enumerate(native.get("custom_scout_missions",[]) or []):
        if not isinstance(raw,dict):
            continue
        purpose=str(raw.get("purpose") or raw.get("kind") or "custom_recon")
        mission_id=str(raw.get("id") or f"custom-{index}-{purpose}")
        missions.append({
            "id":mission_id,
            "kind":str(raw.get("kind") or "custom_recon"),
            "purpose":purpose,
            "target_planet_id":raw.get("target_planet_id"),
            "priority":int(raw.get("priority",110) or 110),
            "source":"explicit",
        })

    contact=enemy_contact_summary(state)
    if not contact["enemy_contact"]:
        return missions

    owned=[p for p in (getattr(state,"planets",[]) or []) if getattr(p,"owner",None)==getattr(state,"player_id",None)]
    sensor_network=deployed_planetary_sensor_network(state)
    penetrating_coverage={
        int(pid) for pid in sensor_network.get("penetrating_covered_planet_ids", [])
    }
    contacts=[
        item for item in [*(getattr(state,"fleets",[]) or []),*(getattr(state,"planets",[]) or [])]
        if getattr(item,"owner",None) not in (None,getattr(state,"player_id",None))
    ]
    for planet in getattr(state,"planets",[]) or []:
        if bool(getattr(planet,"observed",False)):
            continue
        if not owned:
            continue
        if int(planet.id) in penetrating_coverage:
            continue
        nearest_support=min(distance(planet.position,base.position) for base in owned)
        if support_distance is not None and nearest_support>float(support_distance):
            continue
        nearest_contact=min((distance(planet.position,contact.position) for contact in contacts),default=9999.0)
        if nearest_contact>120.0:
            continue
        missions.append({
            "id":f"border-recon-{int(planet.id)}",
            "kind":"border_recon",
            "purpose":"identify systems adjacent to visible foreign forces or territory",
            "target_planet_id":int(planet.id),
            "priority":130,
            "source":"enemy_contact",
            "contact_distance":round(float(nearest_contact),2),
            "nearest_owned_distance":round(float(nearest_support),2),
        })
    return missions
