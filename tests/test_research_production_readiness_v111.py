"""Research must name—and be able to execute—its production follow-through."""
from __future__ import annotations

import stars_ai.research_planner as planner
from stars_ai.models import GameState, Planet, Position, RaceProfile, Tech


def _state(*, has_shipyard: bool) -> GameState:
    home = Planet(
        0, "Alpha", Position(0, 0), owner=1, population=300_000,
        native={"starbase_capabilities": {"can_build_ships": has_shipyard}},
    )
    return GameState(
        "research-production", 2435, 1, RaceProfile(), Tech(), [home], [],
        native={
            "threat_eta_turns": 4,
            "strategic_watchdog": {},
            "production_by_planet": {},
            "research_demands": [
                {
                    "capability_id": "military:urgent_package",
                    "name": "Urgent combat package",
                    "category": "military",
                    "requirements": {"weapons": 1},
                    "need": 4, "urgency": 4, "value": 4, "utilization": 1,
                    "military_emergency": True,
                    "requires_production_plan": True,
                    "enables_roles": ["combat"],
                },
                {
                    "capability_id": "economy:reliable",
                    "name": "Reliable economy capability",
                    "category": "economy",
                    "requirements": {"energy": 1},
                    "need": 2, "urgency": 2, "value": 2, "utilization": 1,
                },
            ],
        },
    )


def _isolate_generic_demands(monkeypatch):
    monkeypatch.setattr(planner, "expansion_research_demands", lambda state, network=None: [])
    monkeypatch.setattr(planner, "stock_capability_catalog", lambda **kwargs: [])


def test_imminent_military_research_is_rejected_when_no_shipyard_can_field_it(monkeypatch):
    _isolate_generic_demands(monkeypatch)
    decision = planner.plan_research(_state(has_shipyard=False))
    assert decision.capability_id == "economy:reliable"


def test_reachable_military_package_names_its_concrete_shipyard_follow_through(monkeypatch):
    _isolate_generic_demands(monkeypatch)
    decision = planner.plan_research(_state(has_shipyard=True))
    assert decision.capability_id == "military:urgent_package"
    assert decision.production_readiness == 1.0
    assert "Queue" in decision.production_follow_through
    assert "Alpha" in decision.production_follow_through
    assert decision.expected_completion_year == 2437
