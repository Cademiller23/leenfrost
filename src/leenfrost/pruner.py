"""Information-density pruner for Leenfrost.

Removes repeated system prompts and low-density turns while preserving
IOC-bearing content (IPs, hashes, hosts, MITRE, Event IDs, emails).
"""

from __future__ import annotations

import re
from typing import Sequence

from leenfrost.config import LeenfrostConfig, get_config
from leenfrost.estimator import count_tokens_in_messages, count_tokens_in_text
from leenfrost.models import Conversation, Message, PruneResult, Role

PRUNER_VERSION = "density_heuristic_v4_ioc"

# High-value cyber artifact patterns — never drop a message that matches
_IOC_RE = re.compile(
    r"("
    r"\b(?:(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\b"  # IPv4
    r"|\b(?:[A-Fa-f0-9]{32}|[A-Fa-f0-9]{40}|[A-Fa-f0-9]{64})\b"  # MD5/SHA1/SHA256
    r"|\bT\d{4}(?:\.\d{3})?\b"  # MITRE
    r"|\bEvent(?:ID)?\s*:?\s*\d{3,5}\b"
    r"|\bEVT:\d+\b"
    r"|\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"  # email
    r"|\b(?:WIN|DESKTOP|LAPTOP|SRV|JUMP|ADMIN|FILE|ETL)-[A-Z0-9-]+\b"
    r"|\b(?:SHA256|MD5|SHA1)\b"
    r"|\bMITRE\b"
    r"|\b(?:powershell|cmd\.exe|rundll32|wmic|vssadmin)\b"
    r")",
    re.IGNORECASE,
)

_LOW_DENSITY_RE = re.compile(
    r"^\s*("
    r"thanks|thank you|thx|ok|okay|got it|cool|sure|please repeat|"
    r"can you repeat|hello|hi there|hey|good morning|good afternoon|"
    r"sounds good|acknowledged|continue|go on|yes|no|yep|nope"
    r")[\s.!?]*$",
    re.IGNORECASE,
)


def _norm_system(content: str) -> str:
    return re.sub(r"\s+", " ", content.strip().lower())


def _has_ioc(content: str) -> bool:
    return _IOC_RE.search(content) is not None


def _is_low_density(content: str) -> bool:
    s = content.strip()
    if len(s) < 8:
        return True
    if _LOW_DENSITY_RE.match(s):
        return True
    # Very short non-IOC chatter
    if len(s) < 40 and not _has_ioc(s) and not any(c.isdigit() for c in s):
        return True
    return False


def prune_messages(
    messages: Sequence[Message],
    *,
    model: str | None = None,
    config: LeenfrostConfig | None = None,
    keep_last_n: int | None = None,
    target_reduction: float | None = None,
) -> PruneResult:
    cfg = config or get_config()
    model_name = model or cfg.default_model
    keep_n = keep_last_n if keep_last_n is not None else 6

    original = list(messages)
    original_tokens = count_tokens_in_messages(original, model=model_name)

    if not original:
        return PruneResult(
            original_messages=[],
            pruned_messages=[],
            original_tokens=0,
            pruned_tokens=0,
            tokens_removed=0,
            reduction_ratio=0.0,
            kept_system=False,
            strategy=PRUNER_VERSION,
        )

    # 1) Keep a single system message (first non-empty); drop duplicate systems
    system_msg: Message | None = None
    non_system: list[Message] = []
    seen_systems: set[str] = set()
    for m in original:
        if m.role == Role.SYSTEM:
            key = _norm_system(m.content)
            if system_msg is None and m.content.strip():
                system_msg = m
                seen_systems.add(key)
            elif key not in seen_systems:
                # Prefer longer system if first was tiny
                if system_msg and len(m.content) > len(system_msg.content) * 1.2:
                    system_msg = m
                seen_systems.add(key)
            # else: duplicate system dropped
        else:
            non_system.append(m)

    # 2) Drop low-density turns unless they carry IOCs
    filtered: list[Message] = []
    for m in non_system:
        if _has_ioc(m.content):
            filtered.append(m)
            continue
        if _is_low_density(m.content):
            continue
        filtered.append(m)

    # 3) Keep last N non-system messages (IOC messages already prioritized by retention)
    if len(filtered) > keep_n:
        # Always retain any IOC message even outside last N
        head, tail = filtered[:-keep_n], filtered[-keep_n:]
        retained_head = [m for m in head if _has_ioc(m.content)]
        # Cap retained head to avoid blow-ups
        if len(retained_head) > 8:
            retained_head = retained_head[-8:]
        filtered = retained_head + tail

    pruned: list[Message] = []
    if system_msg is not None:
        pruned.append(system_msg)
    pruned.extend(filtered)

    # Guarantee Conversation validation: at least one non-system
    if not any(m.role != Role.SYSTEM for m in pruned):
        pruned.append(Message(role=Role.USER, content="."))

    pruned_tokens = count_tokens_in_messages(pruned, model=model_name)
    removed = max(0, original_tokens - pruned_tokens)
    ratio = (removed / original_tokens) if original_tokens else 0.0

    # Optional target_reduction: if we are far below target and still have
    # non-IOC mid-history, drop oldest non-IOC non-system until closer
    if target_reduction is not None and ratio < target_reduction and original_tokens > 0:
        non_sys = [m for m in pruned if m.role != Role.SYSTEM]
        sys_part = [m for m in pruned if m.role == Role.SYSTEM]
        while non_sys and ratio < target_reduction:
            # Drop oldest non-IOC
            drop_idx = next((i for i, m in enumerate(non_sys) if not _has_ioc(m.content)), None)
            if drop_idx is None:
                break
            non_sys.pop(drop_idx)
            candidate = sys_part + non_sys
            if not any(m.role != Role.SYSTEM for m in candidate):
                break
            pt = count_tokens_in_messages(candidate, model=model_name)
            removed = max(0, original_tokens - pt)
            ratio = removed / original_tokens
            pruned = candidate
            pruned_tokens = pt

    return PruneResult(
        original_messages=original,
        pruned_messages=pruned,
        original_tokens=original_tokens,
        pruned_tokens=pruned_tokens,
        tokens_removed=max(0, original_tokens - pruned_tokens),
        reduction_ratio=round(ratio, 4),
        kept_system=system_msg is not None,
        strategy=PRUNER_VERSION,
    )


def prune_conversation(
    conversation: Conversation,
    *,
    model: str | None = None,
    config: LeenfrostConfig | None = None,
    keep_last_n: int | None = None,
    target_reduction: float | None = None,
) -> PruneResult:
    return prune_messages(
        conversation.messages,
        model=model,
        config=config,
        keep_last_n=keep_last_n,
        target_reduction=target_reduction,
    )


def prune_messages_list(
    messages: Sequence[Message],
    *,
    model: str | None = None,
    config: LeenfrostConfig | None = None,
    keep_last_n: int | None = None,
    target_reduction: float | None = None,
) -> PruneResult:
    msgs = list(messages)
    if not any(m.role != Role.SYSTEM for m in msgs):
        msgs = msgs + [Message(role=Role.USER, content=".")]
    return prune_messages(
        msgs,
        model=model,
        config=config,
        keep_last_n=keep_last_n,
        target_reduction=target_reduction,
    )
