"""
Grid carbon-intensity signal.

The agent is asked to trade energy against *carbon*, not just kWh, so it needs a
marginal-intensity signal.  In production this would be a live feed (WattTime,
ElectricityMaps, or a utility DR signal).  For a self-contained MVP we use a
deterministic diurnal + seasonal curve that reproduces the shape of a real
grid: cheap/clean solar trough midday, dirty peaking plants in the evening ramp.

Swap `GridSignal.intensity()` for an API call and nothing else changes.
"""
from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass
class GridSignal:
    base_g_per_kwh: float = 420.0
    solar_depth_g: float = 160.0     # midday reduction from solar penetration
    evening_peak_g: float = 130.0    # 17:00-21:00 peaker uplift
    winter_uplift_g: float = 40.0

    def intensity(self, hour: float, month: int = 6) -> float:
        """Marginal grid carbon intensity, gCO2e/kWh, for a given hour of day."""
        h = hour % 24.0

        # Solar trough centred on 13:00, ~8 h wide.
        solar = self.solar_depth_g * math.exp(-((h - 13.0) ** 2) / (2 * 3.2 ** 2))

        # Evening ramp centred on 19:00, ~2.5 h wide.
        peak = self.evening_peak_g * math.exp(-((h - 19.0) ** 2) / (2 * 1.8 ** 2))

        seasonal = self.winter_uplift_g if month in (11, 12, 1, 2) else 0.0

        return max(60.0, self.base_g_per_kwh - solar + peak + seasonal)

    def regime(self, hour: float, month: int = 6) -> str:
        """Human-readable label the LLM can reason over without doing math."""
        val = self.intensity(hour, month)
        if val < 300:
            return "clean"
        if val < 430:
            return "moderate"
        return "dirty_peak"

    def forecast(self, hour: float, month: int = 6, horizon_h: int = 4) -> list[dict]:
        """Next `horizon_h` hourly intensities - enables pre-cooling decisions."""
        return [
            {
                "hour": int((hour + i) % 24),
                "g_per_kwh": round(self.intensity(hour + i, month), 0),
                "regime": self.regime(hour + i, month),
            }
            for i in range(horizon_h + 1)
        ]


GRID = GridSignal()
