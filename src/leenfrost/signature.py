"""Artifact extraction and dual fingerprints for SCS.

evidence_hash  — exact IOCs / hosts / event IDs  → may $0 bypass
structure_hash — MITRE / alert class / lineage   → inform only, never auto answer
Both hashes include policy versions so cache invalidates on policy change.
"""

from __future__ import annotations

import hashlib
import re
from typing import Iterable

from leenfrost.models import Conversation, Message, Role

# Bump these when pruner policy or triage schema changes
PRUNER_VERSION = "density_heuristic_v3_dedup"
POLICY_VERSION = "soc-triage-v1"
SCHEMA_VERSION = "gate-result-v1"
ROUTER_VERSION = "severity-safety-v1"

_IP = re.compile(
    r"\b(?:(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\b"
)
_IPV6 = re.compile(r"\b(?:[0-9a-fA-F]{1,4}:){2,7}[0-9a-fA-F]{1,4}\b")
_MD5 = re.compile(r"\b[a-fA-F0-9]{32}\b")
_SHA1 = re.compile(r"\b[a-fA-F0-9]{40}\b")
_SHA256 = re.compile(r"\b[a-fA-F0-9]{64}\b")
_MITRE = re.compile(r"\bT\d{4}(?:\.\d{3})?\b")
_EVENT = re.compile(r"\b(?:Event(?:ID)?|ID)[-_ ]?(\d{3,6})\b", re.I)
_HOST = re.compile(r"\b(?:WIN|SRV|DC|LAPTOP|HOST|ENG)[-_][A-Z0-9\-_]{2,}\b", re.I)
_EMAIL = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")
_DOMAIN = re.compile(
    r"\b(?:[a-z0-9](?:[a-z0-9\-]{0,61}[a-z0-9])?\.)+(?:com|net|org|io|ai|corp|local|internal)\b",
    re.I,
)
_CVE = re.compile(r"\bCVE-\d{4}-\d{4,7}\b", re.I)
_ALERT = re.compile(r"\b(?:AC|NET|LAT|DNS|MAL|EXF|PHISH|WMI|CLOUD|RANSOM|VPN|SUPPLY)[-_]?\d{2,6}\b", re.I)
_PROC = re.compile(
    r"\b(?:powershell\.exe|cmd\.exe|outlook\.exe|wscript\.exe|cscript\.exe|"
    r"rundll32\.exe|regsvr32\.exe|mshta\.exe|pwsh\.exe)\b",
    re.I,
)


def _all_text(messages: Iterable[Message]) -> str:
    return "\n".join(m.content for m in messages)


def extract_evidence_artifacts(text: str) -> list[str]:
    """Volatile / instance-specific indicators — exact match material."""
    found: list[str] = []
    for rx in (_SHA256, _SHA1, _MD5, _IP, _IPV6, _EMAIL, _HOST, _CVE):
        found.extend(rx.findall(text))
    for m in _EVENT.finditer(text):
        found.append(f"EVT:{m.group(1)}")
    # de-dupe preserve order
    seen: set[str] = set()
    out: list[str] = []
    for x in found:
        key = x.lower() if isinstance(x, str) else str(x)
        if key in seen:
            continue
        seen.add(key)
        out.append(x if isinstance(x, str) else str(x))
    return out


def extract_structure_artifacts(text: str) -> list[str]:
    """Stable attack pattern — MITRE, process lineage class, alert family."""
    found: list[str] = []
    found.extend(_MITRE.findall(text))
    found.extend(p.lower() for p in _PROC.findall(text))
    found.extend(a.upper() for a in _ALERT.findall(text))
    # parent→child style phrases
    if re.search(r"outlook.*powershell|powershell.*encoded", text, re.I):
        found.append("PATTERN:outlook-encoded-powershell")
    if re.search(r"mfa\s+fail|oauth\s+grant", text, re.I):
        found.append("PATTERN:mfa-oauth-followon")
    if re.search(r"lateral|rdp.*block", text, re.I):
        found.append("PATTERN:lateral-rdp")
    seen: set[str] = set()
    out: list[str] = []
    for x in found:
        k = x.lower()
        if k in seen:
            continue
        seen.add(k)
        out.append(x)
    return out


def conversation_artifacts(messages: list[Message]) -> list[str]:
    """Backward-compatible full artifact list (evidence + structure)."""
    text = _all_text(messages)
    return extract_evidence_artifacts(text) + extract_structure_artifacts(text)


def _version_suffix() -> str:
    return "|".join(
        [
            f"pruner={PRUNER_VERSION}",
            f"policy={POLICY_VERSION}",
            f"schema={SCHEMA_VERSION}",
            f"router={ROUTER_VERSION}",
        ]
    )


def _sha(parts: list[str]) -> str:
    payload = "|".join(parts) + "||" + _version_suffix()
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def evidence_hash(conversation: Conversation) -> str:
    arts = extract_evidence_artifacts(_all_text(conversation.messages))
    if not arts:
        # fall back to full transcript so empty-IOC chats still key stably
        raw = "\n".join(f"{m.role.value}:{m.content}" for m in conversation.messages)
        return hashlib.sha256((raw + "||" + _version_suffix()).encode("utf-8")).hexdigest()
    return _sha(sorted(a.lower() for a in arts))


def structure_hash(conversation: Conversation) -> str:
    arts = extract_structure_artifacts(_all_text(conversation.messages))
    if not arts:
        return hashlib.sha256(("structure:empty||" + _version_suffix()).encode("utf-8")).hexdigest()
    return _sha(sorted(a.lower() for a in arts))


def compute_signature(conversation: Conversation) -> str:
    """Primary SCS key = evidence hash (exact bypass)."""
    return evidence_hash(conversation)


def signature_summary(conversation: Conversation) -> dict:
    text = _all_text(conversation.messages)
    ev = extract_evidence_artifacts(text)
    st = extract_structure_artifacts(text)
    return {
        "evidence_hash": evidence_hash(conversation),
        "structure_hash": structure_hash(conversation),
        "signature": evidence_hash(conversation),
        "evidence_count": len(ev),
        "structure_count": len(st),
        "artifacts_sample": (ev + st)[:16],
        "pruner_version": PRUNER_VERSION,
        "policy_version": POLICY_VERSION,
        "schema_version": SCHEMA_VERSION,
        "router_version": ROUTER_VERSION,
    }


def extract_artifacts(text_or_messages) -> list[str]:
    """Backward-compatible artifact list (evidence + structure)."""
    if isinstance(text_or_messages, str):
        return extract_evidence_artifacts(text_or_messages) + extract_structure_artifacts(
            text_or_messages
        )
    # list[Message] or Conversation-like
    if hasattr(text_or_messages, "messages"):
        msgs = list(text_or_messages.messages)
    else:
        msgs = list(text_or_messages)
    return conversation_artifacts(msgs)

