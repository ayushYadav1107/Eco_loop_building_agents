"""
Module 4 - the Quantitative Savings Dashboard.

    streamlit run dashboard.py

Compares a baseline EnergyPlus run against the AI-supervised run: how much was
saved, where it came from, whether occupants paid for it, and what the agent did.

Theming note. `.streamlit/config.toml` pins `theme.base = "light"`. Without a
pin, `theme.base` is None and Streamlit follows the viewer's OS preference,
which desynchronises Streamlit's own chrome from this file's palette and renders
text invisibly (white widget text on a light surface). With a known baseline the
in-app toggle can restyle everything deterministically. Tables are emitted as
styled HTML rather than `st.dataframe` for the same reason: the dataframe widget
paints to a canvas and ignores CSS, so it would not follow the toggle.

Chart conventions: one y-axis per plot (never dual-axis); blue is always the
baseline and orange always the agent; hairline grids; a legend for two or more
series; a table view under every chart. Every text token clears 4.5:1 on its
own surface in both modes.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Optional

import numpy as np
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
# Theme
# --------------------------------------------------------------------------- #
def _effective_dark() -> bool:
    """What Streamlit is *actually* rendering, not what was configured."""
    try:
        theme = st.context.theme
        kind = theme.get("type") if hasattr(theme, "get") else getattr(theme, "type", None)
        if kind:
            return str(kind).lower() == "dark"
    except Exception:
        pass
    return str(st.get_option("theme.base") or "").lower() == "dark"


if "dark_mode" not in st.session_state:
    st.session_state.dark_mode = _effective_dark()

st.sidebar.title("Eco-Loop")
st.sidebar.caption("Autonomous, LLM-in-the-loop HVAC supervision")
st.sidebar.toggle("Dark theme", key="dark_mode")
DARK: bool = st.session_state.dark_mode

# NOTE on how the toggle works.
# Streamlit stores its active theme in localStorage (`stActiveTheme-/-v2`) and
# that value overrides `config.toml`; a stored "System" is why this app could
# render dark while the config said light, leaving white widget text on a light
# background. Driving that key from the page and reloading was tried and
# rejected - the reload re-runs the injecting component, which reloads again,
# and the app never settles.
#
# So the toggle restyles the page directly instead, and the CSS below is
# deliberately exhaustive: page, header, sidebar, headings, body copy, captions,
# metrics, expanders, inputs, alerts and tables. Tables are hand-rendered HTML
# rather than `st.dataframe` for exactly this reason - the dataframe widget
# paints to a canvas and would ignore every rule here.
#
# `_effective_dark()` seeds the toggle from what Streamlit is *actually*
# rendering, so the two agree on first load and the toggle only ever moves the
# page away from that starting point deliberately.

# Each mode's values are selected for its own surface, not flipped from the other.
PALETTE: Dict[str, str] = {
    "baseline":  "#3987e5" if DARK else "#2a78d6",
    "ai":        "#d95926" if DARK else "#eb6834",
    "page":      "#0d0d0d" if DARK else "#f9f9f7",
    "surface":   "#1a1a19" if DARK else "#ffffff",
    "text":      "#f5f5f3" if DARK else "#111111",
    "secondary": "#c9c8be" if DARK else "#4a4945",
    "muted":     "#a3a199" if DARK else "#6a6864",
    "grid":      "#2c2c2a" if DARK else "#e4e3dc",
    "axis":      "#4a4a46" if DARK else "#bdbcb4",
    "good":      "#12b312" if DARK else "#0a840a",
    "critical":  "#e05a5a" if DARK else "#c22f2f",
    "band":      "rgba(18,179,18,0.14)" if DARK else "rgba(10,132,10,0.10)",
}
PALETTE["cool_sp"] = PALETTE["baseline"]   # semantic: cool hue = cooling setpoint
PALETTE["heat_sp"] = PALETTE["ai"]         # semantic: warm hue = heating setpoint
# The hero figure is text, not a mark, and it sits on a tinted panel of its own
# hue - which eats contrast. The series orange measures only 2.67:1 there on the
# light surface, under the 3:1 minimum for large text; a darker step of the same
# hue restores it to 4.55:1 without changing the colour identity. Dark mode
# already clears the bar (4.49:1), so it keeps the series step.
PALETTE["hero_fig"] = PALETTE["ai"] if DARK else "#b8431c"

FONT = 'system-ui, -apple-system, "Segoe UI", sans-serif'


def rgba(hex_color: str, alpha: float) -> str:
    h = hex_color.lstrip("#")
    return f"rgba({int(h[0:2],16)},{int(h[2:4],16)},{int(h[4:6],16)},{alpha})"


def base_layout(height: int = 320, y_title: str = "", x_title: str = "") -> dict:
    axis = dict(
        showgrid=True, gridcolor=PALETTE["grid"], gridwidth=1, zeroline=False,
        linecolor=PALETTE["axis"], linewidth=1,
        tickfont=dict(color=PALETTE["muted"], size=13),
        title_font=dict(color=PALETTE["secondary"], size=14),
    )
    return dict(
        height=height, margin=dict(t=46, l=8, r=8, b=8),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family=FONT, color=PALETTE["secondary"], size=14),
        hoverlabel=dict(font_size=14, font_family=FONT,
                        bgcolor=PALETTE["surface"], font_color=PALETTE["text"],
                        bordercolor=PALETTE["axis"]),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0,
                    font=dict(color=PALETTE["text"], size=14)),
        xaxis={**axis, "title": x_title},
        yaxis={**axis, "title": y_title},
    )


# Comprehensive override: every Streamlit surface and text element, so the
# toggle restyles the whole page rather than half of it.
st.markdown(
    f"""
    <style>
      html, body, .stApp {{ background: {PALETTE['page']} !important; }}
      header[data-testid="stHeader"] {{ background: {PALETTE['page']} !important; }}
      [data-testid="stToolbar"] * {{ color: {PALETTE['secondary']} !important; }}
      .block-container {{ padding-top: 2rem; max-width: 1560px; }}

      .stApp, .stApp p, .stApp li, .stApp span, .stApp label, .stApp div {{
        color: {PALETTE['text']};
      }}
      .stApp p, .stApp li {{ font-size: 1rem; line-height: 1.6; }}
      h1 {{ font-size: 2.1rem !important; letter-spacing: -0.02em; }}
      h2 {{ font-size: 1.5rem !important; margin-top: .4rem !important; }}
      h1, h2, h3, h4 {{ color: {PALETTE['text']} !important; }}

      [data-testid="stCaptionContainer"], [data-testid="stCaptionContainer"] p {{
        color: {PALETTE['secondary']} !important;
        font-size: 0.95rem !important; line-height: 1.55;
      }}

      section[data-testid="stSidebar"] {{
        background: {PALETTE['surface']} !important;
        border-right: 1px solid {rgba(PALETTE['text'], 0.10)};
      }}
      section[data-testid="stSidebar"] * {{ color: {PALETTE['text']} !important; }}
      section[data-testid="stSidebar"] p {{ font-size: 0.95rem; }}

      div[data-testid="stMetric"] {{
        border: 1px solid {rgba(PALETTE['text'], 0.12)};
        border-radius: 12px; padding: .9rem 1.1rem;
        background: {PALETTE['surface']};
      }}
      div[data-testid="stMetricLabel"] p {{
        color: {PALETTE['secondary']} !important; font-size: .95rem !important;
      }}
      div[data-testid="stMetricValue"] {{
        color: {PALETTE['text']} !important; font-size: 2rem !important;
      }}
      div[data-testid="stMetricDelta"] {{ font-size: .9rem !important; }}

      [data-testid="stExpander"] details {{
        border: 1px solid {rgba(PALETTE['text'], 0.12)} !important;
        border-radius: 10px; background: {PALETTE['surface']};
      }}
      [data-testid="stExpander"] summary, [data-testid="stExpander"] summary * {{
        color: {PALETTE['text']} !important; font-size: .95rem;
      }}

      input, select, textarea {{
        background: {PALETTE['surface']} !important; color: {PALETTE['text']} !important;
      }}
      [data-testid="stAlert"] {{
        background: {rgba(PALETTE['ai'], 0.10)} !important;
        border: 1px solid {rgba(PALETTE['ai'], 0.35)}; border-radius: 10px;
      }}
      [data-testid="stAlert"] * {{ color: {PALETTE['text']} !important; }}
      hr {{ border-color: {rgba(PALETTE['text'], 0.12)} !important; }}

      .eco-hero {{
        display:flex; align-items:center; gap:1.5rem; flex-wrap:wrap;
        padding: 1.3rem 1.6rem; border-radius: 14px; margin: .2rem 0 1.2rem 0;
        border: 1px solid {rgba(PALETTE['ai'], 0.40)};
        background: {rgba(PALETTE['ai'], 0.12)};
      }}
      .eco-hero .fig {{
        font-size: 62px; font-weight: 660; line-height: 1;
        letter-spacing: -0.025em; color: {PALETTE['hero_fig']};
      }}
      .eco-hero .cap {{
        font-size: 1.05rem; line-height: 1.55; color: {PALETTE['text']}; max-width: 46rem;
      }}
      .eco-sec {{
        font-size: 1rem; line-height: 1.6; color: {PALETTE['secondary']};
        margin: -.2rem 0 1rem 0; max-width: 70rem;
      }}
      .eco-ct {{
        font-weight: 600; font-size: 1.08rem; color: {PALETTE['text']};
        margin: .3rem 0 .35rem 0;
      }}

      /* Tables are hand-rendered so they follow the toggle; the built-in
         dataframe paints to a canvas and would ignore all of the above. */
      .eco-tbl-wrap {{ max-height: 330px; overflow: auto; border-radius: 8px;
        border: 1px solid {rgba(PALETTE['text'], 0.12)}; }}
      table.eco-tbl {{ width: 100%; border-collapse: collapse; font-size: .9rem;
        font-variant-numeric: tabular-nums; background: {PALETTE['surface']}; }}
      table.eco-tbl th {{
        position: sticky; top: 0; text-align: left; font-weight: 600;
        background: {PALETTE['surface']}; color: {PALETTE['text']};
        padding: .5rem .7rem; border-bottom: 1px solid {rgba(PALETTE['text'], 0.18)};
        white-space: nowrap;
      }}
      table.eco-tbl td {{
        padding: .42rem .7rem; color: {PALETTE['secondary']};
        border-bottom: 1px solid {rgba(PALETTE['text'], 0.07)};
      }}
      table.eco-tbl tr:hover td {{ background: {rgba(PALETTE['ai'], 0.07)}; }}
      table.eco-tbl td.num {{ text-align: right; }}
    </style>
    """,
    unsafe_allow_html=True,
)


def html_table(df: pd.DataFrame, height: int = 330) -> None:
    """Render a DataFrame as themed HTML (follows the light/dark toggle)."""
    head = "".join(f"<th>{c}</th>" for c in df.columns)
    rows = []
    for _, r in df.iterrows():
        cells = "".join(
            f'<td class="{"num" if isinstance(v, (int, float, np.integer, np.floating)) else ""}">'
            f'{"" if pd.isna(v) else v}</td>'
            for v in r
        )
        rows.append(f"<tr>{cells}</tr>")
    st.markdown(
        f'<div class="eco-tbl-wrap" style="max-height:{height}px">'
        f'<table class="eco-tbl"><thead><tr>{head}</tr></thead>'
        f'<tbody>{"".join(rows)}</tbody></table></div>',
        unsafe_allow_html=True,
    )


# --------------------------------------------------------------------------- #
# Filter row
# --------------------------------------------------------------------------- #
DATASETS = {
    "Summer week (15-21 Jul)": (BASELINE_DIR, AI_DIR, 7),
    "Winter week (15-21 Jan)": (OUTPUT_DIR / "winter_baseline", OUTPUT_DIR / "winter_ai", 1),
}
available = {k: v for k, v in DATASETS.items() if (v[1] / "eplusout.csv").exists()} or DATASETS
choice = st.sidebar.radio("Run period", list(available.keys()) + ["Custom paths"], index=0)

if choice == "Custom paths":
    baseline_dir = Path(st.sidebar.text_input("Baseline dir", str(BASELINE_DIR)))
    ai_dir = Path(st.sidebar.text_input("AI dir", str(AI_DIR)))
    month = int(st.sidebar.number_input("Month (clothing assumption)", 1, 12, 7))
else:
    baseline_dir, ai_dir, month = available[choice]

if st.sidebar.button("Reload data", use_container_width=True):
    st.cache_data.clear()

st.sidebar.divider()
st.sidebar.markdown(
    f"**Control policy**\n\n"
    f"Comfort band  PMV {POLICY.pmv_low} to {POLICY.pmv_high}\n\n"
    f"Cooling  {POLICY.cooling_sp_min}–{POLICY.cooling_sp_max} °C\n\n"
    f"Heating  {POLICY.heating_sp_min}–{POLICY.heating_sp_max} °C\n\n"
    f"Deadband ≥ {POLICY.min_deadband} °C · Slew ≤ {POLICY.max_step_per_hour} °C/h"
)


# --------------------------------------------------------------------------- #
# Loaders
# --------------------------------------------------------------------------- #
@st.cache_data(show_spinner=False)
def load_decisions(p: str) -> pd.DataFrame:
    path = Path(p) / "agent_decisions.jsonl"
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


@st.cache_data(show_spinner=False)
def load_pmv_series(p: str, m: int) -> Optional[list]:
    res = eo.occupied_comfort(Path(p), m)
    return res["series"] if res else None


comparison = load_comparison(str(baseline_dir), str(ai_dir), int(month))
decisions = load_decisions(str(ai_dir))

st.title("Quantitative Savings Dashboard")
st.markdown(
    f"<div class='eco-sec'>{choice} · EnergyPlus closed loop supervised by a local LLM over "
    "MCP. Baseline runs the model's own thermostat schedules with the agent detached; "
    "everything else is identical.</div>", unsafe_allow_html=True)

if comparison.get("baseline_kwh") is None or comparison.get("ai_kwh") is None:
    st.warning("No results for this selection. Run `python main.py run-baseline` and "
               "`python main.py run-ai` first.")
    st.stop()

b_kwh, a_kwh = comparison["baseline_kwh"], comparison["ai_kwh"]
savings_pct = comparison.get("savings_pct") or 0.0
savings_kwh = comparison.get("savings_kwh") or 0.0
b_c, a_c = comparison.get("baseline_comfort"), comparison.get("ai_comfort")


# --------------------------------------------------------------------------- #
# Hero + KPIs
# --------------------------------------------------------------------------- #
comfort_clause = ""
if b_c and a_c:
    comfort_clause = f" Mean occupied PMV {b_c['mean_pmv']:+.2f} → <b>{a_c['mean_pmv']:+.2f}</b>."
st.markdown(
    f"""<div class="eco-hero">
      <div class="fig">{savings_pct:+.1f}%</div>
      <div class="cap"><b>{savings_kwh:,.1f} kWh</b> of HVAC electricity avoided this week
      ({b_kwh:,.0f} → {a_kwh:,.0f} kWh).{comfort_clause}</div>
    </div>""", unsafe_allow_html=True)

k1, k2, k3, k4 = st.columns(4)
k1.metric("HVAC energy", f"{a_kwh:,.0f} kWh", delta=f"baseline {b_kwh:,.0f}", delta_color="off")
if b_c and a_c:
    k2.metric("Mean PMV, occupied", f"{a_c['mean_pmv']:+.2f}",
              delta=f"{abs(b_c['mean_pmv']) - abs(a_c['mean_pmv']):+.2f} toward neutral",
              help="0.00 is thermally neutral.")
    k3.metric("All zones in band", f"{a_c['pct_in_band']:.1f}%",
              delta=f"{comparison['comfort_delta_pp']:+.1f} pp",
              help="Strictest reading: scores the worst zone each timestep, so one "
                   "uncomfortable zone fails the whole interval.")
if not decisions.empty and "source" in decisions.columns:
    n_llm = int((decisions["source"] == "llm").sum())
    k4.metric("LLM decisions", f"{n_llm}/{len(decisions)}",
              delta=f"{len(decisions) - n_llm} fallback", delta_color="off")

st.divider()


# --------------------------------------------------------------------------- #
# Energy
# --------------------------------------------------------------------------- #
st.subheader("Where the saving comes from")
e1, e2 = st.columns([3, 2])

with e1:
    st.markdown("<div class='eco-ct'>Cumulative HVAC energy</div>", unsafe_allow_html=True)
    b_cum, a_cum = load_cumulative(str(baseline_dir)), load_cumulative(str(ai_dir))
    if b_cum is not None and a_cum is not None:
        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=list(b_cum["elapsed_days"]) + list(a_cum["elapsed_days"])[::-1],
            y=list(b_cum["cumulative_kwh"]) + list(a_cum["cumulative_kwh"])[::-1],
            fill="toself", fillcolor=rgba(PALETTE["good"], 0.16),
            line=dict(width=0), hoverinfo="skip", showlegend=False))
        for frame, name, key in ((b_cum, "Baseline", "baseline"), (a_cum, "AI-driven", "ai")):
            fig.add_trace(go.Scatter(
                x=frame["elapsed_days"], y=frame["cumulative_kwh"], name=name, mode="lines",
                line=dict(color=PALETTE[key], width=2.5),
                hovertemplate=f"<b>{name}</b><br>day %{{x:.1f}} · %{{y:,.1f}} kWh<extra></extra>"))
            fig.add_annotation(x=frame["elapsed_days"].iloc[-1], y=frame["cumulative_kwh"].iloc[-1],
                               text=f"  {frame['cumulative_kwh'].iloc[-1]:,.0f}", showarrow=False,
                               xanchor="left", font=dict(color=PALETTE[key], size=14))
        layout = base_layout(340, "Cumulative kWh", "Elapsed days")
        layout["margin"]["r"] = 74
        fig.update_layout(**layout)
        st.plotly_chart(fig, use_container_width=True)
        st.caption(f"Shaded wedge = the saving as it accrues, {savings_kwh:,.1f} kWh by week's end.")
        with st.expander("Table view"):
            t = pd.DataFrame({"day": b_cum["elapsed_days"].round(2),
                              "baseline kWh": b_cum["cumulative_kwh"].round(1),
                              "AI kWh": a_cum["cumulative_kwh"].round(1)})
            t["saved kWh"] = (t["baseline kWh"] - t["AI kWh"]).round(1)
            html_table(t.iloc[::8], 300)

with e2:
    st.markdown("<div class='eco-ct'>Average day</div>", unsafe_allow_html=True)
    b_prof, a_prof = load_profile(str(baseline_dir)), load_profile(str(ai_dir))
    if b_prof is not None and a_prof is not None:
        fig = go.Figure()
        for frame, name, key in ((b_prof, "Baseline", "baseline"), (a_prof, "AI-driven", "ai")):
            fig.add_trace(go.Scatter(
                x=frame["hour"], y=frame["kw"], name=name, mode="lines",
                line=dict(color=PALETTE[key], width=2.5, shape="spline", smoothing=0.4),
                fill="tozeroy", fillcolor=rgba(PALETTE[key], 0.12),
                hovertemplate=f"<b>{name}</b><br>%{{x}}:00 · %{{y:.2f}} kW<extra></extra>"))
        layout = base_layout(340, "Mean HVAC demand (kW)", "Hour of day")
        layout["xaxis"].update(dtick=6, range=[0, 23])
        fig.update_layout(**layout)
        st.plotly_chart(fig, use_container_width=True)
        st.caption("Where the curves separate is where the agent is acting.")

if b_prof is not None and a_prof is not None:
    st.markdown("<div class='eco-ct'>Energy saved by hour</div>", unsafe_allow_html=True)
    delta = pd.DataFrame({"hour": b_prof["hour"], "saved_kw": b_prof["kw"] - a_prof["kw"]})
    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=delta["hour"], y=delta["saved_kw"], width=0.62, showlegend=False,
        marker=dict(color=[PALETTE["good"] if v >= 0 else PALETTE["critical"]
                           for v in delta["saved_kw"]], line=dict(width=0)),
        customdata=[("saves " if v >= 0 else "uses ") + f"{abs(v):.2f} kW" for v in delta["saved_kw"]],
        hovertemplate="%{x}:00 · %{customdata}<extra></extra>"))
    fig.add_hline(y=0, line_color=PALETTE["axis"], line_width=1)
    layout = base_layout(230, "kW vs baseline", "Hour of day")
    layout["xaxis"].update(dtick=2, range=[-0.6, 23.6])
    fig.update_layout(**layout)
    st.plotly_chart(fig, use_container_width=True)
    pos = delta[delta["saved_kw"] >= 0]["saved_kw"].sum()
    neg = -delta[delta["saved_kw"] < 0]["saved_kw"].sum()
    st.caption(f"Above the line = less power than baseline ({pos:.1f} kW total). "
               f"Below = more ({neg:.1f} kW), mostly setback recovery.")
    with st.expander("Table view"):
        html_table(pd.DataFrame({"hour": b_prof["hour"],
                                 "baseline kW": b_prof["kw"].round(3),
                                 "AI kW": a_prof["kw"].round(3),
                                 "saved kW": delta["saved_kw"].round(3)}), 300)

st.divider()


# --------------------------------------------------------------------------- #
# Comfort
# --------------------------------------------------------------------------- #
st.subheader("Did occupants pay for it?")
st.markdown("<div class='eco-sec'>Both runs scored with the same ISO 7730 model and "
            "assumptions, from each run's own EnergyPlus output — so differences reflect "
            "control, not measurement. Occupied zone-timesteps only.</div>",
            unsafe_allow_html=True)

c1, c2 = st.columns(2)

with c1:
    st.markdown("<div class='eco-ct'>Distribution of occupied PMV</div>", unsafe_allow_html=True)
    b_series = load_pmv_series(str(baseline_dir), int(month))
    a_series = load_pmv_series(str(ai_dir), int(month))
    if b_series and a_series:
        edges = np.linspace(-2.0, 1.5, 43)
        centers = (edges[:-1] + edges[1:]) / 2
        fig = go.Figure()
        fig.add_vrect(x0=POLICY.pmv_low, x1=POLICY.pmv_high, fillcolor=PALETTE["band"],
                      line_width=0, layer="below")
        for series, name, key in ((b_series, "Baseline", "baseline"), (a_series, "AI-driven", "ai")):
            dens, _ = np.histogram(series, bins=edges, density=True)
            fig.add_trace(go.Scatter(
                x=centers, y=dens, name=name, mode="lines",
                line=dict(color=PALETTE[key], width=2.5, shape="spline", smoothing=0.7),
                fill="tozeroy", fillcolor=rgba(PALETTE[key], 0.14),
                hovertemplate=f"<b>{name}</b> · PMV %{{x:.2f}}<extra></extra>"))
        fig.add_vline(x=0, line_color=PALETTE["muted"], line_width=1)
        fig.add_annotation(x=0, y=1, yref="paper", text=" neutral", showarrow=False,
                           xanchor="left", yanchor="top",
                           font=dict(color=PALETTE["secondary"], size=13))
        layout = base_layout(330, "Share of occupied time", "PMV")
        layout["yaxis"].update(showticklabels=False)
        fig.update_layout(**layout)
        st.plotly_chart(fig, use_container_width=True)

        def _lean(m: float) -> str:
            return "leans cold" if m < -0.15 else ("leans warm" if m > 0.15 else "sits on neutral")

        b_mean, a_mean = float(np.mean(b_series)), float(np.mean(a_series))
        closer = "closer to" if abs(a_mean) < abs(b_mean) else "further from"
        st.caption(f"Baseline {_lean(b_mean)} ({b_mean:+.2f}); agent {_lean(a_mean)} "
                   f"({a_mean:+.2f}) — {closer} neutral. Shaded band is the target.")

with c2:
    st.markdown("<div class='eco-ct'>Per-zone comfort</div>", unsafe_allow_html=True)
    bz, az = load_zone_comfort(str(baseline_dir), int(month)), load_zone_comfort(str(ai_dir), int(month))
    if az is not None and bz is not None:
        merged = bz.merge(az, on="zone", suffixes=("_base", "_ai"))
        fig = go.Figure()
        for col, name, key in (("pct_in_band_base", "Baseline", "baseline"),
                               ("pct_in_band_ai", "AI-driven", "ai")):
            fig.add_trace(go.Bar(
                y=merged["zone"], x=merged[col], name=name, orientation="h",
                marker=dict(color=PALETTE[key], line=dict(width=0)),
                text=[f"{v:.0f}%" for v in merged[col]], textposition="outside",
                textfont=dict(color=PALETTE["text"], size=13),
                hovertemplate=f"<b>{name}</b> %{{y}} · %{{x:.1f}}%<extra></extra>"))
        layout = base_layout(330, "", "% of occupied timesteps in band")
        layout.update(barmode="group", bargap=0.3, bargroupgap=0.12)
        layout["xaxis"].update(range=[0, 120], ticksuffix="%", dtick=25)
        fig.update_layout(**layout)
        st.plotly_chart(fig, use_container_width=True)
        st.caption(f"Each zone holds {merged['pct_in_band_ai'].min():.0f}–"
                   f"{merged['pct_in_band_ai'].max():.0f}% under AI control. The headline "
                   f"{a_c['pct_in_band']:.0f}% scores the *worst* zone each timestep — one "
                   "setpoint pair cannot satisfy five differently-loaded zones.")
        with st.expander("Table view"):
            t = merged[["zone", "pct_in_band_base", "pct_in_band_ai",
                        "mean_pmv_base", "mean_pmv_ai", "min_pmv_ai"]].round(2)
            t.columns = ["zone", "base %", "AI %", "base PMV", "AI PMV", "AI min PMV"]
            html_table(t, 260)

st.markdown("<div class='eco-ct'>Worst-zone PMV through the run</div>", unsafe_allow_html=True)
if decisions.empty or "state_digest.worst_pmv" not in decisions.columns:
    st.info("No agent decision log for this run.")
else:
    pmv = pd.to_numeric(decisions["state_digest.worst_pmv"], errors="coerce")
    occ = (decisions["state_digest.occupied"].fillna(True).astype(bool)
           if "state_digest.occupied" in decisions.columns else pd.Series([True] * len(pmv)))
    ts = decisions.get("timestamp", pd.Series(range(len(pmv)))).astype(str)
    idx = list(range(len(pmv)))
    fig = go.Figure()
    fig.add_hrect(y0=POLICY.pmv_low, y1=POLICY.pmv_high, fillcolor=PALETTE["band"],
                  line_width=0, layer="below")
    fig.add_trace(go.Scatter(
        x=[i for i, m in zip(idx, occ) if not m], y=pmv[~occ], name="Unoccupied (setback)",
        mode="markers", marker=dict(size=7, symbol="x", color=PALETTE["muted"], opacity=0.6),
        customdata=[t for t, m in zip(ts, occ) if not m],
        hovertemplate="%{customdata} · PMV %{y:.2f}<extra></extra>"))
    fig.add_trace(go.Scatter(
        x=[i for i, m in zip(idx, occ) if m], y=pmv[occ], name="Occupied",
        mode="markers", marker=dict(size=10, color=PALETTE["ai"],
                                    line=dict(width=2, color=PALETTE["page"])),
        customdata=[t for t, m in zip(ts, occ) if m],
        hovertemplate="%{customdata} · PMV %{y:.2f}<extra></extra>"))
    for y in (POLICY.pmv_low, POLICY.pmv_high):
        fig.add_hline(y=y, line_color=PALETTE["good"], line_width=1, opacity=0.65)
    fig.add_annotation(x=0, y=POLICY.pmv_high, text=" comfort band", showarrow=False,
                       xanchor="left", yanchor="bottom", font=dict(color=PALETTE["good"], size=13))
    fig.update_layout(**base_layout(300, "PMV (worst zone)", "Control interval"))
    st.plotly_chart(fig, use_container_width=True)
    occ_in = pmv[occ].between(POLICY.pmv_low, POLICY.pmv_high)
    if len(occ_in):
        st.caption(f"{100 * occ_in.mean():.1f}% of occupied intervals held every zone in band "
                   f"({int(occ_in.sum())}/{len(occ_in)}). Grey crosses are unoccupied setback.")

st.divider()


# --------------------------------------------------------------------------- #
# Agent behaviour
# --------------------------------------------------------------------------- #
st.subheader("What the agent did")
st.markdown("<div class='eco-sec'>Setpoints are injected into the live solver every timestep "
            "via the EnergyPlus actuator API — no IDF is rewritten, no restart.</div>",
            unsafe_allow_html=True)

if decisions.empty:
    st.info("No decisions logged for this run.")
else:
    a1, a2 = st.columns([3, 2])

    with a1:
        st.markdown("<div class='eco-ct'>Injected setpoints</div>", unsafe_allow_html=True)
        fig = go.Figure()
        if "state_digest.occupied" in decisions.columns:
            flags = decisions["state_digest.occupied"].fillna(True).astype(bool)
            start = None
            for i, flag in enumerate(list(flags) + [False]):
                if flag and start is None:
                    start = i
                elif not flag and start is not None:
                    fig.add_vrect(x0=start, x1=i - 1, fillcolor=PALETTE["muted"],
                                  opacity=0.14, line_width=0, layer="below")
                    start = None
        for col, name, key in (("command.cooling_sp", "Cooling setpoint", "cool_sp"),
                               ("command.heating_sp", "Heating setpoint", "heat_sp")):
            if col in decisions.columns:
                fig.add_trace(go.Scatter(
                    x=list(range(len(decisions))),
                    y=pd.to_numeric(decisions[col], errors="coerce"),
                    name=name, mode="lines", line=dict(color=PALETTE[key], width=2.5),
                    hovertemplate=f"<b>{name}</b> · interval %{{x}} · %{{y:.1f}} °C<extra></extra>"))
        fig.update_layout(**base_layout(315, "Setpoint (°C)", "Control interval"))
        st.plotly_chart(fig, use_container_width=True)
        st.caption("Shaded = occupied. The vertical gap is the deadband; seasonal changeover "
                   "parks the idle mode's setpoint so heating and cooling cannot fight.")

    with a2:
        st.markdown("<div class='eco-ct'>Decision latency</div>", unsafe_allow_html=True)
        if "latency_s" in decisions.columns:
            lat = pd.to_numeric(decisions["latency_s"], errors="coerce").dropna()
            fig = go.Figure()
            fig.add_trace(go.Histogram(x=lat, nbinsx=24, name="Decisions",
                                       marker=dict(color=PALETTE["ai"], line=dict(width=0)),
                                       hovertemplate="%{x:.1f}s · %{y} decisions<extra></extra>"))
            fig.add_vline(x=float(lat.median()), line_color=PALETTE["secondary"], line_width=1)
            fig.add_annotation(x=float(lat.median()), y=1, yref="paper",
                               text=f" median {lat.median():.1f}s", showarrow=False,
                               xanchor="left", yanchor="top",
                               font=dict(color=PALETTE["text"], size=13))
            layout = base_layout(315, "Decisions", "Turn latency (s)")
            layout["showlegend"] = False
            fig.update_layout(**layout)
            st.plotly_chart(fig, use_container_width=True)
            st.caption(f"Median {lat.median():.1f}s, max {lat.max():.1f}s. Over budget falls back "
                       "to the last accepted command rather than stalling the simulation.")

    st.markdown("<div class='eco-ct'>Decision log</div>", unsafe_allow_html=True)
    log = pd.DataFrame({
        "time": decisions.get("timestamp"),
        "source": decisions.get("source"),
        "cool °C": pd.to_numeric(decisions.get("command.cooling_sp"), errors="coerce").round(1),
        "heat °C": pd.to_numeric(decisions.get("command.heating_sp"), errors="coerce").round(1),
        "latency s": pd.to_numeric(decisions.get("latency_s"), errors="coerce").round(1),
        "agent reasoning": decisions.get("command.reason", pd.Series([""] * len(decisions))).astype(str).str.slice(0, 130),
    })
    html_table(log, 340)

st.divider()
st.caption("Eco-Loop Building Agents — EnergyPlus (pyenergyplus) + FastMCP + a local "
           "OpenAI-compatible LLM (Ollama / vLLM).")
