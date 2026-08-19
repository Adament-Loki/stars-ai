from stars_ai.player_tech import parse_player_block_race_tech
from stars_ai.standard_mod import parse_mod_text, legal_available_components_for_slot


def _player_block(levels=(3,3,3,3,3,3), prt=9):
    b = bytearray(128)
    b[26:32] = bytes(levels)
    b[76] = prt
    return bytes(b)


def test_player_block_reads_all_six_live_tech_levels():
    state = parse_player_block_race_tech(_player_block())
    assert state.tech.energy == 3
    assert state.tech.weapons == 3
    assert state.tech.propulsion == 3
    assert state.tech.construction == 3
    assert state.tech.electronics == 3
    assert state.tech.biotechnology == 3
    assert state.prt_id == 9


def test_slot_query_filters_by_tech_and_physical_legality():
    # scanner rows plus stock Scout hull row
    text = '''12,1,"Bat Scanner",1,0,0,0,0,0,0,2,1,1,0,1,59,0,0
12,2,"Rhino Scanner",2,0,0,0,0,1,0,5,3,3,0,2,48,50,0
12,3,"Mole Scanner",3,0,0,0,0,4,0,2,9,2,0,2,49,100,0
15,5,"Scout",4,0,0,0,0,0,0,8,10,4,2,4,16,0,50,20,1,1,2,1,6462,1,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,3,65,8,255,255,50,54,52,0,0,0,0,0,0,0,0,0,0,0,0,0'''
    db = parse_mod_text(text)
    tech = parse_player_block_race_tech(_player_block()).tech
    names = [c.name for c in legal_available_components_for_slot(4, 1, db, tech)]
    assert "Bat Scanner" in names
    assert "Rhino Scanner" in names
    assert "Mole Scanner" not in names  # Electronics 4 > player's 3
