"""Leenfrost control plane — enterprise Cost of Intelligence dashboard."""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

import pandas as pd
import streamlit as st

from examples.demo_payload import build
from leenfrost import run_gate, cache_stats, fetch_recent_usage
from leenfrost.openrouter import matrix_models, chat_completion, estimate_cost_usd

st.set_page_config(
    page_title="Leenfrost",
    page_icon="❄",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown(
    """
<style>
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500&display=swap');

html, body, [class*="css"] {
  font-family: 'IBM Plex Sans', system-ui, sans-serif;
}

.stApp {
  background: linear-gradient(165deg, #0B1220 0%, #0F1B2D 45%, #132337 100%);
  color: #E8EEF7;
}

/* Hide default streamlit chrome noise */
#MainMenu {visibility: hidden;}
footer {visibility: hidden;}
header {visibility: hidden;}

.block-container {
  padding-top: 1.5rem;
  padding-bottom: 3rem;
  max-width: 1200px;
}

.lf-hero {
  background: linear-gradient(135deg, #1A4B8C 0%, #0E7490 55%, #155E75 100%);
  border: 1px solid rgba(125, 211, 252, 0.25);
  border-radius: 16px;
  padding: 1.4rem 1.6rem;
  margin-bottom: 1.25rem;
  box-shadow: 0 12px 40px rgba(14, 116, 144, 0.25);
}
.lf-hero h1 {
  margin: 0;
  font-size: 1.75rem;
  font-weight: 700;
  color: #F0F9FF;
  letter-spacing: -0.02em;
}
.lf-hero p {
  margin: 0.35rem 0 0 0;
  color: #BAE6FD;
  font-size: 0.95rem;
}

.lf-card {
  background: rgba(15, 23, 42, 0.72);
  border: 1px solid rgba(56, 189, 248, 0.18);
  border-radius: 14px;
  padding: 1rem 1.1rem;
  margin-bottom: 0.9rem;
}
.lf-card h3 {
  margin: 0 0 0.75rem 0;
  font-size: 0.85rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: #7DD3FC;
}

.lf-metric {
  background: rgba(8, 47, 73, 0.55);
  border: 1px solid rgba(56, 189, 248, 0.15);
  border-radius: 12px;
  padding: 0.85rem 1rem;
  text-align: left;
}
.lf-metric .label {
  font-size: 0.72rem;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: #94A3B8;
  margin-bottom: 0.25rem;
}
.lf-metric .value {
  font-family: 'IBM Plex Mono', monospace;
  font-size: 1.35rem;
  font-weight: 600;
  color: #F8FAFC;
}
.lf-metric .value.accent { color: #38BDF8; }
.lf-metric .value.good { color: #34D399; }
.lf-metric .value.warn { color: #FBBF24; }

.lf-trace {
  font-family: 'IBM Plex Mono', monospace;
  font-size: 0.82rem;
  line-height: 1.55;
  color: #CBD5E1;
  background: rgba(2, 12, 27, 0.65);
  border-left: 3px solid #0EA5E9;
  padding: 0.75rem 1rem;
  border-radius: 0 10px 10px 0;
}
.lf-trace .hit { color: #34D399; font-weight: 600; }

div[data-testid="stDataFrame"] {
  border: 1px solid rgba(56, 189, 248, 0.15);
  border-radius: 12px;
  overflow: hidden;
}

.stButton > button {
  background: linear-gradient(135deg, #0284C7, #0369A1);
  color: white;
  border: none;
  border-radius: 10px;
  font-weight: 600;
  padding: 0.55rem 1.1rem;
  box-shadow: 0 4px 14px rgba(3, 105, 161, 0.35);
}
.stButton > button:hover {
  background: linear-gradient(135deg, #0EA5E9, #0284C7);
  color: white;
  border: none;
}

/* Toggle labels */
.stCheckbox label, .stToggle label {
  color: #E2E8F0 !important;
}

[data-testid="stMetricValue"] {
  font-family: 'IBM Plex Mono', monospace;
  color: #F8FAFC;
}
[data-testid="stMetricLabel"] {
  color: #94A3B8;
}
</style>
""",
    unsafe_allow_html=True,
)

# —— Hero ——
st.markdown(
    """
<div class="lf-hero">
  <h1>Leenfrost</h1>
  <p>Memory has a P&amp;L · Exact evidence → $0 · Related history → ROI-priced · Snowflake + EverOS + live providers</p>
</div>
""",
    unsafe_allow_html=True,
)

# —— Controls ——
c1, c2, c3 = st.columns([1.2, 1.2, 1])
with c1:
    force_miss = st.toggle("Force SCS miss (EverOS + ROI path)", value=False)
with c2:
    use_everos = st.toggle("EverOS memory on", value=True)
with c3:
    run_clicked = st.button("Run cyber SOC gate", type="primary", use_container_width=True)

if run_clicked:
    conv = build()
    with st.spinner("Control plane: SCS → EverOS → ROI → prune → route…"):
        result = run_gate(conv, use_everos=use_everos, use_scs=not force_miss)
        # Log usage when helper exists
        try:
            from leenfrost import log_gate_result

            log_gate_result(result, agent_id=conv.agent_id or "soc-triage")
        except Exception:
            pass
    st.session_state["last_result"] = result

result = st.session_state.get("last_result")

# —— Section 1: Gate ——
st.markdown('<div class="lf-card"><h3>1 · Live gate</h3>', unsafe_allow_html=True)
if result is None:
    st.info("Run the cyber SOC gate to populate metrics.")
else:
    a, b, c, d = st.columns(4)
    with a:
        st.markdown(
            f'<div class="lf-metric"><div class="label">Original</div>'
            f'<div class="value">{result.original_estimate.total_tokens}</div></div>',
            unsafe_allow_html=True,
        )
    with b:
        st.markdown(
            f'<div class="lf-metric"><div class="label">Final</div>'
            f'<div class="value accent">{result.final_tokens}</div></div>',
            unsafe_allow_html=True,
        )
    with c:
        st.markdown(
            f'<div class="lf-metric"><div class="label">Saved</div>'
            f'<div class="value good">{result.savings_percent}%</div></div>',
            unsafe_allow_html=True,
        )
    with d:
        model_cls = "good" if result.scs_hit else "accent"
        st.markdown(
            f'<div class="lf-metric"><div class="label">Model</div>'
            f'<div class="value {model_cls}">{result.route.selected_model}</div></div>',
            unsafe_allow_html=True,
        )
st.markdown("</div>", unsafe_allow_html=True)

# —— Section 2: Memory ROI ——
st.markdown('<div class="lf-card"><h3>2 · Memory ROI (EverOS priced)</h3>', unsafe_allow_html=True)
if result is not None:
    m1, m2, m3, m4 = st.columns(4)
    with m1:
        st.markdown(
            f'<div class="lf-metric"><div class="label">Returned</div>'
            f'<div class="value">{getattr(result, "memory_returned", 0)}</div></div>',
            unsafe_allow_html=True,
        )
    with m2:
        st.markdown(
            f'<div class="lf-metric"><div class="label">Admitted</div>'
            f'<div class="value accent">{getattr(result, "memory_admitted", 0)}</div></div>',
            unsafe_allow_html=True,
        )
    with m3:
        st.markdown(
            f'<div class="lf-metric"><div class="label">Tokens injected</div>'
            f'<div class="value">{getattr(result, "memory_tokens_injected", 0)}</div></div>',
            unsafe_allow_html=True,
        )
    with m4:
        st.markdown(
            f'<div class="lf-metric"><div class="label">Tokens rejected</div>'
            f'<div class="value warn">{getattr(result, "memory_tokens_rejected", 0)}</div></div>',
            unsafe_allow_html=True,
        )
    st.caption("Most systems retrieve top-k and stuff the prompt. Leenfrost asks whether memory is worth its tokens.")
else:
    st.caption("No gate run yet.")
st.markdown("</div>", unsafe_allow_html=True)

# —— Section 3: P&L trace ——
st.markdown('<div class="lf-card"><h3>3 · Token P&amp;L trace</h3>', unsafe_allow_html=True)
if result is not None:
    lines = getattr(result, "pnl_trace", None) or []
    if getattr(result, "scs_hit", False):
        html = '<div class="lf-trace"><span class="hit">SCS EXACT HIT → MODEL $0</span><br>'
        html += "<br>".join(lines)
        html += "</div>"
    else:
        html = '<div class="lf-trace">' + "<br>".join(lines) + "</div>"
    st.markdown(html, unsafe_allow_html=True)
else:
    st.caption("Trace appears after a gate run.")
st.markdown("</div>", unsafe_allow_html=True)

# —— Section 4: SCS ——
st.markdown('<div class="lf-card"><h3>4 · SCS cache</h3>', unsafe_allow_html=True)
stats = cache_stats()
entries = int(stats.get("entries") or 0)
hits = int(stats.get("total_hits") or 0)
s1, s2, s3 = st.columns(3)
with s1:
    st.markdown(
        f'<div class="lf-metric"><div class="label">Entries</div><div class="value">{entries}</div></div>',
        unsafe_allow_html=True,
    )
with s2:
    st.markdown(
        f'<div class="lf-metric"><div class="label">Bypass hits</div><div class="value good">{hits}</div></div>',
        unsafe_allow_html=True,
    )
with s3:
    # $0 is the economic meaning of an exact hit — not a fake KPI
    dollar = "$0.00" if (result and getattr(result, "scs_hit", False)) or hits > 0 else "—"
    st.markdown(
        f'<div class="lf-metric"><div class="label">Model $ on exact hit</div>'
        f'<div class="value good">{dollar}</div></div>',
        unsafe_allow_html=True,
    )
st.markdown("</div>", unsafe_allow_html=True)

# —— Section 5: Live OpenRouter ——
st.markdown(
    '<div class="lf-card"><h3>5 · Live provider probes (real OpenRouter HTTP)</h3>',
    unsafe_allow_html=True,
)
st.caption("Each row is an actual completion on the gated cyber messages — not a simulated matrix.")
if st.button("Run live probes on all matrix models"):
    conv = build()
    gated = run_gate(conv, use_everos=True, use_scs=False)
    messages = [{"role": m.role.value, "content": m.content} for m in gated.final_messages]
    models = matrix_models()
    rows = []
    bar = st.progress(0.0, text="Starting probes…")
    for i, model in enumerate(models):
        bar.progress((i) / max(len(models), 1), text=f"Calling {model}…")
        t0 = time.time()
        try:
            data = chat_completion(model=model, messages=messages, max_tokens=80)
            latency = round(time.time() - t0, 2)
            usage = data.get("usage") or {}
            text = ""
            choices = data.get("choices") or []
            if choices:
                text = ((choices[0].get("message") or {}).get("content") or "")[:140]
            rows.append(
                {
                    "model": model,
                    "status": "ok",
                    "latency_s": latency,
                    "prompt_tokens": usage.get("prompt_tokens"),
                    "est_input_usd": round(estimate_cost_usd(model, gated.final_tokens), 6),
                    "reply_preview": text,
                }
            )
        except Exception as e:
            rows.append(
                {
                    "model": model,
                    "status": f"fail:{type(e).__name__}",
                    "latency_s": round(time.time() - t0, 2),
                    "prompt_tokens": None,
                    "est_input_usd": None,
                    "reply_preview": str(e)[:140],
                }
            )
        bar.progress((i + 1) / max(len(models), 1), text=f"Finished {model}")
    st.session_state["probe_rows"] = rows
    bar.empty()

if st.session_state.get("probe_rows"):
    st.dataframe(pd.DataFrame(st.session_state["probe_rows"]), width="stretch", hide_index=True)
st.markdown("</div>", unsafe_allow_html=True)

# —— Section 6: Usage ——
st.markdown('<div class="lf-card"><h3>6 · Usage history</h3>', unsafe_allow_html=True)
try:
    usage = fetch_recent_usage(20)
    if usage:
        df = pd.DataFrame(usage)
        # Prefer cyber agents; still show all if filter empties
        if "agent_id" in df.columns:
            cyber = df[~df["agent_id"].astype(str).str.contains("finance", case=False, na=False)]
            if len(cyber):
                df = cyber
        st.dataframe(df, width="stretch", hide_index=True)
    else:
        st.caption("No usage rows yet.")
except Exception as e:
    st.caption(f"Usage store unavailable: {e}")
st.markdown("</div>", unsafe_allow_html=True)

# —— SDK ——
st.markdown('<div class="lf-card"><h3>7 · SDK</h3>', unsafe_allow_html=True)
st.code(
    """from leenfrost import run_gate

result = run_gate(conversation, use_everos=True, use_scs=True)
if result.scs_hit:
    # exact evidence — do not call a frontier model
    ...
else:
    messages = result.final_messages  # ROI-priced memory + pruned context
""",
    language="python",
)
st.markdown("</div>", unsafe_allow_html=True)

st.markdown(
    "<p style='text-align:center;color:#64748B;font-size:0.8rem;margin-top:1.5rem;'>"
    "Leenfrost · Snowflake × EverMind · Agent & Token Economy Hackathon</p>",
    unsafe_allow_html=True,
)
