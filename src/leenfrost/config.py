"""Configuration for Leenfrost."""

from __future__ import annotations
from functools import lru_cache
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class LeenfrostConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LEENFROST_", extra="ignore")

    default_model: str = "deepseek/deepseek-v4-flash"
    fallback_encoding: str = "o200k_base"
    max_tokens_per_call: int = 8000
    max_tokens_per_day: int = 500_000
    soft_limit_ratio: float = 0.80
    keep_last_n_turns: int = 4
    min_reduction_target: float = 0.35
    protect_system: bool = True
    frontier_model: str = "deepseek/deepseek-v4-flash"
    standard_model: str = "deepseek/deepseek-v4-flash"
    economy_model: str = "deepseek/deepseek-v4-flash"
    priority_frontier_threshold: int = 8
    priority_economy_threshold: int = 3
    cost_per_million_input: dict[str, float] = Field(default_factory=lambda: {
        "deepseek/deepseek-v4-flash": 0.14,
        "gpt-4o": 2.50,
        "gpt-4o-mini": 0.15,
    })

    def get_input_cost_per_token(self, model: str) -> float:
        return self.cost_per_million_input.get(model, 1.0) / 1_000_000.0


@lru_cache(maxsize=1)
def get_config() -> LeenfrostConfig:
    return LeenfrostConfig()


def reset_config_cache() -> None:
    get_config.cache_clear()
