"""
Eco-Loop Building Agents - single entry point.

Subcommands:
    python main.py prepare                  # stage IDF/EPW from the local EnergyPlus install
    python main.py run-baseline              # native-thermostat reference run
    python main.py run-ai                    # LLM-supervised closed-loop run
    python main.py run-all                   # prepare (if needed) + baseline + ai
    python main.py dashboard                 # launch the Streamlit dashboard
    python main.py serve-mcp                 # run only the FastMCP tool server (debugging)

Every subcommand reads its defaults from `config.SETTINGS` / `.env`, so a plain
`python main.py run-all` is enough once `ENERGYPLUS_DIR` (if needed) and the
Ollama/vLLM endpoint are configured.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

import config
from config import AI_DIR, BASELINE_DIR, SETTINGS


def cmd_prepare(args: argparse.Namespace) -> None:
    from scripts.prepare_model import prepare

    prepare(args.idf, args.epw, args.timestep_per_hour)


def _ensure_model_staged() -> None:
    if not SETTINGS.idf.exists() or not SETTINGS.epw.exists():
        print("[main] staged model not found - running `prepare` first")
        from scripts.prepare_model import prepare

        prepare(None, None, 4)


def _parse_mmdd(value: str, flag: str) -> tuple:
    """Parse a MM-DD string into (month, day)."""
    try:
        month, day = value.split("-")
        m, d = int(month), int(day)
        if not (1 <= m <= 12 and 1 <= d <= 31):
            raise ValueError
        return m, d
    except (ValueError, AttributeError):
        raise SystemExit(f"[main] {flag} must look like MM-DD (e.g. 07-15), got {value!r}")


def _model_for_period(args: argparse.Namespace) -> Path:
    """Return the IDF to simulate, re-instrumented for --start/--end if given.

    A full-year RunPeriod is ~8760 agent turns; at a few seconds per LLM turn
    that is many hours of wall clock. Restricting the run to a representative
    period is the practical way to get a defensible number, and is standard
    building-analysis practice anyway.
    """
    start = getattr(args, "start", None)
    end = getattr(args, "end", None)
    if not start and not end:
        return SETTINGS.idf
    if not (start and end):
        raise SystemExit("[main] --start and --end must be given together")

    import json

    from config import ASSETS_DIR, MODEL_META
    from eco_loop.idf_utils import instrument_idf

    if not MODEL_META.exists():
        raise SystemExit("[main] run `python main.py prepare` first")
    meta = json.loads(MODEL_META.read_text(encoding="utf-8"))
    source = Path(meta["source_idf"])
    if not source.exists():
        raise SystemExit(f"[main] original source IDF missing: {source}")

    bm, bd = _parse_mmdd(start, "--start")
    em, ed = _parse_mmdd(end, "--end")
    dst = ASSETS_DIR / f"model_{bm:02d}{bd:02d}_{em:02d}{ed:02d}.idf"
    instrument_idf(
        source, dst,
        timestep_per_hour=int(meta.get("timestep_per_hour", 4)),
        run_period=(bm, bd, em, ed),
    )
    print(f"[main] run period {bm:02d}-{bd:02d} .. {em:02d}-{ed:02d} -> {dst.name}")
    return dst


def _design_day_only(args: argparse.Namespace) -> bool:
    """An explicit date range always means a weather-file run, never `-D`."""
    if getattr(args, "start", None):
        return False
    return not args.full_year


def cmd_run_baseline(args: argparse.Namespace) -> None:
    _ensure_model_staged()
    from eco_loop.sim_env import run_baseline

    sim = run_baseline(
        _model_for_period(args),
        SETTINGS.epw,
        Path(args.output_dir or BASELINE_DIR),
        design_day_only=_design_day_only(args),
        verbose=args.verbose,
    )
    _report(sim, "baseline")


def cmd_run_ai(args: argparse.Namespace) -> None:
    _ensure_model_staged()
    from eco_loop.agent_orchestrator import Agent
    from eco_loop.sim_env import run_ai

    agent = Agent(tool_transport=args.transport)
    try:
        sim = run_ai(
            _model_for_period(args),
            SETTINGS.epw,
            Path(args.output_dir or AI_DIR),
            decide_fn=agent.decide,
            control_interval_min=args.interval,
            design_day_only=_design_day_only(args),
            non_blocking=args.non_blocking,
            verbose=args.verbose,
        )
    finally:
        agent.close()
    _report(sim, "ai")
    print(f"[main] agent stats: {agent.stats}")


def cmd_run_all(args: argparse.Namespace) -> None:
    _ensure_model_staged()
    cmd_run_baseline(args)
    cmd_run_ai(args)
    _print_savings_summary()


def cmd_compare(args: argparse.Namespace) -> None:
    """Print the energy + comfort comparison for one baseline/AI pair."""
    from eco_loop import eplus_outputs as eo

    result = eo.compare_runs(Path(args.baseline), Path(args.ai), args.month)
    b, a = result.get("baseline_kwh"), result.get("ai_kwh")
    print(f"\n=== {args.label} ===")
    if b is None or a is None:
        print("  no results found - run the simulations first")
        return
    print(f"  baseline HVAC : {b:8.2f} kWh")
    print(f"  AI-driven HVAC: {a:8.2f} kWh")
    if "savings_pct" in result:
        print(f"  savings       : {result['savings_kwh']:+8.2f} kWh  ({result['savings_pct']:+.1f}%)")
    bc, ac = result.get("baseline_comfort"), result.get("ai_comfort")
    if bc and ac:
        print(f"\n  thermal comfort, occupied hours only ({ac['n_occupied_steps']} zone-timesteps):")
        print(f"    baseline : mean PMV {bc['mean_pmv']:+.2f}   in band {bc['pct_in_band']:5.1f}%")
        print(f"    AI       : mean PMV {ac['mean_pmv']:+.2f}   in band {ac['pct_in_band']:5.1f}%")
        print(f"    delta    : {result['comfort_delta_pp']:+.1f} percentage points in band")
        verdict = (
            "comfort preserved" if result["comfort_delta_pp"] >= -2.0
            else "COMFORT DEGRADED - savings came at the occupants' expense"
        )
        print(f"    verdict  : {verdict}")


def cmd_dashboard(args: argparse.Namespace) -> None:
    subprocess.run(
        [sys.executable, "-m", "streamlit", "run", str(Path(__file__).parent / "dashboard.py")],
        check=False,
    )


def cmd_serve_mcp(args: argparse.Namespace) -> None:
    from eco_loop.mcp_tools import serve_http

    print(f"[main] serving MCP tools on http://{SETTINGS.mcp_host}:{SETTINGS.mcp_port}/mcp")
    serve_http()


def _report(sim, label: str) -> None:
    print(f"\n[main] {label} run finished: exit_code={sim.stats.get('exit_code')} "
          f"wall_clock={sim.stats.get('wall_clock_s')}s")
    print(f"[main] {label} stats: {sim.stats}")
    print(f"[main] {label} output dir: {sim.output_dir}")


def _print_savings_summary() -> None:
    from eco_loop import eplus_outputs as eo

    base = eo.summarize_run(BASELINE_DIR)
    ai = eo.summarize_run(AI_DIR)
    print("\n=== Eco-Loop summary ===")
    print(f"baseline: {base}")
    print(f"ai      : {ai}")
    b, a = base.get("total_hvac_kwh"), ai.get("total_hvac_kwh")
    if b and a is not None and b > 0:
        pct = 100.0 * (b - a) / b
        print(f"energy savings: {pct:+.1f}%  ({b:.1f} kWh -> {a:.1f} kWh)")
    print(f"Run `python main.py dashboard` for the full visual breakdown.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p_prep = sub.add_parser("prepare", help="Stage an example IDF/EPW and instrument it for Eco-Loop")
    p_prep.add_argument("--idf")
    p_prep.add_argument("--epw")
    p_prep.add_argument("--timestep-per-hour", type=int, default=4)
    p_prep.set_defaults(func=cmd_prepare)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--output-dir", help="Override the run's output directory")
    common.add_argument("--full-year", action="store_true", help="Run the full annual RunPeriod instead of design days")
    common.add_argument("--start", metavar="MM-DD", help="Start of a custom run period, e.g. 07-15 (use with --end)")
    common.add_argument("--end", metavar="MM-DD", help="End of a custom run period, e.g. 07-21 (use with --start)")
    common.add_argument("-v", "--verbose", action="store_true", help="Echo EnergyPlus console output")

    p_base = sub.add_parser("run-baseline", parents=[common], help="Run with native thermostat schedules (no agent)")
    p_base.set_defaults(func=cmd_run_baseline)

    p_ai = sub.add_parser("run-ai", parents=[common], help="Run with the LLM closed-loop controller")
    p_ai.add_argument("--interval", type=int, default=None, help="Control interval in simulated minutes (default from .env)")
    p_ai.add_argument("--transport", choices=["mcp", "direct"], default=None, help="Tool call transport")
    p_ai.add_argument("--non-blocking", action="store_true", help="Do not stall the simulation waiting on the LLM")
    p_ai.set_defaults(func=cmd_run_ai)

    p_all = sub.add_parser("run-all", parents=[common], help="prepare (if needed) + run-baseline + run-ai")
    p_all.add_argument("--interval", type=int, default=None)
    p_all.add_argument("--transport", choices=["mcp", "direct"], default=None)
    p_all.add_argument("--non-blocking", action="store_true")
    p_all.set_defaults(func=cmd_run_all)

    p_cmp = sub.add_parser("compare", help="Print energy + comfort comparison for a baseline/AI pair")
    p_cmp.add_argument("--baseline", default=str(BASELINE_DIR))
    p_cmp.add_argument("--ai", default=str(AI_DIR))
    p_cmp.add_argument("--month", type=int, default=7, help="Month, for seasonal clothing assumption (default 7)")
    p_cmp.add_argument("--label", default="Eco-Loop comparison")
    p_cmp.set_defaults(func=cmd_compare)

    p_dash = sub.add_parser("dashboard", help="Launch the Streamlit savings dashboard")
    p_dash.set_defaults(func=cmd_dashboard)

    p_mcp = sub.add_parser("serve-mcp", help="Run only the FastMCP tool server (for manual/Inspector use)")
    p_mcp.set_defaults(func=cmd_serve_mcp)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
