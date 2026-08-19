
from __future__ import annotations

TURN_ORDER = [
    "scrap_fleets",
    "waypoint0_manual_load",
    "waypoint0_unload",
    "waypoint0_colonization_ground_combat",
    "waypoint0_task_load",
    "waypoint0_other_tasks",
    "mystery_trader_moves",
    "existing_packets_move_decay",
    "wormhole_entry_jiggle",
    "fleet_movement",
    "inner_strength_fleet_population_growth",
    "salvage_and_inspace_packet_decay",
    "wormhole_exit_jiggle",
    "wormhole_degrade_jump",
    "space_demolition_mine_detonation",
    "mining",
    "production_and_research",
    "super_stealth_spy_bonus",
    "planet_population_growth",
    "new_packet_impact",
    "random_events",
    "fleet_battles",
    "meet_mystery_trader",
    "bombing",
    "depopulate_planets",
    "waypoint1_unload",
    "waypoint1_colonization_ground_combat",
    "waypoint1_load",
    "mine_laying",
    "fleet_transfer",
    "waypoint1_fleet_merge",
    "claim_adjuster_instaforming",
    "minefield_decay",
    "mine_sweeping",
    "starbase_and_fleet_repair",
    "remote_terraforming",
]

TURN_INDEX = {name: i for i, name in enumerate(TURN_ORDER)}

def happens_before(a: str, b: str) -> bool:
    return TURN_INDEX[a] < TURN_INDEX[b]

def can_react_same_turn(threat_event: str, response_event: str) -> bool:
    """
    A response only helps if its event executes before the threat.
    """
    return happens_before(response_event, threat_event)
