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
    "remote_mine": NativeCapability("remote_mine","PARTIAL","A native client capture validates Type-5 task 3 on the stationary current planet waypoint (index 0, warp 0). The writer emits only that exact transition and logs every use."),
    "lay_minefield": NativeCapability("lay_minefield","PARTIAL","A turn-100 client capture validates Type-5 Lay Minefield task 6 with subtask 5, both for a stationary deep-space waypoint and a planet destination. AI emissions preserve those exact forms and remain host-validation traced."),
    "set_planet_queue": NativeCapability("set_planet_queue","VALIDATED","Production queue changes are validated, including Max Terraform auto-build; custom itemType=4 builds use native ship IDs 0..15 and starbase IDs 16..25 for existing designs."),
    "population_transfer": NativeCapability("population_transfer","EXPERIMENTAL","The captured 200 kT transfer uses the host-accepted Type-2 client form. Other positive u16 quantities are enabled with exact block-sequence trace logging."),
    "transport_population": NativeCapability("transport_population","EXPERIMENTAL","The 80 kT Type-1 source-planet load plus the client-controlled 0x51 Transport endpoint that unloads all I/B/G/population is host-validated end-to-end. Type-2 loads from 128 kT and other quantities retain exact per-emission tracing until their own end-to-end replay."),
    "transport_population_with_minerals": NativeCapability("transport_population_with_minerals","PARTIAL","A native client capture proves one Type-2 medium load with selected B/G/Population u16 quantities immediately after a Type37 fleet merge. The writer uses the client-controlled Transport task that unloads all I/B/G/population, with an exact trace for every emission."),
    "colony_operation": NativeCapability("colony_operation","VALIDATED","Observed 25 kT (2,500-colonist) load + WaypointAdd + task 2 Colonize sequence."),
    "transport_minerals": NativeCapability("transport_minerals","VALIDATED","Small exact I/B/G load quantities are derived from two controlled Stars!-generated samples; destination Transport task unloads all cargo and loads optimal fuel."),
    "merge_fleets": NativeCapability("merge_fleets","PARTIAL","A native client capture validates Type37 target fleet 7 followed by source fleet 8, then a mixed Type-2 load and waypoint in the same X file. The writer emits that layout with exact-byte trace logging."),
    "fleet_split": NativeCapability("fleet_split","PARTIAL","latestdemo turn-040 Player-1 client capture observes Type24 FleetSplit followed immediately by a nine-byte Type23 MoveShips block. The writer preserves only a same-turn, FileHash-checked, owned-source pair verbatim and reserves its source fleet."),
    "split_scout_fleet": NativeCapability("split_scout_fleet","PARTIAL","Each excess hull emits the Player-1 turn-040 capture: Type24 source fleet, then Type23 source, new fleet, 0x22, design-slot mask, and a negative ship count. The earlier destination-first positive count is the form Stars! rejected as a corrupt X file."),
    "split_fleet": NativeCapability("split_fleet","PARTIAL","Same captured Type24/Type23 pair as split_scout_fleet, for any stationary cargo-free stack. One ship moves into each newly allocated fleet id so the next turn can route the hulls separately."),
    "transport_unload_remainder": NativeCapability("transport_unload_remainder","PARTIAL","A cargo-bearing freighter already orbiting its owned destination receives the captured Type-5 current-waypoint Transport task with all I/B/G/population unload fields. The transaction is exact-traced pending a dedicated host replay."),
    "research_change": NativeCapability("research_change","VALIDATED","ResearchChange byte 0 is the actual 15/25 percent; byte 1 packs next field in the high nibble and current field in the low nibble."),
    "set_planet_research_mode": NativeCapability("set_planet_research_mode","VALIDATED","PlanetChange leftover-only ON is the empirically observed 6-byte planet-id, 1, 0 form. OFF remains intentionally unsupported."),
    "player_relation_change": NativeCapability("player_relation_change","PARTIAL","Friend relation is empirically validated; Neutral/Enemy native writes remain disabled."),
    "set_player_relation": NativeCapability("set_player_relation","PARTIAL","Friend relation is empirically validated; Neutral/Enemy native writes remain disabled."),
    "set_battle_plan": NativeCapability("set_battle_plan","BLOCKED","SetFleetBattlePlan native writer remains unvalidated."),
    "create_design": NativeCapability("create_design","PARTIAL","Generic ship and starbase proposals are compiled only after every hull and component passes current research plus PRT/LRT eligibility. A starbase needs a populated existing same-hull design to seed the Player-4 captured two-record Type-27 form: player-owned Bn populated seed, then 7n populated commit, followed by Save+Submit. Both full records use the host-validated five-byte visible Base NN wire name; descriptive renames need a separately validated lifecycle. Other starbase lifecycle variants and ship-design lifecycle coverage remain partial."),
    "create_ship_design": NativeCapability("create_ship_design","PARTIAL","Embedded bodies use the round-trip-validated StarsAPI DesignBlock codec. The isolated P1 01/A4 staging-and-final pair is host-accepted; P2 uses its owner-aware 11/A4 form, pending a dedicated replay. Other free slots remain experimental until host/client validation."),
    "delete_ship_design": NativeCapability("delete_ship_design","PARTIAL","Deletion uses the owner-aware two-byte form (P1 00/<slot>, P2 10/<slot>). It is blocked unless the slot has zero live ships, zero queued builds, and zero remaining production. A disposable letstest host consumed Player 1 00 08 and removed that unused ship design."),
    "delete_starbase_design": NativeCapability("delete_starbase_design","PARTIAL","Starbase deletion is the ship delete form with bit 4 of the slot byte set (P1 slot 1 is 00 11). It is blocked while the design is installed, queued, or still has remaining production. A disposable letstest host consumed 00 11 and removed an uninstalled Orbital Fort."),
    "replace_ship_design": NativeCapability("replace_ship_design","BLOCKED","Atomic delete+recreate is intentionally blocked. Replacement is a two-turn lifecycle: delete a provably dead design, verify next-M slot freedom, then create."),
    "modify_design": NativeCapability("modify_design","PARTIAL","Existing unused design slot modification remains separate from free-slot creation and is not used by the v8.7 autonomous utility designer."),
    "scrap_fleet": NativeCapability("scrap_fleet","PARTIAL","Semantics/rules understood; generalized native writer still requires validation."),
    "packet_order": NativeCapability("packet_order","BLOCKED","Packet object/order payload is not fully decoded."),
    "mine_order": NativeCapability("mine_order","PARTIAL","Mine tasks are understood at state level; generalized native task writing remains incomplete."),
}

def capability(action: str) -> NativeCapability:
    return CAPABILITIES.get(action,NativeCapability(action,"BLOCKED","No validated native serialization exists."))

def may_emit_native(action: str) -> bool:
    return capability(action).status in {"VALIDATED", "PARTIAL", "EXPERIMENTAL"}
