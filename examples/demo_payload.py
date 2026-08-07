#!/usr/bin/env python3
"""Leenfrost demo — bloated cyber SOC workload (no finance language)."""

from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from leenfrost import Conversation, Message, Role, run_gate, summarize_gate

SYSTEM = (
    "You are a senior detection engineer agent operating inside a SOC pipeline. "
    "You have access to EDR telemetry, DNS logs, identity events, and threat intel. "
    "Always cite exact event IDs, process hashes, and MITRE ATT&CK techniques. "
    "Never invent indicators. Be precise with severity, confidence, and next actions. "
    "When correlating alerts always show both the root event and the lateral movement path."
)

def build() -> Conversation:
    return Conversation(
        messages=[
            Message(role=Role.SYSTEM, content=SYSTEM),
            Message(role=Role.USER, content="Hi there."),
            Message(role=Role.ASSISTANT, content="Hello! Ready to triage."),
            Message(role=Role.USER, content="Okay, thanks."),
            Message(role=Role.ASSISTANT, content="Standing by."),
            Message(role=Role.SYSTEM, content=SYSTEM),
            Message(role=Role.USER, content="Sure, let's begin."),
            Message(role=Role.USER, content=(
                "Triage alert cluster AC-9182. Host WIN-ENG-04 executed "
                "powershell.exe with encoded command. Parent was outlook.exe. "
                "Flag any match to known C2 domains or MITRE T1059.001. "
                "Also list the top 5 related process hashes from the last 24h."
            )),
            Message(role=Role.ASSISTANT, content=(
                "AC-9182 is high confidence. Encoded PowerShell matches T1059.001. "
                "Decoded payload contacts 185.234.72.19 (known C2, confidence 0.91). "
                "Parent chain: outlook.exe → powershell.exe → cmd.exe. "
                "Related hashes (24h): a3f2c9…, 91bb0e…, 4c11d8…, e7a90f…, 2b66c1…. "
                "Recommend isolate WIN-ENG-04 and block 185.234.72.19."
            )),
            Message(role=Role.USER, content="Got it, thanks."),
            Message(role=Role.ASSISTANT, content="Acknowledged."),
            Message(role=Role.SYSTEM, content=SYSTEM),
            Message(role=Role.USER, content="Please continue."),
            Message(role=Role.USER, content=(
                "Correlate with identity events. Did the same user account "
                "fail MFA or create a new OAuth grant in the last 6 hours? "
                "If yes, give the exact event IDs and timestamps."
            )),
            Message(role=Role.ASSISTANT, content=(
                "Yes. User j.park@corp had 3 failed MFA challenges at 14:02–14:04 UTC "
                "(events ID-44102, ID-44107, ID-44111). New OAuth grant created at "
                "14:06 UTC for app 'DataSync-Helper' (event ID-44130). "
                "This aligns with the PowerShell activity on WIN-ENG-04."
            )),
            Message(role=Role.USER, content="Perfect."),
            Message(role=Role.ASSISTANT, content="Ready for next step."),
            Message(role=Role.USER, content=(
                "One more thing — any lateral movement into the 10.40.0.0/16 server VLAN?"
            )),
            Message(role=Role.ASSISTANT, content=(
                "No successful lateral movement into 10.40.0.0/16 yet. "
                "Two RDP attempts from WIN-ENG-04 to 10.40.12.8 were blocked by "
                "host firewall (events NET-8821, NET-8824). Continue monitoring."
            )),
        ],
        priority=8,
        agent_id="soc-triage-ac9182",
    )

if __name__ == "__main__":
    conv = build()
    result = run_gate(conv)
    print("=" * 60)
    print("LEENFROST DEMO — CYBER SOC")
    print("=" * 60)
    for k, v in summarize_gate(result).items():
        print(f"  {k:22s}: {v}")
    print(f"\n  Tokens saved: {result.tokens_saved} ({result.savings_percent}%)")
    print("=" * 60)
