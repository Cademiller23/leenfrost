"""Leenfrost — Cost of Intelligence dashboard."""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st
import pandas as pd

# Allow importing the package when run via streamlit
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from leenfrost import run_gate, log_gate_result, fetch_recent_usage, summarize_gate
from examples.demo_payload import build

st.set_page_config(
    page_title="Leenfrost — Cost of Intelligence",
    page_icon="❄️",
    layout="wide",
)

st.title("Leenfrost")
st.caption("Token fiscal gateway · measurable cost reduction before the expensive call")

# --- Run gate live ---
col_run, col_meta = st.columns([1, 2])
with col_run:
    if st.button("Run gate on demo workload", type="primary", use_container_width=True):
        with st.spinner("Pruning + budgeting..."):
            conv = build()
            result = run_gate(conv)
            call_id = log_gate_result(result, agent_id=conv.agent_id)
            st.session_state["last_result"] = result
            st.session_state["last_call_id"] = call_id

with col_meta:
    st.markdown(
        """
        **What just happened**
        1. Estimated tokens of the outbound agent payload  
        2. Density-pruned context (system-prompt dedup + filler removal)  
        3. Budget check → model route  
        4. Logged into usage store (Snowflake when available, local mirror otherwise)
        """
    )

# --- Last run metrics ---
if "last_result" in st.session_state:
    r = st.session_state["last_result"]
    st.subheader("Last gate result")
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Original tokens", f"{r.original_estimate.total_tokens:,}")
    m2.metric("Final tokens", f"{r.final_tokens:,}")
    m3.metric("Tokens saved", f"{r.tokens_saved:,}")
    m4.metric("Cost reduction", f"{r.savings_percent:.1f}%", delta=f"-{r.savings_percent:.1f}%")

    c1, c2, c3 = st.columns(3)
    c1.write(f"**Model** · {r.route.selected_model}")
    c2.write(f"**Budget** · {r.budget.action.value}")
    c3.write(f"**Strategy** · {r.pruned.strategy if r.pruned else 'n/a'}")

# --- Historical usage ---
st.subheader("Usage history")
rows = fetch_recent_usage(limit=50)
if not rows:
    st.info("No rows yet. Click **Run gate on demo workload**.")
else:
    df = pd.DataFrame(rows)
    # Summary strip
    total_saved = int(df["tokens_saved"].sum())
    avg_pct = float(df["savings_pct"].mean())
    s1, s2, s3 = st.columns(3)
    s1.metric("Calls logged", len(df))
    s2.metric("Total tokens saved", f"{total_saved:,}")
    s3.metric("Avg reduction", f"{avg_pct:.1f}%")

    st.dataframe(
        df[
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

    st.bar_chart(df.set_index("created_at")[["original_tokens", "final_tokens"]])

st.divider()
st.caption("Leenfrost · Snowflake × Beta Fund Agent & Token Economy Hackathon")
