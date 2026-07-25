"""
Module 2 - the FastMCP server.

These are the only affordances the LLM has on the building.  The server is the
security boundary: `apply_hvac_setpoints` validates every command against
`ControlPolicy` before it can ever reach `set_actuator_value`, so a hallucinated
"set cooling to 5 C" is rejected at the protocol layer rather than wrecking the
simulation.

The server runs in-process on a daemon thread and reads the same `BUS`
singleton the EnergyPlus callback writes to, so the model always sees the live
building rather than a stale snapshot.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastmcp import FastMCP
from pydantic import ValidationError

from config import POLICY, SETTINGS
from eco_loop.grid import GRID
from eco_loop.schemas import SetpointCommand
from eco_loop.state_bus import BUS

class _DropConnectionResetNoise(logging.Filter):
    """Windows' proactor loop logs a full traceback every time a keep-alive HTTP
    connection is recycled (WinError 10054). It is harmless - the MCP session
    reconnects transparently - but across hundreds of tool calls it buries the
    control-loop output. Drop only that record; let everything else through.

    Installed on the `asyncio` logger because the noise is emitted on uvicorn's
    own event loop, which we do not own and cannot attach a handler to.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        exc = record.exc_info[1] if record.exc_info else None
        if isinstance(exc, (ConnectionResetError, ConnectionAbortedError)):
            return False
        return "WinError 10054" not in record.getMessage()


logging.getLogger("asyncio").addFilter(_DropConnectionResetNoise())

mcp = FastMCP(
    name="eco-loop-building",
    instructions=(
        "Live telemetry and actuation for an EnergyPlus building simulation. "
        "Call get_current_building_state first. Diagnose with read_error_logs "
        "only when a reading looks impossible. Finish by calling "
        "apply_hvac_setpoints exactly once."
    ),
)


# --------------------------------------------------------------------------- #
# Tool 1 - observation
# --------------------------------------------------------------------------- #
@mcp.tool
def get_current_building_state() -> Dict[str, Any]:
    """Return the aggregated sensor readings for the most recent control interval.

    Includes outdoor conditions, per-zone air temperature / relative humidity /
    PMV, whole-facility HVAC electric demand, the currently active setpoints and
    the live grid carbon intensity. This is the primary observation tool - call
    it before deciding anything.
    """
    state = BUS.current_state()
    if state is None:
        return {
            "available": False,
            "reason": "simulation has not completed its first control interval yet",
            "policy": POLICY.as_dict(),
        }
    payload = state.to_prompt_dict()
    payload["available"] = True
    payload["run_label"] = BUS.run_label
    payload.update(BUS.totals())
    return payload


# --------------------------------------------------------------------------- #
# Tool 2 - short-term memory
# --------------------------------------------------------------------------- #
@mcp.tool
def get_recent_history(intervals: int = 4) -> Dict[str, Any]:
    """Return the last `intervals` control-interval snapshots (max 12).

    Use this to see whether the zone is drifting, whether the last setpoint
    change actually moved PMV, and whether demand is trending up or down.
    """
    n = max(1, min(int(intervals), 12))
    history = BUS.history(n)
    return {
        "count": len(history),
        "intervals": [
            {
                "timestamp": s.timestamp,
                "outdoor_air_temp_c": round(s.outdoor_air_temp_c, 1),
                "hvac_power_kw": round(s.hvac_power_w / 1000.0, 2),
                "interval_energy_kwh": round(s.interval_energy_kwh, 3),
                "worst_pmv": round(s.worst_pmv(), 2) if s.worst_pmv() is not None else None,
                "cooling_sp_c": round(s.active_cooling_setpoint_c, 1),
                "heating_sp_c": round(s.active_heating_setpoint_c, 1),
            }
            for s in history
        ],
        "recent_decisions": [
            {
                "timestamp": d.timestamp,
                "cooling_sp": d.command.cooling_sp,
                "heating_sp": d.command.heating_sp,
                "source": d.source,
                "reason": d.command.reason[:160],
            }
            for d in BUS.recent_decisions(n)
        ],
    }


# --------------------------------------------------------------------------- #
# Tool 3 - diagnostics with bounded context cost
# --------------------------------------------------------------------------- #
_MAX_ERR_LINES = 60
_MAX_LINE_CHARS = 220
_TAIL_BYTES_PER_LINE = 256


@mcp.tool
def read_error_logs(lines: int = 20) -> Dict[str, Any]:
    """Tail the live EnergyPlus `eplusout.err` diagnostics file.

    Reads only the last `lines` lines (hard cap 60) by seeking from the end of
    the file, so a multi-megabyte warning storm cannot overflow the context
    window. Safe to call while EnergyPlus still holds the file open; if the file
    is missing or locked this returns a status instead of raising.

    Returns severity counts plus the trimmed tail. Call this when a sensor value
    looks physically impossible or a setpoint change had no effect.
    """
    n = max(1, min(int(lines), _MAX_ERR_LINES))
    path: Optional[Path] = BUS.err_path

    if path is None:
        return {"available": False, "reason": "no simulation run is bound yet", "lines": []}
    if not path.exists():
        return {
            "available": False,
            "reason": f"{path.name} not created yet (EnergyPlus writes it during the run)",
            "lines": [],
        }

    try:
        tail = _tail_file(path, n)
    except OSError as exc:
        # Windows can hold an exclusive handle mid-write; that is not fatal.
        return {"available": False, "reason": f"log temporarily unreadable: {exc}", "lines": []}

    severe = sum(1 for ln in tail if "** Severe  **" in ln or "**  Fatal  **" in ln)
    warnings = sum(1 for ln in tail if "** Warning **" in ln)

    return {
        "available": True,
        "file": path.name,
        "lines_returned": len(tail),
        "severe_or_fatal_in_tail": severe,
        "warnings_in_tail": warnings,
        "lines": [ln[:_MAX_LINE_CHARS] for ln in tail],
    }


def _tail_file(path: Path, n: int) -> List[str]:
    """Read the last `n` lines without loading the whole file."""
    size = path.stat().st_size
    read_bytes = min(size, n * _TAIL_BYTES_PER_LINE + 2048)
    with path.open("rb") as fh:
        if read_bytes < size:
            fh.seek(-read_bytes, os.SEEK_END)
        blob = fh.read(read_bytes)
    text = blob.decode("utf-8", errors="replace")
    if read_bytes < size:
        text = text.split("\n", 1)[-1]  # drop the partial first line
    return [ln.rstrip() for ln in text.splitlines() if ln.strip()][-n:]


# --------------------------------------------------------------------------- #
# Tool 4 - carbon signal
# --------------------------------------------------------------------------- #
@mcp.tool
def get_grid_carbon_forecast(horizon_hours: int = 4) -> Dict[str, Any]:
    """Grid carbon intensity now and for the next `horizon_hours` hours.

    Use this to decide whether to pre-cool during a clean/cheap period so the
    compressor can coast through a dirty evening peak.
    """
    state = BUS.current_state()
    if state is None:
        hour, month = 12.0, 7
    else:
        hh, mm = state.timestamp.split(" ")[1].split(":")
        hour = int(hh) + int(mm) / 60.0
        month = int(state.timestamp.split("-")[0])
    return {
        "now_g_per_kwh": round(GRID.intensity(hour, month), 0),
        "now_regime": GRID.regime(hour, month),
        "forecast": GRID.forecast(hour, month, max(1, min(int(horizon_hours), 8))),
    }


# --------------------------------------------------------------------------- #
# Tool 5 - the control policy the agent must respect
# --------------------------------------------------------------------------- #
@mcp.tool
def get_control_policy() -> Dict[str, Any]:
    """Return the hard limits enforced on any setpoint command.

    Commands outside these bounds are rejected server-side, so read this before
    proposing an aggressive setback.
    """
    return {
        "policy": POLICY.as_dict(),
        "control_interval_minutes": SETTINGS.control_interval_min,
        "note": (
            "Setpoint movement is additionally rate-limited to "
            f"{POLICY.max_step_per_interval} degC per control interval to avoid "
            "thermostat hunting."
        ),
    }


# --------------------------------------------------------------------------- #
# Tool 6 - actuation (the only write path)
# --------------------------------------------------------------------------- #
@mcp.tool
def apply_hvac_setpoints(
    cooling_sp: float,
    heating_sp: float,
    reason: str = "",
) -> Dict[str, Any]:
    """Stage new zone thermostat setpoints for injection into the simulation.

    Validates against the control policy (absolute ranges and the minimum
    heating/cooling deadband). A rejected command leaves the previous setpoints
    active and returns `accepted: false` with the reason - read it, correct the
    values, and call again.

    Args:
        cooling_sp: zone cooling setpoint in degC.
        heating_sp: zone heating setpoint in degC.
        reason: one short sentence on the energy/comfort trade-off being made.
    """
    try:
        cmd = SetpointCommand(
            cooling_sp=float(cooling_sp),
            heating_sp=float(heating_sp),
            reason=(reason or "")[:400],
        )
    except (ValidationError, ValueError, TypeError) as exc:
        return {
            "accepted": False,
            "error": _first_error(exc),
            "policy": POLICY.as_dict(),
            "active_setpoints_c": {
                "cooling": BUS.active_command().cooling_sp,
                "heating": BUS.active_command().heating_sp,
            },
        }

    BUS.stage_command(cmd)
    return {
        "accepted": True,
        "staged_setpoints_c": {"cooling": cmd.cooling_sp, "heating": cmd.heating_sp},
        "deadband_c": round(cmd.cooling_sp - cmd.heating_sp, 2),
        "note": (
            "Staged. The simulation applies this at the next timestep, after "
            f"rate-limiting to {POLICY.max_step_per_interval} degC of movement."
        ),
    }


def _first_error(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        errors = exc.errors()
        if errors:
            msg = errors[0].get("msg", str(exc))
            return msg.replace("Value error, ", "")
    return str(exc)


# --------------------------------------------------------------------------- #
# Local (in-process) dispatch table
# --------------------------------------------------------------------------- #
# `mcp.tool` wraps each function in a FunctionTool; `.fn` is the original
# callable. The orchestrator uses this table when ECOLOOP_TOOL_TRANSPORT=direct,
# which removes the HTTP round-trip from the latency budget.
def _unwrap(tool_obj):
    return getattr(tool_obj, "fn", tool_obj)


TOOL_REGISTRY = {
    "get_current_building_state": _unwrap(get_current_building_state),
    "get_recent_history": _unwrap(get_recent_history),
    "read_error_logs": _unwrap(read_error_logs),
    "get_grid_carbon_forecast": _unwrap(get_grid_carbon_forecast),
    "get_control_policy": _unwrap(get_control_policy),
    "apply_hvac_setpoints": _unwrap(apply_hvac_setpoints),
}


# JSON-Schema tool definitions in OpenAI "tools" format. Kept explicit rather
# than reflected so we control exactly how many tokens the schema costs.
OPENAI_TOOL_SPECS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "get_current_building_state",
            "description": (
                "Aggregated sensor readings for the latest control interval: outdoor "
                "conditions, per-zone temperature/RH/PMV, HVAC electric demand, active "
                "setpoints, grid carbon intensity."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_recent_history",
            "description": "Last N control intervals plus the setpoint decisions taken, to detect drift and the effect of the previous change.",
            "parameters": {
                "type": "object",
                "properties": {
                    "intervals": {"type": "integer", "minimum": 1, "maximum": 12, "default": 4}
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_error_logs",
            "description": "Tail the EnergyPlus eplusout.err diagnostics file (max 60 lines). Use only when a reading looks impossible or a change had no effect.",
            "parameters": {
                "type": "object",
                "properties": {
                    "lines": {"type": "integer", "minimum": 1, "maximum": 60, "default": 20}
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_grid_carbon_forecast",
            "description": "Grid carbon intensity now and for the next few hours; use it to justify pre-cooling before a dirty peak.",
            "parameters": {
                "type": "object",
                "properties": {
                    "horizon_hours": {"type": "integer", "minimum": 1, "maximum": 8, "default": 4}
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_control_policy",
            "description": "The hard setpoint limits, minimum deadband and per-interval rate limit enforced on every command.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "apply_hvac_setpoints",
            "description": "Stage validated cooling/heating setpoints for injection into the running simulation. Call exactly once per turn, last.",
            "parameters": {
                "type": "object",
                "properties": {
                    "cooling_sp": {
                        "type": "number",
                        "description": f"degC, {POLICY.cooling_sp_min}-{POLICY.cooling_sp_max}",
                    },
                    "heating_sp": {
                        "type": "number",
                        "description": f"degC, {POLICY.heating_sp_min}-{POLICY.heating_sp_max}",
                    },
                    "reason": {
                        "type": "string",
                        "description": "One sentence on the energy/comfort trade-off.",
                    },
                },
                "required": ["cooling_sp", "heating_sp"],
            },
        },
    },
]


def call_tool_direct(name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    """In-process tool dispatch, used by the `direct` transport and as the
    fallback when the MCP HTTP session is unavailable."""
    fn = TOOL_REGISTRY.get(name)
    if fn is None:
        return {"error": f"unknown tool '{name}'", "available_tools": sorted(TOOL_REGISTRY)}
    try:
        return fn(**(arguments or {}))
    except TypeError as exc:
        return {"error": f"bad arguments for {name}: {exc}"}
    except Exception as exc:  # pragma: no cover - tools must never crash the loop
        return {"error": f"{name} failed: {exc}"}


# --------------------------------------------------------------------------- #
# Server lifecycle
# --------------------------------------------------------------------------- #
_server_thread: Optional[threading.Thread] = None


def serve_http(
    host: Optional[str] = None,
    port: Optional[int] = None,
    log_level: str = "warning",
    show_banner: bool = False,
) -> None:
    """Blocking run of the MCP server over streamable HTTP.

    `log_level` defaults to "warning" because a full simulation issues hundreds
    of tool calls; per-request uvicorn INFO lines would bury the control-loop
    output. Pass log_level="info" when debugging the transport itself.
    """
    kwargs = {
        "transport": "http",
        "host": host or SETTINGS.mcp_host,
        "port": int(port or SETTINGS.mcp_port),
        "log_level": log_level,
        "show_banner": show_banner,
    }
    try:
        mcp.run(**kwargs)
    except TypeError:
        # Older/newer FastMCP that does not accept these transport kwargs.
        mcp.run(transport="http", host=kwargs["host"], port=kwargs["port"])


def start_server_thread(timeout_s: float = 12.0) -> bool:
    """Start the MCP server on a daemon thread and wait until it accepts TCP.

    Returns True if the server is reachable, False if we should fall back to
    direct in-process dispatch.
    """
    global _server_thread
    if _server_thread is not None and _server_thread.is_alive():
        return True

    def _run() -> None:
        try:
            serve_http()
        except Exception as exc:  # pragma: no cover
            print(f"[mcp] server thread exited: {exc}")

    _server_thread = threading.Thread(target=_run, name="fastmcp-http", daemon=True)
    _server_thread.start()

    import socket

    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if not _server_thread.is_alive():
            return False
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.4)
            if sock.connect_ex((SETTINGS.mcp_host, SETTINGS.mcp_port)) == 0:
                return True
        time.sleep(0.25)
    return False


if __name__ == "__main__":
    # Standalone mode: `python -m eco_loop.mcp_tools` exposes the tools to any
    # MCP client (Claude Desktop, MCP Inspector, ...) for manual poking.
    print(json.dumps(BUS.summary(), indent=2))
    print(f"[mcp] serving on http://{SETTINGS.mcp_host}:{SETTINGS.mcp_port}/mcp")
    serve_http()
