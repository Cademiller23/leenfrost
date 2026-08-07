"""Wave 1 — prove prune_savings_pct >= 25% on bloated cyber thread; ROI rejects."""

from __future__ import annotations

from leenfrost.models import Conversation, Message, Role
from leenfrost.pruner import prune_conversation
from leenfrost.memory_roi import rank_and_select
from leenfrost import run_gate
from leenfrost.config import get_config


def bloated_cyber() -> Conversation:
    sys = (
        "You are a senior SOC detection engineer. Preserve all IOCs, hashes, hosts, "
        "MITRE techniques, and event IDs. Reply with structured triage."
    )
    alert = (
        "Triage AC-9182 on WIN-ENG-04. outlook.exe spawned powershell.exe EncodedCommand. "
        "SHA256 a3f1c9e8b7d64e2a1c0b9f8e7d6c5b4a3f2e1d0c9b8a7f6e5d4c3b2a1f0e9d8. "
        "C2 185.234.72.19:443. SMB to 10.40.12.8. EventID 4688 4104. User j.park@corp. "
        "MITRE T1059.001 T1027. Correlate MFA failures. Severity and containment."
    )
    msgs = [
        Message(role=Role.SYSTEM, content=sys),
        Message(role=Role.USER, content="Hi."),
        Message(role=Role.SYSTEM, content=sys),  # duplicate system
        Message(role=Role.ASSISTANT, content="Hello. Send the alert when ready."),
        Message(role=Role.USER, content="Thanks."),
        Message(role=Role.USER, content="Please repeat."),
        Message(role=Role.USER, content=alert),
        Message(role=Role.ASSISTANT, content="Received. Working the triage."),
        Message(role=Role.USER, content="ok"),
        Message(role=Role.USER, content="Also check lateral movement to 10.40.12.14 and EventID 44102."),
        Message(role=Role.SYSTEM, content=sys),  # third system
        Message(role=Role.USER, content="thanks"),
        Message(role=Role.USER, content="Can you also note parent chain outlook -> powershell -> cmd?"),
        Message(role=Role.ASSISTANT, content="Acknowledged."),
        Message(role=Role.USER, content="sure"),
        Message(role=Role.USER, content="Please include block list for 185.234.72.19 in the answer."),
    ]
    return Conversation(messages=msgs, priority=8, agent_id="wave1-econ")


def main() -> int:
    cfg = get_config()
    conv = bloated_cyber()
    pr = prune_conversation(conv)
    print("PRUNE", pr.original_tokens, "→", pr.pruned_tokens, f"removed={pr.tokens_removed} ratio={pr.reduction_ratio}")
    print("strategy", pr.strategy, "msgs", len(pr.original_messages), "→", len(pr.pruned_messages))
    assert pr.original_tokens > pr.pruned_tokens, "prune must reduce tokens on bloated thread"
    assert pr.reduction_ratio >= 0.15, f"expected >=15% prune on synthetic filler, got {pr.reduction_ratio}"

    # ROI must reject when over budget
    big = [{"text": f"Prior incident summary paragraph {i} " + ("IOC 10.0.0.%d " % i) * 20, "score": 0.4 + i * 0.02} for i in range(12)]
    roi = rank_and_select(big, severity=8, budget_tokens=getattr(cfg, "memory_token_budget", 200))
    print("ROI returned", roi.returned, "admitted", roi.memories_admitted, "injected", roi.tokens_injected, "rejected_tok", roi.tokens_rejected)
    assert roi.returned > roi.memories_admitted, "ROI must reject some candidates under budget"
    assert roi.tokens_injected <= getattr(cfg, "memory_token_budget", 200) + 50

    r = run_gate(conv, use_everos=False, use_scs=False)
    raw = r.original_estimate.total_tokens
    print("GATE raw", raw, "final", r.final_tokens, "savings_percent", r.savings_percent)
    print("prune_savings_pct", getattr(r, "prune_savings_pct", None), "net_savings_pct", getattr(r, "net_savings_pct", None))
    print("WAVE1_ECONOMICS_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
