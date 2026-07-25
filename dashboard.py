"""
Module 4 - the Quantitative Savings Dashboard.

    streamlit run dashboard.py

Reads the baseline and AI-driven EnergyPlus output directories plus the agent's
own decision log (`agent_decisions.jsonl`) and answers four questions in order:

    1. How much energy did the agent save?      -> KPI row + cumulative divergence
    2. When did it save it?                     -> daily load profile
    3. Did occupants pay for it?                -> PMV band + per-zone breakdown
    4. What did the agent actually do, and why? -> setpoint trajectory + decision log

Visual conventions follow one rule set throughout: two series (baseline, AI)
in fixed categorical slots that never swap, a single y-axis per plot (no
dual-axis - the two scales would imply a correlation that is not in the data),
hairline grids, and a table view under every chart so no value is reachable
only by hovering.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Optional

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from config import AI_DIR, BASELINE_DIR, OUTPUT_DIR, POLICY
from eco_loop import eplus_outputs as eo

st.set_page_config(
    page_title="Eco-Loop Building Agents",
    layout="wide",
    page_icon="\U0001F343",
    initial_sidebar_state="expanded",
)

# --------------------------------------------------------------------------- #
# Palette - validated with the data-viz palette validator in both modes.
# Slot 1 (blue) = baseline, slot 2 (orange) = AI. Colour follows the entity:
# these never swap, so "orange is the agent" holds across every chart.
# --------------------------------------------------------------------------- #
def _is_dark() -> bool:
    try:
        base = st.get_option("theme.base")
        if base:
            return str(base).lower() == "dark"
    except Exception:
        pass
    return False


DARK = _is_dark()

PALETTE: Dict[str, str] = {
    "baseline":  "#3987e5" if DARK else "#2a78d6",
    "ai":        "#d95926" if DARK else "#eb6834",
    "surface":   "#1a1a19" if DARK else "#fcfcfb",
    "text":      "#ffffff" if DARK else "#0b0b0b",
    "secondary": "#c3c2b7" if DARK else "#52514e",
    "muted":     "#898781",
    "grid":      "#2c2c2a" if DARK else "#e1e0d9",
    "axis":      "#383835" if DARK else "#c3c2b7",
    "good":      "#0ca30c",
    "critical":  "#d03b3b",
    "band":      "rgba(12,163,12,0.10)",
    # Setpoint chart only. It compares two setpoints rather than two runs, so
    # colour is semantic (cool hue = cooling, warm hue = heating) instead of
    # reusing the baseline/AI identity slots.
    "cool_sp":   "#3987e5" if DARK else "#2a78d6",
    "heat_sp":   "#d95926" if DARK else "#eb6834",
}

FONT = 'system-ui, -apple-system, "Segoe UI", sans-serif'


def base_layout(height: int = 340, y_title: str = "", x_title: str = "") -> dict:
    """Shared chart chrome: recessive grid, no chart-junk, legend above plot."""
    axis = dict(
        showgrid=True,
        gridcolor=PALETTE["grid"],
        gridwidth=1,
        zeroline=False,
        linecolor=PALETTE["axis"],
        linewidth=1,
        tickfont=dict(color=PALETTE["muted"], size=11),
        title_font=dict(color=PALETTE["secondary"], size=12),
    )
    return dict(
        height=height,
        margin=dict(t=48, l=8, r=8, b=8),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family=FONT, color=PALETTE["secondary"], size=12),
        hoverlabel=dict(font_size=12, font_family=FONT),
        legend=dict(
            orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0,
            font=dict(color=PALETTE["secondary"], size=12),
        ),
        xaxis={**axis, "title": x_title},
        yaxis={**axis, "title": y_title},
    )


# --------------------------------------------------------------------------- #
# Filter row - one selector scoping every chart below it.
# --------------------------------------------------------------------------- #
DATASETS = {
    "Summer week (15-21 Jul)": (BASELINE_DIR, AI_DIR, 7),
    "Winter week (15-21 Jan)": (OUTPUT_DIR / "winter_baseline", OUTPUT_DIR / "winter_ai", 1),
}

st.sidebar.title("Eco-Loop")
st.sidebar.caption("Autonomous, LLM-in-the-loop HVAC supervision")

available = {k: v for k, v in DATASETS.items() if (v[1] / "eplusout.csv").exists()}
if not available:
    available = DATASETS

choice = st.sidebar.radio("Run period", list(available.keys()) + ["Custom paths"], index=0)

if choice == "Custom paths":
    baseline_dir = Path(st.sidebar.text_input("Baseline output dir", str(BASELINE_DIR)))
    ai_dir = Path(st.sidebar.text_input("AI-driven output dir", str(AI_DIR)))
    month = st.sidebar.number_input(
        "Month (seasonal clothing assumption)", 1, 12, 7,
        help="Drives the ISO 7730 clothing value: 1.0 clo Nov-Mar, 0.5 clo otherwise.",
    )
else:
    baseline_dir, ai_dir, month = available[choice]

if st.sidebar.button("Reload data", use_container_width=True):
    st.cache_data.clear()

st.sidebar.divider()
st.sidebar.caption(
    f"**Comfort band** PMV {POLICY.pmv_low} to {POLICY.pmv_high} (ISO 7730)\n\n"
    f"**Cooling setpoint** {POLICY.cooling_sp_min}-{POLICY.cooling_sp_max} degC\n\n"
    f"**Heating setpoint** {POLICY.heating_sp_min}-{POLICY.heating_sp_max} degC"
)


# --------------------------------------------------------------------------- #
# Cached loaders
# --------------------------------------------------------------------------- #
@st.cache_data(show_spinner=False)
def load_decisions(path_str: str) -> pd.DataFrame:
    path = Path(path_str) / "agent_decisions.jsonl"
    if not path.exists():
        return pd.DataFrame()
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return pd.json_normalize(rows) if rows else pd.DataFrame()


@st.cache_data(show_spinner=False)
def load_comparison(b: str, a: str, m: int) -> dict:
    return eo.compare_runs(Path(b), Path(a), m)


@st.cache_data(show_spinner=False)
def load_cumulative(p: str):
    return eo.cumulative_energy(Path(p))


@st.cache_data(show_spinner=False)
def load_profile(p: str):
    return eo.hourly_power_profile(Path(p))


@st.cache_data(show_spinner=False)
def load_zone_comfort(p: str, m: int):
    return eo.per_zone_comfort(Path(p), m)


comparison = load_comparison(str(baseline_dir), str(ai_dir), int(month))
decisions = load_decisions(str(ai_dir))

st.title("Quantitative Savings Dashboard")
st.caption(
    f"{choice} - EnergyPlus closed-loop simulation supervised by a local LLM over MCP. "
    "Baseline runs the model's native thermostat schedules with the agent detached; "
    "every other variable is identical."
)

if comparison.get("baseline_kwh") is None or comparison.get("ai_kwh") is None:
    st.warning(
        "No results found for this selection.\n\n"
        "Run the pair first, e.g.\n\n"
        "`python main.py run-baseline --start 07-15 --end 07-21 --output-dir outputs/baseline`\n\n"
        "`python main.py run-ai --start 07-15 --end 07-21 --output-dir outputs/ai`"
    )
    st.stop()


# --------------------------------------------------------------------------- #
# 1. KPI row - the headline numbers
# --------------------------------------------------------------------------- #
b_kwh, a_kwh = comparison["baseline_kwh"], comparison["ai_kwh"]
savings_pct = comparison.get("savings_pct")
b_c, a_c = comparison.get("baseline_comfort"), comparison.get("ai_comfort")

k1, k2, k3, k4 = st.columns(4)
k1.metric("Energy saved", f"{savings_pct:+.1f}%" if savings_pct is not None else "n/a",
          delta=f"{comparison.get('savings_kwh', 0):+.1f} kWh", delta_color="normal")
k2.metric("HVAC energy", f"{a_kwh:,.0f} kWh", delta=f"baseline {b_kwh:,.0f} kWh", delta_color="off")
if b_c and a_c:
    k3.metric(
        "Mean PMV, occupied", f"{a_c['mean_pmv']:+.2f}",
        delta=f"{abs(b_c['mean_pmv']) - abs(a_c['mean_pmv']):+.2f} vs baseline, toward neutral",
        help="0.00 is thermally neutral. Closer to zero is better, in either direction.",
    )
else:
    k3.metric("Mean PMV, occupied", "n/a")

if not decisions.empty and "source" in decisions.columns:
    n_llm = int((decisions["source"] == "llm").sum())
    total = len(decisions)
    k4.metric("LLM decisions", f"{n_llm}/{total}",
              delta=f"{total - n_llm} fallback" if total != n_llm else "0 fallback",
              delta_color="off")
else:
    k4.metric("LLM decisions", "n/a")

st.divider()


# --------------------------------------------------------------------------- #
# 2. Energy - cumulative divergence, then when it happened
# --------------------------------------------------------------------------- #
st.subheader("Energy")

e1, e2 = st.columns([3, 2])

with e1:
    st.markdown("**Cumulative HVAC energy**")
    b_cum, a_cum = load_cumulative(str(baseline_dir)), load_cumulative(str(ai_dir))
    if b_cum is not None and a_cum is not None:
        fig = go.Figure()
        for frame, name, key in ((b_cum, "Baseline", "baseline"), (a_cum, "AI-driven", "ai")):
            fig.add_trace(go.Scatter(
                x=frame["elapsed_days"], y=frame["cumulative_kwh"],
                name=name, mode="lines",
                line=dict(color=PALETTE[key], width=2),
                hovertemplate=f"<b>{name}</b><br>day %{{x:.1f}}<br>%{{y:,.1f}} kWh<extra></extra>",
            ))
        # Direct-label the endpoints: the gap at the right edge is the whole story.
        for frame, name, key in ((b_cum, "Baseline", "baseline"), (a_cum, "AI-driven", "ai")):
            fig.add_annotation(
                x=frame["elapsed_days"].iloc[-1], y=frame["cumulative_kwh"].iloc[-1],
                text=f"  {frame['cumulative_kwh'].iloc[-1]:,.0f} kWh",
                showarrow=False, xanchor="left", font=dict(color=PALETTE[key], size=12),
            )
        layout = base_layout(360, "Cumulative kWh", "Elapsed days")
        layout["margin"]["r"] = 96
        fig.update_layout(**layout)
        st.plotly_chart(fig, use_container_width=True)
        st.caption(
            f"The vertical gap at the right edge is the saving: "
            f"**{comparison.get('savings_kwh', 0):,.1f} kWh** over the week. "
            "Both series share one axis and one unit."
        )
        with st.expander("Table view - cumulative energy"):
            tbl = pd.DataFrame({
                "elapsed_days": b_cum["elapsed_days"].round(2),
                "baseline_kwh": b_cum["cumulative_kwh"].round(2),
                "ai_kwh": a_cum["cumulative_kwh"].round(2),
            })
            tbl["gap_kwh"] = (tbl["baseline_kwh"] - tbl["ai_kwh"]).round(2)
            st.dataframe(tbl.iloc[::8], use_container_width=True, height=240)
    else:
        st.info("Cumulative energy unavailable - eplusout.csv missing.")

with e2:
    st.markdown("**Average demand by hour of day**")
    b_prof, a_prof = load_profile(str(baseline_dir)), load_profile(str(ai_dir))
    if b_prof is not None and a_prof is not None:
        fig = go.Figure()
        for frame, name, key in ((b_prof, "Baseline", "baseline"), (a_prof, "AI-driven", "ai")):
            fig.add_trace(go.Scatter(
                x=frame["hour"], y=frame["kw"], name=name, mode="lines",
                line=dict(color=PALETTE[key], width=2, shape="spline", smoothing=0.4),
                hovertemplate=f"<b>{name}</b><br>%{{x}}:00<br>%{{y:.2f}} kW<extra></extra>",
            ))
        layout = base_layout(360, "Mean HVAC demand (kW)", "Hour of day")
        layout["xaxis"].update(dtick=6, range=[0, 23])
        fig.update_layout(**layout)
        st.plotly_chart(fig, use_container_width=True)
        st.caption(
            "Where the two lines separate is where the agent is acting. "
            "Overnight separation is setback; daytime separation is setpoint trimming."
        )
        with st.expander("Table view - hourly profile"):
            prof = pd.DataFrame({
                "hour": b_prof["hour"],
                "baseline_kw": b_prof["kw"].round(3),
                "ai_kw": a_prof["kw"].round(3),
            })
            prof["delta_kw"] = (prof["ai_kw"] - prof["baseline_kw"]).round(3)
            st.dataframe(prof, use_container_width=True, height=240)
    else:
        st.info("Hourly profile unavailable.")

st.divider()


# --------------------------------------------------------------------------- #
# 3. Comfort - did occupants pay for the savings?
# --------------------------------------------------------------------------- #
st.subheader("Thermal comfort")
st.caption(
    "Both runs are scored with the same ISO 7730 implementation and the same "
    "clothing / metabolic assumptions, computed from each run's own EnergyPlus "
    "output - so any difference reflects the control strategy, not the measurement. "
    "Only occupied zone-timesteps are scored; PMV is not a constraint in an empty room."
)

if b_c and a_c:
    c1, c2, c3 = st.columns(3)
    c1.metric("Baseline, all zones in band", f"{b_c['pct_in_band']:.1f}%",
              help=f"mean PMV {b_c['mean_pmv']:+.2f} over {b_c['n_occupied_steps']} occupied zone-timesteps")
    c2.metric("AI-driven, all zones in band", f"{a_c['pct_in_band']:.1f}%",
              delta=f"{comparison['comfort_delta_pp']:+.1f} pp", delta_color="normal",
              help="Scored on the WORST zone at each timestep: one zone out of band fails the whole interval.")
    c3.metric("AI mean PMV", f"{a_c['mean_pmv']:+.2f}",
              delta=f"baseline {b_c['mean_pmv']:+.2f}", delta_color="off",
              help="0.00 is thermally neutral.")

cc1, cc2 = st.columns([3, 2])

with cc1:
    st.markdown("**Worst-zone PMV per control interval**")
    if decisions.empty or "state_digest.worst_pmv" not in decisions.columns:
        st.info("No agent decision log for this run.")
    else:
        pmv = pd.to_numeric(decisions["state_digest.worst_pmv"], errors="coerce")
        occ = (decisions["state_digest.occupied"].fillna(True).astype(bool)
               if "state_digest.occupied" in decisions.columns
               else pd.Series([True] * len(pmv)))
        ts = decisions.get("timestamp", pd.Series(range(len(pmv)))).astype(str)
        idx = list(range(len(pmv)))

        fig = go.Figure()
        fig.add_hrect(y0=POLICY.pmv_low, y1=POLICY.pmv_high,
                      fillcolor=PALETTE["band"], line_width=0, layer="below")
        # Unoccupied first so occupied markers sit on top.
        fig.add_trace(go.Scatter(
            x=[i for i, m in zip(idx, occ) if not m], y=pmv[~occ],
            name="Unoccupied (setback)", mode="markers",
            marker=dict(size=6, symbol="x", color=PALETTE["muted"], opacity=0.5),
            hovertemplate="%{customdata}<br>PMV %{y:.2f}<br>unoccupied<extra></extra>",
            customdata=[t for t, m in zip(ts, occ) if not m],
        ))
        fig.add_trace(go.Scatter(
            x=[i for i, m in zip(idx, occ) if m], y=pmv[occ],
            name="Occupied", mode="markers",
            marker=dict(size=9, color=PALETTE["ai"],
                        line=dict(width=2, color=PALETTE["surface"])),
            hovertemplate="%{customdata}<br>PMV %{y:.2f}<br>occupied<extra></extra>",
            customdata=[t for t, m in zip(ts, occ) if m],
        ))
        for y in (POLICY.pmv_low, POLICY.pmv_high):
            fig.add_hline(y=y, line_color=PALETTE["good"], line_width=1, opacity=0.55)
        fig.add_annotation(x=0, y=POLICY.pmv_high, text=" comfort band", showarrow=False,
                           xanchor="left", yanchor="bottom",
                           font=dict(color=PALETTE["good"], size=11))
        fig.update_layout(**base_layout(360, "PMV (worst zone)", "Control interval"))
        st.plotly_chart(fig, use_container_width=True)
        occ_in = pmv[occ].between(POLICY.pmv_low, POLICY.pmv_high)
        if len(occ_in):
            st.caption(
                f"At decision-interval resolution, {100 * occ_in.mean():.1f}% of occupied "
                f"intervals held every zone in band ({int(occ_in.sum())}/{len(occ_in)}). "
                "The KPI above is the stricter sub-hourly figure."
            )

with cc2:
    st.markdown("**Per-zone comfort, occupied hours**")
    bz = load_zone_comfort(str(baseline_dir), int(month))
    az = load_zone_comfort(str(ai_dir), int(month))
    if az is not None and bz is not None:
        merged = bz.merge(az, on="zone", suffixes=("_base", "_ai"))
        fig = go.Figure()
        fig.add_trace(go.Bar(
            y=merged["zone"], x=merged["pct_in_band_base"], name="Baseline",
            orientation="h", marker=dict(color=PALETTE["baseline"], line=dict(width=0)),
            hovertemplate="<b>Baseline</b> %{y}<br>%{x:.1f}% in band<extra></extra>",
        ))
        fig.add_trace(go.Bar(
            y=merged["zone"], x=merged["pct_in_band_ai"], name="AI-driven",
            orientation="h", marker=dict(color=PALETTE["ai"], line=dict(width=0)),
            hovertemplate="<b>AI-driven</b> %{y}<br>%{x:.1f}% in band<extra></extra>",
        ))
        layout = base_layout(360, "", "% of occupied timesteps in band")
        layout["barmode"] = "group"
        layout["bargap"] = 0.28
        layout["bargroupgap"] = 0.12
        layout["xaxis"].update(range=[0, 100], ticksuffix="%")
        fig.update_layout(**layout)
        st.plotly_chart(fig, use_container_width=True)
        st.caption(
            f"Each zone individually sits at "
            f"{merged['pct_in_band_ai'].min():.0f}-{merged['pct_in_band_ai'].max():.0f}% "
            "under AI control. The headline figure is lower because it scores the "
            "worst zone at every timestep - one setpoint pair cannot satisfy five "
            "differently-loaded zones."
        )
        with st.expander("Table view - per-zone comfort"):
            st.dataframe(
                merged[["zone", "pct_in_band_base", "pct_in_band_ai",
                        "mean_pmv_base", "mean_pmv_ai", "min_pmv_ai"]].round(2),
                use_container_width=True,
            )
    else:
        st.info("Per-zone comfort unavailable.")

st.divider()


# --------------------------------------------------------------------------- #
# 4. Agent behaviour - what it did and why
# --------------------------------------------------------------------------- #
st.subheader("Agent behaviour")

if decisions.empty:
    st.info("No decisions logged for this run.")
else:
    a1, a2 = st.columns([3, 2])

    with a1:
        st.markdown("**Injected setpoints over the run**")
        fig = go.Figure()
        if "state_digest.occupied" in decisions.columns:
            occ = decisions["state_digest.occupied"].fillna(True).astype(bool)
            # Shade occupied stretches so setback vs occupied reads at a glance.
            start = None
            for i, flag in enumerate(list(occ) + [False]):
                if flag and start is None:
                    start = i
                elif not flag and start is not None:
                    fig.add_vrect(x0=start, x1=i - 1, fillcolor=PALETTE["muted"],
                                  opacity=0.10, line_width=0, layer="below")
                    start = None
        # This chart compares two setpoints, not two runs, so it does NOT reuse
        # the baseline/AI slots' meaning. Colour is semantic here: the cool hue
        # is the cooling setpoint, the warm hue is the heating setpoint.
        for col, name, key in (
            ("command.cooling_sp", "Cooling setpoint", "cool_sp"),
            ("command.heating_sp", "Heating setpoint", "heat_sp"),
        ):
            if col in decisions.columns:
                fig.add_trace(go.Scatter(
                    x=list(range(len(decisions))), y=pd.to_numeric(decisions[col], errors="coerce"),
                    name=name, mode="lines", line=dict(color=PALETTE[key], width=2),
                    hovertemplate=f"<b>{name}</b><br>interval %{{x}}<br>%{{y:.1f}} degC<extra></extra>",
                ))
        fig.update_layout(**base_layout(340, "Setpoint (degC)", "Control interval"))
        st.plotly_chart(fig, use_container_width=True)
        st.caption(
            "Shaded bands are occupied periods. The gap between the two lines is the "
            "deadband; seasonal changeover parks the idle mode's setpoint at its "
            "policy limit so heating and cooling can never run against each other."
        )

    with a2:
        st.markdown("**Decision latency**")
        if "latency_s" in decisions.columns:
            lat = pd.to_numeric(decisions["latency_s"], errors="coerce").dropna()
            fig = go.Figure()
            fig.add_trace(go.Histogram(
                x=lat, nbinsx=24, marker=dict(color=PALETTE["ai"], line=dict(width=0)),
                hovertemplate="%{x:.1f}s<br>%{y} decisions<extra></extra>", name="Decisions",
            ))
            layout = base_layout(340, "Decisions", "Agent turn latency (s)")
            layout["showlegend"] = False
            fig.update_layout(**layout)
            st.plotly_chart(fig, use_container_width=True)
            st.caption(
                f"Median {lat.median():.1f}s, max {lat.max():.1f}s against a "
                "hard per-turn budget. Exceeding it falls back to the last accepted "
                "command rather than stalling the simulation."
            )

    st.markdown("**Decision log**")
    cols = [c for c in [
        "timestamp", "source", "command.cooling_sp", "command.heating_sp",
        "command.reason", "latency_s", "tool_calls", "error",
    ] if c in decisions.columns]
    st.dataframe(
        decisions[cols].rename(columns={
            "command.cooling_sp": "cooling_sp_c",
            "command.heating_sp": "heating_sp_c",
            "command.reason": "agent reasoning",
        }),
        use_container_width=True, height=300,
    )

st.divider()
st.caption(
    "Eco-Loop Building Agents - EnergyPlus (pyenergyplus) + FastMCP + a local "
    "OpenAI-compatible LLM (Ollama / vLLM), closing the loop between building "
    "physics and an autonomous supervisory controller."
)
