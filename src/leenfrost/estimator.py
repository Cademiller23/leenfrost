"""Production token estimation using tiktoken."""

from __future__ import annotations
import logging
from functools import lru_cache
from typing import Sequence
import tiktoken
from leenfrost.config import LeenfrostConfig, get_config
from leenfrost.models import Conversation, Message, TokenEstimate, Role

logger = logging.getLogger(__name__)
_TOKENS_PER_MESSAGE = 3
_TOKENS_PER_NAME = 1
_REPLY_PRIMING = 3


@lru_cache(maxsize=32)
def _get_encoding(model: str, fallback: str) -> tiktoken.Encoding:
    try:
        return tiktoken.encoding_for_model(model)
    except KeyError:
        return tiktoken.get_encoding(fallback)


def count_tokens_in_text(text: str, model: str, config: LeenfrostConfig | None = None) -> int:
    cfg = config or get_config()
    enc = _get_encoding(model, cfg.fallback_encoding)
    return len(enc.encode(text))


def count_tokens_in_messages(messages: Sequence[Message], model: str, config: LeenfrostConfig | None = None) -> int:
    cfg = config or get_config()
    enc = _get_encoding(model, cfg.fallback_encoding)
    n = 0
    for m in messages:
        n += _TOKENS_PER_MESSAGE
        n += len(enc.encode(m.role.value))
        n += len(enc.encode(m.content))
        if m.name:
            n += _TOKENS_PER_NAME + len(enc.encode(m.name))
    n += _REPLY_PRIMING
    return n


def estimate_conversation(conversation: Conversation, model: str | None = None, config: LeenfrostConfig | None = None) -> TokenEstimate:
    cfg = config or get_config()
    resolved = model or cfg.default_model
    total = count_tokens_in_messages(conversation.messages, resolved, cfg)
    try:
        name = tiktoken.encoding_for_model(resolved).name
    except KeyError:
        name = cfg.fallback_encoding
    return TokenEstimate(
        total_tokens=total,
        prompt_tokens=total,
        message_count=len(conversation.messages),
        model=resolved,
        encoding_name=name,
    )


def clear_encoding_cache() -> None:
    _get_encoding.cache_clear()
