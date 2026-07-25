"""
Central configuration + EnergyPlus bootstrap for Eco-Loop Building Agents.

`pyenergyplus` is not pip-installable: it lives inside the EnergyPlus install
tree.  `bootstrap_energyplus()` locates that tree and puts it on `sys.path` so
that `from pyenergyplus.api import EnergyPlusAPI` works from anywhere in this
repo.  Every module that needs the API calls it first.
"""
from __future__ import annotations

import json
import os
import platform
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

# --------------------------------------------------------------------------- #
# .env loading (optional dependency)
# --------------------------------------------------------------------------- #
ROOT = Path(__file__).resolve().parent

try:  # pragma: no cover - convenience only
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except Exception:  # pragma: no cover
    pass


def _env(name: str, default):
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    if isinstance(default, bool):
        return raw.strip().lower() in ("1", "true", "yes", "on")
    if isinstance(default, int):
        return int(float(raw))
    if isinstance(default, float):
        return float(raw)
    return raw


# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #
ASSETS_DIR = ROOT / "assets"
OUTPUT_DIR = ROOT / "outputs"
BASELINE_DIR = OUTPUT_DIR / "baseline"
AI_DIR = OUTPUT_DIR / "ai"
MODEL_META = ASSETS_DIR / "model_meta.json"

for _d in (ASSETS_DIR, OUTPUT_DIR, BASELINE_DIR, AI_DIR):
    _d.mkdir(parents=True, exist_ok=True)


# --------------------------------------------------------------------------- #
# EnergyPlus discovery
# --------------------------------------------------------------------------- #
_CANDIDATE_GLOBS = {
    "Windows": [r"C:\EnergyPlusV*", r"C:\Program Files\EnergyPlusV*"],
    "Darwin": ["/Applications/EnergyPlus-*"],
    "Linux": ["/usr/local/EnergyPlus-*", "/opt/EnergyPlus-*"],
}


def find_energyplus_dir() -> Optional[Path]:
    """Return the EnergyPlus root directory (the one containing `pyenergyplus/`)."""
    explicit = os.environ.get("ENERGYPLUS_DIR")
    if explicit:
        p = Path(explicit).expanduser()
        if (p / "pyenergyplus").is_dir():
            return p
        raise FileNotFoundError(
            f"ENERGYPLUS_DIR={p} does not contain a 'pyenergyplus' folder."
        )

    import glob as _glob

    found: List[Path] = []
    for pattern in _CANDIDATE_GLOBS.get(platform.system(), []):
        for hit in _glob.glob(pattern):
            p = Path(hit)
            if (p / "pyenergyplus").is_dir():
                found.append(p)
    if not found:
        return None
    # Highest version string wins (V9-6-0 > V9-5-0, EnergyPlus-24-1-0 > 9-6-0).
    return sorted(found, key=lambda p: p.name)[-1]


_BOOTSTRAPPED = False


def bootstrap_energyplus() -> Path:
    """Put the EnergyPlus install on sys.path; return its root. Idempotent."""
    global _BOOTSTRAPPED
    eplus = find_energyplus_dir()
    if eplus is None:
        raise RuntimeError(
            "EnergyPlus installation not found.\n"
            "Install EnergyPlus 9.5+ (https://energyplus.net/downloads) and either\n"
            "  * set ENERGYPLUS_DIR in your .env, or\n"
            "  * install to a default location (C:\\EnergyPlusV9-6-0, "
            "/Applications/EnergyPlus-9-6-0, /usr/local/EnergyPlus-9-6-0)."
        )
    if not _BOOTSTRAPPED:
        if str(eplus) not in sys.path:
            sys.path.insert(0, str(eplus))
        # Windows needs the folder holding energyplusapi.dll on the DLL search path.
        if platform.system() == "Windows" and hasattr(os, "add_dll_directory"):
            try:
                os.add_dll_directory(str(eplus))
            except OSError:
                pass
        _BOOTSTRAPPED = True
    return eplus


# --------------------------------------------------------------------------- #
# Control policy - the hard safety envelope the LLM may never leave
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ControlPolicy:
    cooling_sp_min: float = 22.0   # degC
    cooling_sp_max: float = 27.0
    heating_sp_min: float = 16.0
    heating_sp_max: float = 22.0
    min_deadband: float = 2.0      # degC between heating and cooling setpoint
    max_step_per_interval: float = 1.5  # degC of setpoint movement per decision
    pmv_low: float = -0.5
    pmv_high: float = 0.5
    baseline_cooling_sp: float = 24.0
    baseline_heating_sp: float = 21.0

    def as_dict(self) -> dict:
        return {
            "cooling_setpoint_range_c": [self.cooling_sp_min, self.cooling_sp_max],
            "heating_setpoint_range_c": [self.heating_sp_min, self.heating_sp_max],
            "min_deadband_c": self.min_deadband,
            "max_setpoint_step_c": self.max_step_per_interval,
            "pmv_comfort_band": [self.pmv_low, self.pmv_high],
            "baseline_setpoints_c": {
                "cooling": self.baseline_cooling_sp,
                "heating": self.baseline_heating_sp,
            },
        }


POLICY = ControlPolicy()


# --------------------------------------------------------------------------- #
# Runtime settings
# --------------------------------------------------------------------------- #
@dataclass
class Settings:
    # --- model files -------------------------------------------------------
    idf: Path = field(default_factory=lambda: Path(_env("ECOLOOP_IDF", "")) if _env("ECOLOOP_IDF", "") else ASSETS_DIR / "model.idf")
    epw: Path = field(default_factory=lambda: Path(_env("ECOLOOP_EPW", "")) if _env("ECOLOOP_EPW", "") else ASSETS_DIR / "weather.epw")

    # --- LLM ---------------------------------------------------------------
    llm_base_url: str = field(default_factory=lambda: _env("ECOLOOP_LLM_BASE_URL", "http://localhost:11434/v1"))
    llm_api_key: str = field(default_factory=lambda: _env("ECOLOOP_LLM_API_KEY", "ollama"))
    llm_model: str = field(default_factory=lambda: _env("ECOLOOP_LLM_MODEL", "llama3.1:8b"))
    llm_temperature: float = field(default_factory=lambda: _env("ECOLOOP_LLM_TEMPERATURE", 0.1))
    llm_timeout_s: float = field(default_factory=lambda: _env("ECOLOOP_LLM_TIMEOUT_S", 25.0))
    llm_max_tool_rounds: int = field(default_factory=lambda: _env("ECOLOOP_LLM_MAX_TOOL_ROUNDS", 4))

    # --- closed loop -------------------------------------------------------
    control_interval_min: int = field(default_factory=lambda: _env("ECOLOOP_CONTROL_INTERVAL_MIN", 15))
    non_blocking_agent: bool = field(default_factory=lambda: _env("ECOLOOP_NON_BLOCKING_AGENT", False))
    # Design-day-only runs use EnergyPlus's native `-D` CLI flag (sizing periods
    # only, ~1-2 simulated days) instead of a full annual RunPeriod - the fast
    # path for hackathon demos and local iteration.
    design_day_only: bool = field(default_factory=lambda: _env("ECOLOOP_DESIGN_DAY_ONLY", True))

    # --- MCP ---------------------------------------------------------------
    mcp_host: str = field(default_factory=lambda: _env("ECOLOOP_MCP_HOST", "127.0.0.1"))
    mcp_port: int = field(default_factory=lambda: _env("ECOLOOP_MCP_PORT", 8848))
    tool_transport: str = field(default_factory=lambda: _env("ECOLOOP_TOOL_TRANSPORT", "mcp"))

    # --- comfort assumptions (ISO 7730 inputs not reported by every model) --
    met_rate: float = field(default_factory=lambda: _env("ECOLOOP_MET", 1.2))
    clo_value: float = field(default_factory=lambda: _env("ECOLOOP_CLO", 0.6))
    air_velocity: float = field(default_factory=lambda: _env("ECOLOOP_AIR_VELOCITY", 0.15))

    @property
    def mcp_url(self) -> str:
        return f"http://{self.mcp_host}:{self.mcp_port}/mcp"

    def zones(self) -> List[str]:
        """Zone names to control, discovered by scripts/prepare_model.py."""
        override = os.environ.get("ECOLOOP_ZONES")
        if override:
            return [z.strip() for z in override.split(",") if z.strip()]
        if MODEL_META.exists():
            meta = json.loads(MODEL_META.read_text(encoding="utf-8"))
            zones = meta.get("controlled_zones") or meta.get("zones") or []
            if zones:
                return zones
        return []

    def people_objects(self) -> List[str]:
        if MODEL_META.exists():
            meta = json.loads(MODEL_META.read_text(encoding="utf-8"))
            return meta.get("people", [])
        return []


SETTINGS = Settings()
