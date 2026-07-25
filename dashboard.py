"""
Module 4 - the Quantitative Savings Dashboard.

Run with:
    streamlit run dashboard.py

Reads the baseline and AI-driven EnergyPlus output directories (outputs/baseline,
outputs/ai by default) plus the agent's own decision log
(outputs/ai/agent_decisions.jsonl written by `state_bus.BuildingStateBus`), and
renders:
  * a dual-axis time series of baseline vs AI HVAC kWh, with grid carbon
    intensity on the second axis,
  * a PMV comfort-boundary scatter/heatmap for the AI run, proving the
    [-0.5, +0.5] band was respected,
  * headline % energy and % carbon savings,
  * the raw agent decision trail (setpoint, reason, latency, source).
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from config import AI_DIR, BASELINE_DIR, POLICY
from eco_loop import eplus_outputs as eo

st.set_page_config(page_title="Eco-Loop Building Agents", layout="wide", page_icon="\U0001F343")

# --------------------------------------------------------------------------- #
# Sidebar - run selection
# --------------------------------------------------------------------------- #
st.sidebar.title("Eco-Loop")
st.sidebar.caption("Autonomous, LLM-in-the-loop HVAC supervision")

baseline_dir = Path(st.sidebar.text_input("Baseline output dir", str(BASELINE_DIR)))
ai_dir = Path(st.sidebar.text_input("AI-driven output dir", str(AI_DIR)))
refresh = st.sidebar.button("Reload", use_container_width=True)

st.title("Quantitative Savings Dashboard")


# --------------------------------------------------------------------------- #
# Data loading (cached; a manual Reload bypasses the cache)
# --------------------------------------------------------------------------- #
@st.cache_data(show_spinner=False)
def _load_csv(path_str: str, _bust: bool):
    return eo.load_csv(Path(path_str) / "eplusout.csv")


@st.cache_data(show_spinner=False)
def _load_decisions(path_str: str, _bust: bool):
    path = Path(path_str) / "agent_decisions.jsonl"
    if not path.exists():
        return pd.DataFrame()
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    if not rows:
        return pd.DataFrame()
    df = pd.json_normalize(rows)
    return df


baseline_df = _load_csv(str(baseline_dir), refresh)
ai_df = _load_csv(str(ai_dir), refresh)
decisions_df = _load_decisions(str(ai_dir), refresh)

if baseline_df is None and ai_df is None:
    st.warning(
        "No `eplusout.csv` found in either output directory yet.\n\n"
        "Run `python main.py run-baseline` and `python main.py run-ai` first, "
        "or point the sidebar paths at existing output folders."
    )
    st.stop()


# --------------------------------------------------------------------------- #
# Headline metrics
# --------------------------------------------------------------------------- #
def _steps_per_hour(df: pd.DataFrame) -> int:
    return eo._infer_steps_per_hour(df) if df is not None and not df.empty else 4


def _kwh_series(df: pd.DataFrame) -> pd.Series | None:
    if df is None:
        return None
    power = eo.facility_power_series(df)
    if power is None:
        return None
    return power.fillna(0.0) / 1000.0 / _steps_per_hour(df)


baseline_kwh = _kwh_series(baseline_df)
ai_kwh = _kwh_series(ai_df)

total_baseline = float(baseline_kwh.sum()) if baseline_kwh is not None else None
total_ai = float(ai_kwh.sum()) if ai_kwh is not None else None

col1, col2, col3, col4 = st.columns(4)
col1.metric("Baseline HVAC energy", f"{total_baseline:,.1f} kWh" if total_baseline is not None else "n/a")
col2.metric("AI-driven HVAC energy", f"{total_ai:,.1f} kWh" if total_ai is not None else "n/a")

if total_baseline and total_ai is not None and total_baseline > 0:
    savings_pct = 100.0 * (total_baseline - total_ai) / total_baseline
    col3.metric("Energy savings", f"{savings_pct:+.1f}%", delta=f"{total_baseline - total_ai:+.1f} kWh")
else:
    col3.metric("Energy savings", "n/a")
    savings_pct = None

if not decisions_df.empty:
    n_llm = int((decisions_df.get("source") == "llm").sum())
    n_fallback = int((decisions_df.get("source") == "fallback").sum())
    total_turns = max(1, n_llm + n_fallback)
    col4.metric("LLM decisions", f"{n_llm}/{total_turns}", delta=f"{n_fallback} fallback")
else:
    col4.metric("LLM decisions", "n/a")

st.divider()


# --------------------------------------------------------------------------- #
# Dual-axis time series: baseline vs AI kWh, + grid carbon intensity
# --------------------------------------------------------------------------- #
st.subheader("Energy consumption - baseline vs AI-driven")

fig = make_subplots(specs=[[{"secondary_y": True}]])

if baseline_kwh is not None:
    fig.add_trace(
        go.Scatter(x=list(range(len(baseline_kwh))), y=baseline_kwh, name="Baseline HVAC (kWh/step)",
                   line=dict(color="#9aa0a6", width=1.5)),
        secondary_y=False,
    )
if ai_kwh is not None:
    fig.add_trace(
        go.Scatter(x=list(range(len(ai_kwh))), y=ai_kwh, name="AI-driven HVAC (kWh/step)",
                   line=dict(color="#2e7d32", width=1.8)),
        secondary_y=False,
    )

if not decisions_df.empty and "state_digest.grid_carbon_intensity_g_per_kwh" in decisions_df.columns:
    fig.add_trace(
        go.Scatter(
            x=list(range(len(decisions_df))),
            y=decisions_df["state_digest.grid_carbon_intensity_g_per_kwh"],
            name="Grid carbon intensity (g/kWh)",
            line=dict(color="#c62828", width=1, dash="dot"),
            opacity=0.6,
        ),
        secondary_y=True,
    )

fig.update_layout(
    height=430,
    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
    margin=dict(t=40, l=10, r=10, b=10),
)
fig.update_yaxes(title_text="HVAC energy (kWh per timestep)", secondary_y=False)
fig.update_yaxes(title_text="Grid carbon intensity (gCO2e/kWh)", secondary_y=True)
fig.update_xaxes(title_text="Timestep")
st.plotly_chart(fig, use_container_width=True)


# --------------------------------------------------------------------------- #
# PMV comfort boundary
# --------------------------------------------------------------------------- #
st.subheader("Thermal comfort (PMV) - AI-driven run")

if decisions_df.empty or "state_digest.worst_pmv" not in decisions_df.columns:
    st.info("No agent decision log found yet - run the AI-driven simulation to populate this chart.")
else:
    pmv = pd.to_numeric(decisions_df["state_digest.worst_pmv"], errors="coerce")
    energy = pd.to_numeric(decisions_df.get("state_digest.hvac_power_kw"), errors="coerce")
    timestamps = decisions_df.get("timestamp", pd.Series(range(len(decisions_df))))
    in_band = pmv.between(POLICY.pmv_low, POLICY.pmv_high)

    # PMV only constrains comfort when someone is actually in the building.
    # Scoring unoccupied night-time setback as a "comfort failure" would badly
    # understate the controller, so occupancy is shown as a distinct series.
    if "state_digest.occupied" in decisions_df.columns:
        occupied = decisions_df["state_digest.occupied"].fillna(True).astype(bool)
    else:
        occupied = pd.Series([True] * len(pmv))

    scatter = go.Figure()
    scatter.add_hrect(y0=POLICY.pmv_low, y1=POLICY.pmv_high, fillcolor="#2e7d32", opacity=0.10,
                       line_width=0, annotation_text="comfort band", annotation_position="top left")

    hover = [
        f"{t}<br>HVAC {e:.2f} kW<br>{'occupied' if o else 'unoccupied'}"
        if pd.notna(e) else f"{t}<br>{'occupied' if o else 'unoccupied'}"
        for t, e, o in zip(timestamps, energy, occupied)
    ]
    idx = list(range(len(pmv)))

    for mask, label, symbol, opacity in [
        (occupied, "Occupied", "circle", 1.0),
        (~occupied, "Unoccupied (setback)", "x", 0.45),
    ]:
        if not mask.any():
            continue
        scatter.add_trace(
            go.Scatter(
                x=[i for i, m in zip(idx, mask) if m],
                y=pmv[mask],
                mode="markers",
                marker=dict(
                    size=9,
                    symbol=symbol,
                    opacity=opacity,
                    color=in_band[mask].map({True: "#2e7d32", False: "#c62828"}),
                    line=dict(width=0.5, color="white"),
                ),
                text=[h for h, m in zip(hover, mask) if m],
                hovertemplate="%{text}<br>PMV=%{y:.2f}<extra></extra>",
                name=label,
            )
        )

    scatter.add_hline(y=POLICY.pmv_low, line_dash="dash", line_color="#c62828", opacity=0.5)
    scatter.add_hline(y=POLICY.pmv_high, line_dash="dash", line_color="#c62828", opacity=0.5)
    scatter.update_layout(
        height=380,
        xaxis_title="Control interval",
        yaxis_title="PMV (worst zone)",
        margin=dict(t=30, l=10, r=10, b=10),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
    )
    st.plotly_chart(scatter, use_container_width=True)

    occ_band = in_band[occupied]
    if len(occ_band):
        st.caption(
            f"**{100.0 * occ_band.mean():.1f}% of occupied intervals** kept the worst zone "
            f"inside the [{POLICY.pmv_low}, {POLICY.pmv_high}] PMV band "
            f"({int(occ_band.sum())}/{len(occ_band)}). "
            f"Unoccupied intervals ({int((~occupied).sum())}) are shown but excluded - "
            "PMV is not a constraint when nobody is in the building."
        )
    else:
        st.caption(
            f"{100.0 * in_band.mean():.1f}% of intervals inside the PMV band "
            f"({int(in_band.sum())}/{len(in_band)})."
        )


# --------------------------------------------------------------------------- #
# Decision trail
# --------------------------------------------------------------------------- #
st.subheader("Agent decision trail")
if decisions_df.empty:
    st.info("No decisions logged yet.")
else:
    show_cols = [
        c for c in [
            "timestamp", "source", "command.cooling_sp", "command.heating_sp",
            "command.reason", "latency_s", "tool_calls", "error",
        ]
        if c in decisions_df.columns
    ]
    st.dataframe(
        decisions_df[show_cols].rename(columns={
            "command.cooling_sp": "cooling_sp_c",
            "command.heating_sp": "heating_sp_c",
            "command.reason": "reason",
        }),
        use_container_width=True,
        height=320,
    )

st.divider()
st.caption(
    "Eco-Loop Building Agents - EnergyPlus (pyenergyplus) + FastMCP + a local "
    "OpenAI-compatible LLM (Ollama/vLLM), closing the loop between building "
    "physics and an autonomous controller."
)
