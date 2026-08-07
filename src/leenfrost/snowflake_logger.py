"""Usage logger for Leenfrost.

Tries Snowflake first. If auth fails (MFA/org policy), falls back to a local
SQLite file that mirrors the exact LEENFROST_USAGE schema so the demo and
dashboard always work.
"""

from __future__ import annotations

import os
import sqlite3
import uuid
from pathlib import Path
from typing import Any

from leenfrost.models import GateResult

_DB_PATH = Path(__file__).resolve().parents[2] / "data" / "leenfrost_usage.db"


def _init_sqlite() -> None:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(_DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS LEENFROST_USAGE (
            call_id TEXT PRIMARY KEY,
            agent_id TEXT,
            conversation_id TEXT,
            original_tokens INTEGER,
            final_tokens INTEGER,
            tokens_saved INTEGER,
            savings_pct REAL,
            model_selected TEXT,
            budget_action TEXT,
            priority INTEGER,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.commit()
    conn.close()


def _log_sqlite(result: GateResult, agent_id: str | None) -> str:
    _init_sqlite()
    call_id = str(uuid.uuid4())
    conn = sqlite3.connect(_DB_PATH)
    conn.execute(
        """
        INSERT INTO LEENFROST_USAGE (
            call_id, agent_id, conversation_id,
            original_tokens, final_tokens, tokens_saved, savings_pct,
            model_selected, budget_action, priority
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            call_id,
            agent_id or "default",
            str(result.conversation_id),
            result.original_estimate.total_tokens,
            result.final_tokens,
            result.tokens_saved,
            result.savings_percent,
            result.route.selected_model,
            result.budget.action.value,
            result.route.priority,
        ),
    )
    conn.commit()
    conn.close()
    return call_id


def _try_snowflake(result: GateResult, agent_id: str | None) -> str | None:
    """Attempt Snowflake. Return call_id on success, None on any auth/network error."""
    try:
        import snowflake.connector
    except ImportError:
        return None

    required = ["SNOWFLAKE_ACCOUNT", "SNOWFLAKE_USER", "SNOWFLAKE_PASSWORD"]
    if not all(os.environ.get(k) for k in required):
        return None

    try:
        conn = snowflake.connector.connect(
            account=os.environ["SNOWFLAKE_ACCOUNT"],
            user=os.environ["SNOWFLAKE_USER"],
            password=os.environ["SNOWFLAKE_PASSWORD"],
            warehouse=os.environ.get("SNOWFLAKE_WAREHOUSE", "LEENFROST_WH"),
            database=os.environ.get("SNOWFLAKE_DATABASE", "LEENFROST"),
            schema=os.environ.get("SNOWFLAKE_SCHEMA", "PUBLIC"),
        )
        call_id = str(uuid.uuid4())
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO LEENFROST_USAGE (
                    call_id, agent_id, conversation_id,
                    original_tokens, final_tokens, tokens_saved, savings_pct,
                    model_selected, budget_action, priority
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    call_id,
                    agent_id or "default",
                    str(result.conversation_id),
                    result.original_estimate.total_tokens,
                    result.final_tokens,
                    result.tokens_saved,
                    result.savings_percent,
                    result.route.selected_model,
                    result.budget.action.value,
                    result.route.priority,
                ),
            )
        conn.commit()
        conn.close()
        return call_id
    except Exception:
        return None


def log_gate_result(result: GateResult, agent_id: str | None = None) -> str:
    """Log to Snowflake when possible, otherwise SQLite. Always succeeds."""
    call_id = _try_snowflake(result, agent_id)
    if call_id:
        return call_id
    return _log_sqlite(result, agent_id)


def fetch_recent_usage(limit: int = 20) -> list[dict[str, Any]]:
    """Read recent rows (SQLite for reliable dashboard)."""
    _init_sqlite()
    conn = sqlite3.connect(_DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT call_id, agent_id, original_tokens, final_tokens,
               tokens_saved, savings_pct, model_selected, budget_action,
               priority, created_at
        FROM LEENFROST_USAGE
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]
