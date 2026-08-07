"""EverOS Memory API v2 client for Leenfrost.

Cloud: https://api.evermind.ai
Auth: Authorization: Bearer <EVEROS_API_KEY>

Soft-fail: network/API errors raise EverOSError only when strict=True;
callers in the gate use strict=False and continue without memory.
"""

from __future__ import annotations

import os
import time
from typing import Any

import httpx

DEFAULT_BASE = "https://api.evermind.ai"


class EverOSError(RuntimeError):
    pass


def _base() -> str:
    return (os.environ.get("EVEROS_BASE_URL") or DEFAULT_BASE).rstrip("/")


def _headers() -> dict[str, str]:
    key = os.environ.get("EVEROS_API_KEY")
    if not key:
        raise EverOSError("EVEROS_API_KEY not set")
    return {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _agent_id() -> str:
    return os.environ.get("EVEROS_AGENT_ID") or "leenfrost-soc"


def _user_id() -> str:
    return os.environ.get("EVEROS_USER_ID") or "soc-analyst-1"


def memory_add(
    messages: list[dict[str, str]],
    *,
    session_id: str,
    user_id: str | None = None,
    agent_id: str | None = None,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """POST /api/v2/memory/add — persist conversation turns for extraction."""
    url = f"{_base()}/api/v2/memory/add"
    body = {
        "session_id": session_id,
        "agent_id": agent_id or _agent_id(),
        "user_id": user_id or _user_id(),
        "messages": messages,
    }
    with httpx.Client(timeout=timeout) as client:
        resp = client.post(url, headers=_headers(), json=body)
        resp.raise_for_status()
        return resp.json()


def memory_flush(
    *,
    session_id: str,
    user_id: str | None = None,
    agent_id: str | None = None,
    timeout: float = 60.0,
) -> dict[str, Any]:
    """POST /api/v2/memory/flush — force extraction so search can hit."""
    url = f"{_base()}/api/v2/memory/flush"
    body = {
        "session_id": session_id,
        "agent_id": agent_id or _agent_id(),
        "user_id": user_id or _user_id(),
    }
    with httpx.Client(timeout=timeout) as client:
        resp = client.post(url, headers=_headers(), json=body)
        resp.raise_for_status()
        return resp.json()


def memory_search(
    query: str,
    *,
    session_id: str | None = None,
    user_id: str | None = None,
    agent_id: str | None = None,
    top_k: int = 12,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """POST /api/v2/memory/search — retrieve related prior incidents."""
    url = f"{_base()}/api/v2/memory/search"
    body: dict[str, Any] = {
        "query": query,
        "agent_id": agent_id or _agent_id(),
        "user_id": user_id or _user_id(),
        "top_k": top_k,
    }
    if session_id:
        body["session_id"] = session_id
    with httpx.Client(timeout=timeout) as client:
        resp = client.post(url, headers=_headers(), json=body)
        resp.raise_for_status()
        return resp.json()


def extract_memory_texts(search_payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalize EverOS search payload into ROI candidate dicts."""
    data = search_payload.get("data") if isinstance(search_payload, dict) else None
    if data is None and isinstance(search_payload, dict):
        data = search_payload
    if not isinstance(data, dict):
        return []

    candidates: list[dict[str, Any]] = []
    for key in ("episodes", "profiles", "agent_cases", "agent_skills", "unprocessed_messages"):
        items = data.get(key) or []
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            text = (
                item.get("content")
                or item.get("text")
                or item.get("summary")
                or item.get("memory")
                or ""
            )
            text = str(text).strip()
            if not text:
                continue
            score = float(
                item.get("score")
                or item.get("quality_score")
                or item.get("confidence")
                or 0.5
            )
            candidates.append(
                {
                    "text": text,
                    "score": score,
                    "source": key,
                    "id": str(item.get("id") or ""),
                }
            )

    # Dedupe by prefix
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for c in candidates:
        k = c["text"][:180]
        if k in seen:
            continue
        seen.add(k)
        unique.append(c)
    return unique


def conversation_to_everos_messages(messages: list[Any]) -> list[dict[str, str]]:
    """Convert Leenfrost Message objects or dicts to EverOS message list."""
    out: list[dict[str, str]] = []
    for m in messages:
        if hasattr(m, "role") and hasattr(m, "content"):
            role = m.role.value if hasattr(m.role, "value") else str(m.role)
            content = str(m.content)
        elif isinstance(m, dict):
            role = str(m.get("role") or "user")
            content = str(m.get("content") or "")
        else:
            continue
        if not content.strip():
            continue
        out.append({"role": role, "content": content})
    return out
