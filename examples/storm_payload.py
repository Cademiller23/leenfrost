"""Large cyber SOC storm conversation for high token-reduction demos."""

from __future__ import annotations

from leenfrost.models import Conversation, Message, Role

# Repeated low-density filler is intentional — pruner target.
_FILLER = (
    "Please acknowledge. Continue monitoring. Standing by for further direction. "
    "No additional commentary required unless severity changes. "
)


def build_storm() -> Conversation:
    msgs: list[Message] = [
        Message(
            role=Role.SYSTEM,
            content=(
                "You are a SOC correlation agent for CrowdStrike + Windows Event + DNS. "
                "Preserve SHA256, IPs, hosts, MITRE techniques, EventIDs, and alert IDs. "
                "Prefer containment decisions over narrative."
            ),
        ),
        Message(role=Role.SYSTEM, content=(
            "You are a SOC correlation agent for CrowdStrike + Windows Event + DNS. "
            "Preserve SHA256, IPs, hosts, MITRE techniques, EventIDs, and alert IDs. "
            "Prefer containment decisions over narrative."
        )),
    ]
    # Noise turns
    for i in range(12):
        msgs.append(Message(role=Role.USER, content=f"Status check {i}. {_FILLER}"))
        msgs.append(Message(role=Role.ASSISTANT, content=f"Acknowledged status {i}. {_FILLER}"))

    # High-value alert cluster (must survive prune)
    msgs.append(
        Message(
            role=Role.USER,
            content=(
                "Alert cluster AC-9182 on WIN-ENG-04. Parent outlook.exe spawned "
                "powershell.exe with EncodedCommand. SHA256 "
                "a3f1c9e8b7d64e2a1c0b9f8e7d6c5b4a3f2e1d0c9b8a7f6e5d4c3b2a1f0e9d8. "
                "C2 185.234.72.19:443. Lateral attempts toward 10.40.12.8 blocked. "
                "EventID 4688, 4104, 4625. MITRE T1059.001 T1027 T1021.001. "
                "DNS to update-service-cdn.internal failed sinkhole. "
                "Need severity, containment, and whether credential dump followed."
            ),
        )
    )
    msgs.append(
        Message(
            role=Role.ASSISTANT,
            content=(
                "Working hypothesis: phishing macro → encoded PowerShell → C2. "
                "No LSASS access confirmed yet. Recommend isolate WIN-ENG-04, "
                "block 185.234.72.19, preserve memory image."
            ),
        )
    )
    for i in range(8):
        msgs.append(Message(role=Role.USER, content=f"Thanks, keep watching channel {i}. {_FILLER}"))
        msgs.append(Message(role=Role.ASSISTANT, content=f"Monitoring channel {i}. {_FILLER}"))

    msgs.append(
        Message(
            role=Role.USER,
            content=(
                "Update: same SHA256 seen on WIN-ENG-11. RDP 10.40.0.0/16. "
                "EventID 4624 type 10 from 10.40.12.8. Escalate if credential access."
            ),
        )
    )
    return Conversation(
        messages=msgs,
        priority=9,
        agent_id="soc-storm-ac9182",
    )


if __name__ == "__main__":
    from leenfrost import run_gate, summarize_gate

    c = build_storm()
    r = run_gate(c, use_everos=False, use_scs=False)
    print("messages", len(c.messages))
    print("original", r.original_estimate.total_tokens, "final", r.final_tokens, "saved%", r.savings_percent)
