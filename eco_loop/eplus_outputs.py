"""
Post-run readers for EnergyPlus outputs.

`sim_env.py` runs EnergyPlus with `-r` (ReadVarsESO), which turns the binary
`eplusout.eso` into `eplusout.csv` - the cheapest, most portable thing for a
Streamlit dashboard to load. `eplusout.sql` (from `Output:SQLite,
SimpleAndTabular;`, injected by `idf_utils.instrument_idf`) is read as a
fallback / cross-check since it survives even if ReadVarsESO was skipped.

Column names in the CSV are exactly the `Output:Variable` request strings
("Zone Name:Variable Name [Unit](Frequency)"), so this module also normalises
them into the tidy shape the dashboard wants.
"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Dict, List, Optional  # noqa: F401  (List used in comfort scoring)

import pandas as pd

# Matches ReadVarsESO's "Key:Variable [Unit](Frequency)" header format.
_HEADER_RE = re.compile(r"^(?P<key>.+?):(?P<var>[^\[\(]+?)\s*(?:\[(?P<unit>[^\]]*)\])?\s*\((?P<freq>[^)]+)\)\s*$")

FACILITY_HVAC_POWER_VARS = [
    "Facility Total HVAC Electricity Demand Rate",
    "Facility Total HVAC Electric Demand Power",
]
FACILITY_TOTAL_POWER_VARS = [
    "Facility Total Electricity Demand Rate",
    "Facility Total Electric Demand Power",
]


def _split_headers(columns: List[str]) -> Dict[str, Dict[str, str]]:
    parsed: Dict[str, Dict[str, str]] = {}
    for col in columns:
        m = _HEADER_RE.match(col.strip())
        if m:
            parsed[col] = {
                "key": m.group("key").strip(),
                "var": m.group("var").strip(),
                "unit": (m.group("unit") or "").strip(),
            }
    return parsed


def load_csv(path: Path) -> Optional[pd.DataFrame]:
    """Load an EnergyPlus `eplusout.csv` (ReadVarsESO output). None if missing."""
    path = Path(path)
    if not path.exists():
        return None
    df = pd.read_csv(path)
    time_col = next((c for c in df.columns if c.strip().lower().startswith("date/time")), df.columns[0])
    df = df.rename(columns={time_col: "datetime_raw"})
    df["timestep"] = range(len(df))
    return df


def _pick_column(df: pd.DataFrame, headers: Dict[str, Dict[str, str]], var_names: List[str], key: str = "Whole Building") -> Optional[str]:
    for var in var_names:
        for col, meta in headers.items():
            if meta["var"].lower() == var.lower() and (key.lower() in meta["key"].lower() or meta["key"] == "*"):
                return col
    return None


def facility_power_series(df: pd.DataFrame) -> Optional[pd.Series]:
    if df is None:
        return None
    headers = _split_headers(list(df.columns))
    col = _pick_column(df, headers, FACILITY_HVAC_POWER_VARS) or _pick_column(df, headers, FACILITY_TOTAL_POWER_VARS)
    if col is None:
        return None
    return pd.to_numeric(df[col], errors="coerce")


def zone_variable_frame(df: pd.DataFrame, var_name: str) -> pd.DataFrame:
    """Wide -> tidy: one column per zone for a given variable name."""
    if df is None:
        return pd.DataFrame()
    headers = _split_headers(list(df.columns))
    cols = {
        meta["key"]: col
        for col, meta in headers.items()
        if meta["var"].lower() == var_name.lower()
    }
    if not cols:
        return pd.DataFrame()
    out = pd.DataFrame({zone: pd.to_numeric(df[col], errors="coerce") for zone, col in cols.items()})
    out.index = df["timestep"]
    return out


def load_sql_timeseries(path: Path, variable_like: str) -> Optional[pd.DataFrame]:
    """Fallback reader for `eplusout.sql` when the CSV is unavailable.

    Uses the ReportData / ReportDataDictionary tables written by
    `Output:SQLite,SimpleAndTabular;`.
    """
    path = Path(path)
    if not path.exists():
        return None
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        query = """
            SELECT rd.TimeIndex AS timestep, rdd.KeyValue AS key_value,
                   rdd.Name AS variable, rd.Value AS value, rdd.Units AS unit
            FROM ReportData rd
            JOIN ReportDataDictionary rdd
              ON rd.ReportDataDictionaryIndex = rdd.ReportDataDictionaryIndex
            WHERE rdd.Name LIKE ?
        """
        df = pd.read_sql_query(query, con, params=[f"%{variable_like}%"])
        con.close()
        return df
    except sqlite3.Error:
        return None


def summarize_run(output_dir: Path) -> Dict[str, object]:
    """Best-effort summary stats for a single run's output directory."""
    csv_path = Path(output_dir) / "eplusout.csv"
    df = load_csv(csv_path)
    if df is None:
        sql_df = load_sql_timeseries(Path(output_dir) / "eplusout.sql", "HVAC Electric")
        if sql_df is None or sql_df.empty:
            return {"available": False}
        kwh = sql_df["value"].sum() / 1000.0 / 4.0  # crude: assumes 15-min steps, W->kWh
        return {"available": True, "total_hvac_kwh": round(kwh, 2), "source": "sql"}

    power = facility_power_series(df)
    if power is None:
        return {"available": True, "total_hvac_kwh": None, "source": "csv", "rows": len(df)}

    steps_per_hour = _infer_steps_per_hour(df)
    kwh = (power.fillna(0.0) / 1000.0 / steps_per_hour).sum()
    return {
        "available": True,
        "total_hvac_kwh": round(float(kwh), 2),
        "rows": len(df),
        "steps_per_hour": steps_per_hour,
        "source": "csv",
    }


def occupied_comfort(output_dir: Path, month: int) -> Optional[Dict[str, object]]:
    """Compute occupied-hours PMV statistics directly from an EnergyPlus CSV.

    This is what makes the comfort claim falsifiable. The agent's own decision
    log only exists for AI runs, so comparing it against the baseline would be
    comparing two different measurements. Here both runs are scored the same
    way - same ISO 7730 implementation, same clothing/metabolic assumptions,
    same "worst occupied zone per timestep" reduction - from the raw results
    each run wrote. Any comfort difference is therefore attributable to the
    control strategy and not to how it was measured.

    Only zone-timesteps with a non-zero occupant count are scored: PMV is not
    a meaningful constraint in an empty room, and including unoccupied setback
    hours would make any night-setback strategy look like a comfort failure.
    """
    from eco_loop.comfort import clo_for_season, safe_pmv

    df = load_csv(Path(output_dir) / "eplusout.csv")
    if df is None:
        return None

    ta = zone_variable_frame(df, "Zone Mean Air Temperature")
    if ta.empty:
        return None
    tr = zone_variable_frame(df, "Zone Mean Radiant Temperature")
    rh = zone_variable_frame(df, "Zone Air Relative Humidity")
    occ = zone_variable_frame(df, "Zone People Occupant Count")
    if occ.empty:
        return None

    clo = clo_for_season(month)
    zones = [z for z in ta.columns if z in occ.columns and occ[z].sum() > 0]

    worst_per_step: List[float] = []
    for i in range(len(ta)):
        worst = None
        for z in zones:
            if occ[z].iloc[i] <= 0:
                continue
            pmv, _ = safe_pmv(
                ta[z].iloc[i],
                tr[z].iloc[i] if z in tr.columns else None,
                rh[z].iloc[i] if z in rh.columns else None,
                vel=0.15, met=1.2, clo=clo,
            )
            if pmv is not None and (worst is None or abs(pmv) > abs(worst)):
                worst = pmv
        if worst is not None:
            worst_per_step.append(worst)

    if not worst_per_step:
        return None

    in_band = [p for p in worst_per_step if -0.5 <= p <= 0.5]
    return {
        "n_occupied_steps": len(worst_per_step),
        "mean_pmv": sum(worst_per_step) / len(worst_per_step),
        "pct_in_band": 100.0 * len(in_band) / len(worst_per_step),
        "min_pmv": min(worst_per_step),
        "max_pmv": max(worst_per_step),
        "series": worst_per_step,
    }


_DT_RE = re.compile(r"(\d{2})/(\d{2})\s+(\d{2}):(\d{2})")


def add_time_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Add `hour` / `day` columns parsed from ReadVarsESO's Date/Time strings.

    EnergyPlus writes hour 24:00:00 for the last interval of a day; it is
    normalised to hour 0 so hour-of-day grouping does not gain a 25th bucket.
    """
    df = df.copy()
    hours, days = [], []
    for raw in df["datetime_raw"].astype(str):
        m = _DT_RE.search(raw)
        if m:
            day, hour, minute = int(m.group(2)), int(m.group(3)), int(m.group(4))
            hours.append(hour % 24 + minute / 60.0)
            days.append(day)
        else:
            hours.append(float("nan"))
            days.append(-1)
    df["hour"] = hours
    df["day"] = days
    return df


def hourly_power_profile(output_dir: Path) -> Optional[pd.DataFrame]:
    """Mean HVAC electric demand (kW) by hour of day across the run."""
    df = load_csv(Path(output_dir) / "eplusout.csv")
    if df is None:
        return None
    power = facility_power_series(df)
    if power is None:
        return None
    df = add_time_columns(df)
    df["kw"] = power.values / 1000.0
    grouped = df.groupby(df["hour"].astype(int))["kw"].mean().reset_index()
    grouped.columns = ["hour", "kw"]
    return grouped


def cumulative_energy(output_dir: Path) -> Optional[pd.DataFrame]:
    """Running total of HVAC energy (kWh) across the run."""
    df = load_csv(Path(output_dir) / "eplusout.csv")
    if df is None:
        return None
    power = facility_power_series(df)
    if power is None:
        return None
    steps_per_hour = _infer_steps_per_hour(df)
    kwh = power.fillna(0.0) / 1000.0 / steps_per_hour
    out = add_time_columns(df)[["hour", "day"]].copy()
    out["cumulative_kwh"] = kwh.cumsum().values
    out["step"] = range(len(out))
    out["elapsed_days"] = out["step"] / (24.0 * steps_per_hour)
    return out


def per_zone_comfort(output_dir: Path, month: int) -> Optional[pd.DataFrame]:
    """Occupied-hours PMV statistics for each zone separately.

    The headline metric collapses all zones to the worst one per timestep, which
    is the right conservative summary but hides that most zones are comfortable.
    This is the breakdown behind it.
    """
    from eco_loop.comfort import clo_for_season, safe_pmv

    df = load_csv(Path(output_dir) / "eplusout.csv")
    if df is None:
        return None
    ta = zone_variable_frame(df, "Zone Mean Air Temperature")
    if ta.empty:
        return None
    tr = zone_variable_frame(df, "Zone Mean Radiant Temperature")
    rh = zone_variable_frame(df, "Zone Air Relative Humidity")
    occ = zone_variable_frame(df, "Zone People Occupant Count")
    if occ.empty:
        return None

    clo = clo_for_season(month)
    rows = []
    for zone in [z for z in ta.columns if z in occ.columns and occ[z].sum() > 0]:
        vals = []
        for i in range(len(ta)):
            if occ[zone].iloc[i] <= 0:
                continue
            pmv, _ = safe_pmv(
                ta[zone].iloc[i],
                tr[zone].iloc[i] if zone in tr.columns else None,
                rh[zone].iloc[i] if zone in rh.columns else None,
                vel=0.15, met=1.2, clo=clo,
            )
            if pmv is not None:
                vals.append(pmv)
        if vals:
            rows.append({
                "zone": zone,
                "pct_in_band": 100.0 * sum(1 for v in vals if -0.5 <= v <= 0.5) / len(vals),
                "mean_pmv": sum(vals) / len(vals),
                "min_pmv": min(vals),
                "max_pmv": max(vals),
                "n": len(vals),
            })
    return pd.DataFrame(rows).sort_values("zone").reset_index(drop=True) if rows else None


def compare_runs(baseline_dir: Path, ai_dir: Path, month: int) -> Dict[str, object]:
    """Headline energy + comfort comparison for one baseline/AI pair."""
    b_kwh = summarize_run(baseline_dir).get("total_hvac_kwh")
    a_kwh = summarize_run(ai_dir).get("total_hvac_kwh")
    b_c = occupied_comfort(baseline_dir, month)
    a_c = occupied_comfort(ai_dir, month)

    out: Dict[str, object] = {"baseline_kwh": b_kwh, "ai_kwh": a_kwh}
    if b_kwh and a_kwh is not None and b_kwh > 0:
        out["savings_pct"] = 100.0 * (b_kwh - a_kwh) / b_kwh
        out["savings_kwh"] = b_kwh - a_kwh
    if b_c and a_c:
        out["baseline_comfort"] = {k: v for k, v in b_c.items() if k != "series"}
        out["ai_comfort"] = {k: v for k, v in a_c.items() if k != "series"}
        out["comfort_delta_pp"] = a_c["pct_in_band"] - b_c["pct_in_band"]
    return out


def _infer_steps_per_hour(df: pd.DataFrame) -> int:
    """Guess timestep resolution from row count vs a typical annual/day run."""
    n = len(df)
    for candidate in (4, 6, 12, 1):
        if n % (24 * candidate) == 0 and n >= 24 * candidate:
            return candidate
    return 4
