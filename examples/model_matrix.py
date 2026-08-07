#!/usr/bin/env python3
"""Gated vs ungated input cost across the Leenfrost provider matrix."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from leenfrost import run_gate
from leenfrost.openrouter import matrix_models, estimate_cost_usd
from examples.demo_payload import build


def main() -> int:
    conv = build()
    result = run_gate(conv)
    ungated = result.original_estimate.total_tokens
    gated = result.final_tokens
    saved = result.tokens_saved
    pct = result.savings_percent

    print("=" * 78)
    print("LEENFROST PROVIDER MATRIX — cyber payload (no Claude)")
    print("=" * 78)
    print(f"ungated={ungated}  gated={gated}  saved={saved} ({pct}%)")
    print("-" * 78)
    print(f"{'model':<42} {'ungated $':>12} {'gated $':>12} {'$ saved':>12}")
    print("-" * 78)

    for model in matrix_models():
        u = estimate_cost_usd(model, ungated)
        g = estimate_cost_usd(model, gated)
        print(f"{model:<42} {u:>12.6f} {g:>12.6f} {round(u - g, 6):>12.6f}")

    print("-" * 78)
    print("SCS cache hit on recurrence → model spend can drop to $0.00")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
