"""Write every Leenfrost gate result into Snowflake LEENFROST_USAGE."""

from __future__ import annotations

import os
import uuid
from typing import Any

from leenfrost.models import GateResult


def _get_connection():
    """Create a Snowflake connection from environment variables.

    Required env vars:
        SNOWFLAKE_ACCOUNT, SNOWFLAKE_USER, SNOWFLAKE_PASSWORD
        SNOWFLAKE_WAREHOUSE (default LEENFROST_WH)
        SNOWFLAKE_DATABASE  (default LEENFROST)
        SNOWFLAKE_SCHEMA    (default PUBLIC)
    """
    try:
        import snowflake.connector
    except ImportError as e:
        raise ImportError(
            "snowflake-connector-python is required. "
            "Install with: pip install 'leenfrost[snowflake]'"
        ) from e

    return snowflake.connector.connect(
        account=os.environ["SNOWFLAKE_ACCOUNT"],
        user=os.environ["SNOWFLAKE_USER"],
        password=os.environ["SNOWFLAKE_PASSWORD"],
        warehouse=os.environ.get("SNOWFLAKE_WAREHOUSE", "LEENFROST_WH"),
        database=os.environ.get("SNOWFLAKE_DATABASE", "LEENFROST"),
        schema=os.environ.get("SNOWFLAKE_SCHEMA", "PUBLIC"),
    )


def log_gate_result(result: GateResult, agent_id: str | None = None) -> str:
    """Insert one row into LEENFROST_USAGE. Returns the call_id."""
    call_id = str(uuid.uuid4())
    agent = agent_id or "default"

    sql = """
        INSERT INTO LEENFROST_USAGE (
            call_id, agent_id, conversation_id,
            original_tokens, final_tokens, tokens_saved, savings_pct,
            model_selected, budget_action, priority
        ) VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
        )
    """
    params: tuple[Any, ...] = (
        call_id,
        agent,
        str(result.conversation_id),
        result.original_estimate.total_tokens,
        result.final_tokens,
        result.tokens_saved,
        result.savings_percent,
        result.route.selected_model,
        result.budget.action.value,
        result.route.priority,
    )

    conn = _get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
        conn.commit()
    finally:
        conn.close()

    return call_id


def fetch_recent_usage(limit: int = 20) -> list[dict[str, Any]]:
    """Return the most recent gate calls for the dashboard."""
    sql = """
        SELECT call_id, agent_id, original_tokens, final_tokens,
               tokens_saved, savings_pct, model_selected, budget_action,
               priority, created_at
        FROM LEENFROST_USAGE
        ORDER BY created_at DESC
        LIMIT %s
    """
    conn = _get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, (limit,))
            cols = [d[0].lower() for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
    finally:
        conn.close()
