"""Priority model router."""

from __future__ import annotations
from leenfrost.config import LeenfrostConfig, get_config
from leenfrost.models import BudgetDecision, EnforcementAction, ModelTier, RouteDecision


def route_model(priority: int, budget_decision: BudgetDecision | None = None, *, config: LeenfrostConfig | None = None) -> RouteDecision:
    cfg = config or get_config()
    forced = False
    if budget_decision and budget_decision.action == EnforcementAction.FORCE_ECONOMY:
        forced = True
        tier, model, reason = ModelTier.ECONOMY, cfg.economy_model, "Budget force-economy"
    elif priority >= cfg.priority_frontier_threshold:
        tier, model, reason = ModelTier.FRONTIER, cfg.frontier_model, f"Priority {priority} ≥ frontier"
    elif priority <= cfg.priority_economy_threshold:
        tier, model, reason = ModelTier.ECONOMY, cfg.economy_model, f"Priority {priority} ≤ economy"
    else:
        tier, model, reason = ModelTier.STANDARD, cfg.standard_model, f"Priority {priority} standard band"
    return RouteDecision(selected_tier=tier, selected_model=model, reason=reason, priority=priority, forced_by_budget=forced)
