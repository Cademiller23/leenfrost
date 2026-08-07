"""Leenfrost control plane dashboard — Cost of Intelligence."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from leenfrost import (
    run_gate,
    log_gate_result,
    fetch_recent_usage,
    cache_stats,
    lookup,
    store,
    signature_summary,
)
from leenfrost.openrouter import matrix_models, estimate_cost_usd
from examples.demo_payload import build

st.set_page_config(
    page_title="Leenfrost — Cost of Intelligence",
    page_icon="❄️",
    layout="wide",
)

st.title("Leenfrost")
st.caption(
    "Token fiscal gateway · density pruning · severity routing · "
    "Snowflake Context-Steering (SCS)"
)

# ---------------------------------------------------------------------------
# Section 1 — Live gate
# ---------------------------------------------------------------------------
st.header("1 · Live gate")
c1, c2 = st.columns([1, 2])
with c1:
    run = st.button("Run cyber SOC workload", type="primary", use_container_width=True)
with c2:
    st.markdown(
        """
        Pipeline: **estimate → SCS lookup → prune → budget → severity route → log**  
        On cache hit the frontier call is **$0**. On miss we prune and store the signature.
        """
    )

if run:
    conv = build()
    sig = signature_summary(conv)
    hit = lookup(conv)
    if hit:
        st.session_state["last_mode"] = "SCS_CACHE_HIT"
        st.session_state["last_hit"] = hit
        st.session_state["last_result"] = None
        st.session_state["last_sig"] = sig
    else:
        result = run_gate(conv)
        store(conv, result, artifacts=sig["artifacts_sample"])
        log_gate_result(result, agent_id=conv.agent_id or "soc-triage")
        st.session_state["last_mode"] = "GATE"
        st.session_state["last_result"] = result
        st.session_state["last_hit"] = None
        st.session_state["last_sig"] = sig

mode = st.session_state.get("last_mode")
if mode == "SCS_CACHE_HIT":
    hit = st.session_state["last_hit"]
    st.success("SCS cache hit — model path bypassed ($0)")
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Original tokens", f"{hit.original_tokens:,}")
    m2.metric("Final tokens", f"{hit.final_tokens:,}")
    m3.metric("Tokens saved", f"{hit.tokens_saved:,}")
    m4.metric("Reduction", f"{hit.savings_pct:.1f}%")
    st.write(f"Signature `{hit.signature[:24]}…` · cache hits **{hit.hits}**")
elif mode == "GATE":
    r = st.session_state["last_result"]
    st.info("Cache miss — full gate executed")
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Original tokens", f"{r.original_estimate.total_tokens:,}")
    m2.metric("Final tokens", f"{r.final_tokens:,}")
    m3.metric("Tokens saved", f"{r.tokens_saved:,}")
    m4.metric("Reduction", f"{r.savings_percent:.1f}%")
    st.write(
        f"**Model** {r.route.selected_model} · **Budget** {r.budget.action.value} · "
        f"**Strategy** {r.pruned.strategy if r.pruned else 'n/a'}"
    )
    sig = st.session_state.get("last_sig") or {}
    if sig:
        st.caption(
            f"Artifacts extracted: {sig.get('artifact_count', 0)} · "
            f"sample: {', '.join(sig.get('artifacts_sample', [])[:8])}"
        )

# ---------------------------------------------------------------------------
# Section 2 — SCS
# ---------------------------------------------------------------------------
st.header("2 · Snowflake Context-Steering (SCS)")
stats = cache_stats()
s1, s2, s3 = st.columns(3)
s1.metric("Cache entries", stats["entries"])
s2.metric("Total bypass hits", stats["total_hits"])
s3.metric("Model $ on hit", "$0.00")
st.markdown(
    """
    Recurring cyber alert signatures (IOC / MITRE / hash / host fingerprints)  
    are served from the warehouse-side cache — **no frontier call**.
    """
)

# ---------------------------------------------------------------------------
# Section 3 — Provider matrix
# ---------------------------------------------------------------------------
st.header("3 · Provider cost matrix (gated vs ungated)")
# Use last gate numbers or run a silent estimate for display
if st.session_state.get("last_result") is not None:
    r = st.session_state["last_result"]
    ungated, gated = r.original_estimate.total_tokens, r.final_tokens
elif st.session_state.get("last_hit") is not None:
    h = st.session_state["last_hit"]
    ungated, gated = h.original_tokens, h.final_tokens
else:
    tmp = run_gate(build())
    ungated, gated = tmp.original_estimate.total_tokens, tmp.final_tokens

rows = []
for model in matrix_models():
    u = estimate_cost_usd(model, ungated)
    g = estimate_cost_usd(model, gated)
    rows.append(
        {
            "model": model,
            "ungated_usd": u,
            "gated_usd": g,
            "usd_saved": round(u - g, 6),
            "ungated_tokens": ungated,
            "gated_tokens": gated,
        }
    )
df_m = pd.DataFrame(rows)
st.dataframe(df_m, use_container_width=True, hide_index=True)
st.caption("Input-token cost estimates. SCS hit → treat gated model spend as $0.")

# ---------------------------------------------------------------------------
# Section 4 — Usage history
# ---------------------------------------------------------------------------
st.header("4 · Usage history")
usage = fetch_recent_usage(40)
if not usage:
    st.info("No rows yet — run the live gate.")
else:
    df_u = pd.DataFrame(usage)
    st.metric("Calls logged", len(df_u))
    st.dataframe(
        df_u[
            [
                "created_at",
                "agent_id",
                "original_tokens",
                "final_tokens",
                "tokens_saved",
                "savings_pct",
                "model_selected",
                "budget_action",
            ]
        ],
        use_container_width=True,
        hide_index=True,
    )

# ---------------------------------------------------------------------------
# Section 5 — SDK
# ---------------------------------------------------------------------------
st.header("5 · SDK — drop-in gate")
st.code(
    '''# pip install -e .  (from the leenfrost repo)
from leenfrost import Conversation, Message, Role, run_gate, lookup, store
from leenfrost.signature import signature_summary

conv = Conversation(
    messages=[Message(role=Role.USER, content="Triage alert ...")],
    priority=9,  # critical → frontier; safety overrides soft budget
    agent_id="soc-agent-1",
)

hit = lookup(conv)
if hit:
    # $0 model path — warehouse served the pruned outcome
    payload_tokens = hit.final_tokens
else:
    result = run_gate(conv)
    store(conv, result)
    messages_for_llm = [
        {"role": m.role.value, "content": m.content}
        for m in result.final_messages
    ]
    # send messages_for_llm to Cortex / OpenRouter / etc.
''',
    language="python",
)

st.divider()
st.caption(
    "Leenfrost · Snowflake × Beta Fund Agent & Token Economy Hackathon · "
    "cyber workloads · no finance demo data"
)
