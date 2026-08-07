"""Memory ROI gate — admit memory only when utility per token justifies cost."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence

from leenfrost.config import LeenfrostConfig, get_config
from leenfrost.estimator import count_tokens_in_text


@dataclass(frozen=True)
class MemoryCandidate:
    text: str
    score: float
    tokens: int
    roi: float
    source: str = ""
    id: str = ""


@dataclass
class MemoryROIResult:
    returned: int
    memories_admitted: int
    admitted: list[MemoryCandidate] = field(default_factory=list)
    rejected: list[MemoryCandidate] = field(default_factory=list)
    tokens_injected: int = 0
    tokens_rejected: int = 0
    budget_tokens: int = 0

    @property
    def memory_admitted(self) -> int:
        return self.memories_admitted


def _severity_weight(severity: int) -> float:
    s = max(1, min(10, int(severity)))
    return 0.55 + (s / 10.0) * 0.45  # 0.55 .. 1.0


def rank_and_select(
    candidates: Sequence[dict[str, Any]] | Sequence[str],
    *,
    budget_tokens: int | None = None,
    severity: int = 5,
    config: LeenfrostConfig | None = None,
    model: str | None = None,
) -> MemoryROIResult:
    """Select memories best-ROI-first until budget_tokens is exhausted.

    Default budget is config.memory_token_budget (200) — hard cap so demos
    show admitted < returned instead of stuffing +487 tokens.
    """
    cfg = config or get_config()
    budget = int(budget_tokens if budget_tokens is not None else getattr(cfg, "memory_token_budget", 200))
    budget = max(0, budget)
    model_name = model or cfg.default_model
    sev_w = _severity_weight(severity)

    normalized: list[MemoryCandidate] = []
    for item in candidates:
        if isinstance(item, str):
            text = item.strip()
            score = 0.5
            source, cid = "", ""
        else:
            text = str(item.get("text") or "").strip()
            score = float(item.get("score") or item.get("quality_score") or item.get("confidence") or 0.5)
            source = str(item.get("source") or "")
            cid = str(item.get("id") or "")
        if not text:
            continue
        tokens = max(1, count_tokens_in_text(text, model=model_name))
        utility = max(0.0, min(1.0, score)) * sev_w
        roi = utility / float(tokens)
        normalized.append(
            MemoryCandidate(text=text, score=score, tokens=tokens, roi=roi, source=source, id=cid)
        )

    normalized.sort(key=lambda c: (-c.roi, c.tokens, -c.score))

    admitted: list[MemoryCandidate] = []
    rejected: list[MemoryCandidate] = []
    used = 0
    for c in normalized:
        if used + c.tokens <= budget:
            admitted.append(c)
            used += c.tokens
        else:
            rejected.append(c)

    return MemoryROIResult(
        returned=len(normalized),
        memories_admitted=len(admitted),
        admitted=admitted,
        rejected=rejected,
        tokens_injected=used,
        tokens_rejected=sum(c.tokens for c in rejected),
        budget_tokens=budget,
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
