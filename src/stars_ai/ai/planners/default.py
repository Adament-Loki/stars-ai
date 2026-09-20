"""Thin planner and policy adapters over the existing strategic domains.

These adapters intentionally contain no strategic algorithm.  They expose one
visible entry point per existing domain so the turn pipeline is inspectable
without making domain code depend on ``StarsAI``.
"""

from __future__ import annotations

from collections.abc import Iterable

from ...design_development import add_design_development_orders
from ...fleet_intent import ensure_fleet_activity
from ...fleet_management import (
    add_tactical_fleet_management,
    enforce_scout_split_precedence,
    prepare_scout_split_precedence,
)
from ...fuel_planner import apply_fuel_safety
from ...strategy.diplomacy import add_diplomacy_orders
from ...strategy.economy import add_economic_orders
from ...strategy.exploration import add_exploration_orders, deconflict_recon_orders
from ...strategy.military import add_military_orders
from ...strategy.research import add_research_orders
from ...v4_coordinator import augment_orders_v4
from ...warp_policy import optimize_active_route_warps
from ..context import TurnContext
from ..pipeline import TurnPlanner


def _orders(context: TurnContext):
    if context.orders is None:
        raise RuntimeError("Planner execution requires an OrderSet")
    return context.orders


def _plan(context: TurnContext):
    if context.strategic_plan is None:
        raise RuntimeError("Planner execution requires a strategic plan")
    return context.strategic_plan


def _research_decision(context: TurnContext):
    if context.research_decision is None:
        raise RuntimeError("Research planner requires a research decision")
    return context.research_decision


class ScoutSplitPreflightPlanner:
    """Queue mandatory singleton scout splits before ordinary movement planning."""

    name = "scout_split_preflight"

    def plan(self, context: TurnContext) -> None:
        prepare_scout_split_precedence(context.state, _orders(context))


class DiplomacyPlanner:
    """Delegate diplomacy proposals to the existing diplomacy strategy."""

    name = "diplomacy"

    def plan(self, context: TurnContext) -> None:
        add_diplomacy_orders(context.state, _orders(context), _plan(context))


class MilitaryPlanner:
    """Delegate military and territorial-response proposals unchanged."""

    name = "military"

    def plan(self, context: TurnContext) -> None:
        add_military_orders(context.state, _orders(context), _plan(context))


class TacticalFleetPlanner:
    """Delegate tactical fleet management unchanged."""

    name = "tactical_fleet_management"

    def plan(self, context: TurnContext) -> None:
        add_tactical_fleet_management(context.state, _orders(context), _plan(context))


class EconomyPlanner:
    """Delegate production, colonization, and logistics proposals unchanged."""

    name = "economy"

    def plan(self, context: TurnContext) -> None:
        add_economic_orders(
            context.state,
            _orders(context),
            _plan(context),
            research_decision=_research_decision(context),
        )


class ExplorationPlanner:
    """Delegate exploration and scout-route proposals unchanged."""

    name = "exploration"

    def plan(self, context: TurnContext) -> None:
        add_exploration_orders(
            context.state,
            _orders(context),
            _plan(context),
            memory=context.memory,
        )


class ResearchPlanner:
    """Emit the already-selected research decision as a semantic order."""

    name = "research"

    def plan(self, context: TurnContext) -> None:
        add_research_orders(
            context.state,
            _orders(context),
            _plan(context),
            decision=_research_decision(context),
        )


class DesignDevelopmentPlanner:
    """Delegate ship and starbase design-development proposals unchanged."""

    name = "design_development"

    def plan(self, context: TurnContext) -> None:
        add_design_development_orders(context.state, _orders(context), _plan(context))


class StrategicLookaheadPlanner:
    """Apply the existing V4 strategic lookahead augmentation."""

    name = "strategic_lookahead"

    def plan(self, context: TurnContext) -> None:
        augment_orders_v4(context.state, _orders(context), _plan(context))


class ReconDeconflictionPolicy:
    """Arbitrate duplicate reconnaissance destinations after proposals exist."""

    name = "recon_deconfliction"

    def plan(self, context: TurnContext) -> None:
        deconflict_recon_orders(context.state, _orders(context))


class RouteWarpOptimizationPolicy:
    """Normalize active route warps after all planning proposals exist."""

    name = "route_warp_optimization"

    def plan(self, context: TurnContext) -> None:
        optimize_active_route_warps(context.state, _orders(context))


class FuelSafetyPolicy:
    """Apply the existing fuel-safety vetoes and fallback handling."""

    name = "fuel_safety"

    def plan(self, context: TurnContext) -> None:
        apply_fuel_safety(context.state, _orders(context))


class FleetActivityPolicy:
    """Ensure every owned fleet has an objective, continuation, hold, or block."""

    name = "fleet_activity"

    def plan(self, context: TurnContext) -> None:
        context.legacy_fleet_intents = ensure_fleet_activity(
            context.state,
            _orders(context),
            _plan(context),
        )


class ScoutSplitEnforcementPolicy:
    """Make mandatory scout splitting win after all ordinary route proposals."""

    name = "scout_split_enforcement"

    def plan(self, context: TurnContext) -> None:
        enforce_scout_split_precedence(context.state, _orders(context))


# This ordering is the legacy ``StarsAgent.play_turn`` order, made explicit.
DEFAULT_PLANNERS: tuple[TurnPlanner, ...] = (
    ScoutSplitPreflightPlanner(),
    DiplomacyPlanner(),
    MilitaryPlanner(),
    TacticalFleetPlanner(),
    EconomyPlanner(),
    ExplorationPlanner(),
    ResearchPlanner(),
    DesignDevelopmentPlanner(),
    StrategicLookaheadPlanner(),
)


# Policies operate on proposed orders and therefore stay distinct from planners.
DEFAULT_POST_PLANNING_POLICIES: tuple[TurnPlanner, ...] = (
    ReconDeconflictionPolicy(),
    RouteWarpOptimizationPolicy(),
    FuelSafetyPolicy(),
    FleetActivityPolicy(),
    ScoutSplitEnforcementPolicy(),
)


def run_pipeline(context: TurnContext, stages: Iterable[TurnPlanner]) -> None:
    """Run an ordered planner or policy sequence without adding a dispatcher layer."""

    for stage in stages:
        stage.plan(context)
