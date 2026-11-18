from __future__ import annotations
from dataclasses import dataclass

@dataclass(frozen=True)
class NativeCapability:
    semantic_action: str
    status: str
    reason: str

CAPABILITIES = {
    "move_fleet": NativeCapability("move_fleet","VALIDATED","WaypointAdd movement and sequential indexed route construction have been empirically validated."),
    "remote_mine": NativeCapability("remote_mine","PARTIAL","Stationary remote mining is Type 5 index 0, warp 0, task 3. A move to a planet is Type 4 task 0 then Type 5 task 3 at the same warp, using the current waypoint object type."),
    "lay_minefield": NativeCapability("lay_minefield","PARTIAL","Task 6 subtask 5 lays mines. A ship already at the planet changes waypoint 0. A ship elsewhere gets a Type 4 move and then Type 5 task 6 with extra 05, as in the examples2 Charity order."),
    "intercept_fleet": NativeCapability("intercept_fleet","EXPERIMENTAL","A fleet waypoint uses object type 0x12 and the target fleet's owner-tagged id. examples2 Patrol fleet 3 targets Ubert fleet 17 as 11 04. Task stays 0."),
    "set_planet_queue": NativeCapability("set_planet_queue","VALIDATED","Production queue changes are validated, including Max Terraform auto-build; custom itemType=4 builds use native ship IDs 0..15 and starbase IDs 16..25 for existing designs."),
    "population_transfer": NativeCapability("population_transfer","EXPERIMENTAL","The captured 200 kT transfer uses the host-accepted Type-2 client form. Other positive u16 quantities are enabled with exact block-sequence trace logging."),
    "transport_population": NativeCapability("transport_population","EXPERIMENTAL","The 80 kT Type-1 source-planet load plus the client-controlled 0x51 Transport endpoint that unloads all I/B/G/population is host-validated end-to-end. Type-2 loads from 128 kT and other quantities retain exact per-emission tracing until their own end-to-end replay."),
    "transport_population_with_minerals": NativeCapability("transport_population_with_minerals","PARTIAL","A native client capture proves one Type-2 medium load with selected B/G/Population u16 quantities immediately after a Type37 fleet merge. The writer uses the client-controlled Transport task that unloads all I/B/G/population, with an exact trace for every emission."),
    "colony_operation": NativeCapability("colony_operation","VALIDATED","Observed 25 kT (2,500-colonist) load + WaypointAdd + task 2 Colonize sequence."),
    "transport_minerals": NativeCapability("transport_minerals","VALIDATED","Small exact I/B/G load quantities are derived from two controlled Stars!-generated samples; destination Transport task unloads all cargo and loads optimal fuel."),
    "merge_fleets": NativeCapability("merge_fleets","PARTIAL","A native client capture validates Type37 target fleet 7 followed by source fleet 8, then a mixed Type-2 load and waypoint in the same X file. The writer emits that layout with exact-byte trace logging."),
    "merge_with_fleet": NativeCapability("merge_with_fleet","EXPERIMENTAL","examples2 fleet 6 merges with fleet 0 by a Type 4 task-0 waypoint at that fleet's planet, then a Type 5 with task 4, object 0x12, and the owner-tagged fleet id. Type 37 remains the merge for fleets already together."),
    "fleet_split": NativeCapability("fleet_split","PARTIAL","latestdemo turn-040 Player-1 client capture observes Type24 FleetSplit followed immediately by a nine-byte Type23 MoveShips block. The writer preserves only a same-turn, FileHash-checked, owned-source pair verbatim and reserves its source fleet."),
    "split_scout_fleet": NativeCapability("split_scout_fleet","PARTIAL","Each excess hull emits the Player-1 turn-040 capture: Type24 source fleet, then Type23 source, new fleet, 0x22, design-slot mask, and a negative ship count. The earlier destination-first positive count is the form Stars! rejected as a corrupt X file."),
    "split_fleet": NativeCapability("split_fleet","PARTIAL","Same captured Type24/Type23 pair as split_scout_fleet, for any stationary cargo-free stack. One ship moves into each newly allocated fleet id so the next turn can route the hulls separately."),
    "transport_unload_remainder": NativeCapability("transport_unload_remainder","PARTIAL","A cargo-bearing freighter already orbiting its owned destination receives the captured Type-5 current-waypoint Transport task with all I/B/G/population unload fields. The transaction is exact-traced pending a dedicated host replay."),
    "research_change": NativeCapability("research_change","VALIDATED","ResearchChange byte 0 is the actual 15/25 percent; byte 1 packs next field in the high nibble and current field in the low nibble."),
    "set_planet_research_mode": NativeCapability("set_planet_research_mode","VALIDATED","PlanetChange leftover-only ON and OFF are the 6-byte planet-id, flag, 0 form. A disposable humanplus host stored ON, then cleared it on the next turn. A fling uses the same Type 35 record with bit 0 as this flag, so the writer emits one combined dword when both are requested."),
    "player_relation_change": NativeCapability("player_relation_change","VALIDATED","Type 38 friend, neutral, and enemy values are 1, 0, and 2. A disposable host stored Enemy for player 2 and advanced the year."),
    "set_player_relation": NativeCapability("set_player_relation","VALIDATED","Type 38 friend, neutral, and enemy values are 1, 0, and 2. A disposable host stored Enemy for player 2 and advanced the year. Humans never receive a relation write."),
    "set_battle_plan": NativeCapability("set_battle_plan","VALIDATED","Type 42 is fleet id then plan index. A disposable host stored plan index 1 on the fleet and advanced the year. The planner only returns armed fleets to plan 0."),
    "scrap_fleet": NativeCapability("scrap_fleet","VALIDATED","Waypoint 0 task 5 scrapped an idle fleet on a disposable humanplus host. The planner scraps only an extra empty freighter at an owned starbase."),
    "patrol_waypoint": NativeCapability("patrol_waypoint","VALIDATED","A move with mission patrol writes waypoint task 7. A disposable host stored task 7 and the destination planet."),
    "repeat_fleet_orders": NativeCapability("repeat_fleet_orders","PARTIAL","Type 10 fleet id plus a u16 flag was consumed and the year advanced. The repeat bit is not separately decoded, so the order stays traced."),
    "bomb_orbit": NativeCapability("bomb_orbit","VALIDATED","Bombing is the host's post-arrival phase. The order is a validated task-0 move onto the enemy world."),
    "rename_fleet": NativeCapability("rename_fleet","VALIDATED","Type 44 uncompressed form is owner-tagged fleet id, class word 2, a zero compression byte, and a C string. A disposable host stored that name and the next M file's Type 21 record decoded to Gap."),
    "create_design": NativeCapability("create_design","PARTIAL","Generic ship and starbase proposals are compiled only after every hull and component passes current research plus PRT/LRT eligibility. A starbase needs a populated existing same-hull design to seed the Player-4 captured two-record Type-27 form: player-owned Bn populated seed, then 7n populated commit, followed by Save+Submit. Both full records use the host-validated five-byte visible Base NN wire name; descriptive renames need a separately validated lifecycle. Other starbase lifecycle variants and ship-design lifecycle coverage remain partial."),
    "create_ship_design": NativeCapability("create_ship_design","PARTIAL","Embedded bodies use the round-trip-validated StarsAPI DesignBlock codec. The isolated P1 01/A4 staging-and-final pair is host-accepted; P2 uses its owner-aware 11/A4 form, pending a dedicated replay. Other free slots remain experimental until host/client validation."),
    "delete_ship_design": NativeCapability("delete_ship_design","PARTIAL","Deletion uses the owner-aware two-byte form (P1 00/<slot>, P2 10/<slot>). It is blocked unless the slot has zero live ships, zero queued builds, and zero remaining production. A disposable letstest host consumed Player 1 00 08 and removed that unused ship design."),
    "delete_starbase_design": NativeCapability("delete_starbase_design","PARTIAL","Starbase deletion is the ship delete form with bit 4 of the slot byte set (P1 slot 1 is 00 11). It is blocked while the design is installed, queued, or still has remaining production. A disposable letstest host consumed 00 11 and removed an uninstalled Orbital Fort."),
    "replace_ship_design": NativeCapability("replace_ship_design","BLOCKED","A design that has built a ship or starbase cannot be edited. Copy it into a free slot. An unused ship slot uses edit_ship_design, one Type 27 record with control 0x60 plus the slot."),
    "edit_ship_design": NativeCapability("edit_ship_design","VALIDATED","examples2 edited Destroyer slot 3 with one Type 27 record, control 01 63, while the built count was 0. A disposable host stored the Energy Capacitor."),
    "copy_starbase_design": NativeCapability("copy_starbase_design","EXPERIMENTAL","examples copied Starbase into slot 2 as Type 27 01 B2 then 01 72, both named Starbase (2). The writer emits that pair. A host replay of our own bytes is still outstanding."),
    "route_ships": NativeCapability("route_ships","VALIDATED","Type 35 bits 15-24 are the ship-route display id. A disposable host of Data to Down And Out stored has_route and route display id 62."),
    "send_message": NativeCapability("send_message","EXPERIMENTAL","examples Type 40 hello is two zero words, the recipient, 1, 0, a u16 length, and the nibble text. The constant 1 and the zero words are copied from that one message."),
    "repeat_transport": NativeCapability("repeat_transport","EXPERIMENTAL","examples2 fleet 6 repeats a warp-9 transport. Charity is load-all ironium 00 10 and Hot Tip is unload-all ironium 00 20, then Type 10 flag 01 00."),
    "modify_design": NativeCapability("modify_design","PARTIAL","Existing unused design slot modification remains separate from free-slot creation and is not used by the v8.7 autonomous utility designer."),
    "fling_minerals": NativeCapability("fling_minerals","VALIDATED","Type 35 packs the destination display id in bits 1-10 and warp-4 in bits 11-14. Warp 5 is nibble 1 and warp 13 is nibble 9. A disposable copy of the packet example hosted a warp-8 packet to planet 75 and a warp-5 packet to planet 90."),
    "packet_order": NativeCapability("packet_order","VALIDATED","Mineral packet queue ids 14-17 use the factory item encoding. On a disposable host, one mixed packet queued with a fling was in flight the next year."),
    "mine_order": NativeCapability("mine_order","PARTIAL","Mine tasks are understood at state level; generalized native task writing remains incomplete."),
}

def capability(action: str) -> NativeCapability:
    return CAPABILITIES.get(action,NativeCapability(action,"BLOCKED","No validated native serialization exists."))

def may_emit_native(action: str) -> bool:
    return capability(action).status in {"VALIDATED", "PARTIAL", "EXPERIMENTAL"}
