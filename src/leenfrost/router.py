"""Severity-aware model router for Leenfrost.

Enterprise policy:
- Critical severity (9–10) never forced to economy by soft budget limits.
  Safety overrides cost.
- High (7–8) uses standard / frontier band.
- Medium/low (1–6) prefers economy when safe.
- Hard budget reject still blocks everyone (including critical) — caller must escalate.
"""

from __future__ import annotations

from leenfrost.config import LeenfrostConfig, get_config
from leenfrost.models import BudgetDecision, EnforcementAction, ModelTier, RouteDecision

# Severity bands (priority field on Conversation)
CRITICAL_MIN = 9   # ransomware, CS beacon, DC compromise
HIGH_MIN = 7
ECONOMY_MAX = 5


def route_model(
    priority: int,
    budget_decision: BudgetDecision | None = None,
    *,
    config: LeenfrostConfig | None = None,
) -> RouteDecision:
    """Select model tier from severity + budget policy.

    Args:
        priority: 1–10 severity / business priority.
        budget_decision: Optional result of evaluate_budget.
        config: Optional config override.
    """
    cfg = config or get_config()
    forced = False
    priority = max(1, min(10, int(priority)))

    # Hard reject — do not route to an expensive path; surface reject upstream
    if budget_decision and budget_decision.action == EnforcementAction.REJECT:
        return RouteDecision(
            selected_tier=ModelTier.ECONOMY,
            selected_model=cfg.economy_model,
            reason=(
                f"Hard budget reject: {budget_decision.reason}. "
                "Routed to economy marker for logging; caller must not execute frontier."
            ),
            priority=priority,
            forced_by_budget=True,
        )

    # Soft limit: force economy UNLESS critical severity (safety override)
    soft_force = bool(
        budget_decision and budget_decision.action == EnforcementAction.FORCE_ECONOMY
    )
    if soft_force and priority < CRITICAL_MIN:
        forced = True
        return RouteDecision(
            selected_tier=ModelTier.ECONOMY,
            selected_model=cfg.economy_model,
            reason=(
                f"Soft budget limit hit; severity {priority} < {CRITICAL_MIN} "
                "so economy is enforced."
            ),
            priority=priority,
            forced_by_budget=True,
        )
    if soft_force and priority >= CRITICAL_MIN:
        # Critical path: acknowledge pressure but keep frontier
        return RouteDecision(
            selected_tier=ModelTier.FRONTIER,
            selected_model=cfg.frontier_model,
            reason=(
                f"Soft budget limit hit BUT severity {priority} is critical — "
                "safety override keeps frontier model."
            ),
            priority=priority,
            forced_by_budget=False,
        )

    # Normal severity routing
    if priority >= CRITICAL_MIN:
        tier = ModelTier.FRONTIER
        model = cfg.frontier_model
        reason = f"Critical severity {priority} ≥ {CRITICAL_MIN} → frontier."
    elif priority >= HIGH_MIN:
        tier = ModelTier.STANDARD
        model = cfg.standard_model
        reason = f"High severity {priority} ≥ {HIGH_MIN} → standard."
    elif priority <= ECONOMY_MAX:
        tier = ModelTier.ECONOMY
        model = cfg.economy_model
        reason = f"Low/medium severity {priority} ≤ {ECONOMY_MAX} → economy."
    else:
        tier = ModelTier.STANDARD
        model = cfg.standard_model
        reason = f"Severity {priority} in standard band."

    return RouteDecision(
        selected_tier=tier,
        selected_model=model,
        reason=reason,
        priority=priority,
        forced_by_budget=forced,
    )
