"""Fleet benchmark vs production path.

Production = one gate decision (may be $0 SCS).
Fleet = same gated final_messages sent to every matrix model for fair compare.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any

from leenfrost.models import Conversation, GateResult, Message
from leenfrost.openrouter import chat_completion, estimate_cost_usd, matrix_models
from leenfrost.pipeline import run_gate
from leenfrost.snowflake_logger import log_gate_result


@dataclass
class FleetRow:
    model: str
    status: str  # ok | error | skipped
    latency_s: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    est_input_usd: float = 0.0
    reply_preview: str = ""
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TurnEnvelope:
    """Caller-printable result — no JSON spelunking required."""

    production: dict[str, Any]
    fleet: list[dict[str, Any]] = field(default_factory=list)
    snowflake_call_id: str | None = None
    gated_messages: list[dict[str, str]] = field(default_factory=list)

    def summary_lines(self) -> list[str]:
        p = self.production
        lines = [
            f"production model={p.get('model')} scs_hit={p.get('scs_hit')} "
            f"structure_hit={p.get('structure_hit')}",
            f"tokens raw={p.get('original_tokens')} final={p.get('final_tokens')} "
            f"prune={p.get('prune_savings_pct')}% net={p.get('net_savings_pct')}%",
            f"memory returned={p.get('memory_returned')} admitted={p.get('memory_admitted')} "
            f"injected={p.get('memory_tokens_injected')}",
            f"snowflake_call_id={self.snowflake_call_id}",
        ]
        if p.get("scs_hit"):
            lines.append("PRODUCTION SPEND: $0.00 (exact SCS bypass)")
        for row in self.fleet:
            prev = (row.get("reply_preview") or "")[:100].replace("\n", " ")
            lines.append(
                f"fleet {row.get('model')}: {row.get('status')} "
                f"{row.get('latency_s')}s prompt={row.get('prompt_tokens')} "
                f"${row.get('est_input_usd')} | {prev}"
            )
        return lines


def _messages_for_provider(messages: list[Message]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for m in messages:
        role = m.role.value if hasattr(m.role, "value") else str(m.role)
        if role == "system":
            role = "system"
        out.append({"role": role, "content": str(m.content)})
    return out


def _production_dict(result: GateResult) -> dict[str, Any]:
    return {
        "conversation_id": str(result.conversation_id),
        "model": getattr(result.route, "selected_model", None),
        "budget_action": getattr(getattr(result.budget, "action", None), "value", None),
        "scs_hit": bool(getattr(result, "scs_hit", False)),
        "scs_hit_kind": getattr(result, "scs_hit_kind", "none"),
        "structure_hit": bool(getattr(result, "structure_hit", False)),
        "template_tokens": int(getattr(result, "template_tokens", 0) or 0),
        "original_tokens": int(result.original_estimate.total_tokens),
        "final_tokens": int(result.final_tokens),
        "tokens_after_prune": int(getattr(result, "tokens_after_prune", result.final_tokens) or 0),
        "prune_savings_pct": float(getattr(result, "prune_savings_pct", 0.0) or 0.0),
        "net_savings_pct": float(getattr(result, "net_savings_pct", 0.0) or 0.0),
        "savings_percent": float(getattr(result, "savings_percent", 0.0) or 0.0),
        "memory_returned": int(getattr(result, "memory_returned", 0) or 0),
        "memory_admitted": int(getattr(result, "memory_admitted", 0) or 0),
        "memory_tokens_injected": int(getattr(result, "memory_tokens_injected", 0) or 0),
        "memory_tokens_rejected": int(getattr(result, "memory_tokens_rejected", 0) or 0),
        "production_model_usd": 0.0 if getattr(result, "scs_hit", False) else None,
        "pnl_trace": list(getattr(result, "pnl_trace", None) or []),
    }


def run_fleet_benchmark(
    gated_messages: list[dict[str, str]],
    *,
    gated_token_estimate: int,
    models: list[str] | None = None,
    max_tokens: int = 128,
    timeout: float = 90.0,
) -> list[FleetRow]:
    """Real OpenRouter calls — same messages for every model."""
    rows: list[FleetRow] = []
    for model in models or matrix_models():
        t0 = time.time()
        try:
            data = chat_completion(
                model, gated_messages, max_tokens=max_tokens, timeout=timeout
            )
            latency = round(time.time() - t0, 2)
            text = ""
            try:
                text = (data.get("choices") or [{}])[0].get("message", {}).get("content") or ""
            except Exception:
                text = str(data)[:200]
            usage = data.get("usage") or {}
            prompt_tokens = int(usage.get("prompt_tokens") or gated_token_estimate or 0)
            completion_tokens = int(usage.get("completion_tokens") or 0)
            rows.append(
                FleetRow(
                    model=model,
                    status="ok",
                    latency_s=latency,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    est_input_usd=estimate_cost_usd(model, prompt_tokens),
                    reply_preview=(text or "").strip()[:240],
                )
            )
        except Exception as e:
            latency = round(time.time() - t0, 2)
            rows.append(
                FleetRow(
                    model=model,
                    status="error",
                    latency_s=latency,
                    prompt_tokens=gated_token_estimate,
                    est_input_usd=estimate_cost_usd(model, gated_token_estimate),
                    reply_preview="",
                    error=f"{type(e).__name__}: {e}",
                )
            )
    return rows


def run_turn(
    conversation: Conversation,
    *,
    use_everos: bool = True,
    use_scs: bool = True,
    run_fleet: bool = True,
    log_usage: bool = True,
    fleet_models: list[str] | None = None,
    fleet_max_tokens: int = 128,
) -> TurnEnvelope:
    """
    Production gate once. Optionally benchmark the gated payload across providers.

    On SCS exact hit, production is $0 — fleet still runs on final_messages only if
    you pass run_fleet=True (demo). Production accounting stays $0.
    """
    result = run_gate(conversation, use_everos=use_everos, use_scs=use_scs)

    # Provider messages: on SCS hit final_messages may be original; still ok for demo
    if result.scs_hit:
        # Benchmark the pre-bypass payload would be unfair; use original conversation
        # for fleet only as "what would have been sent" — production remains $0.
        gated_messages = _messages_for_provider(list(conversation.messages))
        gated_tokens = int(result.original_estimate.total_tokens)
    else:
        gated_messages = _messages_for_provider(list(result.final_messages))
        gated_tokens = int(result.final_tokens)

    call_id = None
    if log_usage:
        try:
            call_id = log_gate_result(
                result,
                agent_id=getattr(conversation, "agent_id", None),
                session_id=str(getattr(conversation, "agent_id", None) or conversation.id),
            )
        except Exception:
            call_id = None

    fleet_rows: list[FleetRow] = []
    if run_fleet:
        fleet_rows = run_fleet_benchmark(
            gated_messages,
            gated_token_estimate=gated_tokens,
            models=fleet_models,
            max_tokens=fleet_max_tokens,
        )

    return TurnEnvelope(
        production=_production_dict(result),
        fleet=[r.to_dict() for r in fleet_rows],
        snowflake_call_id=call_id,
        gated_messages=gated_messages,
    )
