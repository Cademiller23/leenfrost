#!/usr/bin/env python3
"""Live OpenRouter calls on the gated cyber payload — nothing simulated."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from leenfrost import run_gate
from leenfrost.openrouter import matrix_models, estimate_cost_usd, chat_completion
from examples.demo_payload import build


def main() -> int:
    if not os.environ.get("OPENROUTER_API_KEY"):
        print("ERROR: OPENROUTER_API_KEY missing in .env")
        return 1

    conv = build()
    result = run_gate(conv)
    gated_messages = [
        {"role": m.role.value, "content": m.content} for m in result.final_messages
    ]
    ungated = result.original_estimate.total_tokens
    gated = result.final_tokens

    print("=" * 78)
    print("LIVE PROVIDER MATRIX — real OpenRouter completions on gated payload")
    print("=" * 78)
    print(f"ungated_tokens={ungated} gated_tokens={gated} savings={result.savings_percent}%")
    print("-" * 78)

    for model in matrix_models():
        print(f"→ calling {model} ...", flush=True)
        t0 = time.time()
        try:
            data = chat_completion(model, gated_messages, max_tokens=128)
            latency = round(time.time() - t0, 2)
            text = ""
            try:
                text = data["choices"][0]["message"]["content"] or ""
            except Exception:
                text = str(data)[:200]
            usage = data.get("usage") or {}
            prompt_tokens = usage.get("prompt_tokens", gated)
            print(f"  OK  {latency}s  prompt_tokens≈{prompt_tokens}  "
                  f"est_in=${estimate_cost_usd(model, gated):.6f}")
            print(f"  reply: {text[:120].replace(chr(10), ' ')}")
        except Exception as e:
            print(f"  FAIL {type(e).__name__}: {e}")
        print("-" * 78)

    print("Done. Failures usually mean wrong model slug on OpenRouter — fix id and re-run.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
