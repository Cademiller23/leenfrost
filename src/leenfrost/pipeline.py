"""Full Leenfrost pipeline."""

from __future__ import annotations
from leenfrost.budget import evaluate_budget
from leenfrost.config import LeenfrostConfig, get_config
from leenfrost.estimator import count_tokens_in_messages, estimate_conversation
from leenfrost.metrics import compute_savings
from leenfrost.models import BudgetConfig, Conversation, GateResult, Message, TokenEstimate
from leenfrost.pruner import prune_conversation
from leenfrost.router import route_model


def run_gate(
    conversation: Conversation,
    *,
    budget: BudgetConfig | None = None,
    model: str | None = None,
    config: LeenfrostConfig | None = None,
    skip_prune: bool = False,
) -> GateResult:
    cfg = config or get_config()
    resolved = model or cfg.default_model
    original = estimate_conversation(conversation, model=resolved, config=cfg)

    pruned = None
    working: list[Message] = list(conversation.messages)
    if not skip_prune and original.total_tokens > 80:
        pruned = prune_conversation(conversation, model=resolved, config=cfg)
        working = pruned.pruned_messages

    final_tokens = count_tokens_in_messages(working, resolved, cfg)
    post = TokenEstimate(
        total_tokens=final_tokens, prompt_tokens=final_tokens,
        message_count=len(working), model=resolved, encoding_name=original.encoding_name,
    )
    budget_decision = evaluate_budget(post, budget=budget, config=cfg)
    route = route_model(conversation.priority, budget_decision, config=cfg)
    saved, pct, cost = compute_savings(original, final_tokens, model=resolved, config=cfg)

    return GateResult(
        conversation_id=conversation.id,
        original_estimate=original,
        pruned=pruned,
        budget=budget_decision,
        route=route,
        final_messages=working,
        final_tokens=final_tokens,
        tokens_saved=saved,
        savings_percent=pct,
        estimated_cost_usd=cost,
    )
