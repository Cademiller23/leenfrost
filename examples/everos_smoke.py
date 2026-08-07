#!/usr/bin/env python3
"""EverOS: sync add → flush → session-filtered search → Memory ROI."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from leenfrost.everos import (
    extract_memory_texts,
    memory_add,
    memory_flush,
    memory_search,
    messages_for_everos,
)
from leenfrost.memory_roi import memories_to_system_block, rank_and_select


def main() -> int:
    user_id = os.environ.get("EVEROS_USER_ID", "soc-analyst-1")
    session_id = f"soc-seed-{int(time.time())}"
    print(f"session_id={session_id} user_id={user_id}")

    turns = [
        ("user", "I am triaging AC-9182 on WIN-ENG-04. outlook.exe spawned encoded PowerShell."),
        ("assistant", "That pattern is T1059.001. Check C2 and parent chain."),
        ("user", "C2 is 185.234.72.19. Also MFA failures for j.park@corp then OAuth grant."),
        ("assistant", "High confidence. Isolate host, block 185.234.72.19, revoke OAuth grant DataSync-Helper."),
        ("user", "Any lateral movement into 10.40.0.0/16?"),
        ("assistant", "RDP to 10.40.12.8 was blocked (NET-8821, NET-8824). No successful lateral movement."),
        ("user", "Please remember this Outlook-to-PowerShell playbook for future ENG endpoint alerts."),
        ("assistant", "Remembered: Outlook parent + EncodedCommand + C2 + MFA/OAuth follow-on is a priority isolate playbook."),
    ]
    msgs = messages_for_everos(turns, user_sender_id=user_id)

    add_resp = memory_add(session_id=session_id, messages=msgs, async_mode=False)
    print("add:", add_resp)

    time.sleep(2)
    flush_resp = memory_flush(session_id=session_id)
    print("flush:", flush_resp)

    texts: list = []
    for attempt in range(1, 8):
        time.sleep(3)
        search_resp = memory_search(
            query="PowerShell Outlook T1059 C2 MFA OAuth WIN-ENG-04 lateral",
            user_id=user_id,
            session_id=session_id,
            top_k=10,
        )
        texts = extract_memory_texts(search_resp)
        data = search_resp.get("data") or {}
        print(
            f"attempt {attempt}: candidates={len(texts)} "
            f"episodes={len(data.get('episodes') or [])} "
            f"unprocessed={len(data.get('unprocessed_messages') or [])}"
        )
        if texts:
            break

    for t in texts[:8]:
        print(f"  [{t['source']}] {t['score']:.2f} {t['text'][:140]}")

    roi = rank_and_select(texts, budget_tokens=400, severity=9)
    print(
        f"ROI returned={roi.returned} admitted={roi.memories_admitted} "
        f"inj={roi.tokens_injected} rej={roi.tokens_rejected}"
    )
    block = memories_to_system_block(roi)
    if block:
        print(block[:700])

    if not texts:
        print("STILL_EMPTY — see Step C (Playground + Request Logs)")
        return 2
    print("EVEROS SMOKE OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
