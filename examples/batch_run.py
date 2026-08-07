#!/usr/bin/env python3
"""Batch cyber workloads through Leenfrost + SCS cache."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from leenfrost import Conversation, Message, Role, run_gate, log_gate_result
from leenfrost.cache import lookup, store, cache_stats
from leenfrost.signature import signature_summary
from leenfrost.models import GateResult

def load_prompts() -> list[dict]:
    path = ROOT / "data" / "cyber_prompts.json"
    return json.loads(path.read_text())

def to_conversation(item: dict) -> Conversation:
    msgs = [
        Message(role=Role(m["role"]), content=m["content"])
        for m in item["messages"]
    ]
    # ensure non-system present
    if not any(m.role != Role.SYSTEM for m in msgs):
        msgs.append(Message(role=Role.USER, content="Continue analysis."))
    return Conversation(
        messages=msgs,
        priority=int(item.get("severity", 5)),
        agent_id=item.get("id", "cyber"),
    )

def main() -> int:
    items = load_prompts()
    print("=" * 64)
    print("LEENFROST BATCH + SCS (cyber)")
    print("=" * 64)

    total_orig = total_final = total_saved = 0
    cache_hits = 0
    runs = 0

    # Pass 1: populate / measure
    for item in items:
        conv = to_conversation(item)
        sig_info = signature_summary(conv)
        hit = lookup(conv)
        if hit:
            cache_hits += 1
            total_orig += hit.original_tokens
            total_final += hit.final_tokens
            total_saved += hit.tokens_saved
            print(f"[CACHE HIT] {item['id']:12} sig={hit.signature[:12]}… "
                  f"saved={hit.tokens_saved} ({hit.savings_pct}%) hits={hit.hits}")
            continue

        result: GateResult = run_gate(conv)
        store(conv, result, artifacts=sig_info["artifacts_sample"])
        log_gate_result(result, agent_id=conv.agent_id)
        runs += 1
        total_orig += result.original_estimate.total_tokens
        total_final += result.final_tokens
        total_saved += result.tokens_saved
        print(f"[MISS→GATE] {item['id']:12} "
              f"{result.original_estimate.total_tokens}→{result.final_tokens} "
              f"({result.savings_percent}%) arts={sig_info['artifact_count']}")

    # Pass 2: same set should mostly hit cache ($0 path)
    print("-" * 64)
    print("Second pass (expect cache hits / $0 model path):")
    hits2 = 0
    for item in items:
        conv = to_conversation(item)
        hit = lookup(conv)
        if hit:
            hits2 += 1
            print(f"  HIT  {item['id']}")
        else:
            print(f"  MISS {item['id']}")

    pct = round((total_saved / total_orig) * 100, 2) if total_orig else 0.0
    stats = cache_stats()
    print("=" * 64)
    print(f"Workloads          : {len(items)}")
    print(f"First-pass gates   : {runs}")
    print(f"First-pass hits    : {cache_hits}")
    print(f"Second-pass hits   : {hits2}/{len(items)}")
    print(f"Tokens original    : {total_orig}")
    print(f"Tokens final       : {total_final}")
    print(f"Tokens saved       : {total_saved} ({pct}%)")
    print(f"Cache entries      : {stats['entries']}")
    print(f"Cache total hits   : {stats['total_hits']}")
    print("=" * 64)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
