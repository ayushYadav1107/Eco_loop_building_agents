"""
Stage a runnable IDF + EPW pair for Eco-Loop from the local EnergyPlus install.

We deliberately do NOT hand-author an IDF: hand-written EnergyPlus input is
easy to get subtly wrong (field order, units, required sub-objects) and would
violate the "no hallucinated APIs/files" guideline just as much as inventing a
Python method would. Every EnergyPlus install ships an `ExampleFiles/` folder
of validated models - we pick one that already has zone thermostats (so there
is something for the agent to actuate), copy it into `assets/`, run it through
`eco_loop.idf_utils.instrument_idf` to request the variables/meters Eco-Loop
needs, and record the discovered zone list in `assets/model_meta.json`.

Usage:
    python -m scripts.prepare_model
    python -m scripts.prepare_model --idf "5ZoneAirCooled.idf" --epw "USA_IL_Chicago-OHare.Intl.AP.725300_TMY3.epw"
    python -m scripts.prepare_model --list        # show candidate example files and exit
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config  # noqa: E402
from eco_loop import idf_utils  # noqa: E402

# Ranked by how well-suited each is to a thermostat-actuation demo: multi-zone,
# a simple air-cooled/VAV system, dual setpoint thermostats, short-ish runtime.
PREFERRED_IDF_NAMES = [
    "5ZoneAirCooled.idf",
    "5ZoneAutoDXVAV.idf",
    "5ZoneVAV.idf",
    "RefBldgSmallOfficeNew2004_Chicago.idf",
    "RefBldgMediumOfficeNew2004_Chicago.idf",
    "5ZoneTDV.idf",
    "2ZoneDataCenterHVAC_wEconomizer.idf",
]

PREFERRED_EPW_NAMES = [
    "USA_IL_Chicago-OHare.Intl.AP.725300_TMY3.epw",
    "USA_CA_San.Francisco.Intl.AP.724940_TMY3.epw",
    "USA_CO_Golden-NREL.724666_TMY3.epw",
]


def _example_files_dir(eplus_root: Path) -> Path:
    for candidate in ("ExampleFiles", "Examples"):
        p = eplus_root / candidate
        if p.is_dir():
            return p
    raise FileNotFoundError(f"No ExampleFiles directory under {eplus_root}")


def _weather_dir(eplus_root: Path) -> Path:
    for candidate in ("WeatherData", "Weather"):
        p = eplus_root / candidate
        if p.is_dir():
            return p
    raise FileNotFoundError(f"No WeatherData directory under {eplus_root}")


def _has_thermostat(idf_path: Path) -> bool:
    try:
        text = idf_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return "ZoneControl:Thermostat" in idf_utils.strip_comments(text)


def find_candidate_idf(examples_dir: Path, explicit_name: Optional[str] = None) -> Path:
    if explicit_name:
        hit = examples_dir / explicit_name
        if not hit.exists():
            matches = list(examples_dir.rglob(explicit_name))
            if not matches:
                raise FileNotFoundError(f"'{explicit_name}' not found under {examples_dir}")
            hit = matches[0]
        return hit

    for name in PREFERRED_IDF_NAMES:
        matches = list(examples_dir.rglob(name))
        if matches:
            return matches[0]

    # Fall back: scan every example IDF for one with a thermostat, smallest first
    # (smallest tends to mean fewest zones/shortest run - good for a demo).
    all_idfs = sorted(examples_dir.rglob("*.idf"), key=lambda p: p.stat().st_size)
    for p in all_idfs:
        if _has_thermostat(p):
            return p
    raise FileNotFoundError(
        f"No example IDF under {examples_dir} defines a ZoneControl:Thermostat. "
        "Pass --idf explicitly to point at a model of your choosing."
    )


def find_weather(weather_dir: Path, explicit_name: Optional[str] = None) -> Path:
    if explicit_name:
        hit = weather_dir / explicit_name
        if not hit.exists():
            matches = list(weather_dir.rglob(explicit_name))
            if not matches:
                raise FileNotFoundError(f"'{explicit_name}' not found under {weather_dir}")
            hit = matches[0]
        return hit

    for name in PREFERRED_EPW_NAMES:
        matches = list(weather_dir.rglob(name))
        if matches:
            return matches[0]

    any_epw = sorted(weather_dir.rglob("*.epw"))
    if any_epw:
        return any_epw[0]
    raise FileNotFoundError(f"No .epw files under {weather_dir}. Pass --epw explicitly.")


def list_candidates(examples_dir: Path) -> List[Path]:
    return sorted(p for p in examples_dir.rglob("*.idf") if _has_thermostat(p))


def prepare(idf_name: Optional[str], epw_name: Optional[str], timestep_per_hour: int) -> None:
    eplus_root = config.bootstrap_energyplus()
    examples_dir = _example_files_dir(eplus_root)
    weather_dir = _weather_dir(eplus_root)

    src_idf = find_candidate_idf(examples_dir, idf_name)
    src_epw = find_weather(weather_dir, epw_name)

    print(f"[prepare_model] EnergyPlus root : {eplus_root}")
    print(f"[prepare_model] source IDF      : {src_idf}")
    print(f"[prepare_model] source EPW      : {src_epw}")

    config.ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    dst_epw = config.ASSETS_DIR / "weather.epw"
    shutil.copyfile(src_epw, dst_epw)

    dst_idf = config.ASSETS_DIR / "model.idf"
    idf_utils.instrument_idf(src_idf, dst_idf, timestep_per_hour=timestep_per_hour)

    meta = idf_utils.discover_model(dst_idf)
    meta["source_idf"] = str(src_idf)
    meta["source_epw"] = str(src_epw)
    meta["timestep_per_hour"] = timestep_per_hour
    config.MODEL_META.write_text(json.dumps(meta, indent=2), encoding="utf-8")

    print(f"[prepare_model] staged IDF       -> {dst_idf}")
    print(f"[prepare_model] staged EPW       -> {dst_epw}")
    print(f"[prepare_model] controlled zones -> {meta['controlled_zones']}")
    print(f"[prepare_model] model metadata   -> {config.MODEL_META}")

    if not meta["controlled_zones"]:
        print(
            "[prepare_model] WARNING: no ZoneControl:Thermostat found - the AI run "
            "will have nothing to actuate. Pick a different --idf."
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--idf", help="Example IDF filename (relative to ExampleFiles/) to use")
    parser.add_argument("--epw", help="Weather EPW filename (relative to WeatherData/) to use")
    parser.add_argument("--timestep-per-hour", type=int, default=4, help="Sub-hourly Timestep (default: 4 = 15 min)")
    parser.add_argument("--list", action="store_true", help="List candidate example IDFs with a thermostat, then exit")
    args = parser.parse_args()

    if args.list:
        eplus_root = config.bootstrap_energyplus()
        examples_dir = _example_files_dir(eplus_root)
        for p in list_candidates(examples_dir):
            print(p.relative_to(examples_dir))
        return

    prepare(args.idf, args.epw, args.timestep_per_hour)


if __name__ == "__main__":
    main()
