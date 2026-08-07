"""SCS cache: exact evidence → $0 bypass; structure match is informational only."""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from leenfrost.models import Conversation, GateResult
from leenfrost.signature import (
    compute_signature,
    evidence_hash,
    structure_hash,
    signature_summary,
)

_DEFAULT_TTL_SEC = 86400
_DB_PATH = Path("data/leenfrost_cache.db")


@dataclass
class CacheHit:
    signature: str
    original_tokens: int
    final_tokens: int
    tokens_saved: int
    savings_pct: float
    model_selected: str
    budget_action: str
    hits: int
    hit_kind: str = "exact"  # exact | structure
    structure_hash: str = ""
    evidence_hash: str = ""


def _conn() -> sqlite3.Connection:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(_DB_PATH))
    c.row_factory = sqlite3.Row
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS LEENFROST_CACHE (
            signature TEXT PRIMARY KEY,
            structure_hash TEXT,
            evidence_hash TEXT,
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
    # migrate older DBs missing columns
    cols = {r[1] for r in c.execute("PRAGMA table_info(LEENFROST_CACHE)").fetchall()}
    if "structure_hash" not in cols:
        c.execute("ALTER TABLE LEENFROST_CACHE ADD COLUMN structure_hash TEXT")
    if "evidence_hash" not in cols:
        c.execute("ALTER TABLE LEENFROST_CACHE ADD COLUMN evidence_hash TEXT")
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_cache_structure ON LEENFROST_CACHE(structure_hash)"
    )
    c.commit()
    return c


def lookup(conversation: Conversation, *, ttl_sec: int = _DEFAULT_TTL_SEC) -> CacheHit | None:
    """Exact evidence hit only. Structure-only matches never returned here as $0 bypass."""
    sig = compute_signature(conversation)
    ev = evidence_hash(conversation)
    st = structure_hash(conversation)
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
            hit_kind="exact",
            structure_hash=str(row["structure_hash"] or st),
            evidence_hash=str(row["evidence_hash"] or ev),
        )
    finally:
        c.close()


def lookup_structure(
    conversation: Conversation, *, ttl_sec: int = _DEFAULT_TTL_SEC
) -> CacheHit | None:
    """Structural pattern match — informational. Does NOT authorize $0 model bypass."""
    st = structure_hash(conversation)
    ev = evidence_hash(conversation)
    c = _conn()
    try:
        row = c.execute(
            """
            SELECT * FROM LEENFROST_CACHE
            WHERE structure_hash = ? AND signature != ?
            ORDER BY created_at DESC LIMIT 1
            """,
            (st, ev),
        ).fetchone()
        if not row:
            return None
        if time.time() - float(row["created_at"]) > ttl_sec:
            return None
        return CacheHit(
            signature=str(row["signature"]),
            original_tokens=int(row["original_tokens"]),
            final_tokens=int(row["final_tokens"]),
            tokens_saved=int(row["tokens_saved"]),
            savings_pct=float(row["savings_pct"]),
            model_selected=str(row["model_selected"]),
            budget_action=str(row["budget_action"]),
            hits=int(row["hits"]),
            hit_kind="structure",
            structure_hash=st,
            evidence_hash=str(row["evidence_hash"] or ""),
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
    ev = evidence_hash(conversation)
    st = structure_hash(conversation)
    if artifacts is None:
        artifacts = signature_summary(conversation).get("artifacts_sample") or []
    c = _conn()
    try:
        c.execute(
            """
            INSERT INTO LEENFROST_CACHE (
                signature, structure_hash, evidence_hash, agent_id,
                original_tokens, final_tokens, tokens_saved, savings_pct,
                model_selected, budget_action, artifacts_json, created_at, hits
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
            ON CONFLICT(signature) DO UPDATE SET
                structure_hash=excluded.structure_hash,
                evidence_hash=excluded.evidence_hash,
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
                st,
                ev,
                conversation.agent_id,
                result.original_estimate.total_tokens,
                result.final_tokens,
                result.tokens_saved,
                result.savings_percent,
                result.route.selected_model,
                result.budget.action.value,
                json.dumps(artifacts),
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
        row = c.execute(
            "SELECT COUNT(*) AS n, COALESCE(SUM(hits),0) AS hits FROM LEENFROST_CACHE"
        ).fetchone()
        return {"entries": int(row["n"]), "total_hits": int(row["hits"])}
    finally:
        c.close()
