"""Leenfrost gate pipeline — SCS → EverOS ROI → prune → budget → route → log."""

from __future__ import annotations

import os
from typing import Any

from leenfrost.budget import evaluate_budget
from leenfrost.cache import lookup, lookup_structure, store, build_structure_template
from leenfrost.config import LeenfrostConfig, get_config
from leenfrost.estimator import estimate_conversation
from leenfrost.everos import extract_memory_texts, memory_search
from leenfrost.everos_gate import retrieve_and_price_memory, writeback_memory
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
    PruneResult,
    Role,
    RouteDecision,
    TokenEstimate,
)
from leenfrost.pruner import prune_conversation
from leenfrost.router import route_model
from leenfrost.signature import signature_summary
from leenfrost.snowflake_logger import log_gate_result


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


def _economics(raw: int, after_prune: int, final: int) -> dict[str, float | int]:
    raw = max(0, int(raw))
    after_prune = max(0, int(after_prune))
    final = max(0, int(final))
    prune_pct = round(100.0 * (raw - after_prune) / raw, 2) if raw else 0.0
    net_pct = round(100.0 * (raw - final) / raw, 2) if raw else 0.0
    return {
        "raw_tokens": raw,
        "tokens_after_prune": after_prune,
        "final_tokens": final,
        "tokens_saved": max(0, raw - final),
        "prune_savings_pct": prune_pct,
        "net_savings_pct": net_pct,
        "savings_percent": net_pct,
    }


def _build_scs_hit_result(
    conversation: Conversation,
    original: TokenEstimate,
    budget: BudgetDecision,
    cfg: LeenfrostConfig,
) -> GateResult:
    raw = original.total_tokens
    route = RouteDecision(
        selected_tier=ModelTier.ECONOMY,
        selected_model="scs-cache-bypass",
        reason="Exact SCS evidence hit — model spend $0",
        priority=int(conversation.priority or 5),
        forced_by_budget=False,
    )
    pnl = [
        f"RAW {raw}",
        "SCS exact HIT → model tokens 0, cost $0",
        "EverOS skipped (exact evidence)",
        "Prune skipped (cache return)",
    ]
    return GateResult(
        conversation_id=conversation.id,
        original_estimate=original,
        pruned=None,
        budget=budget,
        route=route,
        final_messages=list(conversation.messages),
        final_tokens=0,
        tokens_saved=raw,
        savings_percent=100.0,
        estimated_cost_usd=0.0,
        tokens_after_prune=0,
        prune_savings_pct=100.0,
        net_savings_pct=100.0,
        memory_returned=0,
        memory_admitted=0,
        memory_tokens_injected=0,
        memory_tokens_rejected=0,
        scs_hit=True,
        scs_hit_kind="exact",
        pnl_trace=pnl,
    )


def _maybe_log(result: GateResult, conversation: Conversation) -> None:
    try:
        log_gate_result(
            result,
            agent_id=getattr(conversation, "agent_id", None),
            session_id=str(getattr(conversation, "agent_id", None) or conversation.id),
        )
    except Exception:
        pass


def run_gate(
    conversation: Conversation,
    *,
    use_everos: bool = True,
    use_scs: bool = True,
    config: LeenfrostConfig | None = None,
    budget: BudgetConfig | None = None,
) -> GateResult:
    """
    Production path:
      SCS exact HIT → $0 model, log, return
      MISS → EverOS search+ROI → inject → prune → estimate → budget → route
           → store SCS → EverOS writeback → Snowflake usage log
    """
    cfg = config or get_config()
    original = estimate_conversation(conversation, model=cfg.default_model, config=cfg)
    budget_cfg = budget or BudgetConfig(
        max_tokens_per_call=cfg.max_tokens_per_call,
        max_tokens_per_day=cfg.max_tokens_per_day,
        soft_limit_ratio=cfg.soft_limit_ratio,
        remaining_daily_tokens=cfg.max_tokens_per_day,
    )
    severity = int(conversation.priority or 5)

    # --- 1) SCS exact hit ---
    if use_scs:
        try:
            hit = lookup(conversation)
        except Exception:
            hit = None
        if hit is not None and getattr(hit, "hit_kind", "exact") == "exact":
            budget_decision = evaluate_budget(original, budget_cfg, config=cfg)
            result = _build_scs_hit_result(conversation, original, budget_decision, cfg)
            _maybe_log(result, conversation)
            return result


    # --- 1b) Structure-hash template reuse (NOT $0) ---
    structure_hit = False
    structure_template: str | None = None
    template_tokens = 0
    if use_scs:
        try:
            st_hit = lookup_structure(conversation)
        except Exception:
            st_hit = None
        if st_hit is not None and st_hit.hit_kind == "structure":
            structure_hit = True
            structure_template = st_hit.template or build_structure_template()
            from leenfrost.estimator import count_tokens_in_text
            try:
                template_tokens = count_tokens_in_text(
                    structure_template, model=cfg.default_model
                )
            except TypeError:
                template_tokens = count_tokens_in_text(structure_template)
            # Inject as system note; model still runs bound to CURRENT IOCs
            work_messages_seed = [
                Message(role=Role.SYSTEM, content=structure_template),
            ]
        else:
            work_messages_seed = []
    else:
        work_messages_seed = []

    # --- 2) EverOS search + ROI (miss path) ---
    mem_returned = 0
    mem_admitted = 0
    mem_inj = 0
    mem_rej = 0
    memory_block: str | None = None
    everos_meta: dict[str, Any] = {"everos_ok": False}
    work_messages: list[Message] = list(work_messages_seed) + list(conversation.messages)

    if use_everos:
        try:
            roi, memory_block, everos_meta = retrieve_and_price_memory(
                conversation.messages,
                severity=severity,
                config=cfg,
            )
            mem_returned = int(roi.returned)
            mem_admitted = int(roi.memories_admitted)
            mem_inj = int(roi.tokens_injected)
            mem_rej = int(roi.tokens_rejected)
            if memory_block:
                work_messages = [
                    Message(role=Role.SYSTEM, content=memory_block),
                    *work_messages,
                ]
        except Exception as e:
            everos_meta = {"everos_ok": False, "everos_error": f"{type(e).__name__}: {e}"}

    # --- 3) Density prune on working set ---
    work_conv = Conversation(
        messages=work_messages,
        priority=conversation.priority,
        agent_id=conversation.agent_id,
        id=conversation.id,
    )
    pruned_result: PruneResult = prune_conversation(
        work_conv, model=cfg.default_model, config=cfg
    )
    final_messages = list(pruned_result.pruned_messages)
    final_tokens = int(pruned_result.pruned_tokens)

    # Pre-prune working-set size for honest prune %
    from leenfrost.estimator import count_tokens_in_messages

    pre_prune_tokens = count_tokens_in_messages(work_messages, model=cfg.default_model, config=cfg)
    if isinstance(pre_prune_tokens, TokenEstimate):
        pre_prune_tokens = pre_prune_tokens.total_tokens

    # --- 4) Budget + route on pruned payload ---
    pruned_est = estimate_conversation(
        Conversation(
            messages=final_messages,
            priority=conversation.priority,
            agent_id=conversation.agent_id,
        ),
        model=cfg.default_model,
        config=cfg,
    )
    budget_decision = evaluate_budget(pruned_est, budget_cfg, config=cfg)
    route = route_model(severity, budget_decision, config=cfg)

    # Economics: prune vs working set; net vs raw conversation
    econ = _economics(int(pre_prune_tokens), final_tokens, final_tokens)
    net_vs_raw = _economics(original.total_tokens, final_tokens, final_tokens)
    prune_pct = float(econ["prune_savings_pct"])
    net_pct = float(net_vs_raw["net_savings_pct"])

    pnl = [
        f"RAW {original.total_tokens}",
        f"EVEROS returned {mem_returned} / admitted {mem_admitted} (+{mem_inj} tok)",
        (f"STRUCTURE hit template_tokens={template_tokens}" if structure_hit else "STRUCTURE miss"),
        f"WORKING_SET {pre_prune_tokens}",
        f"PRUNE → {final_tokens} ({prune_pct}%)",
        f"NET vs raw {net_pct}%",
        f"ROUTE {route.selected_model}",
        f"BUDGET {budget_decision.action.value}",
    ]

    result = GateResult(
        conversation_id=conversation.id,
        original_estimate=original,
        pruned=pruned_result,
        budget=budget_decision,
        route=route,
        final_messages=final_messages,
        final_tokens=final_tokens,
        tokens_saved=max(0, original.total_tokens - final_tokens),
        savings_percent=net_pct,
        estimated_cost_usd=0.0,
        tokens_after_prune=final_tokens,
        prune_savings_pct=prune_pct,
        net_savings_pct=net_pct,
        memory_returned=mem_returned,
        memory_admitted=mem_admitted,
        memory_tokens_injected=mem_inj,
        memory_tokens_rejected=mem_rej,
        scs_hit=False,
        scs_hit_kind="none",
        structure_hit=structure_hit,
        template_tokens=template_tokens,
        structure_template=structure_template,
        pnl_trace=pnl,
    )

    # --- 5) Persist SCS so next identical payload can $0 ---
    if budget_decision.allowed:
        try:
            sig = signature_summary(conversation)
            store(
                conversation,
                result,
                artifacts=sig.get("artifacts_sample") or [],
                template=build_structure_template(
                    classification="SOC structure from live triage",
                    notes=f"priority={severity} model={route.selected_model}",
                ),
            )
        except Exception:
            pass

        # --- 6) EverOS writeback (soft-fail) ---
        if use_everos:
            try:
                writeback_memory(
                    list(conversation.messages),
                    session_id=str(conversation.agent_id or conversation.id),
                )
            except Exception:
                pass

    # --- 7) Snowflake / SQLite usage log ---
    _maybe_log(result, conversation)
    return result
