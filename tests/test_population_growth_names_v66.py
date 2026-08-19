
from stars_ai.models import RaceProfile,Planet,Position
from stars_ai.planet_economy import (
    theoretical_max_population, planet_population_capacity,
    population_capacity_fraction, projected_population_growth,
    projected_next_population,
)
from stars_ai.planet_names import get_planet_name


def joat(lrts=None,growth=0.15):
    return RaceProfile(
        primary_trait="Jack of All Trades",
        growth_rate=growth,
        native={"prt_id":9,"lrts":list(lrts or [])},
    )


def test_friendly_planet_names_cover_playtest_ids():
    assert get_planet_name(188)=="Coolidge"
    assert get_planet_name(209)=="Crow"
    assert get_planet_name(339)=="Genesis"
    assert get_planet_name(734)=="Red Dwarf"
    assert get_planet_name(989)=="Zebra"


def test_joat_theoretical_max_population():
    assert theoretical_max_population(joat())==1_200_000


def test_joat_obrm_max_population_modifiers_stack():
    assert theoretical_max_population(joat(["OBRM"]))==1_320_000


def test_he_has_half_nominal_population_capacity():
    r=RaceProfile(primary_trait="Hyper Expansion",growth_rate=.10,native={"prt_id":0,"lrts":[]})
    assert theoretical_max_population(r)==500_000


def test_red_planet_special_capacity():
    p=Planet(0,"Red",Position(0,0),habitability=-5,population=10_000)
    assert planet_population_capacity(p,joat())==25_000


def test_growth_slows_with_crowding():
    r=RaceProfile(primary_trait="unknown",growth_rate=.15,native={"prt_id":-1,"lrts":[]})
    low=Planet(0,"Low",Position(0,0),habitability=100,population=200_000)
    crowded=Planet(1,"Crowded",Position(0,0),habitability=100,population=800_000)
    assert projected_population_growth(low,r)==30_000
    assert 0 < projected_population_growth(crowded,r) < 30_000


def test_growth_reaches_zero_at_theoretical_max():
    r=RaceProfile(primary_trait="unknown",growth_rate=.15,native={"prt_id":-1,"lrts":[]})
    p=Planet(0,"Full",Position(0,0),habitability=100,population=1_000_000)
    assert population_capacity_fraction(p,r)==1.0
    assert projected_population_growth(p,r)==0
    assert projected_next_population(p,r)==1_000_000


def test_overpopulation_causes_deaths():
    r=RaceProfile(primary_trait="unknown",growth_rate=.15,native={"prt_id":-1,"lrts":[]})
    p=Planet(0,"Over",Position(0,0),habitability=100,population=1_100_000)
    assert projected_population_growth(p,r) < 0
