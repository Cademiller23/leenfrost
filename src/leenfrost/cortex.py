"""Snowflake Cortex path for Leenfrost.

Sends the *pruned* message list through Cortex COMPLETE so the warehouse
is on the critical path — not only used for logging.

Auth reality: many trial orgs force MFA on password auth. We therefore:
1. Try snowflake.connector when credentials work
2. Always expose the exact SQL for Snowsight (you are already authenticated there)
"""

from __future__ import annotations

import os
from typing import Any

from leenfrost.models import Message

# Cortex model names available broadly; override with LEENFROST_CORTEX_MODEL
DEFAULT_CORTEX_MODEL = "llama3.1-70b"


def messages_to_prompt(messages: list[Message]) -> str:
    """Flatten chat turns into a single COMPLETE prompt."""
    parts: list[str] = []
    for m in messages:
        parts.append(f"{m.role.value.upper()}:\n{m.content}")
    parts.append("ASSISTANT:")
    return "\n\n".join(parts)


def build_complete_sql(prompt: str, model: str | None = None) -> str:
    """Return runnable Snowsight SQL. Prompt is dollar-quoted safely."""
    model = model or os.environ.get("LEENFROST_CORTEX_MODEL", DEFAULT_CORTEX_MODEL)
    # Dollar-quote to avoid escaping nightmares
    return f"""
SELECT SNOWFLAKE.CORTEX.COMPLETE(
  '{model}',
  $${prompt}$$
) AS cortex_response;
""".strip()


def complete_via_connector(
    messages: list[Message],
    *,
    model: str | None = None,
) -> dict[str, Any]:
    """Live Cortex COMPLETE through the Python connector.

    Raises on auth/network errors so callers can fall back to SQL.
    """
    import snowflake.connector

    model = model or os.environ.get("LEENFROST_CORTEX_MODEL", DEFAULT_CORTEX_MODEL)
    prompt = messages_to_prompt(messages)
    sql = "SELECT SNOWFLAKE.CORTEX.COMPLETE(%s, %s) AS cortex_response"

    conn = snowflake.connector.connect(
        account=os.environ["SNOWFLAKE_ACCOUNT"],
        user=os.environ["SNOWFLAKE_USER"],
        password=os.environ["SNOWFLAKE_PASSWORD"],
        warehouse=os.environ.get("SNOWFLAKE_WAREHOUSE", "LEENFROST_WH"),
        database=os.environ.get("SNOWFLAKE_DATABASE", "LEENFROST"),
        schema=os.environ.get("SNOWFLAKE_SCHEMA", "PUBLIC"),
    )
    try:
        with conn.cursor() as cur:
            cur.execute(sql, (model, prompt))
            row = cur.fetchone()
            text = row[0] if row else ""
        return {
            "ok": True,
            "model": model,
            "response": text,
            "prompt_chars": len(prompt),
            "mode": "connector",
        }
    finally:
        conn.close()


def complete_pruned(
    messages: list[Message],
    *,
    model: str | None = None,
) -> dict[str, Any]:
    """Best-effort Cortex call. Never raises — returns SQL fallback on failure."""
    model = model or os.environ.get("LEENFROST_CORTEX_MODEL", DEFAULT_CORTEX_MODEL)
    prompt = messages_to_prompt(messages)
    sql = build_complete_sql(prompt, model=model)

    try:
        if all(os.environ.get(k) for k in ("SNOWFLAKE_ACCOUNT", "SNOWFLAKE_USER", "SNOWFLAKE_PASSWORD")):
            result = complete_via_connector(messages, model=model)
            result["sql_fallback"] = sql
            return result
    except Exception as e:
        return {
            "ok": False,
            "model": model,
            "error": f"{type(e).__name__}: {e}",
            "sql_fallback": sql,
            "prompt_chars": len(prompt),
            "mode": "sql_fallback",
        }

    return {
        "ok": False,
        "model": model,
        "error": "Missing Snowflake credentials in environment",
        "sql_fallback": sql,
        "prompt_chars": len(prompt),
        "mode": "sql_fallback",
    }
