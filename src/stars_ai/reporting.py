"""One structured source for concise player journals and verbose turn reports.

The report model is assembled while the completed AI turn is still available.
Both Markdown and JSON renderers consume the same :class:`TurnReportModel`;
they never independently infer strategy from raw files or native blocks.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass, replace
from enum import Enum
import json
from pathlib import Path
import re
from typing import Any, Iterable

from .planet_economy import (
    planet_population_capacity,
    population_capacity_fraction,
    projected_next_population,
    projected_population_growth,
)
from .turn_state import mapping as turn_mapping, sequence as turn_sequence


REPORT_SCHEMA_VERSION = 1


def _jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return repr(value)


def _atomic_write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)
    return path


def _markdown(value: Any) -> str:
    text = str(value if value is not None else "—")
    return text.replace("|", "\\|").replace("\n", " ").strip()


def _table(headers: list[str], rows: Iterable[Iterable[Any]]) -> list[str]:
    output = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    output.extend("| " + " | ".join(_markdown(value) for value in row) + " |" for row in rows)
    return output


def _planet_name(state, planet_id: Any) -> str | None:
    if planet_id is None:
        return None
    try:
        numeric_id = int(planet_id)
    except (TypeError, ValueError):
        return str(planet_id)
    planet = next((item for item in state.planets if int(item.id) == numeric_id), None)
    return planet.name if planet is not None else f"Planet {numeric_id}"


def _fleet_name(state, fleet_id: Any) -> str | None:
    if fleet_id is None:
        return None
    try:
        numeric_id = int(fleet_id)
    except (TypeError, ValueError):
        return str(fleet_id)
    fleet = next((item for item in state.fleets if int(item.id) == numeric_id), None)
    return fleet.name if fleet is not None else f"Fleet {numeric_id}"


def _order_subject_target(state, order) -> tuple[str, str]:
    payload = order.payload
    subject = _fleet_name(state, payload.get("fleet_id"))
    if subject is None:
        subject = _planet_name(state, payload.get("planet_id"))
    if subject is None:
        subject = str(order.kind)
    target = _planet_name(state, payload.get("destination_planet_id"))
    if target is None and payload.get("target_fleet_id") is not None:
        target = _fleet_name(state, payload.get("target_fleet_id"))
    if target is None:
        target = str(payload.get("design_name") or payload.get("item") or "—")
    return subject, target


def _native_action_rows(state, orders, emitted: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Make native-surviving actions visible beside their semantic intent."""

    semantic_reasons: dict[tuple[str, object, object], str] = {}
    for order in orders.orders:
        payload = order.payload or {}
        key = (
            str(order.kind),
            payload.get("fleet_id", payload.get("planet_id")),
            payload.get("destination_planet_id"),
        )
        semantic_reasons.setdefault(key, str(order.reason))

    rows = []
    for item in emitted:
        payload = dict(item.get("payload") or {})
        kind = str(item.get("kind", "action"))
        subject = _fleet_name(state, payload.get("fleet_id"))
        if subject is None:
            subject = _planet_name(state, payload.get("planet_id"))
        if subject is None:
            subject = str(payload.get("design_name") or kind)
        target = _planet_name(state, payload.get("destination_planet_id"))
        if target is None and payload.get("target_fleet_id") is not None:
            target = _fleet_name(state, payload.get("target_fleet_id"))
        if target is None:
            target = str(payload.get("design_name") or payload.get("item") or "—")
        key = (
            kind,
            payload.get("fleet_id", payload.get("planet_id")),
            payload.get("destination_planet_id"),
        )
        rows.append({
            "action": kind,
            "subject": subject,
            "target": target,
            "reason": str(
                item.get("reason") or semantic_reasons.get(key) or "Native action emitted."
            ),
        })
    return rows


def _fleet_location(state, fleet) -> str:
    at_world = next(
        (
            planet.name for planet in state.planets
            if abs(float(planet.position.x) - float(fleet.position.x)) <= 0.5
            and abs(float(planet.position.y) - float(fleet.position.y)) <= 0.5
        ),
        None,
    )
    return at_world or f"({int(fleet.position.x)}, {int(fleet.position.y)})"


def _warning_lines(state, skipped: list[dict[str, Any]], waypoint_diagnostics: list[dict[str, Any]]) -> list[str]:
    warnings: list[str] = []
    for outcome in turn_sequence(state, "command_outcomes"):
        if str(outcome.get("status", "")).upper() in {"WARNING", "UNVERIFIED"}:
            warnings.append(str(outcome.get("message", "Prior action needs review.")))
    for diagnostic in turn_sequence(state, "movement_progress_diagnostics"):
        if diagnostic.get("flag"):
            warnings.append(str(diagnostic["flag"]))
    for item in skipped:
        warnings.append(
            f"Native skipped {item.get('kind', 'action')}: {item.get('reason', 'no reason recorded.')}"
        )
    for diagnostic in waypoint_diagnostics:
        if str(diagnostic.get("status", "")).upper() in {"BLOCKED", "WARNING", "UNSAFE"}:
            warnings.append(str(diagnostic.get("reason", "Waypoint safety needs review.")))
    for note in getattr(state, "native", {}).get("report_warnings", []) or []:
        warnings.append(str(note))
    return list(dict.fromkeys(item for item in warnings if item))


@dataclass(slots=True)
class TurnReportModel:
    """JSON-ready report data shared exactly by Markdown and JSON rendering."""

    game_name: str
    player_id: int
    year: int
    turn: int | None
    persona: str
    phase: str
    phase_reason: str
    metrics: dict[str, int]
    primary_objective: str | None
    primary_threat: str | None
    research: dict[str, Any]
    strategy: dict[str, Any]
    overview: list[str]
    decisions: list[dict[str, Any]]
    fleets: list[dict[str, Any]]
    orders: list[dict[str, Any]]
    native_execution: dict[str, Any]
    previous_outcomes: list[dict[str, Any]]
    warnings: list[str]
    schema_version: int = REPORT_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return _jsonable(asdict(self))

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TurnReportModel":
        return cls(**{
            "game_name": str(data.get("game_name", "Unknown game")),
            "player_id": int(data["player_id"]),
            "year": int(data["year"]),
            "turn": int(data["turn"]) if data.get("turn") is not None else None,
            "persona": str(data.get("persona", "Balanced")),
            "phase": str(data.get("phase", "unclassified")),
            "phase_reason": str(data.get("phase_reason", "No phase reason recorded.")),
            "metrics": dict(data.get("metrics", {})),
            "primary_objective": data.get("primary_objective"),
            "primary_threat": data.get("primary_threat"),
            "research": dict(data.get("research", {})),
            "strategy": dict(data.get("strategy", {})),
            "overview": list(data.get("overview", [])),
            "decisions": list(data.get("decisions", [])),
            "fleets": list(data.get("fleets", [])),
            "orders": list(data.get("orders", [])),
            "native_execution": dict(data.get("native_execution", {})),
            "previous_outcomes": list(data.get("previous_outcomes", [])),
            "warnings": list(data.get("warnings", [])),
            "schema_version": int(data.get("schema_version", REPORT_SCHEMA_VERSION)),
        })

    def with_turn(self, turn: int) -> "TurnReportModel":
        return replace(self, turn=int(turn))


def build_turn_report_model(
    state,
    orders,
    decision_or_agent,
    emitted: list[dict[str, Any]],
    skipped: list[dict[str, Any]],
    waypoint_diagnostics: list[dict[str, Any]],
) -> TurnReportModel:
    """Capture one completed turn while planner/state objects are still live."""

    owned_planets = [planet for planet in state.planets if planet.owner == state.player_id]
    own_fleets = [fleet for fleet in state.fleets if fleet.owner == state.player_id]
    known_enemy = [
        planet for planet in state.planets
        if planet.owner not in (None, state.player_id) and bool(planet.observed)
    ]
    context = getattr(decision_or_agent, "context", None)
    plan = (
        getattr(context, "strategic_plan", None)
        if context is not None else getattr(decision_or_agent, "last_plan", None)
    )
    persona_source = (
        getattr(context, "persona", None)
        if context is not None else getattr(decision_or_agent, "persona", None)
    )
    persona = getattr(plan, "persona_name", None) or getattr(persona_source, "name", "Balanced")
    phase_payload = turn_mapping(state, "strategic_phase")
    phase = getattr(plan, "phase", None) or phase_payload.get("phase", "unclassified")
    phase_reason = getattr(plan, "phase_reason", None) or phase_payload.get("reason", "No phase reason recorded.")
    objective_weights = dict(getattr(plan, "objective_weights", {}) or {})
    primary_objective = max(objective_weights, key=objective_weights.get) if objective_weights else None
    research = turn_mapping(state, "research_strategy")
    territorial = turn_mapping(state, "territorial_defense")
    violations = list(territorial.get("violations", []) or [])
    primary_threat = str(territorial.get("reason")) if violations and territorial.get("reason") else None
    metrics = {
        "colonies": len(owned_planets),
        "population": sum(int(planet.population or 0) for planet in owned_planets),
        "factories": sum(int(planet.factories or 0) for planet in owned_planets),
        "mines": sum(int(planet.mines or 0) for planet in owned_planets),
        "defenses": sum(int(planet.defenses or 0) for planet in owned_planets),
        "active_fleets": len(own_fleets),
        "known_enemy_colonies": len(known_enemy),
    }
    fleet_intents = (
        getattr(context, "legacy_fleet_intents", [])
        if context is not None else getattr(decision_or_agent, "fleet_intents", [])
    )
    intent_by_fleet = {
        int(item["fleet_id"]): item
        for item in (fleet_intents or [])
        if item.get("fleet_id") is not None
    }
    fleets = []
    for fleet in sorted(own_fleets, key=lambda item: (str(item.name).casefold(), int(item.id))):
        intent = intent_by_fleet.get(int(fleet.id), {})
        destination = intent.get("destination_planet_id", fleet.destination_planet_id)
        fleets.append({
            "fleet_id": int(fleet.id),
            "name": str(fleet.name),
            "role": str(fleet.role),
            "location": _fleet_location(state, fleet),
            "destination": _planet_name(state, destination) or "—",
            "intent": str(intent.get("action", "UNSPECIFIED")),
            "status": "Holding" if destination is None else "Moving/queued",
            "reason": str(intent.get("reason", "No fleet-intent diagnostic was recorded.")),
        })
    journal = (
        getattr(context, "decision_journal", None)
        if context is not None else getattr(decision_or_agent, "decision_journal", None)
    )
    decisions = list((journal.to_dict() if journal is not None else {}).get("events", []))
    order_rows = []
    for order in sorted(orders.orders, key=lambda item: (-int(item.priority), item.kind, str(item.payload))):
        subject, target = _order_subject_target(state, order)
        order_rows.append({
            "priority": int(order.priority),
            "unit": subject,
            "order": str(order.kind),
            "target": target,
            "reason": str(order.reason),
        })
    outcomes = [dict(item) for item in turn_sequence(state, "command_outcomes")]
    warnings = _warning_lines(state, skipped, waypoint_diagnostics)
    native_execution = {
        "semantic_order_count": len(order_rows),
        "emitted_count": len(emitted),
        "emitted_actions": _native_action_rows(state, orders, emitted),
        "skipped_count": len(skipped),
        "blocked_actions": [
            {
                "action": str(item.get("kind", "action")),
                "result": str(item.get("status", "SKIPPED")),
                "reason": str(item.get("reason", "No reason recorded.")),
            }
            for item in skipped
        ],
        "waypoint_diagnostics": _jsonable(waypoint_diagnostics),
        "capability_execution": _jsonable((state.native or {}).get("capability_execution", {})),
    }
    overview = [
        f"{persona} remains in {phase} phase.",
        f"Primary objective: {primary_objective or 'no objective weight was recorded'}.",
        f"This turn produced {len(order_rows)} semantic order(s); native emitted {len(emitted)} action(s).",
    ]
    if research.get("capability_name") or research.get("current_field"):
        overview.append(
            f"Research focus: {research.get('capability_name') or research.get('current_field')}."
        )
    if warnings:
        overview.append(f"Primary concern: {warnings[0]}")
    strategy = {
        "objectives": [
            {"name": name, "weight": objective_weights[name]}
            for name in sorted(objective_weights, key=lambda name: (-objective_weights[name], name))
        ],
        "plan_notes": list(getattr(plan, "notes", ()) or []),
        "phase_assessment": _jsonable(getattr(plan, "phase_assessment", {}) or {}),
        "territorial_defense": _jsonable(territorial),
        "research": _jsonable(research),
    }
    return TurnReportModel(
        game_name=str(state.game_name),
        player_id=int(state.player_id),
        year=int(state.year),
        turn=None,
        persona=str(persona),
        phase=str(phase),
        phase_reason=str(phase_reason),
        metrics=metrics,
        primary_objective=primary_objective,
        primary_threat=primary_threat,
        research=_jsonable(research),
        strategy=strategy,
        overview=overview,
        decisions=decisions,
        fleets=fleets,
        orders=order_rows,
        native_execution=native_execution,
        previous_outcomes=_jsonable(outcomes),
        warnings=warnings,
    )


def _metric_delta(current: TurnReportModel, previous: TurnReportModel | None, name: str) -> str:
    value = int(current.metrics.get(name, 0))
    if previous is None:
        return "—"
    delta = value - int(previous.metrics.get(name, 0))
    return f"{delta:+d}" if delta else "0"


def _render_decisions(report: TurnReportModel, *, verbose: bool = False) -> list[str]:
    events = report.decisions if verbose else report.decisions[:10]
    if not events:
        return ["No direct planner decision events were recorded for this turn."]
    lines: list[str] = []
    for event in events:
        subject = event.get("subject") or "Empire"
        target = f" → {event['target']}" if event.get("target") else ""
        priority = f" (priority {event['priority']})" if event.get("priority") is not None else ""
        lines.extend([
            f"### {subject}: {event.get('action', 'Decision')}{target}{priority}",
            "",
            event.get("reason", "No reason recorded."),
            "",
        ])
        alternatives = list(event.get("alternatives", []) or [])
        if alternatives:
            lines.append("Alternatives recorded:")
            rows = []
            for candidate in alternatives if verbose else alternatives[:2]:
                if isinstance(candidate, dict):
                    rows.append((candidate.get("planet_name", candidate.get("candidate", candidate.get("planet_id", "candidate"))), candidate.get("score", "—"), candidate.get("selection_basis", candidate.get("result", "considered"))))
                else:
                    rows.append((candidate, "—", "considered"))
            lines.extend(_table(["Candidate", "Score", "Result"], rows))
            lines.append("")
    return lines


def render_turn_entry(report: TurnReportModel, previous: TurnReportModel | None = None) -> str:
    turn_label = f"Turn {report.turn}" if report.turn is not None else "Unnumbered turn"
    lines = [f"# {turn_label} — Year {report.year}", "", "## Overview", ""]
    lines.extend(report.overview)
    lines.extend(["", "## Empire State", ""])
    lines.extend(_table(
        ["Metric", "Current", "Previous", "Change"],
        [
            ("Colonies", report.metrics.get("colonies", 0), previous.metrics.get("colonies", "—") if previous else "—", _metric_delta(report, previous, "colonies")),
            ("Population", report.metrics.get("population", 0), previous.metrics.get("population", "—") if previous else "—", _metric_delta(report, previous, "population")),
            ("Active fleets", report.metrics.get("active_fleets", 0), previous.metrics.get("active_fleets", "—") if previous else "—", _metric_delta(report, previous, "active_fleets")),
        ],
    ))
    lines.extend(["", "## Strategy", "", f"**Persona:** {report.persona}  ", f"**Phase:** {report.phase}  ", f"**Reason:** {report.phase_reason}"])
    if report.primary_objective:
        lines.append(f"**Primary objective:** {report.primary_objective}")
    if report.primary_threat:
        lines.append(f"**Primary threat:** {report.primary_threat}")
    if report.research:
        lines.extend(["", "### Research", "", str(report.research.get("reason") or report.research.get("capability_name") or report.research.get("current_field") or "No research change recorded.")])
    lines.extend(["", "## Important Decisions", ""])
    lines.extend(_render_decisions(report))
    lines.extend(["## Fleet Activity", ""])
    lines.extend(_table(
        ["Fleet", "Role", "Location", "Destination", "Intent", "Status"],
        [
            (fleet["name"], fleet["role"], fleet["location"], fleet["destination"], fleet["intent"], fleet["status"])
            for fleet in report.fleets
        ],
    ) if report.fleets else ["No owned fleets were present in the normalized state."])
    lines.extend(["", "## Final Semantic Orders", ""])
    lines.extend(_table(
        ["Priority", "Unit", "Order", "Target", "Reason"],
        [(order["priority"], order["unit"], order["order"], order["target"], order["reason"]) for order in report.orders],
    ) if report.orders else ["No semantic orders were produced."])
    lines.extend(["", "## Native Execution Summary", "", f"**Semantic orders:** {report.native_execution.get('semantic_order_count', 0)}  ", f"**Native actions emitted:** {report.native_execution.get('emitted_count', 0)}  ", f"**Skipped/blocked:** {report.native_execution.get('skipped_count', 0)}"])
    emitted = report.native_execution.get("emitted_actions", []) or []
    if emitted:
        lines.extend(["", "### Emitted Native Actions", ""])
        lines.extend(_table(
            ["Action", "Subject", "Target", "Reason"],
            [
                (
                    item.get("action", "action"),
                    item.get("subject", "—"),
                    item.get("target", "—"),
                    item.get("reason", "Native action emitted."),
                )
                for item in emitted
            ],
        ))
    blocked = report.native_execution.get("blocked_actions", []) or []
    if blocked:
        lines.extend(["", "### Blocked Actions", ""])
        lines.extend(_table(["Action", "Result", "Reason"], [(item["action"], item["result"], item["reason"]) for item in blocked]))
    lines.extend(["", "## Previous Turn Results", ""])
    if report.previous_outcomes:
        lines.extend(_table(["Expected", "Observed status", "Result"], [
            (item.get("description", item.get("kind", "Prior action")), item.get("status", "UNVERIFIED"), item.get("message", "No outcome message recorded."))
            for item in report.previous_outcomes
        ]))
    else:
        lines.append("No prior emitted action had an observable result this turn.")
    lines.extend(["", "## Watch Next Turn", ""])
    if report.warnings:
        lines.extend(f"- {warning}" for warning in report.warnings)
    else:
        lines.append("No warning was recorded from the available diagnostics.")
    return "\n".join(lines).rstrip() + "\n"


def render_player_journal(reports: Iterable[TurnReportModel]) -> str:
    ordered = sorted(reports, key=lambda item: (item.turn if item.turn is not None else -1, item.year), reverse=True)
    if not ordered:
        raise ValueError("A player journal requires at least one report")
    current = ordered[0]
    lines = [f"# Player {current.player_id} — AI Decision Journal", "", "## Current Status", "", f"**Year:** {current.year}  ", f"**Persona:** {current.persona}  ", f"**Strategic Phase:** {current.phase}  ", f"**Colonies:** {current.metrics.get('colonies', 0)}  ", f"**Known Enemy Colonies:** {current.metrics.get('known_enemy_colonies', 0)}  ", f"**Active Fleets:** {current.metrics.get('active_fleets', 0)}"]
    research_focus = current.research.get("capability_name") or current.research.get("current_field")
    if research_focus:
        lines.append(f"**Research Focus:** {research_focus}")
    if current.primary_objective:
        lines.append(f"**Primary Objective:** {current.primary_objective}")
    if current.primary_threat:
        lines.append(f"**Primary Threat:** {current.primary_threat}")
    if current.warnings:
        lines.append(f"**Major Warning:** {current.warnings[0]}")
    for index, report in enumerate(ordered):
        previous = ordered[index + 1] if index + 1 < len(ordered) else None
        lines.extend(["", "---", "", render_turn_entry(report, previous).rstrip()])
    return "\n".join(lines).rstrip() + "\n"


def render_turn_summary(reports: Iterable[TurnReportModel]) -> str:
    latest: dict[int, TurnReportModel] = {}
    for report in reports:
        previous = latest.get(report.player_id)
        key = (report.turn if report.turn is not None else -1, report.year)
        if previous is None or key > (previous.turn if previous.turn is not None else -1, previous.year):
            latest[report.player_id] = report
    ordered = [latest[player_id] for player_id in sorted(latest)]
    title = "# Turn Summary"
    if ordered:
        newest = max(ordered, key=lambda item: (item.turn if item.turn is not None else -1, item.year))
        title = f"# Turn {newest.turn if newest.turn is not None else '—'} — Year {newest.year}"
    lines = [title, ""]
    lines.extend(_table(
        ["Player", "Phase", "Colonies", "Main Objective", "Major Decision", "Warning"],
        [
            (
                report.player_id,
                report.phase,
                report.metrics.get("colonies", 0),
                report.primary_objective or "—",
                report.decisions[0].get("action", "No direct event") if report.decisions else "No direct event",
                report.warnings[0] if report.warnings else "—",
            )
            for report in ordered
        ],
    ))
    for report in ordered:
        lines.extend(["", f"## Player {report.player_id}", ""])
        lines.extend(report.overview[:2])
    return "\n".join(lines).rstrip() + "\n"


class DecisionJournalReporter:
    """Persist verbose turn models and deterministically rebuild root journals."""

    def __init__(self, output_root: str | Path) -> None:
        self.output_root = Path(output_root)

    @staticmethod
    def _turn_directory(report: TurnReportModel) -> str:
        turn = report.turn if report.turn is not None else report.year
        return f"turn-{int(turn):03d}-year-{int(report.year)}"

    def _player_directory(self, report: TurnReportModel) -> Path:
        return self.output_root / "verbose" / self._turn_directory(report) / f"player-{report.player_id:02d}"

    def write_turn(self, report: TurnReportModel) -> dict[str, Path]:
        """Write one verbose report then rebuild the concise root journals."""

        player_dir = self._player_directory(report)
        serialized = json.dumps(report.to_dict(), indent=2, sort_keys=True) + "\n"
        paths = {"turn_json": _atomic_write(player_dir / "turn.json", serialized)}
        paths["state"] = _atomic_write(player_dir / "state.md", self._render_state(report))
        paths["strategy"] = _atomic_write(player_dir / "strategy.md", self._render_strategy(report))
        paths["decisions"] = _atomic_write(player_dir / "decisions.md", self._render_decisions(report))
        paths["fleets"] = _atomic_write(player_dir / "fleets.md", self._render_fleets(report))
        paths["orders"] = _atomic_write(player_dir / "orders.md", self._render_orders(report))
        paths["native_audit"] = _atomic_write(player_dir / "native-audit.md", self._render_native(report))
        paths["outcome"] = _atomic_write(player_dir / "outcome.md", self._render_outcomes(report))
        self.rebuild()
        paths["player_journal"] = self.output_root / f"{report.player_id}.md"
        paths["turn_summary"] = self.output_root / "TURN_SUMMARY.md"
        return paths

    def _load_reports(self) -> list[TurnReportModel]:
        verbose = self.output_root / "verbose"
        reports: dict[tuple[int, int | None, int], TurnReportModel] = {}
        for path in sorted(verbose.glob("turn-*/player-*/turn.json"), key=lambda item: str(item).casefold()):
            try:
                report = TurnReportModel.from_dict(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                continue
            reports[(report.player_id, report.turn, report.year)] = report
        return list(reports.values())

    def rebuild(self) -> None:
        """Regenerate every root journal from per-turn JSON, avoiding duplicates."""

        reports = self._load_reports()
        by_player: dict[int, list[TurnReportModel]] = {}
        for report in reports:
            by_player.setdefault(report.player_id, []).append(report)
        for player_id in sorted(by_player):
            _atomic_write(self.output_root / f"{player_id}.md", render_player_journal(by_player[player_id]))
        _atomic_write(self.output_root / "TURN_SUMMARY.md", render_turn_summary(reports))
        index_lines = ["# Verbose AI Decision Reports", ""]
        for report in sorted(reports, key=lambda item: (item.turn if item.turn is not None else -1, item.player_id, item.year), reverse=True):
            relative = self._player_directory(report).relative_to(self.output_root).as_posix()
            index_lines.append(f"- Turn {report.turn if report.turn is not None else '—'} / Year {report.year}: Player {report.player_id} — [{relative}/turn.json]({relative}/turn.json)")
        _atomic_write(self.output_root / "verbose" / "index.md", "\n".join(index_lines).rstrip() + "\n")

    @staticmethod
    def _render_state(report: TurnReportModel) -> str:
        lines = [f"# Player {report.player_id} State — Turn {report.turn} / Year {report.year}", ""]
        lines.extend(_table(["Metric", "Value"], sorted(report.metrics.items())))
        return "\n".join(lines).rstrip() + "\n"

    @staticmethod
    def _render_strategy(report: TurnReportModel) -> str:
        lines = [f"# Strategy — Player {report.player_id}", "", f"Phase: **{report.phase}**", "", report.phase_reason, "", "## Objectives", ""]
        lines.extend(_table(["Objective", "Weight"], [(item["name"], item["weight"]) for item in report.strategy.get("objectives", [])]))
        lines.extend(["", "## Research", "", "```json", json.dumps(report.research, indent=2, sort_keys=True), "```"])
        return "\n".join(lines).rstrip() + "\n"

    @staticmethod
    def _render_decisions(report: TurnReportModel) -> str:
        lines = [f"# Decisions — Player {report.player_id}", ""]
        lines.extend(_render_decisions(report, verbose=True))
        return "\n".join(lines).rstrip() + "\n"

    @staticmethod
    def _render_fleets(report: TurnReportModel) -> str:
        lines = [f"# Fleet Activity — Player {report.player_id}", ""]
        lines.extend(_table(["Fleet", "Role", "Location", "Destination", "Intent", "Status", "Reason"], [(item["name"], item["role"], item["location"], item["destination"], item["intent"], item["status"], item["reason"]) for item in report.fleets]))
        return "\n".join(lines).rstrip() + "\n"

    @staticmethod
    def _render_orders(report: TurnReportModel) -> str:
        lines = [f"# Final Semantic Orders — Player {report.player_id}", ""]
        lines.extend(_table(["Priority", "Unit", "Order", "Target", "Reason"], [(item["priority"], item["unit"], item["order"], item["target"], item["reason"]) for item in report.orders]))
        return "\n".join(lines).rstrip() + "\n"

    @staticmethod
    def _render_native(report: TurnReportModel) -> str:
        lines = [f"# Native Audit — Player {report.player_id}", "", "```json", json.dumps(report.native_execution, indent=2, sort_keys=True), "```"]
        return "\n".join(lines).rstrip() + "\n"

    @staticmethod
    def _render_outcomes(report: TurnReportModel) -> str:
        lines = [f"# Previous-Turn Outcomes — Player {report.player_id}", "", "```json", json.dumps(report.previous_outcomes, indent=2, sort_keys=True), "```"]
        return "\n".join(lines).rstrip() + "\n"


def turn_number_from_tag(tag: str | None) -> int | None:
    """Return the stable execution-turn number used in root/verbose paths."""

    match = re.fullmatch(r"turn-(\d+)", str(tag or ""))
    return int(match.group(1)) if match else None


def _audit_planet_name(state, planet_id: int) -> str:
    return _planet_name(state, planet_id) or f"Planet {planet_id}"


def _audit_fleet_name(state, fleet_id: int) -> str:
    fleet = next(
        (item for item in state.fleets if int(item.id) == int(fleet_id) and item.owner == state.player_id),
        None,
    )
    return fleet.name if fleet is not None else f"Fleet {fleet_id}"


def _audit_waypoint_target(state, payload: dict[str, Any]) -> str:
    target_fleet_id = payload.get("destination_fleet_id")
    if target_fleet_id is not None:
        owner = payload.get("destination_fleet_owner")
        fleet = next(
            (
                candidate for candidate in state.fleets
                if int(candidate.id) == int(target_fleet_id)
                and (owner is None or int(candidate.owner) == int(owner))
            ),
            None,
        )
        label = fleet.name if fleet is not None else f"Fleet {target_fleet_id}"
        return f"P{owner if owner is not None else '?'} {label}"
    target = payload.get("destination_planet_id")
    return _audit_planet_name(state, int(target)) if target is not None else "none"


def build_native_decision_report(
    state,
    orders,
    fleet_intents: list[dict[str, Any]],
    emitted: list[dict[str, Any]],
    skipped: list[dict[str, Any]],
    waypoint_diagnostics: list[dict[str, Any]] | None = None,
) -> str:
    """Render the retained forensic decision report outside the native writer.

    It receives semantic decisions plus writer audit facts.  It deliberately
    has no access to native block construction or persistence transactions.
    """

    lines = [
        f"STARS! AI DECISION REPORT — {state.game_name} — Year {state.year} — Player {state.player_id}",
        "",
        "Format: Object Name - Action - Reason/Justification",
        "",
        "COMMAND OUTCOME STATUS",
    ]
    command_outcomes = turn_sequence(state, "command_outcomes")
    if command_outcomes:
        lines.extend(str(item.get("message", "UNVERIFIED - Missing outcome message.")) for item in command_outcomes)
        counts = {
            status: sum(1 for item in command_outcomes if item.get("status") == status)
            for status in ("COMPLETED", "PENDING", "WARNING", "UNVERIFIED")
        }
        lines.append(
            "SUMMARY - completed={COMPLETED}; pending={PENDING}; warnings={WARNING}; unverified={UNVERIFIED}.".format(**counts)
        )
    else:
        lines.extend([
            "NO PRIOR EMITTED COMMANDS DUE - Nothing to verify against this M file.",
            "SUMMARY - completed=0; pending=0; warnings=0; unverified=0.",
        ])

    by_fleet_id = {int(item["fleet_id"]): item for item in fleet_intents if item.get("fleet_id") is not None}
    lines.extend(["", "FLEETS"])
    for fleet in (item for item in state.fleets if item.owner == state.player_id):
        intent = by_fleet_id.get(fleet.id, {
            "action": "BLOCKED",
            "reason": "Fleet missing from activity coverage.",
            "destination_planet_id": None,
        })
        action = str(intent["action"])
        destination = intent.get("destination_planet_id")
        if destination is not None:
            action = f"{action} -> {_audit_planet_name(state, int(destination))}"
        lines.append(f"{fleet.name} - {action} - {intent['reason']}")

    scout_policy = ((state.native or {}).get("scout_exploration_policy", {}) or {})
    scout_build = ((state.native or {}).get("scout_build_policy", {}) or {})
    scout_stack = ((state.native or {}).get("scout_stack_diagnostics", {}) or {})
    lines.extend(["", "SCOUT PRODUCTIVITY"])
    lines.append(
        "Scout hulls={}; fleets={}; singleton fleets={}; stacked fleets={}; wasted survey capacity={}; active routes={}; idle={}; duplicate targets={}.".format(
            scout_policy.get("scout_hull_count", 0), scout_policy.get("scout_fleet_count", 0),
            scout_policy.get("singleton_scout_capacity", 0),
            scout_policy.get("stacked_scout_fleet_count", scout_stack.get("stacked_fleet_count", 0)),
            scout_stack.get("wasted_survey_capacity", 0), scout_policy.get("active_scout_routes", 0),
            scout_policy.get("idle_scout_count", 0), scout_policy.get("duplicate_scout_targets", 0),
        )
    )
    lines.append(
        "Local unknowns: home-radius={}; priority-hub radii={}; desired scout force={}; live/imminent={}; actual build gap={}.".format(
            scout_policy.get("unknown_within_home_radius", 0),
            scout_policy.get("unknown_within_priority_hub_radius", 0), scout_build.get("desired_scout_force", 0),
            scout_build.get("live_imminent_scout_force", 0), scout_build.get("actual_scout_build_gap", 0),
        )
    )

    territorial = turn_mapping(state, "territorial_defense")
    violations = list(territorial.get("violations", []) or [])
    lines.extend(["", "TERRITORIAL DEFENSE"])
    if violations:
        lines.append(str(territorial.get("reason", "Territorial response active.")))
        for violation in violations:
            lines.append(
                f"P{violation.get('owner')} fleet {violation.get('fleet_name', violation.get('fleet_id'))} - "
                f"{str(violation.get('classification', 'unknown')).upper()} VIOLATION near "
                f"{violation.get('anchor_planet_name', 'owned world')} at "
                f"{float(violation.get('distance_ly', 0)):.1f}/{float(violation.get('claim_radius_ly', 0)):.1f} ly; "
                f"severity={float(violation.get('severity', 0)):.2f}; "
                f"patrol={'Y' if violation.get('requires_patrol') else 'N'}; "
                f"minefield={'Y' if violation.get('requires_minefield') else 'N'}."
            )
    else:
        lines.append("No non-friendly armed or transport fleet is inside perceived territory.")

    colony_intents = [item for item in fleet_intents if item.get("role") == "colony"]
    lines.extend(["", "COLONIZATION PRIORITY"])
    if colony_intents:
        for intent in colony_intents:
            fleet_name = intent.get("fleet_name", f"Fleet {intent.get('fleet_id')}")
            candidates = list(intent.get("colony_candidates", []) or [])
            if candidates:
                lines.append(f"{fleet_name} - BEST KNOWN CANDIDATE {candidates[0]['planet_name']} - {intent['reason']}")
                for rank, candidate in enumerate(candidates, 1):
                    lines.append(
                        f"  #{rank} {candidate['planet_name']} - score {candidate['score']:.1f} - "
                        f"habitability {candidate['habitability']}%; fleet distance {candidate['distance_from_fleet']:.1f}; "
                        f"nearest-owned distance {candidate['distance_from_nearest_owned']:.1f}; {candidate['explanation']}"
                    )
            else:
                lines.append(f"{fleet_name} - NO COLONY TARGET - {intent.get('reason', 'No viable candidate')}")
    else:
        lines.append("Colonization - NO COLONY FLEET AVAILABLE - No owned fleet is currently classified as colony.")

    lines.extend(["", "DESIGN DEVELOPMENT"])
    design_orders = [item for item in orders.orders if item.kind in ("create_design", "create_ship_design", "delete_ship_design", "replace_ship_design")]
    if design_orders:
        for order in design_orders:
            payload = order.payload
            lines.append(
                f"{payload.get('name', 'New Design')} - PROPOSE {str(payload.get('role', 'design')).upper()} - "
                f"hull={payload.get('desired_hull_name', 'unknown hull')} - {order.reason} "
                "NATIVE STATUS: PENDING DESIGN-CREATION VALIDATION."
            )
    else:
        lines.append("Design Development - KEEP CURRENT DESIGNS - No material capability gap selected this turn.")

    production_by_planet = {int(item.payload["planet_id"]): item for item in orders.orders if item.kind == "set_planet_queue"}
    lines.extend(["", "PLANET PRODUCTION"])
    for planet in (item for item in state.planets if item.owner == state.player_id):
        order = production_by_planet.get(int(planet.id))
        if order is None:
            capacity = planet_population_capacity(planet, state.race)
            growth = projected_population_growth(planet, state.race)
            next_population = projected_next_population(planet, state.race)
            lines.append(
                f"{planet.name} - RESEARCH / KEEP EMPTY - capacity={population_capacity_fraction(planet, state.race) * 100:.1f}% of {capacity:,}; "
                f"projected growth={growth:+,} -> {next_population:,}; factories {planet.factories}; mines {planet.mines}. "
                "No useful planet build selected this turn."
            )
            continue
        queue = order.payload.get("queue", [])
        queue_text = ", ".join(
            f"{item.get('quantity', 1)}x {item.get('design_name', 'Design')}" if item.get("item") in ("ship_design", "starbase_design")
            else f"{item.get('quantity', 1)}x {item.get('item', '?')}"
            for item in queue
        )
        lines.append(f"{planet.name} - {'BUILD ' + queue_text if queue else 'CLEAR QUEUE -> RESEARCH'} - {order.reason}")

    lines.extend(["", "RESEARCH"])
    research_orders = [item for item in orders.orders if item.kind == "set_research"]
    if research_orders:
        for order in research_orders:
            payload = order.payload
            field = payload.get("current_field", payload.get("field", "unknown"))
            next_field = payload.get("next_field", field)
            lines.append(
                f"Empire Research - {payload.get('capability_name', 'named capability')} - "
                f"{payload.get('posture', 'TARGETED')} - {str(field).upper()} -> {str(next_field).upper()} "
                f"at {payload.get('allocation_percent', 15)}% - contributors={payload.get('contributor_planet_ids', [])} - {order.reason}"
            )
    else:
        lines.append("Empire Research - KEEP CURRENT FIELD - No research-field change selected this turn.")

    lines.extend(["", "DIPLOMACY"])
    relation_orders = [item for item in orders.orders if item.kind == "set_player_relation"]
    emitted_targets = {int(item["payload"]["player_id"]) for item in emitted if item.get("kind") == "set_player_relation"}
    if relation_orders:
        for order in relation_orders:
            target = int(order.payload["player_id"])
            status = "EMITTED" if target in emitted_targets else "NOT EMITTED"
            lines.append(f"Player {target} - {str(order.payload.get('relation', 'unknown')).upper()} - {status} - {order.reason}")
    else:
        lines.append("Diplomacy - NO RELATION CHANGE - Preserve current relations.")

    lines.extend(["", "NATIVE WAYPOINT LIFECYCLE"])
    for diagnostic in waypoint_diagnostics or []:
        requested = f"F{diagnostic.get('requested_target_id')}" if diagnostic.get("requested_target_type") == 2 else f"P{diagnostic.get('requested_target_id')}"
        lines.append(
            f"Fleet {diagnostic.get('fleet_id')} - {diagnostic.get('result')} - normalized destination={diagnostic.get('normalized_destination')}; "
            f"native destination={diagnostic.get('native_waypoint_destination')}; native warp/task={diagnostic.get('native_waypoint_warp')}/{diagnostic.get('native_waypoint_task')}; "
            f"requested {diagnostic.get('requested_mission')} -> {requested} @ W{diagnostic.get('requested_warp')}. {diagnostic.get('reason')}"
        )
    if not waypoint_diagnostics:
        lines.append("No native movement transaction requested.")

    lines.extend(["", "NATIVE EXECUTION DETAILS"])
    for event in emitted:
        payload = event.get("payload", {})
        kind = event.get("kind")
        if kind == "colony_operation":
            name = _audit_fleet_name(state, int(payload.get("fleet_id", -1)))
            target = _audit_waypoint_target(state, payload)
            transfer = payload.get("population_loaded")
            load = f"YES - native 25 kT instruction; expected transfer={int(transfer):,} colonists" if payload.get("load_25kt_population") and transfer is not None else "NO"
            lines.append(
                f"{name} - NATIVE COLONY ORDER -> {target} warp {payload.get('warp', '?')} - "
                f"race-adjusted hab current={payload.get('target_current_habitability', payload.get('target_habitability', '?'))}%; "
                f"current-tech terraform={payload.get('target_current_tech_terraform_habitability', payload.get('target_habitability', '?'))}%; "
                f"eventual terraform={payload.get('target_eventual_terraform_habitability', payload.get('target_habitability', '?'))}%; "
                f"planning value={payload.get('target_habitability', '?')}%; policy={payload.get('colonization_stage', '?')} "
                f"floor={payload.get('habitability_floor', '?')}% basis={payload.get('selection_basis', '?')}; "
                f"home distance={payload.get('target_distance_from_homeworld', '?')} ly; route={payload.get('native_waypoint_action', '?')} waypoint #1; "
                f"load block emitted: {load}; population aboard before={payload.get('cargo_population_before', '?')}; "
                f"source population={payload.get('source_population', '?')}; source after load={payload.get('source_population_after_load', '?')}."
            )
        elif kind in ("move_fleet", "transport_population", "transport_minerals", "transport_unload_remainder", "remote_mine"):
            name = _audit_fleet_name(state, int(payload.get("fleet_id", -1)))
            lines.append(f"{name} - NATIVE {kind.upper()} -> {_audit_waypoint_target(state, payload)} warp {payload.get('warp', '?')} - {event.get('reason', '')}")
    if skipped:
        lines.extend(["", "NATIVE WRITER LIMITATIONS / SKIPPED ACTIONS"])
        for event in skipped:
            lines.append(f"{event.get('kind', 'Order')} - SKIPPED {event.get('kind', 'order')} - {event.get('reason', 'unknown reason')}")

    unintentional = [item for item in fleet_intents if item.get("action") == "BLOCKED"]
    colony_operations = [item for item in fleet_intents if item.get("action") == "LOAD + COLONIZE"]
    colony_other = [item for item in fleet_intents if item.get("role") == "colony" and item.get("action") != "LOAD + COLONIZE"]
    lines.extend(["", "COLONIZE NATIVE STATUS"])
    if colony_operations:
        lines.append("Native Colonize task - EMITTED - Complete validated load/move/task=2 colony sequence generated.")
    elif colony_other:
        lines.append("Native Colonize task - DEFERRED - Colony asset is intentionally holding or returning for population; no purposeless colony movement emitted.")
    else:
        lines.append("Native Colonize task - NOT REQUIRED - No colony fleet available.")
    lines.extend(["", "ACTIVITY CHECK"])
    lines.append(
        f"WARNING - {len(unintentional)} fleet(s) BLOCKED - These fleets require capability/state follow-up; none are silently idle."
        if unintentional else
        "PASS - ALL OWNED FLEETS ACCOUNTED FOR - Every fleet has movement, an existing waypoint, or a conscious armed hold."
    )
    return "\n".join(lines) + "\n"
