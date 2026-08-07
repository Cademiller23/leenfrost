#!/usr/bin/env python3
"""Run gated cyber payload through Cortex (connector or SQL fallback)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from leenfrost import run_gate
from leenfrost.cortex import complete_pruned
from examples.demo_payload import build


def main() -> int:
    conv = build()
    result = run_gate(conv)
    print("=" * 72)
    print("LEENFROST → CORTEX")
    print("=" * 72)
    print(
        f"gated {result.original_estimate.total_tokens} → {result.final_tokens} "
        f"({result.savings_percent}% saved)"
    )
    out = complete_pruned(result.final_messages)
    print(f"mode: {out.get('mode')}  model: {out.get('model')}")
    if out.get("ok"):
        print("--- cortex response ---")
        print((out.get("response") or "")[:1200])
    else:
        print(f"connector unavailable: {out.get('error')}")
        print("--- run this in Snowsight SQL worksheet ---")
        print(out.get("sql_fallback"))
    print("=" * 72)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
