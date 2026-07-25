"""
The shared blackboard between the EnergyPlus callback thread, the FastMCP
server thread and the LLM orchestrator.

EnergyPlus runs its callbacks on the thread that called `run_energyplus`.  The
FastMCP HTTP server runs on its own thread (its own asyncio loop).  Both need a
consistent view of "what is the building doing right now", so all cross-thread
data lives here behind a single re-entrant lock.

Nothing in this module blocks for long: the simulation thread must never wait on
the server thread.
"""
from __future__ import annotations

import json
import threading
from collections import deque
from pathlib import Path
from typing import Deque, Dict, List, Optional

from config import POLICY
from eco_loop.schemas import AgentDecision, BuildingState, SetpointCommand


class BuildingStateBus:
    """Thread-safe, single-writer/many-reader snapshot of the running sim."""

    def __init__(self, history: int = 96) -> None:
        self._lock = threading.RLock()
        self._state: Optional[BuildingState] = None
        self._history: Deque[BuildingState] = deque(maxlen=history)
        self._decisions: Deque[AgentDecision] = deque(maxlen=history)
        self._pending: Optional[SetpointCommand] = None
        self._active: SetpointCommand = SetpointCommand.baseline("initial baseline")
        self._err_path: Optional[Path] = None
        self._run_label: str = "unknown"
        self._decision_log: Optional[Path] = None
        self._cumulative_kwh: float = 0.0
        self._cumulative_carbon_g: float = 0.0

    # -- run wiring ---------------------------------------------------------
    def bind_run(self, label: str, output_dir: Path) -> None:
        with self._lock:
            self._run_label = label
            self._err_path = Path(output_dir) / "eplusout.err"
            self._decision_log = Path(output_dir) / "agent_decisions.jsonl"
            # Truncate: `record_decision` appends, so without this a re-run into
            # an existing output directory would leave the previous run's
            # decisions in the file and the dashboard would chart both.
            try:
                self._decision_log.parent.mkdir(parents=True, exist_ok=True)
                self._decision_log.unlink(missing_ok=True)
            except OSError:
                pass
            self._state = None
            self._history.clear()
            self._decisions.clear()
            self._pending = None
            self._active = SetpointCommand.baseline("initial baseline")
            self._cumulative_kwh = 0.0
            self._cumulative_carbon_g = 0.0

    @property
    def run_label(self) -> str:
        with self._lock:
            return self._run_label

    @property
    def err_path(self) -> Optional[Path]:
        with self._lock:
            return self._err_path

    # -- state (written by the simulation thread) ---------------------------
    def publish_state(self, state: BuildingState) -> None:
        with self._lock:
            self._state = state
            self._history.append(state)
            self._cumulative_kwh += state.interval_energy_kwh
            self._cumulative_carbon_g += state.interval_carbon_g

    def current_state(self) -> Optional[BuildingState]:
        with self._lock:
            return self._state

    def history(self, n: int = 8) -> List[BuildingState]:
        with self._lock:
            return list(self._history)[-n:]

    def totals(self) -> Dict[str, float]:
        with self._lock:
            return {
                "cumulative_kwh": round(self._cumulative_kwh, 4),
                "cumulative_carbon_kg": round(self._cumulative_carbon_g / 1000.0, 4),
            }

    # -- commands (written by MCP tool / orchestrator, read by sim) ---------
    def stage_command(self, cmd: SetpointCommand) -> None:
        """Called by `apply_hvac_setpoints`. Does not touch the simulation yet."""
        with self._lock:
            self._pending = cmd

    def take_pending(self) -> Optional[SetpointCommand]:
        """Simulation thread consumes whatever the agent staged this turn."""
        with self._lock:
            cmd, self._pending = self._pending, None
            return cmd

    def set_active(self, cmd: SetpointCommand) -> None:
        with self._lock:
            self._active = cmd

    def active_command(self) -> SetpointCommand:
        with self._lock:
            return self._active

    # -- decisions ----------------------------------------------------------
    def record_decision(self, decision: AgentDecision) -> None:
        with self._lock:
            self._decisions.append(decision)
            path = self._decision_log
        if path is not None:
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(decision.model_dump(), default=str) + "\n")
            except OSError:
                pass  # logging must never break the control loop

    def recent_decisions(self, n: int = 5) -> List[AgentDecision]:
        with self._lock:
            return list(self._decisions)[-n:]

    # -- diagnostics --------------------------------------------------------
    def summary(self) -> Dict:
        with self._lock:
            state = self._state
            return {
                "run_label": self._run_label,
                "has_state": state is not None,
                "timestamp": state.timestamp if state else None,
                "active_setpoints_c": {
                    "cooling": self._active.cooling_sp,
                    "heating": self._active.heating_sp,
                },
                "decisions_made": len(self._decisions),
                "policy": POLICY.as_dict(),
                **self.totals(),
            }


# Process-wide singleton. The MCP server, the agent and sim_env all import this.
BUS = BuildingStateBus()
