from __future__ import annotations
from dataclasses import dataclass

@dataclass(frozen=True)
class NativeCapability:
    semantic_action: str
    status: str
    reason: str

CAPABILITIES = {
    "move_fleet": NativeCapability("move_fleet","VALIDATED","WaypointAdd movement and sequential indexed route construction have been empirically validated."),
    "intercept_fleet": NativeCapability("intercept_fleet","EXPERIMENTAL","StarsAPI documents Waypoint targetType=2 as a fleet. Territorial patrols may emit a single direct 0x12 target-fleet WaypointAdd when the owned fleet has no existing destination; each block and target owner/id is traced for host validation."),
    "remote_mine": NativeCapability("remote_mine","PARTIAL","Client capture sandbox/GAME.x2 validates Type-5 task 3 on the stationary current planet waypoint (index 0, warp 0). The writer emits only that exact transition and logs every use."),
    "lay_minefield": NativeCapability("lay_minefield","EXPERIMENTAL","A stationary minelayer at an owned territorial anchor uses the same Type-5 current-waypoint form as remote mining, with the inferred Stars! Lay Mines task 4. Every attempted deployment records its full block bytes and precondition trace for host validation."),
    "set_planet_queue": NativeCapability("set_planet_queue","VALIDATED","Production queue changes are validated, including Max Terraform auto-build; custom itemType=4 builds use native ship IDs 0..15 and starbase IDs 16..25 for existing designs."),
    "population_transfer": NativeCapability("population_transfer","EXPERIMENTAL","The captured 200 kT transfer uses the host-accepted Type-2 client form. Other positive u16 quantities are enabled with exact block-sequence trace logging."),
    "transport_population": NativeCapability("transport_population","EXPERIMENTAL","P1's 200 kT Type-2 load plus the matching population task is host-accepted. Other quantities and mixed population/mineral loads are enabled experiments with exact encoded-byte logging."),
    "transport_population_with_minerals": NativeCapability("transport_population_with_minerals","PARTIAL","sandbox/GAME.x1 client capture proves one Type-2 medium load with selected B/G/Population u16 quantities immediately after a Type37 fleet merge. The writer generalizes its selection mask for I/B/G/Population and adds the observed Transport unload/refuel task, with an exact trace for every emission."),
    "colony_operation": NativeCapability("colony_operation","VALIDATED","Observed 25 kT (2,500-colonist) load + WaypointAdd + task 2 Colonize sequence."),
    "transport_minerals": NativeCapability("transport_minerals","VALIDATED","Small exact I/B/G load quantities are derived from two controlled Stars!-generated samples; destination Transport task unloads all cargo and loads optimal fuel."),
    "merge_fleets": NativeCapability("merge_fleets","PARTIAL","sandbox/GAME.x1 client capture validates Type37 target fleet 7 followed by source fleet 8, then a mixed Type-2 load and waypoint in the same X file. The writer emits that layout with exact-byte trace logging."),
    "transport_unload_remainder": NativeCapability("transport_unload_remainder","PARTIAL","An identical active Transport task is preserved idempotently; synthetic task replacement is blocked pending native validation."),
    "research_change": NativeCapability("research_change","VALIDATED","ResearchChange byte 0 is the actual 15/25 percent; byte 1 packs next field in the high nibble and current field in the low nibble."),
    "set_planet_research_mode": NativeCapability("set_planet_research_mode","VALIDATED","PlanetChange leftover-only ON is the empirically observed 6-byte planet-id, 1, 0 form. OFF remains intentionally unsupported."),
    "player_relation_change": NativeCapability("player_relation_change","PARTIAL","Friend relation is empirically validated; Neutral/Enemy native writes remain disabled."),
    "set_player_relation": NativeCapability("set_player_relation","PARTIAL","Friend relation is empirically validated; Neutral/Enemy native writes remain disabled."),
    "set_battle_plan": NativeCapability("set_battle_plan","BLOCKED","SetFleetBattlePlan native writer remains unvalidated."),
    "create_design": NativeCapability("create_design","PARTIAL","Generic ship and starbase proposals are compiled only after every hull and component passes current research plus PRT/LRT eligibility. Starbases use their independent free 0..9 slot namespace and a StarsAPI isStarbase body; their owner-aware Type-27 wrapper extension is explicitly traced pending a dedicated client replay."),
    "create_ship_design": NativeCapability("create_ship_design","PARTIAL","Embedded bodies use the round-trip-validated StarsAPI DesignBlock codec. The isolated P1 01/A4 staging-and-final pair is host-accepted; P2 uses its owner-aware 11/A4 form, pending a dedicated replay. Other free slots remain experimental until host/client validation."),
    "delete_ship_design": NativeCapability("delete_ship_design","PARTIAL","Deletion uses the owner-aware existing-design form (P1 00/<slot>, P2 10/<slot>) and is blocked unless the slot has zero live ships, zero queued builds, and zero remaining production. Dedicated host validation is still required."),
    "replace_ship_design": NativeCapability("replace_ship_design","BLOCKED","Atomic delete+recreate is intentionally blocked. Replacement is a two-turn lifecycle: delete a provably dead design, verify next-M slot freedom, then create."),
    "modify_design": NativeCapability("modify_design","PARTIAL","Existing unused design slot modification remains separate from free-slot creation and is not used by the v8.7 autonomous utility designer."),
    "scrap_fleet": NativeCapability("scrap_fleet","PARTIAL","Semantics/rules understood; generalized native writer still requires validation."),
    "packet_order": NativeCapability("packet_order","BLOCKED","Packet object/order payload is not fully decoded."),
    "mine_order": NativeCapability("mine_order","PARTIAL","Mine tasks are understood at state level; generalized native task writing remains incomplete."),
}

def capability(action: str) -> NativeCapability:
    return CAPABILITIES.get(action,NativeCapability(action,"BLOCKED","No validated native serialization exists."))

def may_emit_native(action: str) -> bool:
    # PARTIAL and EXPERIMENTAL capabilities are intentionally executable when
    # their writer has an explicit codec. Their emitted payload must carry the
    # trust level and exact bytes so a host/client failure is attributable.
    return capability(action).status in {"VALIDATED", "PARTIAL", "EXPERIMENTAL"}
