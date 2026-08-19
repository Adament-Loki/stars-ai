
from __future__ import annotations
from dataclasses import dataclass, field

@dataclass
class RaceDoctrine:
    prt: str
    objective_modifiers: dict[str,float]=field(default_factory=dict)
    tactical_rules: list[str]=field(default_factory=list)

def doctrine_for(primary_trait: str) -> RaceDoctrine:
    key=(primary_trait or "unknown").strip().upper()
    table={
      "HE": RaceDoctrine("HE",{"expand":1.25,"gates":0.0,"mobility":0.85},["Cannot rely on stargates; value fuel/range and distributed production more highly."]),
      "SS": RaceDoctrine("SS",{"intelligence":1.35,"stealth":1.45},["Use cloaking and uncertainty asymmetry; value surprise and information denial."]),
      "WM": RaceDoctrine("WM",{"attack":1.35,"combat":1.25},["Exploit military tempo and favorable combat opportunities."]),
      "CA": RaceDoctrine("CA",{"terraform":1.6,"economy":1.15},["Treat terraforming as a core economic weapon and strategic capability."]),
      "IS": RaceDoctrine("IS",{"population_logistics":1.5,"transport":1.25},["Exploit population growth in fleets and overflow timing."]),
      "SD": RaceDoctrine("SD",{"mine_warfare":1.7,"defend":1.2},["Use minefields as territorial control and timing weapons."]),
      "PP": RaceDoctrine("PP",{"packets":1.8,"mineral_logistics":1.3},["Use packets for warfare/logistics and account for PP decay/overhead advantages."]),
      "IT": RaceDoctrine("IT",{"gates":1.8,"mobility":1.45},["Build gate network early and evaluate controlled overgating for strategic reinforcement."]),
      "AR": RaceDoctrine("AR",{"starbases":1.8,"defend":1.35},["Starbase survival is existential; avoid unsupported base exposure to chaff-supported capital fleets."]),
      "JOAT": RaceDoctrine("JOAT",{"intelligence":1.15,"balanced":1.1},["Exploit broad information and flexible generalist development."]),
    }
    return table.get(key,RaceDoctrine(key,{},["Use generic balanced doctrine until race-specific behavior is identified."]))
