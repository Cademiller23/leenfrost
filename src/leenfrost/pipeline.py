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
    BudgetDecision,
    Conversation,
    EnforcementAction,
    GateResult,
    Message,
    ModelTier,
    Role,
    RouteDecision,
)
from leenfrost.pruner import prune_conversation
from leenfrost.router import route_model
from leenfrost.signature import signature_summary


def _economics(raw: int, after_prune: int, provider: int) -> dict[str, Any]:
    raw = max(0, int(raw))
    after_prune = max(0, int(after_prune))
    provider = max(0, int(provider))
    prune_pct = round(((raw - after_prune) / raw) * 100, 2) if raw else 0.0
    net_pct = round(((raw - provider) / raw) * 100, 2) if raw else 0.0
    return {
        "tokens_after_prune": after_prune,
        "prune_savings_pct": prune_pct,
        "net_savings_pct": net_pct,
        "savings_percent": net_pct,
        "raw_tokens": raw,
        "provider_prompt_tokens": provider,
    }


def _empty_roi(budget_tokens: int) -> MemoryROIResult:
    return MemoryROIResult(
        returned=0,
        memories_admitted=0,
        admitted=[],
        rejected=[],
        tokens_injected=0,
        tokens_rejected=0,
        budget_tokens=budget_tokens,
    )


def _fetch_everos_memories(
    conversation: Conversation,
    *,
    budget_tokens: int,
    config: LeenfrostConfig,
) -> tuple[MemoryROIResult, str | None, dict[str, Any]]:
    """Search EverOS and apply Memory ROI. Never raises — empty on failure."""
    meta: dict[str, Any] = {
        "everos_ok": False,
        "memories_returned": 0,
        "memories_admitted": 0,
        "memory_tokens_injected": 0,
        "memory_tokens_rejected": 0,
    }
    empty = _empty_roi(budget_tokens)
    if not os.environ.get("EVEROS_API_KEY"):
        meta["everos_skip"] = "EVEROS_API_KEY not set"
        meta["log"] = "everos: skipped (no API key)"
        return empty, None, meta
    try:
        user_id = os.environ.get("EVEROS_USER_ID", "soc-analyst-1")
        query_parts = [m.content for m in conversation.messages if m.role != Role.SYSTEM]
        query = "\n".join(query_parts)[:1500] or "SOC triage"
        session_id = str(conversation.agent_id or conversation.id)
        raw = memory_search(query, user_id=user_id)
        cands = extract_memory_texts(raw)
        roi = rank_and_select(
            cands,
            budget_tokens=budget_tokens,
            severity=int(conversation.priority or 5),
            config=config,
        )
        block = memories_to_system_block(roi)
        meta.update(
            {
                "everos_ok": True,
                "memories_returned": roi.returned,
                "memories_admitted": roi.memories_admitted,
                "memory_tokens_injected": roi.tokens_injected,
                "memory_tokens_rejected": roi.tokens_rejected,
                "log": (
                    f"everos: returned={roi.returned} admitted={roi.memories_admitted} "
                    f"injected={roi.tokens_injected} rejected_tok={roi.tokens_rejected}"
                ),
            }
        )
        return roi, block, meta
    except Exception as e:
        meta["everos_error"] = f"{type(e).__name__}: {e}"
        meta["log"] = f"everos: error {meta['everos_error']}"
        return empty, None, meta


def _writeback_everos(conversation: Conversation, result: GateResult) -> dict[str, Any]:
    """Persist triage turn to EverOS for future ROI search. Soft-fail."""
    out: dict[str, Any] = {"writeback": False}
    if not os.environ.get("EVEROS_API_KEY"):
        out["writeback_skip"] = "no key"
        return out
    try:
        user_id = os.environ.get("EVEROS_USER_ID", "soc-analyst-1")
        session_id = str(conversation.agent_id or conversation.id)
        # Compact writeback: system note + conversation tail + outcome economics
        outcome = (
            f"Leenfrost triage outcome: savings={result.savings_percent}% "
            f"final_tokens={result.final_tokens} model={result.route.selected_model} "
            f"scs_hit={getattr(result, 'scs_hit', False)}"
        )
        base = list(conversation.messages)
        base.append(Message(role=Role.ASSISTANT, content=outcome))
        msgs = messages_for_everos(base)
        memory_add(msgs, session_id=session_id, user_id=user_id)
        memory_flush(session_id=session_id, user_id=user_id)
        out["writeback"] = True
        out["session_id"] = session_id
        out["message_count"] = len(msgs)
        return out
    except Exception as e:
        out["writeback_error"] = f"{type(e).__name__}: {e}"
        return out


def _build_scs_hit_result(
    conversation: Conversation,
    original,
    budget: BudgetConfig | None,
    cfg: LeenfrostConfig,
) -> GateResult:
    """Exact evidence hit — $0 model path, memory skipped."""
    try:
        budget_decision = BudgetDecision(
            allowed=True,
            action=EnforcementAction.ALLOW,
            reason="SCS exact evidence hit — model path bypassed ($0 model spend)",
            estimated_tokens=0,
            remaining_daily=budget.remaining_daily_tokens if budget else cfg.max_tokens_per_day,
            soft_limit_hit=False,
        )
    except Exception:
        budget_decision = evaluate_budget(original, budget, config=cfg)

    route = RouteDecision(
        selected_tier=ModelTier.ECONOMY,
        selected_model="scs-cache-bypass",
        reason="Exact SCS signature hit — $0 model spend",
        priority=conversation.priority,
        forced_by_budget=False,
    )
    econ = _economics(original.total_tokens, 0, 0)
    econ["net_savings_pct"] = 100.0 if original.total_tokens > 0 else 0.0
    econ["savings_percent"] = econ["net_savings_pct"]
    econ["prune_savings_pct"] = 0.0

    return GateResult(
        conversation_id=conversation.id,
        original_estimate=original,
        pruned=None,
        budget=budget_decision,
        route=route,
        final_messages=list(conversation.messages),
        final_tokens=0,
        tokens_saved=original.total_tokens,
        savings_percent=econ["savings_percent"],
        estimated_cost_usd=0.0,
        tokens_after_prune=0,
        prune_savings_pct=0.0,
        net_savings_pct=econ["net_savings_pct"],
        raw_tokens=original.total_tokens,
        provider_prompt_tokens=0,
        memory_returned=0,
        memory_admitted=0,
        memory_tokens_injected=0,
        memory_tokens_rejected=0,
        scs_hit=True,
        scs_hit_kind="exact",
        pnl_trace=[
            f"RAW {original.total_tokens}",
            "SCS EXACT HIT",
            "MODEL TOKENS 0",
            "MODEL COST $0",
            "MEMORY ROI skipped (exact evidence)",
        ],
    )


def run_gate(
    conversation: Conversation,
    *,
    budget: BudgetConfig | None = None,
    config: LeenfrostConfig | None = None,
    use_everos: bool = True,
    use_scs: bool = True,
    memory_budget_tokens: int | None = None,
    writeback: bool = True,
) -> GateResult:
    """Full control plane.

    Order:
      1. SCS exact evidence lookup → $0 bypass on hit
      2. EverOS search + Memory ROI (optional inject, budget-capped)
      3. Density prune
      4. Budget + severity route
      5. Store SCS entry
      6. Optional EverOS writeback (non-hit, allowed)
    """
    cfg = config or get_config()
    mem_budget = int(
        memory_budget_tokens
        if memory_budget_tokens is not None
        else getattr(cfg, "memory_token_budget", 200)
    )

    original = estimate_conversation(conversation, config=cfg)

    # --- 1) SCS Level-0 exact cache ---
    if use_scs:
        hit = lookup(conversation)
        if hit is not None:
            return _build_scs_hit_result(conversation, original, budget, cfg)

    # --- 2) EverOS + Memory ROI ---
    everos_meta: dict[str, Any] = {
        "everos_ok": False,
        "memories_returned": 0,
        "memories_admitted": 0,
        "memory_tokens_injected": 0,
        "memory_tokens_rejected": 0,
        "log": "everos: skipped",
    }
    work_messages = list(conversation.messages)
    if use_everos:
        _roi, block, everos_meta = _fetch_everos_memories(
            conversation,
            budget_tokens=mem_budget,
            config=cfg,
        )
        if block:
            work_messages = [Message(role=Role.SYSTEM, content=block)] + work_messages

    # --- 3) Density prune ---
    work_conv = Conversation(
        messages=work_messages,
        priority=conversation.priority,
        agent_id=conversation.agent_id,
        id=conversation.id,
    )
    pruned_result = prune_conversation(work_conv, config=cfg)
    final_messages = list(pruned_result.pruned_messages)
    final_tokens = int(pruned_result.pruned_tokens)

    # Pre-prune working set (history + admitted memory) for honest prune %
    try:
        pre_prune_tokens = count_tokens_in_messages(work_messages, model=cfg.default_model)
    except TypeError:
        pre_prune_tokens = count_tokens_in_messages(work_messages)
    pre_prune_tokens = int(pre_prune_tokens)

    # --- 4) Budget + route ---
    token_est = estimate_conversation(
        Conversation(
            messages=final_messages,
            priority=conversation.priority,
            agent_id=conversation.agent_id,
        ),
        config=cfg,
    )
    budget_cfg = budget
    budget_decision = evaluate_budget(token_est, budget_cfg, config=cfg)
    route = route_model(conversation.priority, budget_decision, config=cfg)

    # If rejected, still return economics for observability
    tokens_saved = max(0, original.total_tokens - final_tokens)
    econ = _economics(pre_prune_tokens, final_tokens, final_tokens)
    # Also expose raw conversation baseline for net vs original user payload
    net_vs_raw = _economics(original.total_tokens, final_tokens, final_tokens)
    econ['raw_tokens'] = original.total_tokens
    econ['net_savings_pct'] = net_vs_raw['net_savings_pct']
    econ['savings_percent'] = econ['prune_savings_pct']  # headline = prune of working set
    econ['tokens_after_prune'] = final_tokens

    try:
        cost_saved = compute_savings(
            original, final_tokens, model=route.selected_model, config=cfg
        )
        if isinstance(cost_saved, tuple):
            # older signature variants
            cost_val = cost_saved[-1] if cost_saved else 0.0
        else:
            cost_val = float(cost_saved) if cost_saved is not None else 0.0
    except Exception:
        cost_val = 0.0

    mem_returned = int(everos_meta.get("memories_returned") or 0)
    mem_admitted = int(everos_meta.get("memories_admitted") or 0)
    mem_inj = int(everos_meta.get("memory_tokens_injected") or 0)
    mem_rej = int(everos_meta.get("memory_tokens_rejected") or 0)

    pnl = [
        f"RAW {original.total_tokens}",
        everos_meta.get("log")
        or f"EVEROS returned {mem_returned} / admitted {mem_admitted}",
        f"AFTER PRUNE {pruned_result.pruned_tokens} (prune_savings {econ['prune_savings_pct']}%)",
        f"MEMORY ROI +{mem_inj} (rejected_tok {mem_rej})",
        f"PROVIDER {final_tokens} (net_savings {econ['net_savings_pct']}%)",
        f"ROUTE {route.selected_model}",
    ]

    result = GateResult(
        conversation_id=conversation.id,
        original_estimate=original,
        pruned=pruned_result,
        budget=budget_decision,
        route=route,
        final_messages=final_messages,
        final_tokens=final_tokens,
        tokens_saved=tokens_saved,
        savings_percent=econ["savings_percent"],
        estimated_cost_usd=cost_val if isinstance(cost_val, (int, float)) else 0.0,
        tokens_after_prune=econ["tokens_after_prune"],
        prune_savings_pct=econ["prune_savings_pct"],
        net_savings_pct=econ["net_savings_pct"],
        raw_tokens=econ["raw_tokens"],
        provider_prompt_tokens=econ["provider_prompt_tokens"],
        memory_returned=mem_returned,
        memory_admitted=mem_admitted,
        memory_tokens_injected=mem_inj,
        memory_tokens_rejected=mem_rej,
        scs_hit=False,
        scs_hit_kind="none",
        pnl_trace=pnl,
    )

    # --- 5) Always persist SCS entry on successful allow (so next turn can hit) ---
    if budget_decision.allowed:
        try:
            sig = signature_summary(conversation)
            store(
                conversation,
                result,
                artifacts=sig.get("artifacts_sample") or [],
            )
        except Exception:
            pass

    # --- 6) EverOS writeback ---
    if writeback and use_everos and budget_decision.allowed:
        wb = _writeback_everos(conversation, result)
        everos_meta.update(wb)

    return result
