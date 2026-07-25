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
from typing import Dict, List, Optional

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


def _infer_steps_per_hour(df: pd.DataFrame) -> int:
    """Guess timestep resolution from row count vs a typical annual/day run."""
    n = len(df)
    for candidate in (4, 6, 12, 1):
        if n % (24 * candidate) == 0 and n >= 24 * candidate:
            return candidate
    return 4
