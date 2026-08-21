
from types import SimpleNamespace
import pytest
from stars_ai.fuel_planner import design_fuel_profile,estimate_fuel,fastest_fuel_safe_warp,best_range_ly,reconnaissance_warp

def design(num,name,hull,slots): return SimpleNamespace(design_number=num,name=name,hull_id=hull,slots=[SimpleNamespace(category=c,item_id=i,count=n) for c,i,n in slots])

def test_faq_fuel_mizer_formula():
    fp={'groups':[{'mass':330,'engine_id':2}],'cargo_mass':0,'fuel':1400,'effective_fuel':1400,'fuel_capacity':1400,'all_ram_scoop':False}
    assert estimate_fuel(fp,1,8,ife=True)==pytest.approx(3.295875)

def test_cotton_picker_mass():
    p=design_fuel_profile(design(5,'Cotton Picker',21,[(1,3,1),(2,1,1),(128,1,1),(128,1,1)]),'miner')
    assert (p.dry_mass,p.fuel_capacity,p.engine_name)==(574,210,'Long Hump 6')

def test_armed_probe_drops_from_w8():
    p=design_fuel_profile(design(0,'Armed Probe',4,[(1,3,1),(2,1,1),(16,1,1)]),'scout').to_dict()
    fp={'groups':[{'mass':p['dry_mass'],'engine_id':p['engine_id']}],'cargo_mass':0,'fuel':50,'effective_fuel':50,'fuel_capacity':50,'all_ram_scoop':False}
    assert fastest_fuel_safe_warp(fp,60,'scan')==7

def test_fuel_mizer_scout_uses_fastest_fuel_safe_arrival_warp():
    fp={'groups':[{'mass':6,'engine_id':2}],'cargo_mass':0,'fuel':100,'effective_fuel':100,'fuel_capacity':100,'all_ram_scoop':False}
    assert reconnaissance_warp(fp,60,ife=True)==9

def test_heavy_miner_speed_is_distance_sensitive():
    p=design_fuel_profile(design(5,'Cotton Picker',21,[(1,3,1),(2,1,1),(128,1,1),(128,1,1)]),'miner').to_dict()
    fp={'groups':[{'mass':p['dry_mass'],'engine_id':p['engine_id']}],'cargo_mass':0,'fuel':210,'effective_fuel':210,'fuel_capacity':210,'all_ram_scoop':False}
    assert fastest_fuel_safe_warp(fp,43.4,'reposition_for_remote_mining')==6
    assert fastest_fuel_safe_warp(fp,60,'reposition_for_remote_mining')<=3

def test_long_range_scout_selected_for_range():
    a=design_fuel_profile(design(0,'Armed Probe',4,[(1,3,1),(2,1,1),(16,1,1)]),'scout').to_dict()
    l=design_fuel_profile(design(1,'Long Range Scout',4,[(1,3,1),(2,1,1),(4096,5,1)]),'scout').to_dict()
    assert l['fuel_capacity']==300 and best_range_ly(l,8)>best_range_ly(a,8)*4
