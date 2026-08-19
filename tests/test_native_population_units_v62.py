
from pathlib import Path

def test_adapter_normalizes_native_planet_population_hundreds():
    src=Path(__file__).parents[1]/'src'/'stars_ai'/'adapters'/'native_core_adapter.py'
    text=src.read_text(encoding='utf-8')
    assert 'population=int(p.population or 0) * 100' in text
    assert "'population_raw_hundreds': int(p.population or 0)" in text

def test_fleet_colonist_cargo_keeps_thousand_colonist_units():
    src=Path(__file__).parents[1]/'src'/'stars_ai'/'adapters'/'native_core_adapter.py'
    text=src.read_text(encoding='utf-8')
    # Fleet cargo is a different native representation; our empirically
    # validated 25k colony load still uses value 25.
    assert 'cargo_population=int(f.population or 0) * 1000' in text
