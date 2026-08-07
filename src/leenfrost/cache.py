"""Leenfrost SCS cache — exact evidence ($0) vs structure pattern (template reuse)."""

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
    signature_summary,
    structure_hash,
)

_DB_PATH = Path(".leenfrost") / "scs_cache.db"
_DEFAULT_TTL_SEC = 86_400


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
    hit_kind: str = "exact"  # exact | structure
    structure_hash: str | None = None
    evidence_hash: str | None = None
    template: str | None = None
    bypass: bool = True  # only True for exact $0 path


def _conn() -> sqlite3.Connection:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(_DB_PATH)
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
            template TEXT,
            created_at REAL,
            hits INTEGER DEFAULT 0
        )
        """
    )
    # migrate template column if older DB
    cols = {r[1] for r in c.execute("PRAGMA table_info(LEENFROST_CACHE)").fetchall()}
    if "template" not in cols:
        c.execute("ALTER TABLE LEENFROST_CACHE ADD COLUMN template TEXT")
    if "structure_hash" not in cols:
        c.execute("ALTER TABLE LEENFROST_CACHE ADD COLUMN structure_hash TEXT")
    if "evidence_hash" not in cols:
        c.execute("ALTER TABLE LEENFROST_CACHE ADD COLUMN evidence_hash TEXT")
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_scs_structure ON LEENFROST_CACHE(structure_hash)"
    )
    c.commit()
    return c


def lookup(conversation: Conversation, *, ttl_sec: int = _DEFAULT_TTL_SEC) -> CacheHit | None:
    """Exact evidence match → $0 model path."""
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
            "UPDATE LEENFROST_CACHE SET hits = hits + 1 WHERE signature = ?", (sig,)
        )
        c.commit()
        return CacheHit(
            signature=str(row["signature"]),
            original_tokens=int(row["original_tokens"]),
            final_tokens=int(row["final_tokens"]),
            tokens_saved=int(row["tokens_saved"]),
            savings_pct=float(row["savings_pct"]),
            model_selected=str(row["model_selected"]),
            budget_action=str(row["budget_action"]),
            hits=int(row["hits"]) + 1,
            hit_kind="exact",
            structure_hash=row["structure_hash"],
            evidence_hash=row["evidence_hash"],
            template=row["template"],
            bypass=True,
        )
    finally:
        c.close()


def lookup_structure(
    conversation: Conversation, *, ttl_sec: int = _DEFAULT_TTL_SEC
) -> CacheHit | None:
    """Structural pattern match — template reuse only. NEVER $0 model bypass."""
    st = structure_hash(conversation)
    ev = evidence_hash(conversation)
    c = _conn()
    try:
        row = c.execute(
            """
            SELECT * FROM LEENFROST_CACHE
            WHERE structure_hash = ?
              AND signature != ?
              AND template IS NOT NULL
              AND length(template) > 20
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (st, ev),
        ).fetchone()
        if not row:
            # fallback: any same structure even without template
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
        c.execute(
            "UPDATE LEENFROST_CACHE SET hits = hits + 1 WHERE signature = ?",
            (row["signature"],),
        )
        c.commit()
        return CacheHit(
            signature=str(row["signature"]),
            original_tokens=int(row["original_tokens"]),
            final_tokens=int(row["final_tokens"]),
            tokens_saved=int(row["tokens_saved"]),
            savings_pct=float(row["savings_pct"]),
            model_selected=str(row["model_selected"]),
            budget_action=str(row["budget_action"]),
            hits=int(row["hits"]) + 1,
            hit_kind="structure",
            structure_hash=st,
            evidence_hash=str(row["evidence_hash"] or ""),
            template=row["template"],
            bypass=False,
        )
    finally:
        c.close()


def build_structure_template(
    *,
    classification: str = "SOC triage pattern",
    mitre: list[str] | None = None,
    actions: list[str] | None = None,
    notes: str = "",
) -> str:
    """Minimal reasoning skeleton bound to CURRENT IOCs by the live model."""
    mitre = mitre or ["T1059", "T1021"]
    actions = actions or [
        "Preserve current IOCs (IPs, hashes, hosts, EventIDs)",
        "Classify severity for THIS alert only",
        "Recommend containment bound to current evidence",
    ]
    lines = [
        "Prior structure pattern (similar alert class — bind to CURRENT IOCs only):",
        f"- Classification skeleton: {classification}",
        f"- MITRE hints: {', '.join(mitre)}",
        "- Recommended action pattern:",
    ]
    for a in actions:
        lines.append(f"  • {a}")
    if notes:
        lines.append(f"- Notes: {notes[:400]}")
    lines.append(
        "Do not copy prior IPs/hashes. Use only artifacts present in the current messages."
    )
    return "\n".join(lines)


def store(
    conversation: Conversation,
    result: GateResult,
    *,
    artifacts: list[str] | None = None,
    template: str | None = None,
) -> str:
    sig = compute_signature(conversation)
    ev = evidence_hash(conversation)
    st = structure_hash(conversation)
    if artifacts is None:
        artifacts = signature_summary(conversation).get("artifacts_sample") or []
    # Default template from current triage shape if not provided
    if not template:
        template = build_structure_template(
            classification="Repeated SOC structure",
            notes=f"Saved from agent={getattr(conversation, 'agent_id', None) or 'soc'}",
        )
    budget_action = getattr(getattr(result, "budget", None), "action", None)
    if budget_action is not None and hasattr(budget_action, "value"):
        budget_action = budget_action.value
    model = getattr(getattr(result, "route", None), "selected_model", "") or ""
    c = _conn()
    try:
        c.execute(
            """
            INSERT INTO LEENFROST_CACHE (
                signature, structure_hash, evidence_hash, agent_id,
                original_tokens, final_tokens, tokens_saved, savings_pct,
                model_selected, budget_action, artifacts_json, template,
                created_at, hits
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
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
                template=COALESCE(excluded.template, LEENFROST_CACHE.template),
                created_at=excluded.created_at
            """,
            (
                sig,
                st,
                ev,
                getattr(conversation, "agent_id", None),
                int(getattr(result.original_estimate, "total_tokens", 0) or 0),
                int(getattr(result, "final_tokens", 0) or 0),
                int(getattr(result, "tokens_saved", 0) or 0),
                float(getattr(result, "savings_percent", 0.0) or 0.0),
                str(model),
                str(budget_action or ""),
                json.dumps(artifacts or []),
                template,
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
        templated = c.execute(
            "SELECT COUNT(*) AS n FROM LEENFROST_CACHE WHERE template IS NOT NULL AND length(template) > 20"
        ).fetchone()["n"]
        return {
            "entries": int(row["n"]),
            "total_hits": int(row["hits"]),
            "templated_entries": int(templated),
        }
    finally:
        c.close()
