"""Leenfrost control plane — live gate, SCS, live provider probes, SDK."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from leenfrost import (
    run_gate,
    log_gate_result,
    fetch_recent_usage,
    cache_stats,
    lookup,
    store,
    signature_summary,
)
from leenfrost.openrouter import matrix_models, estimate_cost_usd, chat_completion
from examples.demo_payload import build

st.set_page_config(page_title="Leenfrost", page_icon="❄️", layout="wide")
st.title("Leenfrost")
st.caption(
    "Live token fiscal gateway · density pruning · severity routing · SCS cache · OpenRouter probes"
)

# --- 1 Live gate ---
st.header("1 · Live gate (cyber SOC)")
if st.button("Run cyber SOC workload", type="primary"):
    conv = build()
    sig = signature_summary(conv)
    hit = lookup(conv)
    if hit:
        st.session_state["mode"] = "HIT"
        st.session_state["hit"] = hit
        st.session_state["result"] = None
        st.session_state["sig"] = sig
    else:
        result = run_gate(conv)
        store(conv, result, artifacts=sig["artifacts_sample"])
        log_gate_result(result, agent_id=conv.agent_id or "soc-triage")
        st.session_state["mode"] = "GATE"
        st.session_state["result"] = result
        st.session_state["hit"] = None
        st.session_state["sig"] = sig
        st.session_state["gated_messages"] = [
            {"role": m.role.value, "content": m.content} for m in result.final_messages
        ]
        st.session_state["ungated_tokens"] = result.original_estimate.total_tokens
        st.session_state["gated_tokens"] = result.final_tokens

mode = st.session_state.get("mode")
if mode == "HIT":
    h = st.session_state["hit"]
    st.success("SCS cache hit — frontier path bypassed ($0 model)")
    a, b, c, d = st.columns(4)
    a.metric("Original", f"{h.original_tokens:,}")
    b.metric("Final", f"{h.final_tokens:,}")
    c.metric("Saved", f"{h.tokens_saved:,}")
    d.metric("Reduction", f"{h.savings_pct:.1f}%")
elif mode == "GATE":
    r = st.session_state["result"]
    st.info("Cache miss — full gate ran")
    a, b, c, d = st.columns(4)
    a.metric("Original", f"{r.original_estimate.total_tokens:,}")
    b.metric("Final", f"{r.final_tokens:,}")
    c.metric("Saved", f"{r.tokens_saved:,}")
    d.metric("Reduction", f"{r.savings_percent:.1f}%")
    st.write(
        f"**{r.route.selected_model}** · budget `{r.budget.action.value}` · "
        f"`{r.pruned.strategy if r.pruned else 'n/a'}`"
    )
    sig = st.session_state.get("sig") or {}
    st.caption(
        f"Artifacts: {sig.get('artifact_count', 0)} · "
        + ", ".join(sig.get("artifacts_sample", [])[:10])
    )

# --- 2 SCS ---
st.header("2 · SCS cache")
stats = cache_stats()
x, y, z = st.columns(3)
x.metric("Entries", stats["entries"])
y.metric("Bypass hits", stats["total_hits"])
z.metric("Model $ on hit", "$0.00")

# --- 3 Live providers ---
st.header("3 · Live provider probes (real OpenRouter HTTP)")
st.caption("Each row is an actual completion request on the gated cyber messages.")
probe = st.button("Run live probes on all matrix models")

if probe:
    if not os.environ.get("OPENROUTER_API_KEY"):
        st.error("OPENROUTER_API_KEY not set in .env")
    else:
        if "gated_messages" not in st.session_state:
            conv = build()
            result = run_gate(conv)
            st.session_state["gated_messages"] = [
                {"role": m.role.value, "content": m.content}
                for m in result.final_messages
            ]
            st.session_state["gated_tokens"] = result.final_tokens
            st.session_state["ungated_tokens"] = result.original_estimate.total_tokens

        msgs = st.session_state["gated_messages"]
        gated_tokens = st.session_state["gated_tokens"]
        results = []
        progress = st.progress(0.0)
        status = st.empty()
        table_slot = st.empty()

        models = matrix_models()
        for i, model in enumerate(models):
            status.markdown(f"⏳ **{model}** — calling OpenRouter…")
            row = {
                "model": model,
                "status": "error",
                "latency_s": None,
                "prompt_tokens": None,
                "est_input_usd": estimate_cost_usd(model, gated_tokens),
                "reply_preview": "",
            }
            t0 = time.time()
            try:
                data = chat_completion(model, msgs, max_tokens=96)
                row["latency_s"] = round(time.time() - t0, 2)
                row["status"] = "ok"
                usage = data.get("usage") or {}
                row["prompt_tokens"] = usage.get("prompt_tokens")
                try:
                    row["reply_preview"] = (
                        data["choices"][0]["message"]["content"] or ""
                    )[:160]
                except Exception:
                    row["reply_preview"] = ""
                status.markdown(f"✅ **{model}** — {row['latency_s']}s")
            except Exception as e:
                row["latency_s"] = round(time.time() - t0, 2)
                row["reply_preview"] = f"{type(e).__name__}: {e}"
                status.markdown(f"❌ **{model}** — failed")
            results.append(row)
            progress.progress((i + 1) / len(models))
            table_slot.dataframe(pd.DataFrame(results), use_container_width=True, hide_index=True)

        st.session_state["probe_results"] = results
        status.markdown("**Probes finished**")

if st.session_state.get("probe_results"):
    st.subheader("Last probe results")
    st.dataframe(
        pd.DataFrame(st.session_state["probe_results"]),
        use_container_width=True,
        hide_index=True,
    )

# --- 4 Usage (cyber only filter optional) ---
st.header("4 · Usage history")
usage = fetch_recent_usage(50)
if usage:
    df = pd.DataFrame(usage)
    # hide legacy finance demo rows from the main story if present
    if "agent_id" in df.columns:
        df_view = df[~df["agent_id"].astype(str).str.contains("finance", case=False, na=False)]
        if df_view.empty:
            df_view = df
    else:
        df_view = df
    st.metric("Calls shown", len(df_view))
    st.dataframe(df_view, use_container_width=True, hide_index=True)
else:
    st.info("No usage rows yet.")

# --- 5 SDK ---
st.header("5 · SDK")
st.code(
    """from leenfrost import Conversation, Message, Role, run_gate, lookup, store

conv = Conversation(
    messages=[Message(role=Role.USER, content="Triage cluster AC-9182 ...")],
    priority=9,
    agent_id="soc-agent-1",
)
hit = lookup(conv)
if hit:
    # $0 path
    tokens = hit.final_tokens
else:
    result = run_gate(conv)
    store(conv, result)
    messages_for_llm = [{"role": m.role.value, "content": m.content} for m in result.final_messages]
    # → Cortex / OpenRouter
""",
    language="python",
)

st.divider()
st.caption("Leenfrost · cyber SOC workloads · live OpenRouter probes · SCS $0 bypass")
