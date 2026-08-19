
from stars_ai.decision_trace import DecisionTrace
from stars_ai.trace_helpers import trace_ranked_choice

trace = DecisionTrace(player_id=1, turn=2400, persona="Expansionist")

trace_ranked_choice(
    trace,
    category="research",
    decision="Choose research field",
    selected="Propulsion",
    reason="Expansion phase values mobility more than incremental combat strength.",
    ranked=[
        ("Propulsion", [
            ("phase_fit", 1.0, 1.4, "Early expansion"),
            ("next_unlock_value", 0.8, 1.2, "Improved engine"),
            ("goal_fit", 1.0, 1.5, "Reach Propulsion 8"),
        ]),
        ("Weapons", [
            ("phase_fit", 0.3, 1.4, "No immediate war"),
            ("next_unlock_value", 0.7, 1.2, "Improved weapon"),
            ("goal_fit", 0.0, 1.5, "No weapons objective"),
        ]),
    ],
    goals=["Reach Propulsion 8", "Own 10 planets"],
)

print(trace.render_text())
trace.write_json("decision-trace.json")
trace.write_text("decision-trace.txt")
