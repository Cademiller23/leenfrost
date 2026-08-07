"""Structural signature for Snowflake Context-Steering (SCS).

Builds a deterministic fingerprint from high-value cyber artifacts so recurring
alert storms can hit an in-warehouse cache and bypass expensive model calls.
"""

from __future__ import annotations

import hashlib
import re
from typing import Iterable

from leenfrost.models import Conversation, Message

_IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_SHA = re.compile(r"\b[a-fA-F0-9]{32,64}\b")
_MITRE = re.compile(r"\bT\d{4}(?:\.\d{3})?\b")
_EVENT = re.compile(r"\b(?:ID|NET|AC|EVT)-?\d+\b", re.I)
_DOMAIN = re.compile(r"\b(?:[a-z0-9-]+\.)+[a-z]{2,}\b", re.I)
_HOST = re.compile(r"\b(?:WIN|SRV|DC|FILE|ENG)-[A-Z0-9-]+\b", re.I)
_EMAIL = re.compile(r"\b[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}\b", re.I)
_CVE = re.compile(r"\bCVE-\d{4}-\d+\b", re.I)


def extract_artifacts(text: str) -> list[str]:
    """Extract sorted unique high-value artifacts from text."""
    found: set[str] = set()
    for rx in (_IPV4, _SHA, _MITRE, _EVENT, _DOMAIN, _HOST, _EMAIL, _CVE):
        for m in rx.findall(text):
            found.add(m.lower())
    return sorted(found)


def conversation_artifacts(messages: Iterable[Message]) -> list[str]:
    arts: set[str] = set()
    for m in messages:
        arts.update(extract_artifacts(m.content))
    return sorted(arts)


def compute_signature(conversation: Conversation) -> str:
    """SHA256 over sorted artifacts. Empty artifacts → content hash fallback."""
    arts = conversation_artifacts(conversation.messages)
    if not arts:
        raw = "\n".join(f"{m.role.value}:{m.content}" for m in conversation.messages)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()
    payload = "|".join(arts)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def signature_summary(conversation: Conversation) -> dict:
    arts = conversation_artifacts(conversation.messages)
    return {
        "signature": compute_signature(conversation),
        "artifact_count": len(arts),
        "artifacts_sample": arts[:12],
    }
