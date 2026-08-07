"""Leenfrost end-to-end gate: SCS → EverOS → Memory ROI → prune → budget → route."""

from __future__ import annotations

import os
from typing import Any

from leenfrost.budget import evaluate_budget
from leenfrost.cache import lookup, store
from leenfrost.config import LeenfrostConfig, get_config
from leenfrost.estimator import estimate_conversation, count_tokens_in_messages
from leenfrost.everos import (
    extract_memory_texts,
    memory_add,
    memory_flush,
    memory_search,
    messages_for_everos,
)
from leenfrost.memory_roi import MemoryROIResult, memories_to_system_block, rank_and_select
from leenfrost.metrics import compute_savings
from leenfrost.models import (
    BudgetConfig,
    Conversation,
    GateResult,
    Message,
    Role,
)
from leenfrost.pruner import prune_conversation
from leenfrost.router import route_model
from leenfrost.signature import signature_summary


def _build_search_query(conversation: Conversation) -> str:
    parts: list[str] = []
    for m in conversation.messages:
        if m.role == Role.SYSTEM:
            continue
        parts.append(m.content)
    text = " ".join(parts)
    return text[:800] if text.strip() else "security incident triage"


def _fetch_everos_memories(
    conversation: Conversation,
    *,
    severity: int,
    memory_budget_tokens: int,
) -> tuple[MemoryROIResult | None, str | None, dict[str, Any]]:
    """Search EverOS and apply Memory ROI. Never raises — returns empty on failure."""
    meta: dict[str, Any] = {"everos_ok": False}
    if not os.environ.get("EVEROS_API_KEY"):
        meta["everos_skip"] = "EVEROS_API_KEY not set"
        return None, None, meta

    try:
        user_id = os.environ.get("EVEROS_USER_ID", "soc-analyst-1")
        query = _build_search_query(conversation)
        raw = memory_search(query=query, user_id=user_id, top_k=8, method="hybrid")
        candidates = extract_memory_texts(raw)
        roi = rank_and_select(
            candidates,
            budget_tokens=memory_budget_tokens,
            severity=severity,
        )
        block = memories_to_system_block(roi)
        meta.update(
            {
                "everos_ok": True,
                "memories_returned": roi.returned,
                "memories_admitted": roi.memories_admitted,
                "memory_tokens_injected": roi.tokens_injected,
                "memory_tokens_rejected": roi.tokens_rejected,
            }
        )
        return roi, block, meta
    except Exception as e:
        meta["everos_error"] = f"{type(e).__name__}: {e}"
        return None, None, meta


def _writeback_everos(conversation: Conversation, result: GateResult) -> dict[str, Any]:
    """Persist triage turn to EverOS for future ROI search."""
    out: dict[str, Any] = {"writeback": False}
    if not os.environ.get("EVEROS_API_KEY"):
        return out
    try:
        user_id = os.environ.get("EVEROS_USER_ID", "soc-analyst-1")
        session_id = f"gate-{conversation.id}"
        # Compact writeback: system outcome + last user ask
        user_bits = [m.content for m in conversation.messages if m.role == Role.USER]
        last_user = user_bits[-1] if user_bits else "triage request"
        summary = (
            f"Leenfrost gated triage. tokens {result.original_estimate.total_tokens}"
            f"→{result.final_tokens} ({result.savings_percent}% saved). "
            f"model={result.route.selected_model} budget={result.budget.action.value}."
        )
        msgs = messages_for_everos(
            [("user", last_user[:500]), ("assistant", summary)],
            user_sender_id=user_id,
        )
        memory_add(session_id=session_id, messages=msgs, async_mode=False)
        memory_flush(session_id=session_id)
        out["writeback"] = True
        out["session_id"] = session_id
    except Exception as e:
        out["writeback_error"] = f"{type(e).__name__}: {e}"
    return out


def run_gate(
    conversation: Conversation,
    *,
    budget: BudgetConfig | None = None,
    config: LeenfrostConfig | None = None,
    use_everos: bool = True,
    use_scs: bool = True,
    memory_budget_tokens: int = 500,
    writeback: bool = True,
) -> GateResult:
    """Full control plane.

    Order:
      1. SCS exact evidence lookup → $0 bypass on hit
      2. EverOS search + Memory ROI (optional inject)
      3. Density prune
      4. Budget + severity route
      5. Optional EverOS writeback
    """
    cfg = config or get_config()
    severity = conversation.priority

    # --- SCS Level-0 exact cache ---
    if use_scs:
        hit = lookup(conversation)
        if hit is not None:
            # Reconstruct a GateResult-shaped outcome from cache without model spend
            original = estimate_conversation(conversation, config=cfg)
            # Build minimal messages from cache is not stored as full text in all versions;
            # use original conversation pruned path skipped — report cache economics.
            tokens_saved = max(0, hit.original_tokens - hit.final_tokens)
            pct = round(100.0 * tokens_saved / hit.original_tokens, 2) if hit.original_tokens else 0.0
            # Fall through to a synthetic allow decision for API stability
            from leenfrost.models import BudgetDecision, EnforcementAction, RouteDecision, ModelTier

            budget_decision = BudgetDecision(
                allowed=True,
                action=EnforcementAction.ALLOW,
                reason="SCS exact evidence hit — model path bypassed",
                estimated_tokens=hit.final_tokens,
                remaining_daily=budget.remaining_daily_tokens if budget else 0,
                soft_limit_hit=False,
            )
            route = RouteDecision(
                selected_tier=ModelTier.ECONOMY,
                selected_model="scs-cache-bypass",
                reason="Exact SCS signature hit — $0 model spend",
                priority=severity,
                forced_by_budget=False,
            )
            return GateResult(
                conversation_id=conversation.id,
                original_estimate=original,
                pruned=None,
                budget=budget_decision,
                route=route,
                final_messages=list(conversation.messages),
                final_tokens=hit.final_tokens,
                tokens_saved=tokens_saved,
                savings_percent=pct if pct else hit.savings_pct,
                estimated_cost_usd=0.0,
                scs_hit=True,
                scs_hit_kind=getattr(hit, "hit_kind", "exact"),
                pnl_trace=[
                    f"RAW {original.total_tokens}",
                    "SCS EXACT HIT",
                    "MODEL TOKENS 0",
                    "MODEL COST $0",
                ],
            )

    # --- EverOS + Memory ROI ---
    working_messages = list(conversation.messages)
    everos_meta: dict[str, Any] = {}
    if use_everos:
        roi, block, everos_meta = _fetch_everos_memories(
            conversation,
            severity=severity,
            memory_budget_tokens=memory_budget_tokens,
        )
        if block:
            working_messages = [
                Message(role=Role.SYSTEM, content=block),
                *working_messages,
            ]

    # Ensure Conversation validation (needs a non-system message)
    if not any(m.role != Role.SYSTEM for m in working_messages):
        working_messages.append(Message(role=Role.USER, content="."))

    enriched = Conversation(
        messages=working_messages,
        priority=conversation.priority,
        agent_id=conversation.agent_id,
        id=conversation.id,
    )

    original_estimate = estimate_conversation(enriched, config=cfg)

    pruned_result = prune_conversation(enriched, config=cfg)
    if hasattr(pruned_result, "final_messages"):
        final_messages = list(pruned_result.final_messages)
    elif hasattr(pruned_result, "messages"):
        final_messages = list(pruned_result.messages)
    elif hasattr(pruned_result, "pruned_messages"):
        final_messages = list(pruned_result.pruned_messages)
    else:
        raise AttributeError(
            f"PruneResult fields={list(type(pruned_result).model_fields)} "
            "— expected final_messages/messages/pruned_messages"
        )
    final_tokens = pruned_result.pruned_tokens

    budget_cfg = budget or BudgetConfig(
        max_tokens_per_call=cfg.max_tokens_per_call,
        max_tokens_per_day=500_000,
        remaining_daily_tokens=500_000,
    )
    # Re-estimate pruned payload for budget (TokenEstimate, not raw int)
    pruned_conv = Conversation(
        messages=final_messages,
        priority=conversation.priority,
        agent_id=conversation.agent_id,
        id=conversation.id,
    )
    token_est = estimate_conversation(pruned_conv, model=cfg.default_model, config=cfg)
    budget_decision = evaluate_budget(token_est, budget_cfg, config=cfg)
    route = route_model(severity, budget_decision, config=cfg)

    tokens_saved, savings_percent, cost_saved = compute_savings(
        original_estimate, final_tokens, model=route.selected_model, config=cfg
    )

    mem_returned = int(everos_meta.get("memories_returned") or 0)
    mem_admitted = int(everos_meta.get("memories_admitted") or 0)
    mem_inj = int(everos_meta.get("memory_tokens_injected") or 0)
    mem_rej = int(everos_meta.get("memory_tokens_rejected") or 0)
    raw_tok = original_estimate.total_tokens
    pnl = [
        f"RAW {raw_tok}",
        f"EVEROS returned {mem_returned} / admitted {mem_admitted}",
        f"MEMORY ROI +{mem_inj} / rejected {mem_rej}",
        f"PRUNE → {final_tokens}",
        f"MODEL {route.selected_model}",
        f"SAVED {tokens_saved} ({savings_percent}%)",
    ]
    result = GateResult(
        conversation_id=conversation.id,
        original_estimate=original_estimate,
        pruned=pruned_result,
        budget=budget_decision,
        route=route,
        final_messages=final_messages,
        final_tokens=final_tokens,
        tokens_saved=tokens_saved,
        savings_percent=savings_percent,
        estimated_cost_usd=cost_saved,
        memory_returned=mem_returned,
        memory_admitted=mem_admitted,
        memory_tokens_injected=mem_inj,
        memory_tokens_rejected=mem_rej,
        scs_hit=False,
        scs_hit_kind="none",
        pnl_trace=pnl,
    )

    # Attach EverOS economics on the object if model allows extra fields; store via signature path
    if budget_decision.allowed:
        try:
            sig = signature_summary(conversation)
            store(conversation, result, artifacts=sig.get("artifacts_sample") or [])
        except Exception:
            pass

    if writeback and use_everos and budget_decision.allowed:
        wb = _writeback_everos(conversation, result)
        everos_meta.update(wb)

    # Stash meta for dashboard via route.reason extension when useful
    if everos_meta.get("everos_ok"):
        # Non-breaking: reason already set; dashboard can call EverOS metrics separately
        pass

    return result
