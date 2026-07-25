"""
Pydantic contracts shared by the simulation, the MCP tools and the LLM.

Everything crossing the LLM boundary is validated here.  The model is free to
say anything it likes; only structures that survive `SetpointCommand` reach the
EnergyPlus actuator API.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from config import POLICY


# --------------------------------------------------------------------------- #
# Observations (simulation -> agent)
# --------------------------------------------------------------------------- #
class ZoneObservation(BaseModel):
    """One zone's aggregated state over the last control interval."""

    zone: str
    mean_air_temp_c: float
    mean_radiant_temp_c: Optional[float] = None
    relative_humidity_pct: Optional[float] = None
    pmv: Optional[float] = None
    ppd_pct: Optional[float] = None
    cooling_setpoint_c: Optional[float] = None
    heating_setpoint_c: Optional[float] = None
    occupant_count: Optional[float] = None

    def comfort_flag(self) -> str:
        if self.pmv is None:
            return "unknown"
        if self.pmv < POLICY.pmv_low:
            return "too_cold"
        if self.pmv > POLICY.pmv_high:
            return "too_warm"
        return "comfortable"


class BuildingState(BaseModel):
    """The full snapshot handed to the agent once per control interval."""

    timestamp: str = Field(..., description="Simulated date/time, ISO-like string")
    sim_minutes: float = Field(..., description="Simulated minutes since run start")
    outdoor_air_temp_c: float
    outdoor_rh_pct: Optional[float] = None
    direct_solar_w_m2: Optional[float] = None
    hvac_power_w: float = Field(0.0, description="Interval-mean facility HVAC electric demand")
    facility_power_w: Optional[float] = None
    interval_energy_kwh: float = 0.0
    grid_carbon_intensity_g_per_kwh: float = 0.0
    interval_carbon_g: float = 0.0
    occupied: bool = True
    zones: List[ZoneObservation] = Field(default_factory=list)
    active_cooling_setpoint_c: float = POLICY.baseline_cooling_sp
    active_heating_setpoint_c: float = POLICY.baseline_heating_sp

    # -- convenience aggregates used in prompts -----------------------------
    def worst_pmv(self) -> Optional[float]:
        vals = [z.pmv for z in self.zones if z.pmv is not None]
        if not vals:
            return None
        return max(vals, key=abs)

    def mean_zone_temp(self) -> Optional[float]:
        vals = [z.mean_air_temp_c for z in self.zones]
        return sum(vals) / len(vals) if vals else None

    def zones_out_of_band(self) -> List[str]:
        return [z.zone for z in self.zones if z.comfort_flag() not in ("comfortable", "unknown")]

    def comfort_action_hint(self) -> str:
        """Turn the worst-zone PMV into an explicit, directional instruction.

        Small local models reliably report PMV but often move the setpoint the
        wrong way. Stating the required direction in the observation itself -
        not just the raw number - removes that failure mode.
        """
        worst = self.worst_pmv()
        if worst is None:
            return "no PMV available; hold setpoints unless energy is clearly excessive"
        if worst < POLICY.pmv_low:
            return (
                f"worst PMV {worst:+.2f} is BELOW {POLICY.pmv_low} (over-cooled): "
                "RAISE cooling_sp - this fixes comfort and saves energy"
            )
        if worst > POLICY.pmv_high:
            return (
                f"worst PMV {worst:+.2f} is ABOVE {POLICY.pmv_high} (too warm): "
                "LOWER cooling_sp to restore comfort"
            )
        return (
            f"worst PMV {worst:+.2f} is inside the comfort band: "
            "raise cooling_sp slightly to save energy, staying under "
            f"{POLICY.pmv_high}"
        )

    def to_prompt_dict(self) -> Dict:
        """Compact, token-cheap representation. Full precision is never needed."""
        return {
            "timestamp": self.timestamp,
            "outdoor_air_temp_c": round(self.outdoor_air_temp_c, 1),
            "outdoor_rh_pct": round(self.outdoor_rh_pct, 0) if self.outdoor_rh_pct is not None else None,
            "occupied": self.occupied,
            "hvac_power_kw": round(self.hvac_power_w / 1000.0, 2),
            "interval_energy_kwh": round(self.interval_energy_kwh, 3),
            "grid_carbon_intensity_g_per_kwh": round(self.grid_carbon_intensity_g_per_kwh, 0),
            "active_setpoints_c": {
                "cooling": round(self.active_cooling_setpoint_c, 2),
                "heating": round(self.active_heating_setpoint_c, 2),
            },
            "worst_pmv": round(self.worst_pmv(), 2) if self.worst_pmv() is not None else None,
            "comfort_action_hint": self.comfort_action_hint(),
            "zones_out_of_comfort_band": self.zones_out_of_band(),
            "zones": [
                {
                    "zone": z.zone,
                    "air_temp_c": round(z.mean_air_temp_c, 1),
                    "rh_pct": round(z.relative_humidity_pct, 0) if z.relative_humidity_pct is not None else None,
                    "pmv": round(z.pmv, 2) if z.pmv is not None else None,
                    "status": z.comfort_flag(),
                }
                for z in self.zones
            ],
        }


# --------------------------------------------------------------------------- #
# Commands (agent -> simulation)
# --------------------------------------------------------------------------- #
class SetpointCommand(BaseModel):
    """A validated actuator command. Construction failure == rejected command."""

    cooling_sp: float = Field(..., description="Zone cooling setpoint, degC")
    heating_sp: float = Field(..., description="Zone heating setpoint, degC")
    reason: str = Field("", max_length=400)
    confidence: float = Field(1.0, ge=0.0, le=1.0)

    @field_validator("cooling_sp")
    @classmethod
    def _cool_range(cls, v: float) -> float:
        if not (POLICY.cooling_sp_min <= v <= POLICY.cooling_sp_max):
            raise ValueError(
                f"cooling_sp {v} outside allowed range "
                f"[{POLICY.cooling_sp_min}, {POLICY.cooling_sp_max}] degC"
            )
        return round(float(v), 2)

    @field_validator("heating_sp")
    @classmethod
    def _heat_range(cls, v: float) -> float:
        if not (POLICY.heating_sp_min <= v <= POLICY.heating_sp_max):
            raise ValueError(
                f"heating_sp {v} outside allowed range "
                f"[{POLICY.heating_sp_min}, {POLICY.heating_sp_max}] degC"
            )
        return round(float(v), 2)

    @model_validator(mode="after")
    def _deadband(self) -> "SetpointCommand":
        if self.cooling_sp - self.heating_sp < POLICY.min_deadband:
            raise ValueError(
                f"deadband {self.cooling_sp - self.heating_sp:.2f} degC is below the "
                f"{POLICY.min_deadband} degC minimum (would cause simultaneous "
                "heating and cooling)"
            )
        return self

    # -- rate limiting ------------------------------------------------------
    def rate_limited(self, previous: Optional["SetpointCommand"]) -> "SetpointCommand":
        """Clamp movement relative to the previous accepted command."""
        if previous is None:
            return self
        step = POLICY.max_step_per_interval
        cool = min(max(self.cooling_sp, previous.cooling_sp - step), previous.cooling_sp + step)
        heat = min(max(self.heating_sp, previous.heating_sp - step), previous.heating_sp + step)
        if cool - heat < POLICY.min_deadband:
            heat = cool - POLICY.min_deadband
            heat = min(max(heat, POLICY.heating_sp_min), POLICY.heating_sp_max)
            cool = max(cool, heat + POLICY.min_deadband)
        return SetpointCommand(
            cooling_sp=cool,
            heating_sp=heat,
            reason=self.reason,
            confidence=self.confidence,
        )

    @classmethod
    def baseline(cls, reason: str = "fallback to baseline schedule") -> "SetpointCommand":
        return cls(
            cooling_sp=POLICY.baseline_cooling_sp,
            heating_sp=POLICY.baseline_heating_sp,
            reason=reason,
            confidence=0.0,
        )


class AgentDecision(BaseModel):
    """Everything worth logging about one agent turn."""

    timestamp: str
    sim_minutes: float
    command: SetpointCommand
    source: str = Field("llm", description="llm | fallback | hold | baseline")
    latency_s: float = 0.0
    tool_calls: List[str] = Field(default_factory=list)
    model: str = ""
    error: Optional[str] = None
    state_digest: Dict = Field(default_factory=dict)
