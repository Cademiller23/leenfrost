"""Leenfrost control plane — miss/hit demo, token arithmetic, multi-model probes, Cortex SQL."""

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

from examples.demo_payload import build as build_demo
from examples.storm_payload import build_storm
from leenfrost import run_gate, cache_stats, fetch_recent_usage
from leenfrost.openrouter import matrix_models, chat_completion, estimate_cost_usd
from leenfrost.cortex_sql import complete_sql
from leenfrost.prompt_layout import ordered_for_provider
from leenfrost.signature import signature_summary
from leenfrost.cache import lookup_structure

st.set_page_config(page_title="Leenfrost", page_icon="❄", layout="wide")

st.markdown(
    """
<style>
html, body, [class*="css"] { font-family: 'Inter', system-ui, sans-serif; }
.stApp { background: linear-gradient(165deg, #0B1220 0%, #0F1B2D 50%, #132337 100%); color: #E8EEF7; }
#MainMenu, footer, header { visibility: hidden; }
.block-container { padding-top: 1.2rem; max-width: 1180px; }
.lf-hero {
  background: linear-gradient(135deg, #1A4B8C 0%, #0E7490 100%);
  border: 1px solid rgba(125,211,252,.25); border-radius: 14px;
  padding: 1.2rem 1.4rem; margin-bottom: 1rem;
}
.lf-hero h1 { margin:0; color:#F0F9FF; font-size:1.6rem; }
.lf-hero p { margin:.3rem 0 0; color:#BAE6FD; font-size:.92rem; }
.lf-card {
  background: rgba(15,23,42,.75); border: 1px solid rgba(56,189,248,.16);
  border-radius: 12px; padding: 1rem 1.1rem; margin-bottom: .85rem;
}
.lf-card h3 {
  margin: 0 0 .7rem; font-size: .78rem; letter-spacing: .06em;
  text-transform: uppercase; color: #7DD3FC;
}
.lf-metric {
  background: rgba(8,47,73,.5); border: 1px solid rgba(56,189,248,.12);
  border-radius: 10px; padding: .7rem .85rem;
}
.lf-metric .label { font-size:.68rem; color:#94A3B8; text-transform:uppercase; letter-spacing:.04em; }
.lf-metric .value { font-family: ui-monospace, monospace; font-size:1.25rem; font-weight:600; color:#F8FAFC; }
.lf-metric .value.good { color:#34D399; }
.lf-metric .value.accent { color:#38BDF8; }
.lf-eq {
  font-family: ui-monospace, monospace; font-size:.84rem; color:#CBD5E1;
  background: rgba(2,12,27,.65); border-left: 3px solid #0EA5E9;
  padding: .7rem 1rem; border-radius: 0 8px 8px 0; line-height: 1.55;
}
.stButton > button {
  background: linear-gradient(135deg, #0284C7, #0369A1); color: #fff;
  border: none; border-radius: 8px; font-weight: 600;
}
</style>
""",
    unsafe_allow_html=True,
)

st.markdown(
    """
<div class="lf-hero">
  <h1>Leenfrost</h1>
  <p>Memory has a P&amp;L · Exact evidence → $0 · Related history → ROI-priced · All providers via OpenRouter · Cortex SQL for Snowsight</p>
</div>
""",
    unsafe_allow_html=True,
)

# —— Payload + session conversation (same object for miss then hit) ——
payload_kind = st.radio("Workload", ["Standard cyber SOC", "Storm (large)"], horizontal=True)
if "conv" not in st.session_state or st.button("Reset conversation"):
    st.session_state["conv"] = build_storm() if payload_kind.startswith("Storm") else build_demo()
    st.session_state.pop("miss_result", None)
    st.session_state.pop("hit_result", None)

conv = st.session_state["conv"]
use_everos = st.toggle("EverOS memory (ROI-priced)", value=True)

c1, c2, c3 = st.columns(3)
with c1:
    run_miss = st.button("1 · Run MISS path", type="primary", use_container_width=True)
with c2:
    run_hit = st.button("2 · Run HIT path (same evidence)", use_container_width=True)
with c3:
    run_probes = st.button("3 · Live multi-model probes", use_container_width=True)

with st.expander("Advanced"):
    st.caption("Demo controls — not production defaults.")
    st.write(f"agent_id=`{conv.agent_id}` messages=`{len(conv.messages)}` priority=`{conv.priority}`")

if run_miss:
    with st.spinner("MISS: EverOS → ROI → prune → route…"):
        st.session_state["miss_result"] = run_gate(conv, use_everos=use_everos, use_scs=False)
        try:
            from leenfrost import log_gate_result
            log_gate_result(st.session_state["miss_result"], agent_id=conv.agent_id or "soc")
        except Exception:
            pass

if run_hit:
    with st.spinner("HIT: SCS exact evidence…"):
        st.session_state["hit_result"] = run_gate(conv, use_everos=use_everos, use_scs=True)
        try:
            from leenfrost import log_gate_result
            log_gate_result(st.session_state["hit_result"], agent_id=conv.agent_id or "soc")
        except Exception:
            pass

def render_result(title: str, r) -> None:
    st.markdown(f'<div class="lf-card"><h3>{title}</h3>', unsafe_allow_html=True)
    a, b, c, d = st.columns(4)
    with a:
        st.markdown(
            f'<div class="lf-metric"><div class="label">Raw tokens</div>'
            f'<div class="value">{getattr(r, "raw_tokens", r.original_estimate.total_tokens)}</div></div>',
            unsafe_allow_html=True,
        )
    with b:
        label = "Model tokens" if r.scs_hit else "Provider prompt"
        val = getattr(r, "model_tokens", r.final_tokens) if r.scs_hit else getattr(r, "provider_prompt_tokens", r.final_tokens)
        cls = "good" if r.scs_hit else "accent"
        st.markdown(
            f'<div class="lf-metric"><div class="label">{label}</div>'
            f'<div class="value {cls}">{val}</div></div>',
            unsafe_allow_html=True,
        )
    with c:
        st.markdown(
            f'<div class="lf-metric"><div class="label">Context saved %</div>'
            f'<div class="value good">{r.savings_percent}%</div></div>',
            unsafe_allow_html=True,
        )
    with d:
        cls = "good" if r.scs_hit else "accent"
        st.markdown(
            f'<div class="lf-metric"><div class="label">Route</div>'
            f'<div class="value {cls}">{r.route.selected_model}</div></div>',
            unsafe_allow_html=True,
        )

    # Token equation (honest)
    if r.scs_hit:
        eq = (
            f"RAW {getattr(r, 'raw_tokens', r.original_estimate.total_tokens)} inspected<br>"
            f"SCS EXACT HIT → MODEL TOKENS 0 · MODEL COST $0<br>"
            f"MEMORY ROI skipped (exact evidence)"
        )
    else:
        raw = getattr(r, "raw_tokens", r.original_estimate.total_tokens)
        mem = getattr(r, "memory_injected_tokens", r.memory_tokens_injected)
        prov = getattr(r, "provider_prompt_tokens", r.final_tokens)
        pruned = getattr(r, "pruned_tokens_before_memory", prov)
        eq = (
            f"RAW {raw}<br>"
            f"AFTER PRUNE (pre-display) ~ {pruned}<br>"
            f"MEMORY INJECTED +{mem} (ROI admitted {r.memory_admitted}/{r.memory_returned}, rejected tok {r.memory_tokens_rejected})<br>"
            f"PROVIDER PROMPT {prov}<br>"
            f"ROUTE {r.route.selected_model}"
        )
    st.markdown(f'<div class="lf-eq">{eq}</div>', unsafe_allow_html=True)

    # Structure badge
    try:
        st_hit = lookup_structure(conv)
        if st_hit and not r.scs_hit:
            st.info("Structure fingerprint matched a prior pattern — informs triage; does **not** authorize $0 model bypass.")
    except Exception:
        pass

    st.markdown("</div>", unsafe_allow_html=True)

if st.session_state.get("miss_result") is not None:
    render_result("Miss path · EverOS + ROI + prune", st.session_state["miss_result"])
if st.session_state.get("hit_result") is not None:
    render_result("Hit path · SCS exact evidence", st.session_state["hit_result"])

# Cortex SQL
st.markdown('<div class="lf-card"><h3>Cortex · Snowsight SQL</h3>', unsafe_allow_html=True)
src = st.session_state.get("miss_result") or st.session_state.get("hit_result")
if src is not None and not src.scs_hit:
    ordered = ordered_for_provider(list(src.final_messages))
    sql = complete_sql(ordered)
    st.code(sql, language="sql")
    st.caption("Paste into Snowsight. Python connector may be MFA-blocked; SQL is the live Cortex proof.")
elif src is not None and src.scs_hit:
    st.caption("Exact SCS hit — no model call; Cortex not required for this run.")
else:
    st.caption("Run a MISS path to generate Cortex SQL for the gated payload.")
st.markdown("</div>", unsafe_allow_html=True)

# SCS stats
st.markdown('<div class="lf-card"><h3>SCS cache</h3>', unsafe_allow_html=True)
stats = cache_stats()
x, y, z = st.columns(3)
x.metric("Entries", int(stats.get("entries") or 0))
y.metric("Bypass hits", int(stats.get("total_hits") or 0))
z.metric("Model $ on exact hit", "$0.00")
st.markdown("</div>", unsafe_allow_html=True)

# Multi-model probes (all models)
st.markdown('<div class="lf-card"><h3>Live provider probes · full matrix</h3>', unsafe_allow_html=True)
st.caption("Real OpenRouter HTTP on the gated miss payload — every model in the matrix.")
if run_probes:
    base = st.session_state.get("miss_result")
    if base is None:
        base = run_gate(conv, use_everos=use_everos, use_scs=False)
        st.session_state["miss_result"] = base
    messages = [{"role": m.role.value, "content": m.content} for m in base.final_messages]
    rows = []
    bar = st.progress(0.0, text="Probing…")
    models = matrix_models()
    for i, model in enumerate(models):
        bar.progress(i / max(len(models), 1), text=f"{model}")
        t0 = time.time()
        try:
            data = chat_completion(model=model, messages=messages, max_tokens=64)
            latency = round(time.time() - t0, 2)
            usage = data.get("usage") or {}
            text = ""
            ch = data.get("choices") or []
            if ch:
                text = ((ch[0].get("message") or {}).get("content") or "")[:120]
            rows.append({
                "model": model,
                "status": "ok",
                "latency_s": latency,
                "prompt_tokens": usage.get("prompt_tokens"),
                "est_input_usd": round(estimate_cost_usd(model, base.final_tokens), 6),
                "reply_preview": text,
            })
        except Exception as e:
            rows.append({
                "model": model,
                "status": f"fail:{type(e).__name__}",
                "latency_s": round(time.time() - t0, 2),
                "prompt_tokens": None,
                "est_input_usd": None,
                "reply_preview": str(e)[:120],
            })
        bar.progress((i + 1) / max(len(models), 1), text=f"done {model}")
    st.session_state["probe_rows"] = rows
    bar.empty()

if st.session_state.get("probe_rows"):
    st.dataframe(pd.DataFrame(st.session_state["probe_rows"]), width="stretch", hide_index=True)
st.markdown("</div>", unsafe_allow_html=True)

st.markdown('<div class="lf-card"><h3>Usage history</h3>', unsafe_allow_html=True)
try:
    usage = fetch_recent_usage(15)
    if usage:
        st.dataframe(pd.DataFrame(usage), width="stretch", hide_index=True)
except Exception as e:
    st.caption(str(e))
st.markdown("</div>", unsafe_allow_html=True)

st.caption("Leenfrost · Snowflake × EverMind · multi-provider · SCS exact $0 · Memory ROI")
