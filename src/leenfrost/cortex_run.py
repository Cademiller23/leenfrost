"""Execute Cortex COMPLETE against the gated prompt via JWT key-pair auth."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from leenfrost.models import Message
from leenfrost.cortex_sql import messages_to_prompt


def _private_key_bytes() -> bytes:
    from cryptography.hazmat.backends import default_backend
    from cryptography.hazmat.primitives import serialization

    path = os.environ.get("SNOWFLAKE_PRIVATE_KEY_PATH")
    if not path or not Path(path).exists():
        raise RuntimeError("SNOWFLAKE_PRIVATE_KEY_PATH missing or file not found")
    with open(path, "rb") as f:
        p_key = serialization.load_pem_private_key(f.read(), password=None, backend=default_backend())
    return p_key.private_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


def connect():
    import snowflake.connector

    account = os.environ.get("SNOWFLAKE_ACCOUNT")
    user = os.environ.get("SNOWFLAKE_USER")
    if not account or not user:
        raise RuntimeError("SNOWFLAKE_ACCOUNT / SNOWFLAKE_USER required")
    conn = snowflake.connector.connect(
        account=account,
        user=user,
        private_key=_private_key_bytes(),
        warehouse=os.environ.get("SNOWFLAKE_WAREHOUSE", "LEENFROST_WH"),
        database=os.environ.get("SNOWFLAKE_DATABASE", "LEENFROST"),
        schema=os.environ.get("SNOWFLAKE_SCHEMA", "PUBLIC"),
    )
    # Explicit session context — connector warehouse= sometimes does not bind
    wh = os.environ.get("SNOWFLAKE_WAREHOUSE", "LEENFROST_WH")
    db = os.environ.get("SNOWFLAKE_DATABASE", "LEENFROST")
    sch = os.environ.get("SNOWFLAKE_SCHEMA", "PUBLIC")
    with conn.cursor() as cur:
        cur.execute(f"USE WAREHOUSE {wh}")
        cur.execute(f"USE DATABASE {db}")
        cur.execute(f"USE SCHEMA {sch}")
    return conn


def run_cortex_complete(
    messages: list[Message],
    model: str = "llama3.1-8b",
) -> dict[str, Any]:
    prompt = messages_to_prompt(messages)
    sql = "SELECT SNOWFLAKE.CORTEX.COMPLETE(%s, %s) AS cortex_response"
    try:
        conn = connect()
    except Exception as e:
        return {
            "ok": False,
            "model": model,
            "response": None,
            "error": f"connect: {type(e).__name__}: {e}",
            "mode": "jwt",
        }
    try:
        with conn.cursor() as cur:
            cur.execute(sql, (model, prompt))
            row = cur.fetchone()
            text = row[0] if row else ""
        return {
            "ok": True,
            "model": model,
            "response": text,
            "error": None,
            "mode": "jwt",
            "prompt_chars": len(prompt),
        }
    except Exception as e:
        return {
            "ok": False,
            "model": model,
            "response": None,
            "error": f"{type(e).__name__}: {e}",
            "mode": "jwt",
            "prompt_chars": len(prompt),
        }
    finally:
        conn.close()
