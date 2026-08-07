"""Savings metrics."""

from __future__ import annotations
from leenfrost.config import LeenfrostConfig, get_config
from leenfrost.models import GateResult, TokenEstimate


def compute_savings(original: TokenEstimate, final_tokens: int, *, model: str | None = None, config: LeenfrostConfig | None = None) -> tuple[int, float, float]:
    cfg = config or get_config()
    resolved = model or original.model
    saved = max(0, original.total_tokens - final_tokens)
    pct = round((saved / original.total_tokens) * 100.0, 2) if original.total_tokens else 0.0
    cost = round(saved * cfg.get_input_cost_per_token(resolved), 6)
    return saved, pct, cost


def summarize_gate(result: GateResult) -> dict:
    return {
        "conversation_id": str(result.conversation_id),
        "original_tokens": result.original_estimate.total_tokens,
        "final_tokens": result.final_tokens,
        "tokens_saved": result.tokens_saved,
        "savings_percent": result.savings_percent,
        "model": result.route.selected_model,
        "budget_action": result.budget.action.value,
        "priority": result.route.priority,
    }
