"""EverOS integration helpers — search+ROI and writeback. Soft-fail always."""

from __future__ import annotations

import uuid
from typing import Any

from leenfrost.config import LeenfrostConfig, get_config
from leenfrost.everos import (
    conversation_to_everos_messages,
    extract_memory_texts,
    memory_add,
    memory_flush,
    memory_search,
)
from leenfrost.memory_roi import MemoryROIResult, memories_to_system_block, rank_and_select
from leenfrost.models import Message, Role


def _query_from_messages(messages: list[Message], limit: int = 1200) -> str:
    parts = [m.content.strip() for m in messages if m.role != Role.SYSTEM]
    q = "\n".join(parts)
    return q[:limit] if len(q) > limit else q


def retrieve_and_price_memory(
    messages: list[Message],
    *,
    severity: int = 5,
    session_id: str | None = None,  # kept for API compat; NOT sent to search
    config: LeenfrostConfig | None = None,
    strict: bool = False,
) -> tuple[MemoryROIResult, str | None, dict[str, Any]]:
    meta: dict[str, Any] = {
        "everos_ok": False,
        "everos_error": None,
        "returned": 0,
        "admitted": 0,
        "injected": 0,
        "rejected_tok": 0,
    }
    cfg = config or get_config()
    budget = int(getattr(cfg, "memory_token_budget", 200))
    empty = MemoryROIResult(
        returned=0,
        memories_admitted=0,
        admitted=[],
        rejected=[],
        tokens_injected=0,
        tokens_rejected=0,
        budget_tokens=budget,
    )
    try:
        q = _query_from_messages(messages)
        if not q.strip():
            return empty, None, meta
        raw = memory_search(q)  # user_id only — no session_id
        cands = extract_memory_texts(raw)
        roi = rank_and_select(
            cands,
            budget_tokens=budget,
            severity=severity,
            config=cfg,
        )
        block = memories_to_system_block(roi)
        meta.update(
            {
                "everos_ok": True,
                "returned": roi.returned,
                "admitted": roi.memories_admitted,
                "injected": roi.tokens_injected,
                "rejected_tok": roi.tokens_rejected,
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
        if strict:
            raise
        return empty, None, meta


def writeback_memory(
    messages: list[Message],
    *,
    session_id: str | None = None,
    strict: bool = False,
) -> dict[str, Any]:
    meta: dict[str, Any] = {"writeback_ok": False, "error": None, "session_id": None}
    try:
        sid = session_id or f"soc-wb-{uuid.uuid4().hex[:12]}"
        payload = conversation_to_everos_messages(messages)
        if not payload:
            meta["error"] = "no_messages"
            return meta
        add_res = memory_add(payload, session_id=sid)
        flush_res = memory_flush(session_id=sid)
        meta.update(
            {
                "writeback_ok": True,
                "session_id": sid,
                "add": add_res,
                "flush": flush_res,
                "message_count": len(payload),
            }
        )
        return meta
    except Exception as e:
        meta["error"] = f"{type(e).__name__}: {e}"
        if strict:
            raise
        return meta
