"""Generate slide grounds and the icon set for the Idea deck.

Flat fills read as bleak at projector scale, so the grounds carry a gradient, a
faint drafting rule, a thermal glow and a vignette — depth that survives PDF
export, unlike animation. Icons are drawn here rather than pulled from a pack
so their weight and palette match the deck exactly.
"""
from __future__ import annotations

import math
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import Circle, FancyBboxPatch, Polygon, Rectangle

OUT = Path(__file__).resolve().parent
ICONS = OUT / "icons"
W, H = 13.333, 7.5
DPI = 150

EMBER = "#D9541F"
EMBER_L = "#F28A4C"
TEAL_L = "#3FC18B"
CHALK = "#F4F7FA"
NAVY_IN = "#0B1421"


def _grid(ax, w, h, step, colour, alpha, lw=0.6):
    for x in np.arange(0, w, step):
        ax.plot([x, x], [0, h], color=colour, alpha=alpha, lw=lw, zorder=1)
    for y in np.arange(0, h, step):
        ax.plot([0, w], [y, y], color=colour, alpha=alpha, lw=lw, zorder=1)


def _radial(ax, cx, cy, radius, colour, peak, steps=40):
    """Soft glow built from stacked translucent discs — cheap and PDF-safe."""
    for i in range(steps, 0, -1):
        frac = i / steps
        ax.add_patch(Circle((cx, cy), radius * frac, color=colour,
                            alpha=peak * (1 - frac) ** 2.2, lw=0, zorder=2))


def background_dark() -> None:
    fig, ax = plt.subplots(figsize=(W, H), dpi=DPI)
    ax.set_xlim(0, W); ax.set_ylim(0, H); ax.axis("off")
    ax.set_position([0, 0, 1, 1])

    # base vertical gradient, deep at the bottom so titles sit on the lighter band
    grad = np.linspace(0, 1, 512).reshape(-1, 1)
    ax.imshow(grad, extent=[0, W, 0, H], aspect="auto", origin="lower",
              cmap=matplotlib.colors.LinearSegmentedColormap.from_list(
                  "navy", ["#080E18", "#16253C"]), zorder=0)

    _grid(ax, W, H, 0.42, "#7FA8D0", 0.045)
    _radial(ax, W * 0.80, H * 0.86, 5.6, EMBER, 0.055)
    _radial(ax, W * 0.10, H * 0.12, 5.0, "#2F7FD0", 0.05)

    # vignette: four edge washes, cheaper than a real radial mask
    for xy, wh in (((0, 0), (W, 0.9)), ((0, H - 0.9), (W, 0.9)),
                   ((0, 0), (0.9, H)), ((W - 0.9, 0), (0.9, H))):
        ax.add_patch(Rectangle(xy, *wh, color="#03070E", alpha=0.18, lw=0, zorder=3))

    fig.savefig(OUT / "bg_dark.png", dpi=DPI, facecolor=NAVY_IN,
                bbox_inches="tight", pad_inches=0)
    plt.close(fig)
    print("wrote bg_dark.png")


def background_light() -> None:
    fig, ax = plt.subplots(figsize=(W, H), dpi=DPI)
    ax.set_xlim(0, W); ax.set_ylim(0, H); ax.axis("off")
    ax.set_position([0, 0, 1, 1])

    grad = np.linspace(0, 1, 512).reshape(-1, 1)
    ax.imshow(grad, extent=[0, W, 0, H], aspect="auto", origin="lower",
              cmap=matplotlib.colors.LinearSegmentedColormap.from_list(
                  "paper", ["#F0E9DC", "#FDFBF7"]), zorder=0)

    _grid(ax, W, H, 0.42, "#8A7B5E", 0.05)
    _radial(ax, W * 0.93, H * 0.93, 4.6, EMBER, 0.05)
    _radial(ax, W * 0.05, H * 0.05, 4.2, "#2A78D6", 0.035)

    fig.savefig(OUT / "bg_light.png", dpi=DPI, facecolor="#FAF7F2",
                bbox_inches="tight", pad_inches=0)
    plt.close(fig)
    print("wrote bg_light.png")


# --------------------------------------------------------------------------- #
# icon set — drawn, not imported, so weight and palette match the deck
# --------------------------------------------------------------------------- #
def _icon(name: str, draw, colour: str, lw: float = 7.0) -> None:
    fig, ax = plt.subplots(figsize=(1.6, 1.6), dpi=160)
    ax.set_xlim(0, 100); ax.set_ylim(0, 100); ax.axis("off")
    ax.set_position([0, 0, 1, 1])
    draw(ax, colour, lw)
    fig.savefig(ICONS / f"{name}.png", transparent=True, dpi=160,
                bbox_inches="tight", pad_inches=0.06)
    plt.close(fig)


def _building(ax, c, lw):
    ax.add_patch(Rectangle((22, 14), 30, 66, fill=False, ec=c, lw=lw, joinstyle="round"))
    ax.add_patch(Rectangle((52, 34), 26, 46, fill=False, ec=c, lw=lw, joinstyle="round"))
    for y in (26, 42, 58):
        ax.plot([30, 44], [y, y], color=c, lw=lw * 0.75, solid_capstyle="round")
    for y in (46, 62):
        ax.plot([60, 70], [y, y], color=c, lw=lw * 0.75, solid_capstyle="round")


def _chip(ax, c, lw):
    ax.add_patch(FancyBboxPatch((26, 26), 48, 48, boxstyle="round,pad=0,rounding_size=6",
                                fill=False, ec=c, lw=lw))
    ax.add_patch(Rectangle((42, 42), 16, 16, fill=False, ec=c, lw=lw * 0.8))
    for x in (36, 50, 64):
        ax.plot([x, x], [74, 86], color=c, lw=lw * 0.8, solid_capstyle="round")
        ax.plot([x, x], [14, 26], color=c, lw=lw * 0.8, solid_capstyle="round")
    for y in (36, 50, 64):
        ax.plot([14, 26], [y, y], color=c, lw=lw * 0.8, solid_capstyle="round")
        ax.plot([74, 86], [y, y], color=c, lw=lw * 0.8, solid_capstyle="round")


def _loop(ax, c, lw):
    th = np.linspace(0.45 * math.pi, 2.15 * math.pi, 200)
    ax.plot(50 + 30 * np.cos(th), 50 + 30 * np.sin(th), color=c, lw=lw,
            solid_capstyle="round")
    ax.add_patch(Polygon([[48, 84], [66, 78], [52, 66]], closed=True, color=c))


def _shield(ax, c, lw):
    ax.plot([50, 22, 22, 50, 78, 78, 50], [88, 74, 42, 14, 42, 74, 88],
            color=c, lw=lw, solid_capstyle="round", solid_joinstyle="round")
    ax.plot([36, 46, 66], [52, 40, 64], color=c, lw=lw, solid_capstyle="round",
            solid_joinstyle="round")


def _bolt(ax, c, lw):
    ax.add_patch(Polygon([[56, 92], [26, 50], [46, 50], [42, 10], [74, 54],
                          [53, 54]], closed=True, fill=False, ec=c, lw=lw,
                         joinstyle="round"))


def _thermo(ax, c, lw):
    ax.plot([50, 50], [30, 82], color=c, lw=lw, solid_capstyle="round")
    ax.add_patch(Circle((50, 22), 13, fill=False, ec=c, lw=lw))
    for y in (48, 60, 72):
        ax.plot([58, 68], [y, y], color=c, lw=lw * 0.7, solid_capstyle="round")


def _clock(ax, c, lw):
    ax.add_patch(Circle((50, 50), 34, fill=False, ec=c, lw=lw))
    ax.plot([50, 50], [50, 72], color=c, lw=lw, solid_capstyle="round")
    ax.plot([50, 68], [50, 44], color=c, lw=lw, solid_capstyle="round")


def _code(ax, c, lw):
    ax.plot([36, 18, 36], [76, 50, 24], color=c, lw=lw, solid_capstyle="round",
            solid_joinstyle="round")
    ax.plot([64, 82, 64], [76, 50, 24], color=c, lw=lw, solid_capstyle="round",
            solid_joinstyle="round")
    ax.plot([58, 42], [82, 18], color=c, lw=lw * 0.85, solid_capstyle="round")


def _book(ax, c, lw):
    ax.plot([50, 50], [26, 78], color=c, lw=lw * 0.8, solid_capstyle="round")
    ax.plot([50, 22, 22, 50], [26, 34, 76, 78], color=c, lw=lw,
            solid_capstyle="round", solid_joinstyle="round")
    ax.plot([50, 78, 78, 50], [26, 34, 76, 78], color=c, lw=lw,
            solid_capstyle="round", solid_joinstyle="round")


def icons() -> None:
    ICONS.mkdir(exist_ok=True)
    spec = [
        ("building", _building, EMBER), ("building_l", _building, EMBER_L),
        ("chip", _chip, EMBER),         ("chip_l", _chip, EMBER_L),
        ("loop", _loop, EMBER),         ("loop_l", _loop, EMBER_L),
        ("shield", _shield, "#0D7A4F"), ("shield_l", _shield, TEAL_L),
        ("bolt", _bolt, EMBER),         ("bolt_l", _bolt, EMBER_L),
        ("thermo", _thermo, "#0D7A4F"), ("thermo_l", _thermo, TEAL_L),
        ("clock", _clock, "#121B2B"),   ("clock_l", _clock, CHALK),
        ("code", _code, EMBER),         ("code_l", _code, EMBER_L),
        ("book", _book, EMBER),         ("book_l", _book, EMBER_L),
    ]
    for name, draw, colour in spec:
        _icon(name, draw, colour)
    print(f"wrote {len(spec)} icons")


if __name__ == "__main__":
    background_dark()
    background_light()
    icons()
