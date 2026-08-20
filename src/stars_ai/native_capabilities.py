from __future__ import annotations
from dataclasses import dataclass

@dataclass(frozen=True)
class NativeCapability:
    semantic_action: str
    status: str
    reason: str

CAPABILITIES = {
    "move_fleet": NativeCapability("move_fleet","VALIDATED","WaypointAdd movement and sequential indexed route construction have been empirically validated."),
    "set_planet_queue": NativeCapability("set_planet_queue","VALIDATED","Production queue changes are validated, including Max Terraform auto-build; custom itemType=4 builds use native ship IDs 0..15 and starbase IDs 16..25 for existing designs."),
    "population_transfer": NativeCapability("population_transfer","PARTIAL","General population logistics are now planned; arbitrary load quantities remain an experimental extension of the observed 25 kT form."),
    "transport_population": NativeCapability("transport_population","PARTIAL","Destination Transport task unload-all is validated; source population quantity generalizes the observed 25 kT one-byte load to bounded 1..255 kT and requires host/client validation."),
    "colony_operation": NativeCapability("colony_operation","VALIDATED","Observed 25 kT (2,500-colonist) load + WaypointAdd + task 2 Colonize sequence."),
    "transport_minerals": NativeCapability("transport_minerals","VALIDATED","Small exact I/B/G load quantities are derived from two controlled Stars!-generated samples; destination Transport task unloads all cargo and loads optimal fuel."),
    "transport_unload_remainder": NativeCapability("transport_unload_remainder","PARTIAL","An identical active Transport task is preserved idempotently; synthetic task replacement is blocked pending native validation."),
    "research_change": NativeCapability("research_change","VALIDATED","ResearchChange byte 0 is the actual 15/25 percent; byte 1 packs next field in the high nibble and current field in the low nibble."),
    "set_planet_research_mode": NativeCapability("set_planet_research_mode","VALIDATED","PlanetChange leftover-only ON is the empirically observed 6-byte planet-id, 1, 0 form. OFF remains intentionally unsupported."),
    "player_relation_change": NativeCapability("player_relation_change","PARTIAL","Friend relation is empirically validated; Neutral/Enemy native writes remain disabled."),
    "set_player_relation": NativeCapability("set_player_relation","PARTIAL","Friend relation is empirically validated; Neutral/Enemy native writes remain disabled."),
    "set_battle_plan": NativeCapability("set_battle_plan","BLOCKED","SetFleetBattlePlan native writer remains unvalidated."),
    "create_design": NativeCapability("create_design","BLOCKED","Generic advisory proposals are not serialized. Exact native creation uses create_ship_design only."),
    "create_ship_design": NativeCapability("create_ship_design","PARTIAL","Embedded ship bodies are encoded and round-trip validated with the StarsAPI DesignBlock codec port. The two-byte Type27 create wrapper remains empirical (fresh slot-4 evidence: 11/A4 staging then 11/64 final); other free slots remain experimental until host/client validation."),
    "delete_ship_design": NativeCapability("delete_ship_design","PARTIAL","Existing design deletion uses the directly observed Type27 10/<slot> record, but emission is hard-blocked unless the slot has zero live ships, zero queued builds, and zero remaining production."),
    "replace_ship_design": NativeCapability("replace_ship_design","BLOCKED","Atomic delete+recreate is intentionally blocked. Replacement is a two-turn lifecycle: delete a provably dead design, verify next-M slot freedom, then create."),
    "modify_design": NativeCapability("modify_design","PARTIAL","Existing unused design slot modification remains separate from free-slot creation and is not used by the v8.7 autonomous utility designer."),
    "scrap_fleet": NativeCapability("scrap_fleet","PARTIAL","Semantics/rules understood; generalized native writer still requires validation."),
    "packet_order": NativeCapability("packet_order","BLOCKED","Packet object/order payload is not fully decoded."),
    "mine_order": NativeCapability("mine_order","PARTIAL","Mine tasks are understood at state level; generalized native task writing remains incomplete."),
}

def capability(action: str) -> NativeCapability:
    return CAPABILITIES.get(action,NativeCapability(action,"BLOCKED","No validated native serialization exists."))

def may_emit_native(action: str) -> bool:
    return capability(action).status=="VALIDATED"
