
from pathlib import Path

from stars_ai.population_units import (
    COLONISTS_PER_CARGO_KT,
    COLONY_LOAD_COLONISTS,
    COLONY_LOAD_KT,
    colonists_from_cargo_kt,
)

def test_adapter_normalizes_native_planet_population_hundreds():
    src=Path(__file__).parents[1]/'src'/'stars_ai'/'adapters'/'native_core_adapter.py'
    text=src.read_text(encoding='utf-8')
    assert 'population=int(p.population or 0) * 100' in text
    assert "'population_raw_hundreds': int(p.population or 0)" in text

def test_fleet_colonist_cargo_converts_native_kt_to_colonists():
    src=Path(__file__).parents[1]/'src'/'stars_ai'/'adapters'/'native_core_adapter.py'
    text=src.read_text(encoding='utf-8')
    assert 'cargo_population=colonists_from_cargo_kt(f.population)' in text
    assert "'population_raw_kt': int(f.population or 0)" in text
    assert COLONISTS_PER_CARGO_KT == 100
    assert COLONY_LOAD_KT == 25
    assert COLONY_LOAD_COLONISTS == 2_500
    assert colonists_from_cargo_kt(25) == 2_500
