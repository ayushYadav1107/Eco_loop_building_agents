"""
ISO 7730 / ASHRAE-55 Fanger PMV-PPD model.

EnergyPlus can report `Zone Thermal Comfort Fanger Model PMV`, but only for
`People` objects that explicitly enable a thermal comfort model.  Many stock
example files do not.  Rather than making the whole closed loop depend on the
IDF being edited correctly, we compute PMV here from variables every model
reports (zone air temperature, mean radiant temperature, relative humidity) and
use the native EnergyPlus output when it happens to be available.
"""
from __future__ import annotations

import math
from typing import Tuple


def pmv_ppd(
    ta: float,
    tr: float,
    vel: float,
    rh: float,
    met: float = 1.2,
    clo: float = 0.6,
    wme: float = 0.0,
) -> Tuple[float, float]:
    """
    Predicted Mean Vote and Predicted Percentage Dissatisfied (ISO 7730:2005).

    Args:
        ta:  dry-bulb air temperature, degC
        tr:  mean radiant temperature, degC
        vel: relative air velocity, m/s
        rh:  relative humidity, %
        met: metabolic rate, met (1 met = 58.15 W/m2)
        clo: clothing insulation, clo (1 clo = 0.155 m2K/W)
        wme: external work, met

    Returns:
        (pmv, ppd_percent)
    """
    # Water vapour partial pressure, Pa
    pa = rh * 10.0 * math.exp(16.6536 - 4030.183 / (ta + 235.0))

    icl = 0.155 * clo                     # thermal insulation, m2K/W
    m = met * 58.15                       # metabolic rate, W/m2
    w = wme * 58.15                       # external work, W/m2
    mw = m - w                            # internal heat production

    fcl = 1.0 + 1.29 * icl if icl <= 0.078 else 1.05 + 0.645 * icl

    hcf = 12.1 * math.sqrt(max(vel, 0.0))  # forced convection coefficient
    taa = ta + 273.0
    tra = tr + 273.0

    # First guess of clothing surface temperature
    tcla = taa + (35.5 - ta) / (3.5 * icl + 0.1)

    p1 = icl * fcl
    p2 = p1 * 3.96
    p3 = p1 * 100.0
    p4 = p1 * taa
    p5 = 308.7 - 0.028 * mw + p2 * (tra / 100.0) ** 4

    xn = tcla / 100.0
    xf = tcla / 50.0
    eps = 0.00015
    hc = hcf
    n = 0
    while abs(xn - xf) > eps:
        xf = (xf + xn) / 2.0
        hcn = 2.38 * abs(100.0 * xf - taa) ** 0.25   # natural convection
        hc = hcf if hcf > hcn else hcn
        xn = (p5 + p4 * hc - p2 * xf ** 4) / (100.0 + p3 * hc)
        n += 1
        if n > 150:
            raise ValueError("PMV clothing-temperature iteration did not converge")

    tcl = 100.0 * xn - 273.0  # clothing surface temperature, degC

    # Heat loss components, W/m2
    hl1 = 3.05 * 0.001 * (5733.0 - 6.99 * mw - pa)          # skin diffusion
    hl2 = 0.42 * (mw - 58.15) if mw > 58.15 else 0.0         # sweat evaporation
    hl3 = 1.7 * 0.00001 * m * (5867.0 - pa)                  # latent respiration
    hl4 = 0.0014 * m * (34.0 - ta)                           # dry respiration
    hl5 = 3.96 * fcl * (xn ** 4 - (tra / 100.0) ** 4)        # radiation
    hl6 = fcl * hc * (tcl - ta)                              # convection

    ts = 0.303 * math.exp(-0.036 * m) + 0.028                # thermal sensation
    pmv = ts * (mw - hl1 - hl2 - hl3 - hl4 - hl5 - hl6)
    ppd = 100.0 - 95.0 * math.exp(-0.03353 * pmv ** 4 - 0.2179 * pmv ** 2)
    return pmv, ppd


def safe_pmv(
    ta: float,
    tr: float | None,
    rh: float | None,
    vel: float = 0.15,
    met: float = 1.2,
    clo: float = 0.6,
) -> Tuple[float | None, float | None]:
    """Non-throwing wrapper with sane substitutions for missing inputs.

    Never raises: this runs inside an EnergyPlus C callback where an exception
    would tear down the simulation.
    """
    try:
        if ta is None or not math.isfinite(ta):
            return None, None
        tr_eff = tr if (tr is not None and math.isfinite(tr)) else ta
        rh_eff = rh if (rh is not None and math.isfinite(rh)) else 50.0
        rh_eff = min(max(rh_eff, 1.0), 99.0)
        return pmv_ppd(ta, tr_eff, vel, rh_eff, met, clo)
    except Exception:
        return None, None


def clo_for_season(month: int) -> float:
    """Simple seasonal clothing schedule (ASHRAE-55 typical ensembles)."""
    return 1.0 if month in (11, 12, 1, 2, 3) else 0.5
