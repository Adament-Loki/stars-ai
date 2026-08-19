
from pathlib import Path

from stars_ai.decision_trace import DecisionTrace, ScoreFactor
from stars_ai.trace_helpers import trace_ranked_choice


def test_trace_records_ranked_decision(tmp_path):
    t = DecisionTrace(player_id=2, turn=2405, persona="Expansionist")
    trace_ranked_choice(
        t,
        category="research",
        decision="Choose research field",
        selected="Propulsion",
        reason="Best support for current expansion goals.",
        ranked=[
            ("Propulsion", [
                ("unlock_value", 0.9, 1.5, "Better engines"),
                ("persona_fit", 1.0, 0.8, "Expansionist"),
            ]),
            ("Weapons", [
                ("unlock_value", 0.7, 1.0, "Useful but not urgent"),
                ("persona_fit", 0.4, 0.8, "Low opening-war priority"),
            ]),
        ],
        goals=["Reach Propulsion 8", "Explore 60% of galaxy"],
    )
    assert len(t.events) == 1
    e = t.events[0]
    assert e.selected == "Propulsion"
    assert e.candidates[0].score > e.candidates[1].score

    text_path = t.write_text(tmp_path / "trace.txt")
    json_path = t.write_json(tmp_path / "trace.json")
    assert "Propulsion" in text_path.read_text()
    assert '"selected": "Propulsion"' in json_path.read_text()


def test_disqualified_candidate_is_visible():
    t = DecisionTrace()
    c = t.score_candidate(
        "Ally Player 3",
        [ScoreFactor("trust", 0.9, 1.0)],
        disqualified=True,
        disqualify_reason="Human players cannot be allied with",
    )
    t.record(
        "diplomacy",
        "Evaluate alliance",
        "Hard alliance eligibility rule applied.",
        candidates=[c],
        rules=["Human players cannot be allied with"],
    )
    rendered = t.render_text()
    assert "DISQUALIFIED" in rendered
    assert "Human players cannot be allied with" in rendered


def test_trace_can_be_disabled():
    t = DecisionTrace(enabled=False)
    t.record("research", "noop", "disabled")
    assert t.events == []
