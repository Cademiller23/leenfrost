"""Memory ROI Gate — admit EverOS memories only when value exceeds token cost."""

from __future__ import annotations

from dataclasses import dataclass, field

from leenfrost.estimator import count_tokens_in_text
from leenfrost.config import get_config


@dataclass
class MemoryCandidate:
    text: str
    score: float
    source: str = ""
    id: str = ""
    tokens: int = 0
    roi: float = 0.0


@dataclass
class MemoryROIResult:
    returned: int
    admitted: list[MemoryCandidate] = field(default_factory=list)
    rejected: list[MemoryCandidate] = field(default_factory=list)
    tokens_injected: int = 0
    tokens_rejected: int = 0
    budget_tokens: int = 0

    @property
    def memories_admitted(self) -> int:
        return len(self.admitted)

    @property
    def memories_rejected(self) -> int:
        return len(self.rejected)


def rank_and_select(
    raw: list[dict],
    *,
    budget_tokens: int = 500,
    severity: int = 5,
    min_roi: float = 0.0001,
    model: str | None = None,
) -> MemoryROIResult:
    """Score each memory, sort by ROI, fill budget."""
    cfg = get_config()
    model_name = model or cfg.default_model
    severity_weight = 0.5 + (max(1, min(10, severity)) / 10.0)

    candidates: list[MemoryCandidate] = []
    for item in raw:
        text = (item.get("text") or "").strip()
        if not text:
            continue
        tokens = max(1, count_tokens_in_text(text, model=model_name))
        rel = max(0.01, float(item.get("score") or 0.5))
        value = rel * severity_weight
        roi = value / tokens
        candidates.append(
            MemoryCandidate(
                text=text,
                score=rel,
                source=str(item.get("source") or ""),
                id=str(item.get("id") or ""),
                tokens=tokens,
                roi=roi,
            )
        )

    candidates.sort(key=lambda c: c.roi, reverse=True)

    admitted: list[MemoryCandidate] = []
    rejected: list[MemoryCandidate] = []
    used = 0
    for c in candidates:
        if c.roi < min_roi:
            rejected.append(c)
            continue
        if used + c.tokens <= budget_tokens:
            admitted.append(c)
            used += c.tokens
        else:
            rejected.append(c)

    return MemoryROIResult(
        returned=len(candidates),
        admitted=admitted,
        rejected=rejected,
        tokens_injected=used,
        tokens_rejected=sum(c.tokens for c in rejected),
        budget_tokens=budget_tokens,
    )


def memories_to_system_block(result: MemoryROIResult) -> str | None:
    if not result.admitted:
        return None
    lines = [
        "Prior incident memory (ROI-selected; low-value memories discarded):",
    ]
    for i, m in enumerate(result.admitted, 1):
        lines.append(f"{i}. ({m.tokens} tok, roi={m.roi:.5f}) {m.text}")
    return "\n".join(lines)
