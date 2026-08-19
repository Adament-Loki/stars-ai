
from __future__ import annotations
from dataclasses import dataclass

@dataclass(frozen=True)
class NativeCapability:
    semantic_action: str
    status: str
    reason: str

CAPABILITIES = {
    "move_fleet": NativeCapability("move_fleet","VALIDATED","Novel fleet movement has been empirically validated."),
    "set_planet_queue": NativeCapability("set_planet_queue","VALIDATED","Production queue changes are validated; custom itemType=4 builds of existing ship designs are enabled from StarsAPI plus controlled X samples."),
    "population_transfer": NativeCapability("population_transfer","PARTIAL","General transport remains incomplete; scoped observed forms are supported."),
    "colony_operation": NativeCapability("colony_operation","VALIDATED","Observed 25k load + WaypointAdd + task 2 Colonize sequence."),
    "transport_minerals": NativeCapability("transport_minerals","VALIDATED","Small exact I/B/G load quantities are derived from two controlled Stars!-generated samples; destination Transport task unloads all cargo and loads optimal fuel."),
    "transport_unload_remainder": NativeCapability("transport_unload_remainder","VALIDATED","Recovery reuses the complete validated Transport policy: Unload All I/B/G/Population + Load Optimal fuel."),
    "research_change": NativeCapability("research_change","VALIDATED","ResearchChange uses 0F plus field code 60..65; Propulsion/Electronics/Biotechnology empirically confirmed and remaining fields follow the contiguous mapping."),
    "player_relation_change": NativeCapability("player_relation_change","PARTIAL","Friend relation is empirically validated; Neutral/Enemy native writes remain disabled."),
    "set_player_relation": NativeCapability("set_player_relation","PARTIAL","Friend relation is empirically validated; Neutral/Enemy native writes remain disabled."),
    "set_battle_plan": NativeCapability("set_battle_plan","BLOCKED","SetFleetBattlePlan native writer remains unvalidated."),
    "create_design": NativeCapability("create_design","BLOCKED","Brand-new design creation has produced corrupt files; modify existing editable slot only."),
    "modify_design": NativeCapability("modify_design","PARTIAL","Existing unused design slot modification has been empirically validated for scoped changes."),
    "scrap_fleet": NativeCapability("scrap_fleet","PARTIAL","Semantics/rules understood; generalized native writer still requires validation."),
    "packet_order": NativeCapability("packet_order","BLOCKED","Packet object/order payload is not fully decoded."),
    "mine_order": NativeCapability("mine_order","PARTIAL","Mine tasks are understood at state level; generalized native task writing remains incomplete."),
}

def capability(action: str) -> NativeCapability:
    return CAPABILITIES.get(action,NativeCapability(action,"BLOCKED","No validated native serialization exists."))

def may_emit_native(action: str) -> bool:
    return capability(action).status=="VALIDATED"
