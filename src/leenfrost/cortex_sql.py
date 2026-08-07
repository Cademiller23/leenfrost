"""Emit Snowsight-ready Cortex SQL for the last gated payload."""

from __future__ import annotations

from leenfrost.models import Message


def messages_to_prompt(messages: list[Message]) -> str:
    parts: list[str] = []
    for m in messages:
        parts.append(f"{m.role.value.upper()}: {m.content}")
    return "\n".join(parts)


def ai_complete_sql(
    messages: list[Message],
    model: str = "llama3.1-70b",
) -> str:
    """Prefer AI_COMPLETE when available; COMPLETE remains valid fallback in worksheet."""
    prompt = messages_to_prompt(messages).replace("'", "''")
    # AI_COMPLETE signature varies by account; provide both forms.
    return f"""USE DATABASE LEENFROST;
USE WAREHOUSE LEENFROST_WH;

-- Preferred (structured) when AI_COMPLETE is enabled on the account:
-- SELECT SNOWFLAKE.CORTEX.AI_COMPLETE(
--   model => '{model}',
--   prompt => $$ ... $$,
--   response_format => {{ 'type': 'json' }}
-- );

-- Portable demo path (works where COMPLETE is available):
SELECT SNOWFLAKE.CORTEX.COMPLETE(
  '{model}',
  $$
{prompt}
$$
) AS cortex_response;
"""


def complete_sql(messages: list[Message], model: str = "llama3.1-70b") -> str:
    return ai_complete_sql(messages, model=model)
