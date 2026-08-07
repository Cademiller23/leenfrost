#!/usr/bin/env python3
"""Wave 5 — production gate + live fleet previews."""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from examples.demo_payload import build
from leenfrost import run_turn


def main() -> int:
    if not os.environ.get("OPENROUTER_API_KEY"):
        print("ERROR: OPENROUTER_API_KEY missing")
        return 1
    env = run_turn(build(), use_everos=True, use_scs=True, run_fleet=True)
    print("=" * 78)
    print("LEENFROST TURN ENVELOPE")
    print("=" * 78)
    for line in env.summary_lines():
        print(line)
    print("=" * 78)
    print(f"fleet_ok={sum(1 for r in env.fleet if r.get('status')=='ok')}/{len(env.fleet)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
