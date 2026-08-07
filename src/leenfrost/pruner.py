"""Information-density context pruner with system-prompt deduplication."""

from __future__ import annotations
import re
from typing import Sequence
from leenfrost.config import LeenfrostConfig, get_config
from leenfrost.estimator import count_tokens_in_messages
from leenfrost.models import Conversation, Message, PruneResult, Role

_HIGH_VALUE = [
    re.compile(r"\b\d+(\.\d+)?%?\b"),
    re.compile(r"\b(Q[1-4]|FY|YoY|MoM|QoQ|USD|\$|€|£)\b", re.I),
    re.compile(r"\b(if|when|unless|flag any)\b", re.I),
    re.compile(r"\b[A-Z]{2,5}_[A-Z0-9_]+\b"),
    re.compile(r"\b(SELECT|FROM|WHERE|JOIN|SKU-)\b", re.I),
    re.compile(r"\b\d+\.\d+M\b"),
]
_FILLER = re.compile(
    r"^(okay|ok|sure|got it|understood|thanks|thank you|perfect|great|"
    r"alright|hi there|hello|yes|no|yep|nope|please continue|one more thing)[\s\.\!\,]*$",
    re.I,
)


def _is_high_value(text: str) -> bool:
    return any(p.search(text) for p in _HIGH_VALUE)


def _is_filler(text: str) -> bool:
    s = text.strip()
    return len(s) < 25 or bool(_FILLER.match(s))


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def prune_conversation(
    conversation: Conversation,
    *,
    model: str | None = None,
    config: LeenfrostConfig | None = None,
    keep_last_n: int | None = None,
    target_reduction: float | None = None,
) -> PruneResult:
    cfg = config or get_config()
    resolved = model or cfg.default_model
    keep_n = keep_last_n if keep_last_n is not None else cfg.keep_last_n_turns
    target = target_reduction if target_reduction is not None else cfg.min_reduction_target

    original = list(conversation.messages)
    original_tokens = count_tokens_in_messages(original, resolved, cfg)

    # Deduplicate system prompts
    seen_sys: set[str] = set()
    dup_ids: set[int] = set()
    for m in original:
        if m.role == Role.SYSTEM:
            key = _norm(m.content)
            if key in seen_sys:
                dup_ids.add(id(m))
            else:
                seen_sys.add(key)

    non_sys = [m for m in original if m.role != Role.SYSTEM]
    tail = non_sys[-keep_n:] if keep_n else []
    tail_ids = {id(m) for m in tail}

    candidates = []
    for m in original:
        if id(m) in tail_ids:
            continue
        is_dup = id(m) in dup_ids
        if m.role == Role.SYSTEM and not is_dup:
            continue
        score = -20.0 if is_dup else (50.0 if m.role == Role.SYSTEM else 0.0)
        if _is_high_value(m.content):
            score += 14.0
        if _is_filler(m.content):
            score -= 10.0
        score += min(len(m.content) / 120.0, 5.0)
        candidates.append((score, m))

    candidates.sort(key=lambda x: x[0])
    current = list(original)
    current_tokens = original_tokens
    removed: set[int] = set()

    for score, msg in candidates:
        if current_tokens <= original_tokens * (1.0 - target):
            break
        if score >= 10.0:
            continue
        trial = [m for m in current if id(m) != id(msg)]
        t_tokens = count_tokens_in_messages(trial, resolved, cfg)
        if t_tokens < current_tokens:
            removed.add(id(msg))
            current = trial
            current_tokens = t_tokens

    pruned = [m for m in original if id(m) not in removed]
    pruned_tokens = count_tokens_in_messages(pruned, resolved, cfg)
    removed_count = max(0, original_tokens - pruned_tokens)
    ratio = removed_count / original_tokens if original_tokens else 0.0

    return PruneResult(
        original_messages=original,
        pruned_messages=pruned,
        original_tokens=original_tokens,
        pruned_tokens=pruned_tokens,
        tokens_removed=removed_count,
        reduction_ratio=round(ratio, 4),
        kept_system=any(m.role == Role.SYSTEM for m in pruned),
        strategy="density_heuristic_v3_dedup",
    )
