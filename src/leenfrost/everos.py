"""EverOS Memory API v2 client for Leenfrost."""

from __future__ import annotations

import os
import time
from typing import Any

import httpx

DEFAULT_BASE = "https://api.evermind.ai"


class EverOSError(RuntimeError):
    pass


def _base_url() -> str:
    return (os.environ.get("EVEROS_BASE_URL") or DEFAULT_BASE).rstrip("/")


def _headers() -> dict[str, str]:
    key = os.environ.get("EVEROS_API_KEY")
    if not key:
        raise EverOSError("EVEROS_API_KEY is not set")
    return {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }


def _post(path: str, body: dict[str, Any], *, timeout: float = 90.0) -> dict[str, Any]:
    url = f"{_base_url()}{path}"
    with httpx.Client(timeout=timeout) as client:
        resp = client.post(url, headers=_headers(), json=body)
        if resp.status_code >= 400:
            raise EverOSError(f"EverOS {path} HTTP {resp.status_code}: {resp.text[:800]}")
        if not resp.content:
            return {}
        return resp.json()


def memory_add(
    *,
    session_id: str,
    messages: list[dict[str, Any]],
    app_id: str | None = None,
    project_id: str | None = None,
    async_mode: bool = False,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "session_id": session_id,
        "app_id": app_id or os.environ.get("EVEROS_APP_ID", "default"),
        "project_id": project_id or os.environ.get("EVEROS_PROJECT_ID", "default"),
        "messages": messages,
        "async_mode": async_mode,
    }
    return _post("/api/v2/memory/add", body)


def memory_flush(
    *,
    session_id: str,
    app_id: str | None = None,
    project_id: str | None = None,
) -> dict[str, Any]:
    body = {
        "session_id": session_id,
        "app_id": app_id or os.environ.get("EVEROS_APP_ID", "default"),
        "project_id": project_id or os.environ.get("EVEROS_PROJECT_ID", "default"),
    }
    return _post("/api/v2/memory/flush", body)


def memory_search(
    *,
    query: str,
    user_id: str | None = None,
    agent_id: str | None = None,
    session_id: str | None = None,
    top_k: int = 8,
    method: str = "hybrid",
    app_id: str | None = None,
    project_id: str | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "query": query,
        "top_k": top_k,
        "method": method,
        "app_id": app_id or os.environ.get("EVEROS_APP_ID", "default"),
        "project_id": project_id or os.environ.get("EVEROS_PROJECT_ID", "default"),
    }
    uid = user_id or os.environ.get("EVEROS_USER_ID")
    aid = agent_id or os.environ.get("EVEROS_AGENT_ID")
    # Prefer user_id for human/SOC analyst memory (matches sender_id on messages)
    if uid:
        body["user_id"] = uid
    elif aid:
        body["agent_id"] = aid
    else:
        body["user_id"] = "soc-analyst-1"

    if session_id:
        # Required shape for unprocessed_messages per EverOS docs
        body["filters"] = {"session_id": session_id}

    return _post("/api/v2/memory/search", body)


def messages_for_everos(
    role_contents: list[tuple[str, str]],
    *,
    user_sender_id: str = "soc-analyst-1",
    agent_sender_id: str = "leenfrost-soc",
) -> list[dict[str, Any]]:
    now = int(time.time() * 1000)
    out: list[dict[str, Any]] = []
    for i, (role, content) in enumerate(role_contents):
        sid = user_sender_id if role == "user" else agent_sender_id
        out.append(
            {
                "sender_id": sid,
                "sender_name": sid,
                "role": role,
                "timestamp": now + i * 15_000,
                "content": content,
            }
        )
    return out


def extract_memory_texts(search_response: dict[str, Any]) -> list[dict[str, Any]]:
    data = search_response.get("data") or search_response
    candidates: list[dict[str, Any]] = []

    for ep in data.get("episodes") or []:
        summary = (ep.get("summary") or ep.get("episode") or "").strip()
        score = float(ep.get("score") or 0.6)
        if summary:
            candidates.append(
                {"text": summary, "score": score, "source": "episode", "id": str(ep.get("id") or "")}
            )
        for fact in ep.get("atomic_facts") or []:
            content = (fact.get("content") or "").strip()
            if content:
                candidates.append(
                    {
                        "text": content,
                        "score": float(fact.get("score") or score),
                        "source": "atomic_fact",
                        "id": str(fact.get("id") or ""),
                    }
                )

    for um in data.get("unprocessed_messages") or []:
        content = (um.get("content") or "").strip()
        if len(content) >= 40:
            candidates.append(
                {
                    "text": content,
                    "score": 0.55,
                    "source": "unprocessed_message",
                    "id": str(um.get("id") or ""),
                }
            )

    for key in ("agent_cases", "agent_skills", "profiles"):
        for item in data.get(key) or []:
            if not isinstance(item, dict):
                continue
            text = (
                item.get("key_insight")
                or item.get("description")
                or item.get("content")
                or item.get("summary")
                or ""
            ).strip()
            if text:
                candidates.append(
                    {
                        "text": text,
                        "score": float(item.get("quality_score") or item.get("confidence") or 0.5),
                        "source": key,
                        "id": str(item.get("id") or ""),
                    }
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
