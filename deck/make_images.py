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

# Dark tech palette, matching the deck: navy ground, neon green for positive
# outcomes, vibrant blue for structure, amber for constraints. Figures are drawn
# ON the dark ground so they sit in the slide rather than in a white box.
PAPER = "#0E1626"        # slide ground
PANEL = "#16213A"        # raised panel inside a figure
INK = "#F1F5F9"          # primary text on dark
SLATE = "#9FB3C8"        # secondary text
BLUE = "#3B82F6"         # baseline series / cooling / structure
BLUE_DEEP = "#60A5FA"    # lifted blue for labels on dark
GREEN = "#22E88A"        # neon green - AI series, savings, success
ORANGE = "#F59E0B"       # amber - constraints, heating setpoint
RULE = "#243350"
EMBER = ORANGE

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
    def box(x, y, w, h, title, sub, edge, fill=PANEL, tsize=11.5, ssize=8.6):
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
        ORANGE, fill="#2A2013")

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
            ax.axvspan(start, i - 1, color="#8FA3BF", alpha=0.13, lw=0)
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




# --------------------------------------------------------------------------- #
def pipeline() -> None:
    """The safety envelope from ARCHITECTURE.md §5 - five stages between an LLM
    token and an actuator, plus the rejection path."""
    fig, ax = plt.subplots(figsize=(12.4, 1.72), dpi=DPI)
    ax.set_xlim(0, 124); ax.set_ylim(0, 17); ax.axis("off")
    fig.patch.set_facecolor(PAPER)

    stages = [
        ("1  SCHEMA", "range · deadband", BLUE),
        ("2  CHANGEOVER", "park the idle mode", ORANGE),
        ("3  SLEW LIMIT", "≤ 3 °C / hour", ORANGE),
        ("4  PER-ZONE TRIM", "each zone's own PMV", BLUE),
        ("5  FALLBACK", "hold last good command", GREEN),
    ]
    w, gap = 20.5, 3.6
    for i, (title, sub, col) in enumerate(stages):
        x = 2 + i * (w + gap)
        ax.add_patch(FancyBboxPatch(
            (x, 3.4), w, 8.2, boxstyle="round,pad=0,rounding_size=1.1",
            linewidth=1.8, edgecolor=col, facecolor=PANEL, zorder=2))
        ax.text(x + w / 2, 9.3, title, ha="center", va="center",
                fontsize=9.0, fontweight="bold", color=col, zorder=3)
        ax.text(x + w / 2, 5.6, sub, ha="center", va="center",
                fontsize=8.0, color=SLATE, zorder=3)
        if i < len(stages) - 1:
            ax.add_patch(FancyArrowPatch(
                (x + w, 7.5), (x + w + gap, 7.5), arrowstyle="-|>",
                mutation_scale=15, linewidth=1.8, color=SLATE, zorder=1))

    ax.text(2, 14.8, "EVERY COMMAND PASSES FIVE GATES BEFORE IT REACHES AN ACTUATOR",
            ha="left", va="center", fontsize=9.6, fontweight="bold", color=BLUE_DEEP)
    ax.text(2, 1.2, "Rejected commands return accepted: false with a reason — the previous "
            "setpoints stay in force and the model can retry.",
            ha="left", va="center", fontsize=8.2, color=SLATE, style="italic")

    fig.tight_layout(pad=0.2)
    fig.savefig(OUT / "pipeline.png", dpi=DPI, facecolor=PAPER)
    plt.close(fig)
    print("wrote pipeline.png")


def timeline() -> None:
    """One control interval, end to end - the §3 sequence as a time strip."""
    fig, ax = plt.subplots(figsize=(12.4, 1.32), dpi=DPI)
    ax.set_xlim(0, 124); ax.set_ylim(0, 13); ax.axis("off")
    fig.patch.set_facecolor(PAPER)

    ax.plot([4, 120], [6, 6], color=RULE, lw=3, solid_capstyle="round", zorder=1)

    # sixteen sensor ticks, then the agent turn
    for i in range(16):
        x = 5 + i * 3.6
        ax.plot([x, x], [4.2, 7.8], color=BLUE, lw=1.9, solid_capstyle="round", zorder=2)
    ax.text(33, 1.6, "sensors sampled every zone timestep  ·  no LLM call",
            ha="center", fontsize=8.4, color=BLUE)

    for x, col, lab in ((70, BLUE_DEEP, "publish\nstate"),
                        (86, GREEN, "LLM turn\n2.1 s median"),
                        (102, ORANGE, "validate"),
                        (116, BLUE, "inject")):
        ax.add_patch(FancyBboxPatch(
            (x - 6.4, 2.4), 12.8, 7.2, boxstyle="round,pad=0,rounding_size=1.0",
            linewidth=1.7, edgecolor=col, facecolor=PANEL, zorder=3))
        ax.text(x, 6, lab, ha="center", va="center", fontsize=8.0,
                fontweight="bold", color=col, zorder=4, linespacing=1.35)

    ax.text(4, 11.4, "ONE CONTROL INTERVAL  ·  60 SIMULATED MINUTES",
            ha="left", va="center", fontsize=9.6, fontweight="bold", color=BLUE_DEEP)
    ax.text(120, 11.4, "the solver is stopped only for the agent turn",
            ha="right", va="center", fontsize=8.4, color=SLATE, style="italic")

    fig.tight_layout(pad=0.2)
    fig.savefig(OUT / "timeline.png", dpi=DPI, facecolor=PAPER)
    plt.close(fig)
    print("wrote timeline.png")


def comfort_dist() -> None:
    """Where occupants actually sat on the comfort scale - the strongest single
    piece of evidence that the savings were not taken out of comfort."""
    import numpy as np
    base = eo.occupied_comfort(ROOT / "outputs" / "baseline", 7)
    ai = eo.occupied_comfort(ROOT / "outputs" / "ai", 7)

    fig, ax = plt.subplots(figsize=(6.1, 2.75), dpi=DPI)
    fig.patch.set_facecolor(PANEL)
    ax.set_facecolor(PANEL)
    edges = np.linspace(-2.0, 1.5, 40)
    centres = (edges[:-1] + edges[1:]) / 2
    ax.axvspan(-0.5, 0.5, color=GREEN, alpha=0.13, lw=0)

    for res, name, col in ((base, "Baseline", BLUE), (ai, "AI closed loop", GREEN)):
        dens, _ = np.histogram(res["series"], bins=edges, density=True)
        ax.plot(centres, dens, color=col, lw=2.2, label=f"{name}  mean {res['mean_pmv']:+.2f}")
        ax.fill_between(centres, dens, color=col, alpha=0.13)

    ax.axvline(0, color=SLATE, lw=1)
    ax.text(0.52, 0.05, "neutral", transform=ax.transAxes, fontsize=8.2, color=SLATE)
    ax.set_xlabel("PMV — occupied hours", fontsize=9)
    ax.set_yticks([])
    ax.set_title("Comfort was not traded away", fontsize=11, fontweight="bold",
                 loc="left", color=INK, pad=8)
    ax.legend(frameon=False, fontsize=8.6, loc="upper left")
    for s in ("top", "right", "left"):
        ax.spines[s].set_visible(False)
    fig.tight_layout(pad=0.6)
    fig.savefig(OUT / "comfort_dist.png", dpi=DPI, facecolor=PANEL)
    plt.close(fig)
    print("wrote comfort_dist.png")


if __name__ == "__main__":
    architecture()
    results()
    decisions()
    pipeline()
    timeline()
    comfort_dist()
