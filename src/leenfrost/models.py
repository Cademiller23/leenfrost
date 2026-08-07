"""Core data models for Leenfrost."""

from __future__ import annotations
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import UUID, uuid4
from pydantic import BaseModel, ConfigDict, Field, model_validator


class Role(str, Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class ModelTier(str, Enum):
    FRONTIER = "frontier"
    STANDARD = "standard"
    ECONOMY = "economy"


class EnforcementAction(str, Enum):
    ALLOW = "allow"
    FORCE_ECONOMY = "force_economy"
    REJECT = "reject"


class Message(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    role: Role
    content: str = Field(..., min_length=0)
    name: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class Conversation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID = Field(default_factory=uuid4)
    messages: list[Message] = Field(..., min_length=1)
    agent_id: str | None = None
    priority: int = Field(default=5, ge=1, le=10)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def require_non_system(self) -> Conversation:
        if not any(m.role != Role.SYSTEM for m in self.messages):
            raise ValueError("Conversation must contain at least one non-system message")
        return self


class TokenEstimate(BaseModel):
    model_config = ConfigDict(frozen=True)
    total_tokens: int = Field(..., ge=0)
    prompt_tokens: int = Field(..., ge=0)
    message_count: int = Field(..., ge=0)
    model: str
    encoding_name: str
    estimated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class BudgetConfig(BaseModel):
    max_tokens_per_call: int = Field(default=8000, ge=100)
    max_tokens_per_day: int = Field(default=500_000, ge=1000)
    soft_limit_ratio: float = Field(default=0.80, ge=0.5, le=0.99)
    remaining_daily_tokens: int = Field(default=500_000, ge=0)
    agent_id: str = "default"


class BudgetDecision(BaseModel):
    model_config = ConfigDict(frozen=True)
    action: EnforcementAction
    allowed: bool
    reason: str
    estimated_tokens: int
    remaining_daily: int
    soft_limit_hit: bool = False


class RouteDecision(BaseModel):
    model_config = ConfigDict(frozen=True)
    selected_tier: ModelTier
    selected_model: str
    reason: str
    priority: int
    forced_by_budget: bool = False


class PruneResult(BaseModel):
    model_config = ConfigDict(frozen=True)
    original_messages: list[Message]
    pruned_messages: list[Message]
    original_tokens: int
    pruned_tokens: int
    tokens_removed: int
    reduction_ratio: float = Field(..., ge=0.0, le=1.0)
    kept_system: bool
    strategy: str

    @property
    def reduction_percent(self) -> float:
        return round(self.reduction_ratio * 100.0, 2)


class GateResult(BaseModel):
    model_config = ConfigDict(frozen=True)
    conversation_id: UUID
    original_estimate: TokenEstimate
    pruned: PruneResult | None
    budget: BudgetDecision
    route: RouteDecision
    final_messages: list[Message]
    final_tokens: int
    tokens_saved: int
    tokens_after_prune: int = 0
    prune_savings_pct: float = 0.0
    net_savings_pct: float = 0.0
    savings_percent: float
    estimated_cost_usd: float | None = None
    memory_returned: int = 0
    memory_admitted: int = 0
    memory_tokens_injected: int = 0
    memory_tokens_rejected: int = 0
    scs_hit: bool = False
    scs_hit_kind: str = "none"
    pnl_trace: list[str] = []
    raw_tokens: int = 0
    pruned_tokens_before_memory: int = 0
    memory_injected_tokens: int = 0
    provider_prompt_tokens: int = 0
    model_tokens: int = 0
    processed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
