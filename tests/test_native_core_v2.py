from stars_ai.native.race import parse_full_race_data
from stars_ai.native.objects import parse_object
from stars_ai.native.battle_plan import parse_battle_plan
from stars_ai.native.production import parse_queue


def test_mt_mask_byte_order_matches_starsapi_setter():
    full=bytearray(0x68)
    full[18:24]=bytes([3,3,3,3,3,3])
    full[68]=9
    full[74]=0x0D
    full[75]=0x00
    race=parse_full_race_data(bytes(full))
    assert race.mt_mask == 0x0D00


def test_mystery_trader_item_mapping():
    # object id: type 3 in high bits; enough bytes for MT payload
    d=bytearray(18)
    oid=(3<<13)
    d[0:2]=oid.to_bytes(2,'little')
    d[2:4]=(1000).to_bytes(2,'little'); d[4:6]=(1100).to_bytes(2,'little')
    d[6:8]=(1200).to_bytes(2,'little'); d[8:10]=(1300).to_bytes(2,'little')
    d[10]=9
    d[14:16]=(1<<11).to_bytes(2,'little')
    obj=parse_object(bytes(d))
    assert obj.object_kind == 'MysteryTrader'
    assert 'Jump Gate' in obj.fields['items']


def test_battle_plan_decode():
    # owner 0, plan 2, tactic 3; any target; enemies
    w0=(2<<4)|(3<<8)
    w1=1|(3<<4)|(1<<8)
    d=w0.to_bytes(2,'little')+w1.to_bytes(2,'little')
    bp=parse_battle_plan(d)
    assert bp.plan_id==2 and bp.tactic_name=='Maximize net damage'
    assert bp.primary_target_name=='Any' and bp.attack_who_name=='Enemies'


def test_production_queue_decode():
    c1=(7<<10)|5
    c2=(0<<4)|2
    q=parse_queue(c1.to_bytes(2,'little')+c2.to_bytes(2,'little'))
    assert q[0].item_name=='Factory' and q[0].count==5
