"""OpenRouter client — live probes only for verified model ids."""

from __future__ import annotations

import os
from typing import Any

import httpx

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# Verified live in this hackathon run (Muse removed after 403)
DEFAULT_MODELS = [
    "openai/gpt-5.5",
    "minimax/minimax-m3",
    "qwen/qwen3.6-35b-a3b",
    "moonshotai/kimi-k2-0905",
    "nvidia/nemotron-3-ultra-550b-a55b",
    "deepseek/deepseek-v4-flash",
]

COST_PER_MILLION_INPUT: dict[str, float] = {
    "openai/gpt-5.5": 2.50,
    "minimax/minimax-m3": 0.30,
    "qwen/qwen3.6-35b-a3b": 0.10,
    "moonshotai/kimi-k2-0905": 0.60,
    "nvidia/nemotron-3-ultra-550b-a55b": 0.50,
    "deepseek/deepseek-v4-flash": 0.14,
}


def matrix_models() -> list[str]:
    raw = os.environ.get("LEENFROST_MATRIX_MODELS", "")
    if raw.strip():
        return [m.strip() for m in raw.split(",") if m.strip()]
    return list(DEFAULT_MODELS)


def estimate_cost_usd(model: str, tokens: int) -> float:
    rate = COST_PER_MILLION_INPUT.get(model, 1.0)
    return round((tokens / 1_000_000.0) * rate, 6)


def chat_completion(
    model: str,
    messages: list[dict[str, str]],
    *,
    api_key: str | None = None,
    max_tokens: int = 256,
    timeout: float = 90.0,
) -> dict[str, Any]:
    key = api_key or os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise RuntimeError("OPENROUTER_API_KEY not set")
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/Cademiller23/leenfrost",
        "X-Title": "Leenfrost",
    }
    body = {"model": model, "messages": messages, "max_tokens": max_tokens}
    with httpx.Client(timeout=timeout) as client:
        resp = client.post(OPENROUTER_URL, headers=headers, json=body)
        resp.raise_for_status()
        return resp.json()
