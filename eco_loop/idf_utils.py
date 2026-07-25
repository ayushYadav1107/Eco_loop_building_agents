"""
Minimal IDF reader/writer.

Just enough parsing to (a) discover which zones are thermostatically controlled,
(b) find `People` objects, and (c) append the `Output:Variable` / `Output:Meter`
requests the dashboard needs.  We deliberately do not use eppy so the repo has
no extra native dependency.

IDF grammar used here: objects are `;`-terminated, fields are `,`-separated,
`!` starts a comment that runs to end of line.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

_COMMENT = re.compile(r"!.*?$", re.MULTILINE)


def strip_comments(text: str) -> str:
    return _COMMENT.sub("", text)


def parse_objects(text: str) -> List[List[str]]:
    """Return every IDF object as ``[type, field1, field2, ...]`` (upper-cased type)."""
    objects: List[List[str]] = []
    for chunk in strip_comments(text).split(";"):
        fields = [f.strip() for f in chunk.split(",")]
        fields = [f for f in fields if f != ""]
        if len(fields) >= 1 and fields[0]:
            objects.append(fields)
    return objects


def _objects_of(objects: List[List[str]], type_name: str) -> List[List[str]]:
    t = type_name.upper()
    return [o for o in objects if o[0].upper() == t]


def discover_model(idf_path: Path) -> Dict[str, List[str]]:
    """Extract zones, thermostatically-controlled zones and People object names."""
    text = Path(idf_path).read_text(encoding="utf-8", errors="replace")
    objects = parse_objects(text)

    zones = [o[1] for o in _objects_of(objects, "Zone") if len(o) > 1]

    # ZoneControl:Thermostat -> field 2 is the Zone or ZoneList name.
    controlled: List[str] = []
    for o in _objects_of(objects, "ZoneControl:Thermostat"):
        if len(o) > 2:
            controlled.append(o[2])

    # Resolve ZoneList references to their member zones.
    zonelists: Dict[str, List[str]] = {}
    for o in _objects_of(objects, "ZoneList"):
        if len(o) > 2:
            zonelists[o[1].upper()] = o[2:]

    expanded: List[str] = []
    for name in controlled:
        expanded.extend(zonelists.get(name.upper(), [name]))

    # Keep only names that are real zones, preserving IDF order and uniqueness.
    zone_upper = {z.upper(): z for z in zones}
    seen = set()
    controlled_zones: List[str] = []
    for name in expanded:
        real = zone_upper.get(name.upper())
        if real and real.upper() not in seen:
            seen.add(real.upper())
            controlled_zones.append(real)

    people = [o[1] for o in _objects_of(objects, "People") if len(o) > 1]

    return {
        "zones": zones,
        "controlled_zones": controlled_zones or zones,
        "people": people,
    }


# --------------------------------------------------------------------------- #
# Output request injection
# --------------------------------------------------------------------------- #
REQUIRED_VARIABLES: List[Tuple[str, str]] = [
    ("*", "Zone Mean Air Temperature"),
    ("*", "Zone Mean Radiant Temperature"),
    ("*", "Zone Air Relative Humidity"),
    ("*", "Zone Thermostat Cooling Setpoint Temperature"),
    ("*", "Zone Thermostat Heating Setpoint Temperature"),
    ("*", "Zone People Occupant Count"),
    ("*", "Zone Thermal Comfort Fanger Model PMV"),
    ("*", "Zone Thermal Comfort Fanger Model PPD"),
    ("*", "Site Outdoor Air Drybulb Temperature"),
    ("*", "Site Outdoor Air Relative Humidity"),
    ("*", "Site Direct Solar Radiation Rate per Area"),
    ("*", "Facility Total HVAC Electricity Demand Rate"),
    ("*", "Facility Total Electricity Demand Rate"),
]

REQUIRED_METERS: List[str] = [
    "Electricity:Facility",
    "Electricity:HVAC",
    "Cooling:Electricity",
    "Heating:Electricity",
]

_MARKER = "!-- ECO-LOOP INSTRUMENTATION --"

# Matches a whole `RunPeriod, ... ;` object at line start. The comma directly
# after the type name is what keeps this from also matching
# `RunPeriodControl:SpecialDays` or `RunPeriod:CustomRange`.
_RUNPERIOD_RE = re.compile(r"(?ims)^[ \t]*RunPeriod[ \t]*,.*?;")


def make_run_period(begin_month: int, begin_day: int, end_month: int, end_day: int) -> str:
    """Build a fully-specified RunPeriod object.

    Every field is written explicitly - including the two empty year fields -
    because EnergyPlus 9.0+ inserted `Begin Year` / `End Year` into the middle
    of this object. Editing by field position against a parsed object is unsafe
    (empty fields are easy to drop), so callers replace the whole object.

    `Day of Week for Start Day` is intentionally left blank so EnergyPlus uses
    the actual weekday from the weather file, which keeps occupancy schedules
    aligned with the real calendar for the chosen period.
    """
    return (
        "RunPeriod,\n"
        "  Eco-Loop Run Period,     !- Name\n"
        f"  {int(begin_month)},{'':<22}!- Begin Month\n"
        f"  {int(begin_day)},{'':<22}!- Begin Day of Month\n"
        "  ,                        !- Begin Year\n"
        f"  {int(end_month)},{'':<22}!- End Month\n"
        f"  {int(end_day)},{'':<22}!- End Day of Month\n"
        "  ,                        !- End Year\n"
        "  ,                        !- Day of Week for Start Day\n"
        "  Yes,                     !- Use Weather File Holidays and Special Days\n"
        "  Yes,                     !- Use Weather File Daylight Saving Period\n"
        "  No,                      !- Apply Weekend Holiday Rule\n"
        "  Yes,                     !- Use Weather File Rain Indicators\n"
        "  Yes;                     !- Use Weather File Snow Indicators\n"
    )


def instrument_idf(
    src: Path,
    dst: Path,
    timestep_per_hour: int = 4,
    run_period: Optional[Tuple[int, int, int, int]] = None,
) -> Path:
    """Copy `src` to `dst`, appending the outputs Eco-Loop needs.

    Args:
        run_period: optional ``(begin_month, begin_day, end_month, end_day)``.
            When given, the model's existing RunPeriod is replaced so the
            simulation covers only that span - the practical way to get a
            representative-period run instead of a full 365-day year.

    Idempotent: re-running strips the previously injected block first.
    """
    text = Path(src).read_text(encoding="utf-8", errors="replace")
    if _MARKER in text:
        text = text.split(_MARKER)[0]

    # Force a sub-hourly timestep so the control interval is meaningful.
    append_timestep = not re.search(r"(?im)^\s*Timestep\s*,", strip_comments(text))
    if not append_timestep:
        text = re.sub(
            r"(?is)\bTimestep\s*,\s*[0-9]+\s*;",
            f"Timestep,{timestep_per_hour};",
            text,
            count=1,
        )

    if run_period is not None:
        replacement = make_run_period(*run_period)
        text, n = _RUNPERIOD_RE.subn(replacement, text, count=1)
        if n == 0:
            # Model had no RunPeriod (design-day-only file); add one.
            text = text.rstrip() + "\n\n" + replacement

    lines = [text.rstrip(), "", _MARKER, ""]
    if append_timestep:
        lines.append(f"Timestep,{timestep_per_hour};")

    for key, var in REQUIRED_VARIABLES:
        lines.append(f"Output:Variable,{key},{var},Timestep;")
    for meter in REQUIRED_METERS:
        lines.append(f"Output:Meter,{meter},Timestep;")
    lines.append("Output:SQLite,SimpleAndTabular;")
    lines.append("")

    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text("\n".join(lines), encoding="utf-8")
    return dst
