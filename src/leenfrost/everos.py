"""EverOS Memory API v2 client for Leenfrost."""

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


def _ts_ms() -> int:
    return int(time.time() * 1000)


def memory_search(
    query: str,
    *,
    user_id: str | None = None,
    agent_id: str | None = None,
    top_k: int = 12,
    timeout: float = 30.0,
) -> dict[str, Any]:
    url = f"{_base()}/api/v2/memory/search"
    body: dict[str, Any] = {"query": query, "top_k": top_k}
    if agent_id is not None and user_id is None:
        body["agent_id"] = agent_id
    else:
        body["user_id"] = user_id or _user_id()
    with httpx.Client(timeout=timeout) as client:
        resp = client.post(url, headers=_headers(), json=body)
        if resp.status_code >= 400:
            raise EverOSError(f"search {resp.status_code}: {resp.text[:500]}")
        return resp.json()


def memory_add(
    messages: list[dict[str, Any]],
    *,
    session_id: str,
    user_id: str | None = None,
    agent_id: str | None = None,
    timeout: float = 30.0,
) -> dict[str, Any]:
    url = f"{_base()}/api/v2/memory/add"
    uid = user_id or _user_id()
    aid = agent_id or _agent_id()
    base_ts = _ts_ms()
    normalized: list[dict[str, Any]] = []
    for i, m in enumerate(messages):
        role = str(m.get("role") or "user").lower()
        # EverOS chat roles: map system → user content prefix (system role often rejected)
        content = str(m.get("content") or "")
        if not content.strip():
            continue
        if role == "system":
            role = "user"
            content = f"[system] {content}"
        if role not in ("user", "assistant", "tool"):
            role = "user"
        sender = str(m.get("sender_id") or (uid if role == "user" else aid))
        ts = m.get("timestamp")
        if ts is None:
            ts = base_ts + i
        else:
            ts = int(ts)
            if ts < 1_000_000_000_000:
                ts *= 1000
        normalized.append(
            {
                "role": role,
                "content": content,
                "sender_id": sender,
                "timestamp": ts,
            }
        )
    if not normalized:
        raise EverOSError("memory_add: no messages after normalization")
    body = {
        "session_id": str(session_id)[:64],
        "user_id": uid,
        "messages": normalized,
    }
    with httpx.Client(timeout=timeout) as client:
        resp = client.post(url, headers=_headers(), json=body)
        if resp.status_code >= 400:
            raise EverOSError(f"add {resp.status_code}: {resp.text[:500]}")
        return resp.json()


def memory_flush(
    *,
    session_id: str,
    user_id: str | None = None,
    agent_id: str | None = None,
    timeout: float = 60.0,
) -> dict[str, Any]:
    url = f"{_base()}/api/v2/memory/flush"
    body: dict[str, Any] = {"session_id": str(session_id)[:64]}
    if agent_id is not None and user_id is None:
        body["agent_id"] = agent_id
    else:
        body["user_id"] = user_id or _user_id()
    with httpx.Client(timeout=timeout) as client:
        resp = client.post(url, headers=_headers(), json=body)
        if resp.status_code >= 400:
            raise EverOSError(f"flush {resp.status_code}: {resp.text[:500]}")
        return resp.json()


def extract_memory_texts(search_payload: dict[str, Any]) -> list[dict[str, Any]]:
    data = search_payload.get("data") if isinstance(search_payload, dict) else None
    if data is None and isinstance(search_payload, dict):
        data = search_payload
    if not isinstance(data, dict):
        return []
    candidates: list[dict[str, Any]] = []
    for key in ("episodes", "profiles", "agent_cases", "agent_skills", "unprocessed_messages"):
        for item in data.get(key) or []:
            if not isinstance(item, dict):
                continue
            text = str(
                item.get("summary")
                or item.get("content")
                or item.get("text")
                or item.get("memory")
                or ""
            ).strip()
            if not text:
                continue
            score = float(
                item.get("score") or item.get("quality_score") or item.get("confidence") or 0.55
            )
            candidates.append(
                {"text": text, "score": score, "source": key, "id": str(item.get("id") or "")}
            )
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for c in candidates:
        k = c["text"][:180]
        if k in seen:
            continue
        seen.add(k)
        unique.append(c)
    return unique


def conversation_to_everos_messages(messages: list[Any]) -> list[dict[str, Any]]:
    """Only user/assistant for writeback; system folded into user prefix."""
    uid = _user_id()
    aid = _agent_id()
    base_ts = _ts_ms()
    out: list[dict[str, Any]] = []
    for i, m in enumerate(messages):
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
        role_l = role.lower()
        if role_l == "system":
            role_l = "user"
            content = f"[system] {content}"
        if role_l not in ("user", "assistant"):
            role_l = "user"
        sender = uid if role_l == "user" else aid
        out.append(
            {
                "role": role_l,
                "content": content,
                "sender_id": sender,
                "timestamp": base_ts + i,
            }
        )
    return out


def messages_for_everos(messages: list[Any]) -> list[dict[str, Any]]:
    return conversation_to_everos_messages(messages)
