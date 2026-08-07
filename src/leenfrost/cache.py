"""Snowflake Context-Steering (SCS) cache.

On signature hit: return cached gate outcome and skip expensive path ($0 model).
On miss: caller runs full gate and stores the result.
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from leenfrost.models import GateResult
from leenfrost.signature import compute_signature
from leenfrost.models import Conversation

_DB = Path(__file__).resolve().parents[2] / "data" / "leenfrost_cache.db"
_DEFAULT_TTL_SEC = 24 * 3600


def _conn() -> sqlite3.Connection:
    _DB.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(_DB)
    c.row_factory = sqlite3.Row
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS LEENFROST_CACHE (
            signature TEXT PRIMARY KEY,
            agent_id TEXT,
            original_tokens INTEGER,
            final_tokens INTEGER,
            tokens_saved INTEGER,
            savings_pct REAL,
            model_selected TEXT,
            budget_action TEXT,
            artifacts_json TEXT,
            created_at REAL,
            hits INTEGER DEFAULT 0
        )
        """
    )
    c.commit()
    return c


@dataclass(frozen=True)
class CacheHit:
    signature: str
    original_tokens: int
    final_tokens: int
    tokens_saved: int
    savings_pct: float
    model_selected: str
    budget_action: str
    hits: int
    bypass: bool = True  # $0 model path


def lookup(conversation: Conversation, *, ttl_sec: int = _DEFAULT_TTL_SEC) -> CacheHit | None:
    sig = compute_signature(conversation)
    c = _conn()
    try:
        row = c.execute(
            "SELECT * FROM LEENFROST_CACHE WHERE signature = ?", (sig,)
        ).fetchone()
        if not row:
            return None
        if time.time() - float(row["created_at"]) > ttl_sec:
            return None
        c.execute(
            "UPDATE LEENFROST_CACHE SET hits = hits + 1 WHERE signature = ?",
            (sig,),
        )
        c.commit()
        return CacheHit(
            signature=sig,
            original_tokens=int(row["original_tokens"]),
            final_tokens=int(row["final_tokens"]),
            tokens_saved=int(row["tokens_saved"]),
            savings_pct=float(row["savings_pct"]),
            model_selected=str(row["model_selected"]),
            budget_action=str(row["budget_action"]),
            hits=int(row["hits"]) + 1,
        )
    finally:
        c.close()


def store(
    conversation: Conversation,
    result: GateResult,
    *,
    artifacts: list[str] | None = None,
) -> str:
    sig = compute_signature(conversation)
    c = _conn()
    try:
        c.execute(
            """
            INSERT INTO LEENFROST_CACHE (
                signature, agent_id, original_tokens, final_tokens, tokens_saved,
                savings_pct, model_selected, budget_action, artifacts_json, created_at, hits
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
            ON CONFLICT(signature) DO UPDATE SET
                original_tokens=excluded.original_tokens,
                final_tokens=excluded.final_tokens,
                tokens_saved=excluded.tokens_saved,
                savings_pct=excluded.savings_pct,
                model_selected=excluded.model_selected,
                budget_action=excluded.budget_action,
                artifacts_json=excluded.artifacts_json,
                created_at=excluded.created_at
            """,
            (
                sig,
                conversation.agent_id,
                result.original_estimate.total_tokens,
                result.final_tokens,
                result.tokens_saved,
                result.savings_percent,
                result.route.selected_model,
                result.budget.action.value,
                json.dumps(artifacts or []),
                time.time(),
            ),
        )
        c.commit()
    finally:
        c.close()
    return sig


def cache_stats() -> dict[str, Any]:
    c = _conn()
    try:
        total = c.execute("SELECT COUNT(*) AS n FROM LEENFROST_CACHE").fetchone()["n"]
        hits = c.execute("SELECT COALESCE(SUM(hits),0) AS h FROM LEENFROST_CACHE").fetchone()["h"]
        return {"entries": int(total), "total_hits": int(hits)}
    finally:
        c.close()
