"""Wave 2 — EverOS search+ROI soft-fail; writeback; metrics shape."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(".env"))

from leenfrost.models import Conversation, Message, Role
from leenfrost.everos_gate import retrieve_and_price_memory, writeback_memory
from leenfrost import run_gate


def sample_conv() -> Conversation:
    return Conversation(
        messages=[
            Message(
                role=Role.SYSTEM,
                content="You are a SOC detection engineer. Preserve IOCs and MITRE.",
            ),
            Message(
                role=Role.USER,
                content=(
                    "Triage AC-9182 WIN-ENG-04 powershell EncodedCommand "
                    "SHA256 a3f1c9e8b7d64e2a1c0b9f8e7d6c5b4a3f2e1d0c9b8a7f6e5d4c3b2a1f0e9d8 "
                    "C2 185.234.72.19 T1059.001 j.park@corp"
                ),
            ),
        ],
        priority=8,
        agent_id="wave2-everos",
    )


def main() -> int:
    if not os.environ.get("EVEROS_API_KEY"):
        print("WARN: EVEROS_API_KEY missing — soft-fail path only")
    conv = sample_conv()
    roi, block, meta = retrieve_and_price_memory(list(conv.messages), severity=8)
    print("meta", meta)
    print("roi", roi.returned, roi.memories_admitted, roi.tokens_injected, roi.tokens_rejected)
    if roi.returned:
        assert roi.memories_admitted <= roi.returned
    wb = writeback_memory(list(conv.messages), session_id="wave2-everos-wb")
    print("writeback", wb)

    r = run_gate(conv, use_everos=True, use_scs=False)
    print(
        "gate memory",
        r.memory_returned,
        r.memory_admitted,
        r.memory_tokens_injected,
        r.memory_tokens_rejected,
    )
    print("savings", r.savings_percent, "final", r.final_tokens)
    # Soft-fail: must not crash even if EverOS down
    r2 = run_gate(conv, use_everos=True, use_scs=True)
    print("scs_or_second", r2.scs_hit, r2.route.selected_model)
    print("WAVE2_EVEROS_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
