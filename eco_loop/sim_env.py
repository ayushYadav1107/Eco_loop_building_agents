"""
Module 1 - the PyEnergyPlus wrapper.

`EnergyPlusSimulation` owns the EnergyPlus state object, registers the runtime
callbacks, resolves sensor/actuator handles once per environment, aggregates
sub-hourly data into control-interval batches, hands each batch to the cognitive
layer, and writes the returned setpoints back into the running simulation.

Everything inside a callback is defensive: EnergyPlus invokes these functions
from C, and an escaping Python exception aborts the run.  A failed timestep
degrades to "hold the last accepted command", never to a crash.
"""
from __future__ import annotations

import statistics
import threading
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional

import config
from config import POLICY, SETTINGS
from eco_loop.comfort import clo_for_season, safe_pmv
from eco_loop.grid import GRID
from eco_loop.schemas import AgentDecision, BuildingState, SetpointCommand, ZoneObservation
from eco_loop.state_bus import BUS

DecideFn = Callable[[BuildingState], AgentDecision]

# EnergyPlus KindOfSim value for a weather-file run period. Verified empirically
# against EnergyPlus 26.1 rather than taken from the enum header, since
# pyenergyplus does not expose the enum and its docstring for `kind_of_sim` is a
# copy-paste of `current_environment_num`.
KIND_RUNPERIOD_WEATHER = 3

# Approximate PMV change per degC of zone temperature, used to size the
# per-zone trim. Same figure the observation layer uses for its suggestions.
_PMV_PER_DEGC = 0.3


# --------------------------------------------------------------------------- #
# Handle bookkeeping
# --------------------------------------------------------------------------- #
# Variable names moved around across EnergyPlus versions; try each in order.
FACILITY_HVAC_POWER_CANDIDATES = [
    ("Facility Total HVAC Electricity Demand Rate", "Whole Building"),
    ("Facility Total HVAC Electric Demand Power", "Whole Building"),
]
FACILITY_TOTAL_POWER_CANDIDATES = [
    ("Facility Total Electricity Demand Rate", "Whole Building"),
    ("Facility Total Electric Demand Power", "Whole Building"),
]
SITE_VARIABLES = {
    "oat": [("Site Outdoor Air Drybulb Temperature", "Environment")],
    "orh": [("Site Outdoor Air Relative Humidity", "Environment")],
    "solar": [("Site Direct Solar Radiation Rate per Area", "Environment")],
}
ZONE_VARIABLES = {
    "air_temp": "Zone Mean Air Temperature",
    "radiant_temp": "Zone Mean Radiant Temperature",
    "rh": "Zone Air Relative Humidity",
    "cool_sp": "Zone Thermostat Cooling Setpoint Temperature",
    "heat_sp": "Zone Thermostat Heating Setpoint Temperature",
    "occupants": "Zone People Occupant Count",
}


@dataclass
class _Accumulator:
    """Per-interval rolling aggregation. Cheap enough to run every timestep."""

    samples: int = 0
    hvac_power_w: List[float] = field(default_factory=list)
    facility_power_w: List[float] = field(default_factory=list)
    oat: List[float] = field(default_factory=list)
    orh: List[float] = field(default_factory=list)
    solar: List[float] = field(default_factory=list)
    zones: Dict[str, Dict[str, List[float]]] = field(default_factory=dict)
    minutes: float = 0.0

    def reset(self) -> None:
        self.samples = 0
        self.hvac_power_w.clear()
        self.facility_power_w.clear()
        self.oat.clear()
        self.orh.clear()
        self.solar.clear()
        self.zones.clear()
        self.minutes = 0.0

    def push_zone(self, zone: str, key: str, value: Optional[float]) -> None:
        if value is None:
            return
        self.zones.setdefault(zone, {}).setdefault(key, []).append(value)


def _mean(values: List[float], default: float = 0.0) -> float:
    return statistics.fmean(values) if values else default


# --------------------------------------------------------------------------- #
# Simulation wrapper
# --------------------------------------------------------------------------- #
class EnergyPlusSimulation:
    """One EnergyPlus run, optionally supervised by the LLM agent."""

    def __init__(
        self,
        idf: Path,
        epw: Path,
        output_dir: Path,
        label: str = "ai",
        decide_fn: Optional[DecideFn] = None,
        control_interval_min: Optional[int] = None,
        zones: Optional[List[str]] = None,
        non_blocking: Optional[bool] = None,
        design_day_only: Optional[bool] = None,
        verbose: bool = False,
    ) -> None:
        config.bootstrap_energyplus()
        from pyenergyplus.api import EnergyPlusAPI  # noqa: E402  (needs sys.path)

        self.idf = Path(idf)
        self.epw = Path(epw)
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.label = label
        self.decide_fn = decide_fn
        self.control_interval_min = int(control_interval_min or SETTINGS.control_interval_min)
        self.zones = list(zones or SETTINGS.zones())
        self.non_blocking = SETTINGS.non_blocking_agent if non_blocking is None else non_blocking
        self.design_day_only = SETTINGS.design_day_only if design_day_only is None else design_day_only
        self.verbose = verbose

        self.api = EnergyPlusAPI()
        self.state = self.api.state_manager.new_state()

        # Handle caches (re-resolved at each new environment).
        self._handles_ready = False
        self._var_handles: Dict[str, int] = {}
        self._zone_var_handles: Dict[str, Dict[str, int]] = {}
        self._pmv_handles: Dict[str, int] = {}
        self._cool_act: Dict[str, int] = {}
        self._heat_act: Dict[str, int] = {}

        self._acc = _Accumulator()
        self._minutes_per_step = 60.0 / 4.0  # refreshed once data is ready
        self._sim_minutes = 0.0
        self._interval_index = 0

        self._active_cmd = SetpointCommand.baseline("initial baseline")
        self._zone_trim: Dict[str, float] = {}
        # Hours of the day observed to be occupied so far. Populated as the run
        # proceeds, then used to anticipate the next occupied period (optimal
        # start). Day 1's morning is necessarily unanticipated - there is no
        # history yet - which is exactly how a real BMS behaves on commissioning.
        self._occupied_hours: set = set()
        self._pending_future = None
        self._executor = None

        self.stats = {
            "intervals": 0,
            "llm_decisions": 0,
            "fallbacks": 0,
            "timesteps": 0,
            "actuation_errors": 0,
        }

    # ------------------------------------------------------------------ #
    # Public entry point
    # ------------------------------------------------------------------ #
    def run(self) -> int:
        """Execute the simulation. Returns the EnergyPlus exit code (0 == ok)."""
        if not self.idf.exists():
            raise FileNotFoundError(f"IDF not found: {self.idf}")
        if not self.epw.exists():
            raise FileNotFoundError(f"EPW not found: {self.epw}")

        BUS.bind_run(self.label, self.output_dir)
        BUS.set_active(self._active_cmd)

        if self.non_blocking and self.decide_fn is not None:
            from concurrent.futures import ThreadPoolExecutor

            self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="agent")

        self.api.runtime.set_console_output_status(self.state, self.verbose)
        self._register_callbacks()
        self._request_variables()

        argv = [
            "-d", str(self.output_dir),
            "-w", str(self.epw),
            "-x",           # run ExpandObjects (HVACTemplate support)
            "-r",           # run ReadVarsESO -> eplusout.csv for the dashboard
        ]
        if self.design_day_only:
            # Native EnergyPlus CLI flag: simulate only the SizingPeriod:DesignDay
            # objects (1-2 simulated days) instead of the full annual RunPeriod.
            # Fast, deterministic, and needs no IDF editing.
            argv.append("-D")
        argv.append(str(self.idf))

        t0 = time.time()
        exit_code = self.api.runtime.run_energyplus(self.state, argv)
        self.stats["wall_clock_s"] = round(time.time() - t0, 1)

        if self._executor is not None:
            self._executor.shutdown(wait=False, cancel_futures=True)
        self.api.state_manager.delete_state(self.state)
        return exit_code

    # ------------------------------------------------------------------ #
    # Callback registration
    # ------------------------------------------------------------------ #
    def _register_callbacks(self) -> None:
        rt = self.api.runtime
        rt.callback_begin_new_environment(self.state, self._on_new_environment)
        rt.callback_end_zone_timestep_after_zone_reporting(
            self.state, self.callback_end_zone_timestep_after_zone_reporting
        )
        rt.callback_message(self.state, self._on_message)

    def _request_variables(self) -> None:
        """Output variables must be requested before the run to be readable."""
        ex = self.api.exchange
        for candidates in (FACILITY_HVAC_POWER_CANDIDATES, FACILITY_TOTAL_POWER_CANDIDATES):
            for name, key in candidates:
                ex.request_variable(self.state, name, key)
        for candidates in SITE_VARIABLES.values():
            for name, key in candidates:
                ex.request_variable(self.state, name, key)
        for zone in self.zones:
            for var in ZONE_VARIABLES.values():
                ex.request_variable(self.state, var, zone)
        for people in SETTINGS.people_objects():
            ex.request_variable(self.state, "Zone Thermal Comfort Fanger Model PMV", people)

    # ------------------------------------------------------------------ #
    # Handle resolution
    # ------------------------------------------------------------------ #
    def _on_new_environment(self, state) -> None:
        """Handles are only valid within an environment; force a re-resolve."""
        self._handles_ready = False
        self._var_handles.clear()
        self._zone_var_handles.clear()
        self._pmv_handles.clear()
        self._cool_act.clear()
        self._heat_act.clear()
        self._acc.reset()

    def _first_valid_variable(self, state, candidates) -> int:
        ex = self.api.exchange
        for name, key in candidates:
            handle = ex.get_variable_handle(state, name, key)
            if handle >= 0:
                return handle
        return -1

    def _resolve_handles(self, state) -> bool:
        ex = self.api.exchange
        if not ex.api_data_fully_ready(state):
            return False

        try:
            self._minutes_per_step = 60.0 / max(1, ex.num_time_steps_in_hour(state))
        except Exception:
            self._minutes_per_step = 15.0

        self._var_handles["hvac_power"] = self._first_valid_variable(
            state, FACILITY_HVAC_POWER_CANDIDATES
        )
        self._var_handles["facility_power"] = self._first_valid_variable(
            state, FACILITY_TOTAL_POWER_CANDIDATES
        )
        for key, candidates in SITE_VARIABLES.items():
            self._var_handles[key] = self._first_valid_variable(state, candidates)

        for zone in self.zones:
            per_zone: Dict[str, int] = {}
            for key, var in ZONE_VARIABLES.items():
                per_zone[key] = ex.get_variable_handle(state, var, zone)
            self._zone_var_handles[zone] = per_zone

            # Actuators: EMS "Zone Temperature Control" overrides the thermostat.
            self._cool_act[zone] = ex.get_actuator_handle(
                state, "Zone Temperature Control", "Cooling Setpoint", zone
            )
            self._heat_act[zone] = ex.get_actuator_handle(
                state, "Zone Temperature Control", "Heating Setpoint", zone
            )

        for people in SETTINGS.people_objects():
            h = ex.get_variable_handle(
                state, "Zone Thermal Comfort Fanger Model PMV", people
            )
            if h >= 0:
                self._pmv_handles[people] = h

        missing_actuators = [z for z in self.zones if self._cool_act.get(z, -1) < 0]
        if missing_actuators and self.decide_fn is not None:
            self._log(
                "WARNING: no 'Zone Temperature Control' actuator for "
                f"{missing_actuators}. Those zones need a ZoneControl:Thermostat."
            )

        self._handles_ready = True
        self._log(
            f"[{self.label}] handles resolved | zones={len(self.zones)} "
            f"| timestep={self._minutes_per_step:.0f} min "
            f"| control interval={self.control_interval_min} min"
        )
        return True

    def _in_scoring_environment(self, state) -> bool:
        """True only for the environment whose results we actually report.

        When a model has `SimulationControl / Run Simulation for Sizing Periods
        = Yes`, EnergyPlus replays the design days as ordinary simulations and
        this callback fires for them too. Letting the agent actuate during
        sizing would both waste LLM calls and risk perturbing autosized
        equipment capacities - which would make the AI run's equipment differ
        from the baseline's and invalidate the comparison.

        KIND_RUNPERIOD_WEATHER == 3 was verified empirically against
        EnergyPlus 26.1 (a weather-file run reports kind_of_sim == 3 for every
        non-warmup timestep). In `-D` mode only design days run at all, so no
        filtering is needed or wanted.
        """
        if self.design_day_only:
            return True
        try:
            return self.api.exchange.kind_of_sim(state) == KIND_RUNPERIOD_WEATHER
        except Exception:
            return True  # unknown API shape: do not silently skip the whole run

    def _value(self, state, handle: int) -> Optional[float]:
        if handle is None or handle < 0:
            return None
        try:
            return float(self.api.exchange.get_variable_value(state, handle))
        except Exception:
            return None

    # ------------------------------------------------------------------ #
    # The main callback
    # ------------------------------------------------------------------ #
    def callback_end_zone_timestep_after_zone_reporting(self, state) -> None:
        """Called by EnergyPlus at the end of every zone timestep.

        Responsibilities, in order:
          1. skip warmup / sizing periods,
          2. sample every sensor into the interval accumulator,
          3. re-assert the currently active actuator values (they do not latch),
          4. once per control interval, build a `BuildingState` and ask the agent.
        """
        try:
            ex = self.api.exchange
            if ex.warmup_flag(state):
                return
            if not self._in_scoring_environment(state):
                return
            if not self._handles_ready and not self._resolve_handles(state):
                return

            self.stats["timesteps"] += 1
            self._sim_minutes += self._minutes_per_step
            self._acc.minutes += self._minutes_per_step
            self._acc.samples += 1

            self._sample(state)
            self._actuate(state, self._active_cmd)

            if self._acc.minutes + 1e-6 >= self.control_interval_min:
                self._on_control_interval(state)

        except Exception:
            # Never let an exception cross back into the EnergyPlus C runtime.
            self.stats["actuation_errors"] += 1
            self._log("callback error:\n" + traceback.format_exc())

    # ------------------------------------------------------------------ #
    # Sampling / actuation
    # ------------------------------------------------------------------ #
    def _sample(self, state) -> None:
        acc = self._acc
        hvac = self._value(state, self._var_handles.get("hvac_power", -1))
        facility = self._value(state, self._var_handles.get("facility_power", -1))
        if hvac is not None:
            acc.hvac_power_w.append(hvac)
        if facility is not None:
            acc.facility_power_w.append(facility)
        for key, bucket in (("oat", acc.oat), ("orh", acc.orh), ("solar", acc.solar)):
            v = self._value(state, self._var_handles.get(key, -1))
            if v is not None:
                bucket.append(v)

        for zone, handles in self._zone_var_handles.items():
            for key, handle in handles.items():
                acc.push_zone(zone, key, self._value(state, handle))

        # Native EnergyPlus PMV, when the model defines a Fanger comfort People object.
        for people, handle in self._pmv_handles.items():
            v = self._value(state, handle)
            if v is not None:
                acc.push_zone(people, "eplus_pmv", v)

    def _update_zone_trims(self, building_state: BuildingState) -> None:
        """Recompute each zone's band offset from its own PMV.

        The agent chooses one building-wide setpoint pair; this closes the gap
        between that pair and what each individual zone actually needs. Zones
        differ in orientation, envelope exposure and internal gain, so a single
        pair leaves the hardest zone out of band even when the average zone is
        comfortable (measured: per-zone 81-99% in band, worst-zone 67%).

        Deliberately a plain proportional loop, not another LLM call: it runs
        per zone per interval, needs no reasoning, and keeping it out of the
        prompt leaves the model's job exactly as simple as before.
        """
        if not building_state.occupied and not building_state.pre_occupancy:
            # Unoccupied: no comfort target to chase, so apply a uniform
            # setback and let the trims decay away rather than fighting it.
            # Pre-occupancy is excluded - that is precisely when the trims need
            # to already be working.
            self._zone_trim = {z: 0.0 for z in self._zone_trim}
            return

        for zone_obs in building_state.zones:
            if zone_obs.pmv is None:
                continue
            target = -zone_obs.pmv / _PMV_PER_DEGC
            target = max(-POLICY.max_zone_trim_c, min(POLICY.max_zone_trim_c, target))
            previous = self._zone_trim.get(zone_obs.zone, 0.0)
            # Partial step toward the target damps oscillation between intervals.
            self._zone_trim[zone_obs.zone] = previous + POLICY.zone_trim_gain * (target - previous)

    def _actuate(self, state, cmd: SetpointCommand) -> None:
        """Setpoint actuators must be re-written every timestep to stay overridden."""
        if self.decide_fn is None:
            return  # baseline run: leave the native thermostat schedules alone
        ex = self.api.exchange
        for zone in self.zones:
            ch = self._cool_act.get(zone, -1)
            hh = self._heat_act.get(zone, -1)
            trim = self._zone_trim.get(zone, 0.0)
            zone_cmd = cmd.shifted(trim) if trim else cmd
            if ch >= 0:
                ex.set_actuator_value(state, ch, zone_cmd.cooling_sp)
            if hh >= 0:
                ex.set_actuator_value(state, hh, zone_cmd.heating_sp)

    # ------------------------------------------------------------------ #
    # Control interval: build state, consult the agent, commit the command
    # ------------------------------------------------------------------ #
    def _on_control_interval(self, state) -> None:
        building_state = self._build_state(state)
        self._acc.reset()
        self._interval_index += 1
        self.stats["intervals"] += 1

        BUS.publish_state(building_state)

        if self.decide_fn is None:
            return  # baseline run is observation-only

        # Local loop first: per-zone trims are refreshed every interval from
        # live PMV, independently of whether the agent turn succeeds. If the
        # LLM times out and we fall back, zone-level comfort correction keeps
        # working on the last accepted building-wide command.
        self._update_zone_trims(building_state)

        decision = self._consult_agent(building_state)
        if decision is None:
            return  # non-blocking mode, previous command stays active

        # An MCP `apply_hvac_setpoints` call takes precedence: it is the tool the
        # model actually invoked, already validated server-side.
        staged = BUS.take_pending()
        command = staged or decision.command
        # Seasonal changeover first (park the idle mode's setpoint), then slew
        # limiting, so the parked value is not itself ramped in slowly.
        # Pre-occupancy counts as occupied for parking, so the comfort floor is
        # already in place when people walk in rather than starting to move then.
        command = command.seasonal_park(
            building_state.active_mode(),
            building_state.occupied or building_state.pre_occupancy,
        )
        command = command.rate_limited(self._active_cmd, self.control_interval_min)

        self._active_cmd = command
        BUS.set_active(command)
        decision.command = command
        BUS.record_decision(decision)

        if decision.source == "llm":
            self.stats["llm_decisions"] += 1
        else:
            self.stats["fallbacks"] += 1

        self._log(
            f"[{self.label} {building_state.timestamp}] "
            f"OAT={building_state.outdoor_air_temp_c:5.1f}C "
            f"PMV={building_state.worst_pmv() if building_state.worst_pmv() is None else round(building_state.worst_pmv(), 2)} "
            f"kW={building_state.hvac_power_w / 1000:5.2f} "
            f"-> cool={command.cooling_sp:.1f} heat={command.heating_sp:.1f} "
            f"({decision.source}, {decision.latency_s:.1f}s)"
        )

    def _consult_agent(self, building_state: BuildingState) -> Optional[AgentDecision]:
        """Blocking (default) or fire-and-forget agent invocation.

        Blocking mode is what the brief asks for: a hard timeout inside
        `agent_orchestrator` guarantees the callback returns in bounded time and
        falls back to the baseline setpoints if the model is slow.

        Non-blocking mode keeps the simulation running at full speed and applies
        each decision at the *next* interval - the last accepted command stays
        active meanwhile.
        """
        if not self.non_blocking:
            return self.decide_fn(building_state)

        fut = self._pending_future
        result: Optional[AgentDecision] = None
        if fut is not None and fut.done():
            try:
                result = fut.result()
            except Exception as exc:  # pragma: no cover - defensive
                self._log(f"async agent failed: {exc}")
            self._pending_future = None

        if self._pending_future is None:
            self._pending_future = self._executor.submit(self.decide_fn, building_state)
        return result

    # ------------------------------------------------------------------ #
    # State assembly
    # ------------------------------------------------------------------ #
    def _build_state(self, state) -> BuildingState:
        ex = self.api.exchange
        acc = self._acc

        month = int(ex.month(state))
        day = int(ex.day_of_month(state))
        hour = int(ex.hour(state))
        minute = int(ex.minutes(state))
        # EnergyPlus reports minute 60 at the end of an hour; normalise it.
        if minute >= 60:
            minute, hour = 0, (hour + 1) % 24
        timestamp = f"{month:02d}-{day:02d} {hour:02d}:{minute:02d}"
        hour_frac = hour + minute / 60.0

        clo = clo_for_season(month)
        zone_obs: List[ZoneObservation] = []
        for zone in self.zones:
            bucket = acc.zones.get(zone, {})
            air = _mean(bucket.get("air_temp", []), default=float("nan"))
            if air != air:  # NaN -> zone produced no data this interval
                continue
            mrt = _mean(bucket.get("radiant_temp", [])) if bucket.get("radiant_temp") else None
            rh = _mean(bucket.get("rh", [])) if bucket.get("rh") else None

            native_pmv = bucket.get("eplus_pmv")
            if native_pmv:
                pmv, ppd = _mean(native_pmv), None
            else:
                pmv, ppd = safe_pmv(
                    air, mrt, rh,
                    vel=SETTINGS.air_velocity,
                    met=SETTINGS.met_rate,
                    clo=clo,
                )

            zone_obs.append(
                ZoneObservation(
                    zone=zone,
                    mean_air_temp_c=air,
                    mean_radiant_temp_c=mrt,
                    relative_humidity_pct=rh,
                    pmv=pmv,
                    ppd_pct=ppd,
                    cooling_setpoint_c=_mean(bucket["cool_sp"]) if bucket.get("cool_sp") else None,
                    heating_setpoint_c=_mean(bucket["heat_sp"]) if bucket.get("heat_sp") else None,
                    occupant_count=_mean(bucket["occupants"]) if bucket.get("occupants") else None,
                )
            )

        hvac_w = _mean(acc.hvac_power_w)
        interval_h = acc.minutes / 60.0
        kwh = hvac_w * interval_h / 1000.0
        intensity = GRID.intensity(hour_frac, month)
        occupants = sum(z.occupant_count or 0.0 for z in zone_obs)

        occupied_now = (
            occupants > 0.5
            if zone_obs and any(z.occupant_count is not None for z in zone_obs)
            else 7 <= hour < 19
        )
        if occupied_now:
            self._occupied_hours.add(hour)
        # Optimal start: look one and two hours ahead against the occupancy
        # pattern learned so far. Two hours is the warm-up time the slew limit
        # allows from a deep setback (3 degC/h over a ~6 degC setback).
        pre_occupancy = (not occupied_now) and any(
            (hour + ahead) % 24 in self._occupied_hours for ahead in (1, 2)
        )

        return BuildingState(
            timestamp=timestamp,
            sim_minutes=self._sim_minutes,
            outdoor_air_temp_c=_mean(acc.oat),
            outdoor_rh_pct=_mean(acc.orh) if acc.orh else None,
            direct_solar_w_m2=_mean(acc.solar) if acc.solar else None,
            hvac_power_w=hvac_w,
            facility_power_w=_mean(acc.facility_power_w) if acc.facility_power_w else None,
            interval_energy_kwh=kwh,
            grid_carbon_intensity_g_per_kwh=intensity,
            interval_carbon_g=kwh * intensity,
            occupied=occupied_now,
            pre_occupancy=pre_occupancy,
            hour_of_day=hour_frac,
            zones=zone_obs,
            active_cooling_setpoint_c=self._active_cmd.cooling_sp,
            active_heating_setpoint_c=self._active_cmd.heating_sp,
        )

    # ------------------------------------------------------------------ #
    # Misc
    # ------------------------------------------------------------------ #
    def _on_message(self, message: bytes) -> None:
        if not self.verbose:
            return
        try:
            text = message.decode("utf-8", errors="replace").strip()
        except Exception:
            return
        if text:
            print(f"  E+ | {text}")

    def _log(self, msg: str) -> None:
        print(msg, flush=True)


# --------------------------------------------------------------------------- #
# Convenience runners used by main.py
# --------------------------------------------------------------------------- #
def run_baseline(idf: Path, epw: Path, output_dir: Path, **kwargs) -> EnergyPlusSimulation:
    sim = EnergyPlusSimulation(idf, epw, output_dir, label="baseline", decide_fn=None, **kwargs)
    code = sim.run()
    sim.stats["exit_code"] = code
    return sim


def run_ai(idf: Path, epw: Path, output_dir: Path, decide_fn: DecideFn, **kwargs) -> EnergyPlusSimulation:
    sim = EnergyPlusSimulation(idf, epw, output_dir, label="ai", decide_fn=decide_fn, **kwargs)
    code = sim.run()
    sim.stats["exit_code"] = code
    return sim
