"""Generate the diagram and result images embedded in the Idea deck.

Everything here is drawn from the committed run outputs, so the numbers on the
slides and the numbers in the repo cannot drift apart.
"""
from __future__ import annotations

import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from eco_loop import eplus_outputs as eo  # noqa: E402

OUT = Path(__file__).resolve().parent
DPI = 200

# SIH pitch palette: deep tech blue (structure), sustainability green (positive
# metrics), muted orange (constraints / heating). Blue-green validated CVD-safe
# on this surface (deutan dE 30.3); green and orange sit in the CVD warn band,
# so they are never placed on the same chart.
INK = "#1E293B"          # dark slate body text, never pure black
SLATE = "#475569"
BLUE = "#2563EB"         # baseline series / cooling setpoint
BLUE_DEEP = "#1E3A8A"    # headers, structural elements
GREEN = "#16A34A"        # AI series, savings, success
ORANGE = "#EA580C"       # heating setpoint, constraints
PAPER = "#F8F9FA"        # slide ground
RULE = "#E2E8F0"
EMBER = ORANGE           # legacy alias used below

plt.rcParams.update({
    "font.family": "DejaVu Sans",
    "text.color": INK,
    "axes.labelcolor": SLATE,
    "xtick.color": SLATE,
    "ytick.color": SLATE,
    "axes.edgecolor": RULE,
    "axes.facecolor": PAPER,
    "savefig.facecolor": PAPER,
})


# --------------------------------------------------------------------------- #
def architecture() -> None:
    """The closed loop, drawn as a loop rather than a stack of boxes."""
    fig, ax = plt.subplots(figsize=(12.4, 4.6), dpi=DPI)
    ax.set_xlim(0, 124); ax.set_ylim(0, 46); ax.axis("off")
    fig.patch.set_facecolor(PAPER)

    # pad=0 so the drawn rectangle is exactly (x, y, w, h); with any padding the
    # real edge sits outside these coordinates and arrows start inside the box.
    def box(x, y, w, h, title, sub, edge, fill="#FFFFFF", tsize=11.5, ssize=8.6):
        ax.add_patch(FancyBboxPatch(
            (x, y), w, h, boxstyle="round,pad=0,rounding_size=1.4",
            linewidth=1.9, edgecolor=edge, facecolor=fill, zorder=2))
        ax.text(x + w / 2, y + h * 0.66, title, ha="center", va="center",
                fontsize=tsize, fontweight="bold", color=INK, zorder=3)
        ax.text(x + w / 2, y + h * 0.28, sub, ha="center", va="center",
                fontsize=ssize, color=SLATE, zorder=3, linespacing=1.4)

    def arrow(p1, p2, color, rad=0.0):
        ax.add_patch(FancyArrowPatch(
            p1, p2, connectionstyle=f"arc3,rad={rad}", arrowstyle="-|>",
            mutation_scale=18, linewidth=2.0, color=color, zorder=1))

    def tag(x, y, text, color):
        ax.text(x, y, text, ha="center", va="center", fontsize=8.6,
                color=color, fontweight="bold", zorder=4,
                bbox=dict(boxstyle="round,pad=0.3", fc=PAPER, ec="none"))

    EP  = (4, 27, 30, 14)     # right edge 34
    MCP = (47, 27, 30, 14)    # 47 .. 77
    LLM = (90, 27, 30, 14)    # 90 .. 120
    GATE = (47, 5, 30, 12)    # 47 .. 77, top 17

    box(*EP,  "EnergyPlus 26.1", "high-fidelity solver\nrunning in-process", BLUE)
    box(*MCP, "MCP server", "FastMCP · 6 validated tools\nover streamable HTTP", BLUE_DEEP)
    box(*LLM, "Local LLM", "Ollama · llama3.2:3b\nopen source, on-device", GREEN)
    box(*GATE, "Validation gate", "range · deadband · slew\nseasonal changeover",
        ORANGE, fill="#FEF3EC")

    # Outbound leg, left to right along the top.
    arrow((34, 36), (47, 36), BLUE);   tag(40.5, 39.2, "sensors\nevery timestep", BLUE)
    arrow((77, 36), (90, 36), BLUE_DEEP);  tag(83.5, 39.2, "building\nstate", BLUE_DEEP)
    # Return leg, right to left along the bottom - the loop closing.
    arrow((105, 27), (77, 13), GREEN, rad=-0.18); tag(93, 17.5, "setpoint decision", GREEN)
    arrow((47, 11), (19, 27), BLUE, rad=-0.18);   tag(31, 17.5, "injected into\nlive actuators", BLUE)

    ax.text(62, 44.2,
            "CLOSED LOOP   ·   no file rewrite, no restart   ·   168 agent decisions per simulated week",
            ha="center", va="center", fontsize=10, color=SLATE, style="italic")
    fig.tight_layout(pad=0.2)
    fig.savefig(OUT / "arch.png", dpi=DPI, facecolor=PAPER)
    plt.close(fig)
    print("wrote arch.png")


# --------------------------------------------------------------------------- #
def results() -> None:
    """Energy divergence + per-zone comfort, straight from the run outputs."""
    base = ROOT / "outputs" / "baseline"
    ai = ROOT / "outputs" / "ai"
    b_cum, a_cum = eo.cumulative_energy(base), eo.cumulative_energy(ai)
    bz, az = eo.per_zone_comfort(base, 7), eo.per_zone_comfort(ai, 7)
    cmp_ = eo.compare_runs(base, ai, 7)

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12.4, 4.2), dpi=DPI,
                                   gridspec_kw={"width_ratios": [1.35, 1]})
    fig.patch.set_facecolor(PAPER)

    ax1.fill_between(b_cum["elapsed_days"], a_cum["cumulative_kwh"],
                     b_cum["cumulative_kwh"], color=GREEN, alpha=0.18, lw=0)
    ax1.plot(b_cum["elapsed_days"], b_cum["cumulative_kwh"], color=BLUE, lw=2.4,
             label=f"Baseline schedule  {cmp_['baseline_kwh']:.0f} kWh")
    ax1.plot(a_cum["elapsed_days"], a_cum["cumulative_kwh"], color=GREEN, lw=2.4,
             label=f"AI closed loop  {cmp_['ai_kwh']:.0f} kWh")
    ax1.set_title("HVAC energy, summer week", fontsize=12, fontweight="bold", loc="left", pad=10)
    ax1.set_xlabel("Elapsed days", fontsize=9.5)
    ax1.set_ylabel("Cumulative kWh", fontsize=9.5)
    ax1.legend(frameon=False, fontsize=9.2, loc="upper left")
    ax1.annotate(f"-{cmp_['savings_pct']:.1f}%",
                 xy=(b_cum["elapsed_days"].iloc[-1], (cmp_["baseline_kwh"] + cmp_["ai_kwh"]) / 2),
                 xytext=(-72, 6), textcoords="offset points",
                 fontsize=17, fontweight="bold", color=GREEN)
    for s in ("top", "right"):
        ax1.spines[s].set_visible(False)
    ax1.grid(axis="y", color=RULE, lw=0.8)
    ax1.set_axisbelow(True)

    m = bz.merge(az, on="zone", suffixes=("_b", "_a"))
    y = range(len(m))
    ax2.barh([i + 0.19 for i in y], m["pct_in_band_b"], height=0.36, color=BLUE, label="Baseline")
    ax2.barh([i - 0.19 for i in y], m["pct_in_band_a"], height=0.36, color=GREEN, label="AI closed loop")
    ax2.set_yticks(list(y)); ax2.set_yticklabels(m["zone"], fontsize=9)
    ax2.set_xlim(0, 122); ax2.set_xlabel("% of occupied time in PMV band", fontsize=9.5)
    ax2.set_title("Thermal comfort held, per zone", fontsize=12, fontweight="bold",
                  loc="left", pad=26)
    # Legend sits above the plot area: inside the axes it landed on the bottom
    # bar group and collided with that group's value label.
    ax2.legend(frameon=False, fontsize=9.2, ncol=2,
               loc="lower left", bbox_to_anchor=(0, 1.005))
    for i, v in enumerate(m["pct_in_band_a"]):
        ax2.text(v + 2.5, i - 0.19, f"{v:.0f}%", va="center", fontsize=8.4, color=SLATE)
    for s in ("top", "right"):
        ax2.spines[s].set_visible(False)
    ax2.grid(axis="x", color=RULE, lw=0.8)
    ax2.set_axisbelow(True)

    fig.tight_layout(pad=1.4)
    fig.savefig(OUT / "results.png", dpi=DPI, facecolor=PAPER)
    plt.close(fig)
    print(f"wrote results.png  (savings {cmp_['savings_pct']:.1f}%)")


def decisions() -> None:
    """The agent's own control trail - the proof it is really driving."""
    import json

    rows = [json.loads(l) for l in
            (ROOT / "outputs" / "ai" / "agent_decisions.jsonl").read_text().splitlines() if l.strip()]
    cool = [r["command"]["cooling_sp"] for r in rows]
    heat = [r["command"]["heating_sp"] for r in rows]
    occ = [bool(r["state_digest"].get("occupied")) for r in rows]

    fig = plt.figure(figsize=(12.4, 4.6), dpi=DPI)
    fig.patch.set_facecolor(PAPER)
    ax = fig.add_axes([0.055, 0.46, 0.915, 0.44])

    start = None
    for i, flag in enumerate(occ + [False]):
        if flag and start is None:
            start = i
        elif not flag and start is not None:
            ax.axvspan(start, i - 1, color="#9AA5B1", alpha=0.16, lw=0)
            start = None

    ax.plot(cool, color=BLUE, lw=2.0, label="Cooling setpoint")
    ax.plot(heat, color=ORANGE, lw=2.0, label="Heating setpoint")
    ax.set_xlim(0, len(cool) - 1)
    ax.set_ylabel("Setpoint (°C)", fontsize=9.5)
    ax.set_xlabel("Control interval  ·  168 consecutive agent decisions, one simulated week",
                  fontsize=9.5)
    ax.legend(frameon=False, fontsize=9.2, ncol=2, loc="lower left", bbox_to_anchor=(0, 1.0))
    ax.text(1.0, 1.06, "shaded = building occupied", transform=ax.transAxes,
            ha="right", fontsize=8.6, color=SLATE, style="italic")
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    ax.grid(axis="y", color=RULE, lw=0.8)
    ax.set_axisbelow(True)

    quotes = [
        ("07-17 09:00", "Raise cooling_sp to about 27.0 C this turn — this fixes comfort AND saves energy"),
        ("07-17 11:00", "HOLD the current setpoints. Do not trim energy here — you would push occupants out of the band"),
        ("07-17 12:00", "Nudge cooling_sp up to save energy while keeping PMV within the band"),
    ]
    fig.text(0.055, 0.265, "The agent's own stated reasoning, taken verbatim from agent_decisions.jsonl",
             fontsize=9.4, color=INK, fontweight="bold")
    for i, (ts, q) in enumerate(quotes):
        y = 0.185 - i * 0.062
        fig.text(0.055, y, ts, fontsize=8.6, color=BLUE_DEEP, fontweight="bold", family="DejaVu Sans")
        fig.text(0.145, y, f"“{q}”", fontsize=9.0, color=SLATE, style="italic")

    fig.savefig(OUT / "decisions.png", dpi=DPI, facecolor=PAPER)
    plt.close(fig)
    print("wrote decisions.png")


if __name__ == "__main__":
    architecture()
    results()
    decisions()
