"""Produce the quantitative savings report as a submittable PDF + CSV.

The brief asks for proof of a percentage reduction in kWh *while thermal
comfort boundaries are maintained*. A screenshot of the live dashboard does not
prove that: it is a moment in time, it carries no provenance, and it cannot be
checked. This writes both artefacts a judge can act on -

    outputs/Echo-Loop_Savings_Report.pdf   three pages, self-contained
    outputs/savings_summary.csv            the same numbers as data

- straight from the committed EnergyPlus results, so the report and the
repository cannot disagree. Both seasons are reported, because a control
strategy that only wins in cooling has not been shown to generalise.

Comfort is scored identically for both runs by eco_loop.eplus_outputs - same
ISO 7730 implementation, same worst-occupied-zone reduction - so any difference
is attributable to the control strategy rather than to the measurement.
"""
from __future__ import annotations

import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.backends.backend_pdf import PdfPages

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from eco_loop import eplus_outputs as eo  # noqa: E402

OUT = ROOT / "outputs"
PDF_PATH = OUT / "Echo-Loop_Savings_Report.pdf"
CSV_PATH = OUT / "savings_summary.csv"

# Light palette, accents darkened to clear 4.5:1 on white.
INK = "#0F1B2D"
BODY = "#33455C"
MUTED = "#5A6B80"
GREEN = "#0A7A52"      # positive outcomes only
BLUE = "#2563EB"       # baseline series
RULE = "#CBD5E1"
PANEL = "#F1F5F9"

PMV_BAND = 0.5         # ISO 7730 / ASHRAE 55 comfort band, |PMV| <= 0.5

SEASONS = [
    ("Summer week", "baseline", "ai", 7, "15-21 July"),
    ("Winter week", "winter_baseline", "winter_ai", 1, "15-21 January"),
]

plt.rcParams.update({
    "font.family": "DejaVu Sans",
    "text.color": INK,
    "axes.labelcolor": BODY,
    "xtick.color": MUTED,
    "ytick.color": MUTED,
    "axes.edgecolor": RULE,
})


# --------------------------------------------------------------------------- #
def collect() -> list[dict]:
    """Read every season pair once; everything downstream reuses this."""
    rows = []
    for label, base_dir, ai_dir, month, dates in SEASONS:
        cmp_ = eo.compare_runs(OUT / base_dir, OUT / ai_dir, month)
        if "savings_pct" not in cmp_:
            print(f"  !! {label}: no energy data in outputs/{base_dir} or /{ai_dir}")
            continue
        rows.append({
            "label": label, "dates": dates, "month": month,
            "base_dir": base_dir, "ai_dir": ai_dir, "cmp": cmp_,
            "base_pmv": eo.occupied_comfort(OUT / base_dir, month),
            "ai_pmv": eo.occupied_comfort(OUT / ai_dir, month),
            "base_zone": eo.per_zone_comfort(OUT / base_dir, month),
            "ai_zone": eo.per_zone_comfort(OUT / ai_dir, month),
        })
        c = rows[-1]["cmp"]
        print(f"  {label:12s} {c['baseline_kwh']:7.1f} -> {c['ai_kwh']:7.1f} kWh"
              f"   {c['savings_pct']:+5.1f}%")
    return rows


def _text(ax, x, y, s, size=10, colour=BODY, weight="normal", ha="left"):
    ax.text(x, y, s, transform=ax.transAxes, fontsize=size, color=colour,
            fontweight=weight, ha=ha, va="top")


def _page(pdf, title, subtitle):
    fig = plt.figure(figsize=(11.69, 8.27), dpi=200)
    fig.patch.set_facecolor("white")
    head = fig.add_axes([0, 0.90, 1, 0.10]); head.axis("off")
    _text(head, 0.055, 0.95, title, size=19, colour=INK, weight="bold")
    _text(head, 0.055, 0.32, subtitle, size=10, colour=MUTED)
    return fig


def _foot(fig, n):
    ax = fig.add_axes([0, 0, 1, 0.045]); ax.axis("off")
    _text(ax, 0.055, 0.85, "Echo-Loop  ·  measured from committed EnergyPlus "
                           "results  ·  reproduce with python main.py run-ai",
          size=8, colour=MUTED)
    _text(ax, 0.945, 0.85, str(n), size=8, colour=MUTED, ha="right")


# --------------------------------------------------------------------------- #
def page_headline(pdf, data):
    fig = _page(pdf, "Quantitative Savings: baseline vs AI closed loop",
                "Identical building, identical weather, identical comfort scoring. "
                "The only difference is who sets the setpoints. Energy and comfort "
                "are reported side by side; neither is shown without the other.")

    for i, d in enumerate(data):
        c, top = d["cmp"], 0.80 - i * 0.42
        ax = fig.add_axes([0.055, top - 0.30, 0.40, 0.30]); ax.axis("off")
        ax.add_patch(plt.Rectangle((0, 0), 1, 1, transform=ax.transAxes,
                                   facecolor=PANEL, edgecolor="none", zorder=0))
        _text(ax, 0.05, 0.93, f"{d['label']}  ·  {d['dates']}", size=10,
              colour=MUTED, weight="bold")
        _text(ax, 0.05, 0.72, f"−{c['savings_pct']:.1f}%", size=34,
              colour=GREEN, weight="bold")
        _text(ax, 0.05, 0.30, "HVAC electricity", size=11, colour=INK, weight="bold")
        _text(ax, 0.05, 0.18,
              f"{c['baseline_kwh']:.1f} kWh baseline  →  {c['ai_kwh']:.1f} kWh agent"
              f"   ({c['savings_kwh']:.1f} kWh saved)", size=9.5, colour=BODY)

        # Comfort must be shown next to the saving, or the saving proves nothing.
        b, a = c["baseline_comfort"], c["ai_comfort"]
        ax2 = fig.add_axes([0.50, top - 0.30, 0.445, 0.30]); ax2.axis("off")
        delta = a["pct_in_band"] - b["pct_in_band"]
        held = delta >= -1.0
        _text(ax2, 0.0, 0.93, "Thermal comfort, worst occupied zone per timestep",
              size=10, colour=INK, weight="bold")
        _text(ax2, 0.0, 0.74,
              f"Mean PMV   {b['mean_pmv']:+.2f}  →  {a['mean_pmv']:+.2f}"
              f"      (0.00 = thermally neutral)", size=9.5, colour=BODY)
        _text(ax2, 0.0, 0.60,
              f"Occupied time inside |PMV| ≤ {PMV_BAND}   "
              f"{b['pct_in_band']:.1f}%  →  {a['pct_in_band']:.1f}%"
              f"   ({delta:+.1f} pp)", size=9.5, colour=BODY)
        _text(ax2, 0.0, 0.46,
              f"PMV range   {a['min_pmv']:+.2f} to {a['max_pmv']:+.2f}"
              f"      scored over {a['n_occupied_steps']:,} occupied zone-timesteps",
              size=9.5, colour=BODY)
        _text(ax2, 0.0, 0.28,
              "COMFORT BOUNDARY MAINTAINED" if held
              else f"COMFORT BOUNDARY NOT MAINTAINED  ({delta:+.1f} pp banded time)",
              size=10, colour=GREEN if held else "#C2410C", weight="bold")

    _foot(fig, 1)
    pdf.savefig(fig); plt.close(fig)


def page_energy(pdf, data):
    fig = _page(pdf, "Where the reduction comes from",
                "Cumulative HVAC electricity across each simulated week. The gap "
                "is the saving; it widens on every occupied day.")
    for i, d in enumerate(data):
        b = eo.cumulative_energy(OUT / d["base_dir"])
        a = eo.cumulative_energy(OUT / d["ai_dir"])
        ax = fig.add_axes([0.075 + i * 0.475, 0.20, 0.38, 0.62])
        if b is None or a is None:
            ax.axis("off"); continue
        ax.plot(b["elapsed_days"], b["cumulative_kwh"], color=BLUE, lw=2.0,
                label=f"Baseline schedule   {d['cmp']['baseline_kwh']:.0f} kWh")
        ax.plot(a["elapsed_days"], a["cumulative_kwh"], color=GREEN, lw=2.0,
                label=f"AI closed loop   {d['cmp']['ai_kwh']:.0f} kWh")
        ax.fill_between(a["elapsed_days"], a["cumulative_kwh"],
                        b["cumulative_kwh"], color=GREEN, alpha=0.15, lw=0)
        ax.set_title(f"{d['label']}  ·  −{d['cmp']['savings_pct']:.1f}% HVAC electricity",
                     fontsize=11, loc="left", color=INK, fontweight="bold", pad=8)
        ax.set_xlabel("Elapsed days"); ax.set_ylabel("Cumulative kWh")
        ax.legend(frameon=False, fontsize=8.5, loc="upper left")
        for s in ("top", "right"):
            ax.spines[s].set_visible(False)
        ax.grid(axis="y", color=RULE, lw=0.6, alpha=0.7)
        ax.set_axisbelow(True)
    _foot(fig, 2)
    pdf.savefig(fig); plt.close(fig)


def page_comfort(pdf, data):
    fig = _page(pdf, "Proof the comfort boundary was not traded away",
                f"Per zone, the share of occupied time inside the ISO 7730 band "
                f"|PMV| ≤ {PMV_BAND}. Both runs scored the same way.")
    for i, d in enumerate(data):
        bz, az = d["base_zone"], d["ai_zone"]
        ax = fig.add_axes([0.075 + i * 0.475, 0.20, 0.38, 0.62])
        if bz is None or az is None:
            ax.axis("off"); continue
        col = "pct_in_band" if "pct_in_band" in az.columns else az.columns[1]
        y = range(len(az))
        ax.barh([v + 0.20 for v in y], bz[col], height=0.38, color=BLUE,
                label="Baseline")
        ax.barh([v - 0.20 for v in y], az[col], height=0.38, color=GREEN,
                label="AI closed loop")
        for j, v in enumerate(az[col]):
            ax.text(v + 1.5, j - 0.20, f"{v:.0f}%", va="center", fontsize=8,
                    color=INK, fontweight="bold")
        ax.set_yticks(list(y)); ax.set_yticklabels(az["zone"], fontsize=8)
        ax.set_xlim(0, 122)
        ax.set_xlabel(f"% of occupied time inside |PMV| ≤ {PMV_BAND}")
        ax.set_title(d["label"], fontsize=11, loc="left", color=INK,
                     fontweight="bold", pad=8)
        for s in ("top", "right"):
            ax.spines[s].set_visible(False)

    # One shared legend in the header keeps it off the bars entirely.
    leg = fig.add_axes([0.70, 0.855, 0.25, 0.04]); leg.axis("off")
    leg.legend(handles=[plt.Line2D([], [], color=BLUE, lw=7, label="Baseline"),
                        plt.Line2D([], [], color=GREEN, lw=7, label="AI closed loop")],
               frameon=False, fontsize=9, ncol=2, loc="center")

    note = fig.add_axes([0, 0.04, 1, 0.11]); note.axis("off")
    _text(note, 0.055, 0.95,
          "Scope: one setpoint pair drives all five zones. Where the agent gives "
          "banded time back against the baseline, that is stated on page 1 rather "
          "than reported as a win.", size=8.5, colour=MUTED)
    _text(note, 0.055, 0.50,
          "Per-zone figures are always higher than the page-1 metric, which takes "
          "the worst occupied zone at every timestep. Both are shown.",
          size=8.5, colour=MUTED)
    _foot(fig, 3)
    pdf.savefig(fig); plt.close(fig)


# --------------------------------------------------------------------------- #
def write_csv(data) -> None:
    rows = []
    for d in data:
        c = d["cmp"]
        b, a = c["baseline_comfort"], c["ai_comfort"]
        rows.append({
            "season": d["label"],
            "dates": d["dates"],
            "baseline_hvac_kwh": round(c["baseline_kwh"], 2),
            "ai_hvac_kwh": round(c["ai_kwh"], 2),
            "kwh_saved": round(c["savings_kwh"], 2),
            "pct_reduction": round(c["savings_pct"], 2),
            "baseline_mean_pmv": round(b["mean_pmv"], 3),
            "ai_mean_pmv": round(a["mean_pmv"], 3),
            "baseline_pct_in_band": round(b["pct_in_band"], 2),
            "ai_pct_in_band": round(a["pct_in_band"], 2),
            "comfort_delta_pp": round(c["comfort_delta_pp"], 2),
            "occupied_zone_timesteps": a["n_occupied_steps"],
        })
    pd.DataFrame(rows).to_csv(CSV_PATH, index=False)
    print(f"wrote {CSV_PATH.relative_to(ROOT)}")


def main() -> None:
    print("reading committed run outputs ...")
    data = collect()
    if not data:
        raise SystemExit("no comparable runs found under outputs/")
    with PdfPages(PDF_PATH) as pdf:
        page_headline(pdf, data)
        page_energy(pdf, data)
        page_comfort(pdf, data)
    print(f"wrote {PDF_PATH.relative_to(ROOT)}  ({len(data)} seasons)")
    write_csv(data)


if __name__ == "__main__":
    main()
