"""Leenfrost — Token fiscal gateway + SCS + Cortex path."""

from leenfrost.budget import evaluate_budget
from leenfrost.cache import cache_stats, lookup, store
from leenfrost.config import LeenfrostConfig, get_config
from leenfrost.cortex import build_complete_sql, complete_pruned, messages_to_prompt
from leenfrost.estimator import count_tokens_in_messages, count_tokens_in_text, estimate_conversation
from leenfrost.metrics import compute_savings, summarize_gate
from leenfrost.models import (
    BudgetConfig, BudgetDecision, Conversation, EnforcementAction,
    GateResult, Message, ModelTier, PruneResult, Role, RouteDecision, TokenEstimate,
)
from leenfrost.openrouter import chat_completion, estimate_cost_usd, matrix_models
from leenfrost.pipeline import run_gate
from leenfrost.pruner import prune_conversation
from leenfrost.router import route_model
from leenfrost.signature import compute_signature, extract_artifacts, signature_summary
from leenfrost.snowflake_logger import fetch_recent_usage, log_gate_result

__version__ = "0.4.0"
__all__ = [
    "__version__", "LeenfrostConfig", "get_config",
    "Role", "ModelTier", "EnforcementAction", "Message", "Conversation",
    "TokenEstimate", "BudgetConfig", "BudgetDecision", "RouteDecision",
    "PruneResult", "GateResult",
    "count_tokens_in_text", "count_tokens_in_messages", "estimate_conversation",
    "prune_conversation", "evaluate_budget", "route_model",
    "compute_savings", "summarize_gate", "run_gate",
    "log_gate_result", "fetch_recent_usage",
    "compute_signature", "extract_artifacts", "signature_summary",
    "lookup", "store", "cache_stats",
    "matrix_models", "estimate_cost_usd", "chat_completion",
    "messages_to_prompt", "build_complete_sql", "complete_pruned",
]
