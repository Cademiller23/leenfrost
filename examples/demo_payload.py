#!/usr/bin/env python3
"""Leenfrost demo — bloated agent workload that produces real cost reduction."""

from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from leenfrost import Conversation, Message, Role, run_gate, summarize_gate

SYSTEM = (
    "You are a senior financial analyst agent operating inside Snowflake Cortex. "
    "You have full access to SALES_FACT, CUSTOMER_DIM, PRODUCT_DIM, REGION_DIM, and EXCHANGE_RATES. "
    "Always cite exact columns. Be precise with numbers, percentages, and currency. "
    "Never invent data. Format large numbers with one decimal place and unit (e.g. $148.2M). "
    "When comparing periods always show both absolute and percentage change."
)

def build() -> Conversation:
    return Conversation(
        messages=[
            Message(role=Role.SYSTEM, content=SYSTEM),
            Message(role=Role.USER, content="Hi there."),
            Message(role=Role.ASSISTANT, content="Hello! How can I help you today?"),
            Message(role=Role.USER, content="Okay, thanks."),
            Message(role=Role.ASSISTANT, content="You're welcome. Ready when you are."),
            Message(role=Role.SYSTEM, content=SYSTEM),  # duplicate — must be pruned
            Message(role=Role.USER, content="Sure, let's begin."),
            Message(role=Role.USER, content=(
                "Pull Q3 2025 revenue for the APAC region broken down by country and "
                "product category. Flag any country with >8% sequential decline. "
                "Also list the top 5 Software SKUs by revenue."
            )),
            Message(role=Role.ASSISTANT, content=(
                "APAC Q3 2025 revenue was $148.2M (+11.4% YoY). "
                "Japan $62.1M, Australia $41.3M, Singapore $22.8M, Rest of APAC $22.0M. "
                "Categories: Hardware 48%, Software 31%, Services 21%. "
                "No country exceeded an 8% sequential decline. "
                "Top Software SKUs: SKU-8821 ($4.1M), SKU-4402 ($3.7M), SKU-9910 ($3.2M), "
                "SKU-1103 ($2.9M), SKU-7744 ($2.6M)."
            )),
            Message(role=Role.USER, content="Got it, thanks."),
            Message(role=Role.ASSISTANT, content="Happy to help."),
            Message(role=Role.SYSTEM, content=SYSTEM),  # another duplicate
            Message(role=Role.USER, content="Please continue."),
            Message(role=Role.USER, content=(
                "Now compare the same breakdown to Q2 2025. Highlight the largest "
                "absolute dollar movers and any country that declined more than 5% QoQ."
            )),
            Message(role=Role.ASSISTANT, content=(
                "Q2→Q3 movers: Japan +$6.8M, Australia +$3.1M, Singapore +$1.9M. "
                "Largest absolute mover is Japan. No country declined more than 5% QoQ."
            )),
            Message(role=Role.USER, content="Perfect."),
            Message(role=Role.ASSISTANT, content="Glad that was useful."),
            Message(role=Role.USER, content="One more thing — can you also show the FX impact?"),
            Message(role=Role.ASSISTANT, content=(
                "FX impact on APAC Q3 was approximately −$1.4M versus constant currency, "
                "driven mainly by JPY weakness (−2.1%) and AUD softness (−1.3%)."
            )),
        ],
        priority=7,
        agent_id="finance-apac-q3",
    )

if __name__ == "__main__":
    conv = build()
    result = run_gate(conv)
    print("=" * 60)
    print("LEENFROST DEMO — COST OF INTELLIGENCE")
    print("=" * 60)
    for k, v in summarize_gate(result).items():
        print(f"  {k:22s}: {v}")
    print(f"\n  Tokens saved         : {result.tokens_saved}")
    print(f"  Savings              : {result.savings_percent}%")
    if result.pruned:
        print(f"  Strategy             : {result.pruned.strategy}")
        print(f"  Messages             : {len(result.final_messages)} / {len(conv.messages)}")
    print("=" * 60)
