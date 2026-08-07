"""Leenfrost usage logger — Snowflake JWT first, SQLite fallback.

Every successful run_gate should call log_gate_result once.
"""

from __future__ import annotations

import os
import sqlite3
import uuid
from pathlib import Path
from typing import Any

from leenfrost.models import GateResult

_DB_PATH = Path(".leenfrost") / "usage.db"


def _private_key_bytes():
    from cryptography.hazmat.backends import default_backend
    from cryptography.hazmat.primitives import serialization

    key_path = os.environ.get("SNOWFLAKE_PRIVATE_KEY_PATH")
    if not key_path or not Path(key_path).expanduser().exists():
        return None
    with open(Path(key_path).expanduser(), "rb") as f:
        p_key = serialization.load_pem_private_key(
            f.read(), password=None, backend=default_backend()
        )
    return p_key.private_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


def _snowflake_connect():
    import snowflake.connector

    account = os.environ.get("SNOWFLAKE_ACCOUNT")
    user = os.environ.get("SNOWFLAKE_USER")
    if not account or not user:
        raise RuntimeError("SNOWFLAKE_ACCOUNT / SNOWFLAKE_USER missing")

    warehouse = os.environ.get("SNOWFLAKE_WAREHOUSE") or "COMPUTE_WH"
    database = os.environ.get("SNOWFLAKE_DATABASE") or "LEENFROST"
    schema = os.environ.get("SNOWFLAKE_SCHEMA") or "PUBLIC"

    pkb = _private_key_bytes()
    kwargs: dict[str, Any] = {
        "account": account,
        "user": user,
        "warehouse": warehouse,
        "database": database,
        "schema": schema,
    }
    if pkb is not None:
        kwargs["private_key"] = pkb
    else:
        password = os.environ.get("SNOWFLAKE_PASSWORD")
        if not password:
            raise RuntimeError("No Snowflake private key or password")
        kwargs["password"] = password

    conn = snowflake.connector.connect(**kwargs)
    # Ensure context (JWT sessions sometimes lack warehouse)
    with conn.cursor() as cur:
        for wh in (warehouse, "COMPUTE_WH", "LEENFROST_WH"):
            try:
                cur.execute(f'USE WAREHOUSE "{wh}"')
                break
            except Exception:
                continue
        try:
            cur.execute(f'USE DATABASE "{database}"')
            cur.execute(f'USE SCHEMA "{schema}"')
        except Exception:
            pass
    return conn


def _row_from_result(
    result: GateResult,
    *,
    agent_id: str | None,
    session_id: str | None,
    turn_index: int | None,
    call_id: str,
) -> dict[str, Any]:
    budget_action = getattr(getattr(result, "budget", None), "action", None)
    if budget_action is not None and hasattr(budget_action, "value"):
        budget_action = budget_action.value
    model = getattr(getattr(result, "route", None), "selected_model", None)
    original = int(
        getattr(getattr(result, "original_estimate", None), "total_tokens", 0)
        or getattr(result, "raw_tokens", 0)
        or 0
    )
    final = int(getattr(result, "final_tokens", 0) or 0)
    return {
        "call_id": call_id,
        "agent_id": agent_id or getattr(result, "agent_id", None),
        "conversation_id": str(getattr(result, "conversation_id", "") or ""),
        "session_id": session_id,
        "turn_index": turn_index,
        "original_tokens": original,
        "tokens_after_prune": int(getattr(result, "tokens_after_prune", 0) or 0),
        "memory_tokens_injected": int(getattr(result, "memory_tokens_injected", 0) or 0),
        "final_tokens": final,
        "tokens_saved": int(getattr(result, "tokens_saved", max(0, original - final)) or 0),
        "prune_savings_pct": float(getattr(result, "prune_savings_pct", 0.0) or 0.0),
        "net_savings_pct": float(getattr(result, "net_savings_pct", 0.0) or 0.0),
        "savings_pct": float(
            getattr(result, "savings_percent", getattr(result, "net_savings_pct", 0.0)) or 0.0
        ),
        "model_selected": model,
        "budget_action": str(budget_action) if budget_action is not None else None,
        "priority": int(getattr(getattr(result, "route", None), "priority", 0) or 0),
        "scs_hit": bool(getattr(result, "scs_hit", False)),
        "everos_returned": int(getattr(result, "memory_returned", 0) or 0),
        "everos_admitted": int(getattr(result, "memory_admitted", 0) or 0),
    }


def _init_sqlite() -> None:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(_DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS LEENFROST_USAGE (
            call_id TEXT PRIMARY KEY,
            agent_id TEXT,
            conversation_id TEXT,
            session_id TEXT,
            turn_index INTEGER,
            original_tokens INTEGER,
            tokens_after_prune INTEGER,
            memory_tokens_injected INTEGER,
            final_tokens INTEGER,
            tokens_saved INTEGER,
            prune_savings_pct REAL,
            net_savings_pct REAL,
            savings_pct REAL,
            model_selected TEXT,
            budget_action TEXT,
            priority INTEGER,
            scs_hit INTEGER,
            everos_returned INTEGER,
            everos_admitted INTEGER,
            created_at TEXT DEFAULT (datetime('now'))
        )
        """
    )
    conn.commit()
    conn.close()


def _log_sqlite(row: dict[str, Any]) -> str:
    _init_sqlite()
    conn = sqlite3.connect(_DB_PATH)
    conn.execute(
        """
        INSERT OR REPLACE INTO LEENFROST_USAGE (
            call_id, agent_id, conversation_id, session_id, turn_index,
            original_tokens, tokens_after_prune, memory_tokens_injected, final_tokens,
            tokens_saved, prune_savings_pct, net_savings_pct, savings_pct,
            model_selected, budget_action, priority, scs_hit,
            everos_returned, everos_admitted
        ) VALUES (
            :call_id, :agent_id, :conversation_id, :session_id, :turn_index,
            :original_tokens, :tokens_after_prune, :memory_tokens_injected, :final_tokens,
            :tokens_saved, :prune_savings_pct, :net_savings_pct, :savings_pct,
            :model_selected, :budget_action, :priority, :scs_hit,
            :everos_returned, :everos_admitted
        )
        """,
        {
            **row,
            "scs_hit": 1 if row.get("scs_hit") else 0,
        },
    )
    conn.commit()
    conn.close()
    return row["call_id"]


def _log_snowflake(row: dict[str, Any]) -> str:
    sql = """
        INSERT INTO LEENFROST_USAGE (
            call_id, agent_id, conversation_id, session_id, turn_index,
            original_tokens, tokens_after_prune, memory_tokens_injected, final_tokens,
            tokens_saved, prune_savings_pct, net_savings_pct, savings_pct,
            model_selected, budget_action, priority, scs_hit,
            everos_returned, everos_admitted
        ) VALUES (
            %(call_id)s, %(agent_id)s, %(conversation_id)s, %(session_id)s, %(turn_index)s,
            %(original_tokens)s, %(tokens_after_prune)s, %(memory_tokens_injected)s, %(final_tokens)s,
            %(tokens_saved)s, %(prune_savings_pct)s, %(net_savings_pct)s, %(savings_pct)s,
            %(model_selected)s, %(budget_action)s, %(priority)s, %(scs_hit)s,
            %(everos_returned)s, %(everos_admitted)s
        )
    """
    conn = _snowflake_connect()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, row)
        conn.commit()
    finally:
        conn.close()
    return row["call_id"]


def log_gate_result(
    result: GateResult,
    *,
    agent_id: str | None = None,
    session_id: str | None = None,
    turn_index: int | None = None,
    call_id: str | None = None,
) -> str:
    """Prefer Snowflake; fall back to SQLite. Always returns call_id."""
    cid = call_id or str(uuid.uuid4())
    row = _row_from_result(
        result,
        agent_id=agent_id,
        session_id=session_id,
        turn_index=turn_index,
        call_id=cid,
    )
    try:
        _log_snowflake(row)
        row["_backend"] = "snowflake"
        return cid
    except Exception as e:
        # Soft-fail to SQLite so demo never loses telemetry
        cid2 = _log_sqlite(row)
        # stash last error for diagnostics
        try:
            Path(".leenfrost").mkdir(parents=True, exist_ok=True)
            Path(".leenfrost/last_sf_error.txt").write_text(f"{type(e).__name__}: {e}")
        except Exception:
            pass
        return cid2


def fetch_recent_usage(limit: int = 20) -> list[dict[str, Any]]:
    """Try Snowflake first, else SQLite."""
    try:
        conn = _snowflake_connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT call_id, agent_id, conversation_id, session_id, turn_index,
                           original_tokens, tokens_after_prune, memory_tokens_injected,
                           final_tokens, tokens_saved, prune_savings_pct, net_savings_pct,
                           savings_pct, model_selected, budget_action, priority, scs_hit,
                           everos_returned, everos_admitted, created_at
                    FROM LEENFROST_USAGE
                    ORDER BY created_at DESC
                    LIMIT %s
                    """,
                    (limit,),
                )
                cols = [d[0].lower() for d in cur.description]
                return [dict(zip(cols, row)) for row in cur.fetchall()]
        finally:
            conn.close()
    except Exception:
        _init_sqlite()
        conn = sqlite3.connect(_DB_PATH)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT call_id, agent_id, conversation_id, session_id, turn_index,
                   original_tokens, tokens_after_prune, memory_tokens_injected,
                   final_tokens, tokens_saved, prune_savings_pct, net_savings_pct,
                   savings_pct, model_selected, budget_action, priority, scs_hit,
                   everos_returned, everos_admitted, created_at
            FROM LEENFROST_USAGE
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
