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

# A zone within this many degC of a setpoint is being actively held there.
_SETPOINT_PIN_C = 0.7
# Outdoor temperature above which a free-floating zone will drift warm.
_COOLING_OAT_C = 18.0
# Approximate PMV change per degC of zone temperature (ISO 7730, this building
# and its clothing/metabolic assumptions). Used only to suggest a target
# setpoint to the agent - the actual result is always re-measured next interval.
_PMV_PER_DEGC = 0.3
# Cap on a single suggested correction, so one bad PMV reading cannot provoke a
# large swing. The slew limiter enforces the real bound regardless.
_MAX_SUGGESTED_STEP_C = 3.0


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
    # True when the building is still empty but occupancy is expected shortly,
    # learned from previously observed occupied hours. Drives optimal start:
    # a purely reactive controller only begins warming when people arrive, so
    # the first occupied hour is always uncomfortable.
    pre_occupancy: bool = False
    hour_of_day: float = 12.0
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

    def active_mode(self) -> str:
        """Which setpoint is currently the binding lever: 'heating' or 'cooling'.

        Decided from where the zones actually sit relative to the two
        setpoints, not from the calendar - a cold snap in shoulder season puts
        the building in heating mode regardless of the month.
        """
        mz = self.mean_zone_temp()
        # Strongest signal: the zone is pinned to one of the setpoints, which
        # means that setpoint's equipment is actively controlling it.
        if mz is not None:
            if abs(mz - self.active_cooling_setpoint_c) <= _SETPOINT_PIN_C:
                return "cooling"
            if abs(mz - self.active_heating_setpoint_c) <= _SETPOINT_PIN_C:
                return "heating"
        # Otherwise the zone is floating inside the deadband with neither
        # setpoint binding, so outdoor conditions decide which way it will
        # drift and therefore which lever matters next. Nearest-setpoint
        # distance is deliberately NOT used here: a zone sitting at 22 C on a
        # 31 C day is closer to the heating setpoint but is plainly being
        # over-cooled, and advising heating_sp there is exactly backwards.
        return "cooling" if self.outdoor_air_temp_c >= _COOLING_OAT_C else "heating"

    def comfort_action_hint(self) -> str:
        """Turn the worst-zone PMV into an explicit, directional instruction.

        Small local models reliably report PMV but often move the wrong
        setpoint, or the right one in the wrong direction. Naming the exact
        lever and direction in the observation itself removes both failure
        modes. Two things drive the wording:

        * which setpoint is binding (`active_mode`) - in heating season the
          lever is `heating_sp`, and advice phrased only in terms of
          `cooling_sp` is useless or actively harmful;
        * whether anyone is present - PMV is not a constraint in an empty
          building, and treating it as one suppresses the night setback that
          is the single largest source of savings.
        """
        worst = self.worst_pmv()
        mode = self.active_mode()
        lever = "heating_sp" if mode == "heating" else "cooling_sp"

        if self.pre_occupancy:
            return (
                f"building is empty but OCCUPANCY STARTS SHORTLY ({mode} mode): "
                "pre-condition now so the space is comfortable when people "
                "arrive. Move setpoints to occupied-comfort values this turn - "
                "recovering after they arrive is always too late."
            )

        if not self.occupied:
            return (
                f"building is UNOCCUPIED ({mode} mode): comfort is not binding. "
                f"Set back aggressively - lower heating_sp and raise cooling_sp "
                "toward the policy limits to save energy. Recover in time for "
                "the next occupied period."
            )

        if worst is None:
            return f"occupied, no PMV available ({mode} mode); hold setpoints"

        if worst < POLICY.pmv_low or worst > POLICY.pmv_high:
            # Give a concrete target, not just a direction. A small local model
            # reads "RAISE heating_sp" and moves it 0.3 degC, which is far too
            # little to clear a -0.8 PMV deficit; naming the number it should
            # reach converts the instruction into something it can execute.
            # Aim for PMV ~0 (band centre) rather than the edge, so normal
            # drift does not immediately push back out of band.
            delta_c = -worst / _PMV_PER_DEGC
            delta_c = max(-_MAX_SUGGESTED_STEP_C, min(_MAX_SUGGESTED_STEP_C, delta_c))
            too_cold = worst < POLICY.pmv_low
            if mode == "heating":
                target = self.active_heating_setpoint_c + delta_c
                target = round(min(max(target, POLICY.heating_sp_min), POLICY.heating_sp_max), 1)
                verb, lever_name = ("RAISE", "heating_sp") if too_cold else ("LOWER", "heating_sp")
                bonus = "" if too_cold else " - this fixes comfort AND saves energy"
            else:
                target = self.active_cooling_setpoint_c + delta_c
                target = round(min(max(target, POLICY.cooling_sp_min), POLICY.cooling_sp_max), 1)
                verb, lever_name = ("RAISE", "cooling_sp") if too_cold else ("LOWER", "cooling_sp")
                bonus = " - this fixes comfort AND saves energy" if too_cold else ""
            state_word = "too cold" if too_cold else "too warm"
            return (
                f"occupied and worst PMV {worst:+.2f} is outside "
                f"[{POLICY.pmv_low}, {POLICY.pmv_high}] ({state_word}, {mode} mode): "
                f"{verb} {lever_name} to about {target:.1f} C this turn{bonus}"
            )

        # Hysteresis: only trim when there is real margin. Trimming whenever PMV
        # merely sits inside the band makes the agent optimise straight to the
        # boundary and then oscillate across it - which scores an excellent mean
        # PMV and a poor percentage-in-band.
        margin = min(worst - POLICY.pmv_low, POLICY.pmv_high - worst)
        if margin < POLICY.comfort_trim_margin:
            edge = "cold" if worst < 0 else "warm"
            return (
                f"occupied, worst PMV {worst:+.2f} is only {margin:.2f} from the "
                f"{edge} edge of the comfort band ({mode} mode): HOLD the current "
                "setpoints. Do not trim energy here - you would push occupants "
                "out of the band for a negligible saving."
            )
        return (
            f"occupied and worst PMV {worst:+.2f} is comfortably inside the band "
            f"with {margin:.2f} margin ({mode} mode): safe to trim energy by "
            f"nudging {lever} toward the efficient side, keeping "
            f"{POLICY.comfort_trim_margin:.1f} margin in hand"
        )

    def to_prompt_dict(self) -> Dict:
        """Compact, token-cheap representation. Full precision is never needed."""
        return {
            "timestamp": self.timestamp,
            "outdoor_air_temp_c": round(self.outdoor_air_temp_c, 1),
            "outdoor_rh_pct": round(self.outdoor_rh_pct, 0) if self.outdoor_rh_pct is not None else None,
            "occupied": self.occupied,
            "occupancy_starts_soon": self.pre_occupancy,
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

    # -- seasonal changeover ------------------------------------------------
    def seasonal_park(self, mode: str, occupied: bool = True) -> "SetpointCommand":
        """Park the non-binding setpoint so the two modes cannot fight.

        Without this the agent controls both setpoints independently and can
        pull the cooling setpoint down to 22 C in January - where solar and
        internal gains then trigger mechanical cooling in the middle of the
        heating season, burning energy to undo the heating it is simultaneously
        paying for. Measured cost of exactly that failure: winter energy 7%
        *worse* than baseline.

        Real BMS solve this with seasonal changeover, and so do we: whichever
        mode is active keeps its setpoint under agent control, and the other is
        pinned to its policy extreme so its equipment stays off.
        """
        if mode == "heating":
            # Park cooling high so the chiller cannot start in heating season.
            cool = POLICY.cooling_sp_max if not occupied else POLICY.occupied_cooling_ceiling
            heat = min(self.heating_sp, cool - POLICY.min_deadband)
        else:
            # Park heating low, but keep a comfort floor while people are in.
            heat = POLICY.heating_sp_min if not occupied else POLICY.occupied_heating_floor
            heat = max(min(heat, POLICY.heating_sp_max), POLICY.heating_sp_min)
            cool = max(self.cooling_sp, heat + POLICY.min_deadband)
            cool = min(cool, POLICY.cooling_sp_max)
        return SetpointCommand(
            cooling_sp=cool, heating_sp=heat,
            reason=self.reason, confidence=self.confidence,
        )

    def shifted(self, offset_c: float) -> "SetpointCommand":
        """Shift the whole comfort band by `offset_c`, for one zone.

        Both setpoints move together, so the heating/cooling deadband is
        preserved by construction and the two systems still cannot fight. A
        positive offset (zone is too cold) raises the band: more heating, less
        cooling. Clamping is applied after the shift, with the heating setpoint
        yielding if the pair would otherwise close up below the deadband.
        """
        cool = min(max(self.cooling_sp + offset_c, POLICY.cooling_sp_min), POLICY.cooling_sp_max)
        heat = min(max(self.heating_sp + offset_c, POLICY.heating_sp_min), POLICY.heating_sp_max)
        if cool - heat < POLICY.min_deadband:
            heat = min(max(cool - POLICY.min_deadband, POLICY.heating_sp_min), POLICY.heating_sp_max)
            cool = min(max(heat + POLICY.min_deadband, POLICY.cooling_sp_min), POLICY.cooling_sp_max)
        return SetpointCommand(
            cooling_sp=cool, heating_sp=heat,
            reason=self.reason, confidence=self.confidence,
        )

    # -- rate limiting ------------------------------------------------------
    def rate_limited(
        self,
        previous: Optional["SetpointCommand"],
        interval_minutes: float = 60.0,
    ) -> "SetpointCommand":
        """Clamp movement relative to the previous accepted command.

        The cap scales with the control interval so the permitted ramp rate is
        the same physical degC/hour regardless of how often the agent runs.
        """
        if previous is None:
            return self
        step = max(0.25, POLICY.max_step_per_hour * (interval_minutes / 60.0))
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
