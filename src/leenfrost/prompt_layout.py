"""Prefix-cache-aware prompt construction.

Stable prefix (policy + schema + admitted memories) then dynamic suffix (live alert).
Does not claim ownership of provider GPU KV cache — only constructs for cacheability.
"""

from __future__ import annotations

from leenfrost.models import Message, Role


STABLE_POLICY = (
    "You are a SOC triage agent. Preserve all IOCs, hashes, hosts, MITRE techniques, "
    "and event IDs. Reply with concise structured triage."
)

STABLE_SCHEMA = (
    "Output fields: classification, severity (1-10), mitre, actions, live_artifacts."
)


def build_prefix_suffix(
    final_messages: list[Message],
    memory_block: str | None = None,
) -> tuple[list[Message], list[Message]]:
    """Split into stable prefix messages and dynamic suffix messages."""
    prefix: list[Message] = [
        Message(role=Role.SYSTEM, content=STABLE_POLICY),
        Message(role=Role.SYSTEM, content=STABLE_SCHEMA),
    ]
    if memory_block:
        prefix.append(Message(role=Role.SYSTEM, content=memory_block))

    # Dynamic: non-system turns + any extra system that is not our stable policy
    suffix: list[Message] = []
    for m in final_messages:
        if m.role == Role.SYSTEM and m.content in (STABLE_POLICY, STABLE_SCHEMA):
            continue
        if memory_block and m.role == Role.SYSTEM and m.content == memory_block:
            continue
        suffix.append(m)
    return prefix, suffix


def ordered_for_provider(
    final_messages: list[Message],
    memory_block: str | None = None,
) -> list[Message]:
    prefix, suffix = build_prefix_suffix(final_messages, memory_block)
    return prefix + suffix
