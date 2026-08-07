"""Budget gate for Leenfrost."""

from __future__ import annotations
from leenfrost.config import LeenfrostConfig, get_config
from leenfrost.models import BudgetConfig, BudgetDecision, EnforcementAction, TokenEstimate


def evaluate_budget(estimate: TokenEstimate, budget: BudgetConfig | None = None, *, config: LeenfrostConfig | None = None) -> BudgetDecision:
    cfg = config or get_config()
    b = budget or BudgetConfig(
        max_tokens_per_call=cfg.max_tokens_per_call,
        max_tokens_per_day=cfg.max_tokens_per_day,
        soft_limit_ratio=cfg.soft_limit_ratio,
        remaining_daily_tokens=cfg.max_tokens_per_day,
    )
    est = estimate.total_tokens
    remaining = b.remaining_daily_tokens
    soft_threshold = int(b.max_tokens_per_day * b.soft_limit_ratio)

    if est > b.max_tokens_per_call:
        return BudgetDecision(action=EnforcementAction.REJECT, allowed=False,
            reason=f"Estimated {est} exceeds per-call limit {b.max_tokens_per_call}",
            estimated_tokens=est, remaining_daily=remaining)
    if est > remaining:
        return BudgetDecision(action=EnforcementAction.REJECT, allowed=False,
            reason=f"Estimated {est} exceeds remaining daily {remaining}",
            estimated_tokens=est, remaining_daily=remaining, soft_limit_hit=True)

    used = b.max_tokens_per_day - remaining
    soft = used >= soft_threshold
    if soft:
        return BudgetDecision(action=EnforcementAction.FORCE_ECONOMY, allowed=True,
            reason=f"Soft limit hit ({used}/{b.max_tokens_per_day}). Forcing economy.",
            estimated_tokens=est, remaining_daily=remaining, soft_limit_hit=True)

    return BudgetDecision(action=EnforcementAction.ALLOW, allowed=True,
        reason="Within budget", estimated_tokens=est, remaining_daily=remaining)
