# Eco-Loop Building Agents

Autonomous, LLM-in-the-loop HVAC supervision for EnergyPlus. A local
open-source LLM (Llama 3 / Mistral via Ollama or vLLM) receives live building
telemetry through an MCP tool server every 15-60 simulated minutes, reasons
about the energy/carbon/comfort trade-off, and injects validated setpoints
back into the running simulation - closing the loop that a static BMS schedule
never can.

See [`ARCHITECTURE.md`](ARCHITECTURE.md) for the full design rationale
(tool-calling bridge, latency management, log handling, safety envelope).

## Directory structure

```
POC/
├── main.py                     # single CLI entry point (prepare / run-baseline / run-ai / dashboard)
├── config.py                   # settings, EnergyPlus discovery/bootstrap, ControlPolicy
├── dashboard.py                # Streamlit Quantitative Savings Dashboard
├── requirements.txt
├── .env.example                # copy to .env and edit
├── ARCHITECTURE.md
├── README.md
├── eco_loop/
│   ├── __init__.py
│   ├── schemas.py               # BuildingState / SetpointCommand / AgentDecision (pydantic)
│   ├── comfort.py                # ISO 7730 Fanger PMV/PPD implementation
│   ├── grid.py                    # grid carbon-intensity signal
│   ├── state_bus.py                # thread-safe blackboard shared by sim + MCP + agent
│   ├── idf_utils.py                 # minimal IDF reader + Output:Variable/Meter injector
│   ├── sim_env.py                    # Module 1: PyEnergyPlus wrapper (callbacks, handles, actuation)
│   ├── mcp_tools.py                   # Module 2: FastMCP server + tool definitions
│   ├── mcp_client.py                   # sync bridge from the sim thread to the async MCP session
│   ├── agent_orchestrator.py            # Module 3: LLM cognitive loop, timeout/fallback
│   └── eplus_outputs.py                  # eplusout.csv / eplusout.sql readers for the dashboard
├── scripts/
│   └── prepare_model.py          # stages a validated EnergyPlus ExampleFiles IDF + EPW
├── assets/                       # staged model.idf / weather.epw / model_meta.json (generated)
└── outputs/
    ├── baseline/                 # baseline run's EnergyPlus outputs
    └── ai/                       # AI-driven run's outputs + agent_decisions.jsonl
```

## Requirements coverage

| Core requirement | Where it lives |
|---|---|
| EnergyPlus simulation via a Python bridge | `eco_loop/sim_env.py` — in-process `pyenergyplus` C API, not a subprocess |
| Open-source LLM, locally hosted | Ollama `llama3.2:3b` via an OpenAI-compatible endpoint (`eco_loop/agent_orchestrator.py`) |
| MCP server exposing agentic tools | `eco_loop/mcp_tools.py` — FastMCP, 6 tools over streamable HTTP |
| Tools parse files & extract runtime errors | `read_error_logs` tails live `eplusout.err` with a hard cap |
| **Feedback** — continuous streamed metrics | Sensors read **every zone timestep** (672/week): zone temps, MRT, RH, occupancy, PMV, facility HVAC power |
| **Reasoning** — comfort, demand, carbon | PMV band `[-0.5, +0.5]`, occupancy state, and `get_grid_carbon_forecast` |
| **Control actions** — dynamic setpoints | `apply_hvac_setpoints`, validated server-side |
| **Forward injection** into the *active* instance | `set_actuator_value` on `Zone Temperature Control`, re-asserted every timestep — no restart, no IDF rewrite |
| Quantifiable savings | −8.5 % summer / −3.7 % winter, with comfort scored identically for both runs |

Deliverables 1–4 are in this repo (source, `.idf` models, dashboard + committed
results, architecture doc). The demonstration video and presentation are
submitted separately.

## Verified results

End-to-end on EnergyPlus 26.1.0 + Ollama `llama3.2:3b`, `5ZoneAirCooled.idf`
with Chicago TMY3, 60-minute control interval, one-week run periods:

| | Baseline | AI-driven | |
|---|---|---|---|
| **Summer week** (15-21 Jul) | 396.87 kWh | **363.04 kWh** | **-8.5%** |
| mean PMV, occupied | -0.43 | **-0.02** | closer to neutral |
| **Winter week** (15-21 Jan) | 67.60 kWh | **65.13 kWh** | **-3.7%** |
| mean PMV, occupied | +0.06 | -0.18 | |

**336/336 real LLM decisions across both weeks - 0 fallbacks, 0 timeouts,
0 actuation errors.** Results are reproducible: the controller runs at
temperature 0 and repeated runs give identical totals.

Reproduce with:

```bash
python main.py run-baseline --start 07-15 --end 07-21 --output-dir outputs/baseline
```

```bash
python main.py run-ai --start 07-15 --end 07-21 --output-dir outputs/ai
```

```bash
python main.py compare --baseline outputs/baseline --ai outputs/ai --month 7
```

On the comfort numbers: the agent keeps occupants **closer to thermal
neutrality on average** than the baseline in summer while cutting 8.5% of
energy. The stricter "percentage of intervals with every zone in band" metric
is lower (75.0% vs 88.2%), because it scores the *worst zone at each timestep* -
one unhappy zone out of five fails the whole interval, and the agent drives a
single setpoint pair for all five. Per zone it achieves 86-96%. This is a real
limitation, documented honestly in
[`ARCHITECTURE.md` §8](ARCHITECTURE.md#8-known-limitation-one-setpoint-pair-for-five-zones)
together with what it would take to fix.

See [`ARCHITECTURE.md` §6](ARCHITECTURE.md#6-measured-results-and-tuning-findings)
for the tuning findings behind these numbers - why model choice must be driven
by available VRAM, why the agent needs the *direction* of a comfort fix rather
than just the PMV value, and why it once ran the chiller in January.

## Building models and what the agent changes

The agent does **not** rewrite `.idf` files. EnergyPlus runs inside the Python
process and setpoints are injected straight into the live solver via
`set_actuator_value`, re-asserted every zone timestep - so the next timestep's
physics uses the setpoint the LLM just chose. That is what makes this a closed
loop rather than a batch of separate runs.

Committed model + result artifacts:

| Path | What it is |
|---|---|
| `assets/model.idf` | Instrumented baseline model (source example + Eco-Loop's output requests) |
| `assets/model_MMDD_MMDD.idf` | Runtime-generated variants with a rewritten `RunPeriod` for representative-period runs |
| `assets/model_meta.json` | Provenance: source model, weather file, discovered zones, timestep |
| `outputs/*/agent_decisions.jsonl` | **The agent's full control trail** - setpoints, reasoning, tool calls, latency, per interval |
| `outputs/*/eplusout.csv` | Per-timestep EnergyPlus results powering the dashboard |

Because the results are committed, a fresh clone can run
`python main.py dashboard` and see the comparison without re-running anything.
See [`ARCHITECTURE.md` §2](ARCHITECTURE.md#what-the-agent-actually-modifies-and-what-it-does-not)
for why runtime injection is used instead of iterative `.idf` regeneration.

## Prerequisites

1. **EnergyPlus 9.5+** installed locally (https://energyplus.net/downloads).
   `config.py` auto-discovers common install locations
   (`C:\EnergyPlusV9-6-0`, `/Applications/EnergyPlus-9-6-0`,
   `/usr/local/EnergyPlus-9-6-0`); otherwise set `ENERGYPLUS_DIR` in `.env`.
2. **Python 3.9+** (the version EnergyPlus 9.5+ ships `pyenergyplus` bindings for).
3. **Ollama** (or vLLM) serving an OpenAI-compatible endpoint. Pick the model
   by what fits your VRAM — see [`ARCHITECTURE.md` §3](ARCHITECTURE.md); a 3B
   model that fits entirely on the GPU beats an 8B one that does not:
   ```bash
   ollama pull llama3.2:3b
   ollama serve
   ```
4. Dependencies — two files, because the dashboard needs far less than the loop:
   ```bash
   pip install -r requirements.txt -r requirements-sim.txt
   ```
   `requirements.txt` alone is enough to run **only** the dashboard against the
   committed results (no EnergyPlus, no Ollama). That is also all Streamlit
   Community Cloud installs.

## Deploy the dashboard

The dashboard reads committed EnergyPlus results, so it runs anywhere — it
imports neither `pyenergyplus` nor the LLM client. That makes it deployable to
**Streamlit Community Cloud** as-is:

1. Go to [share.streamlit.io](https://share.streamlit.io) and sign in with GitHub.
2. **New app → Deploy a public app from GitHub**, then set:
   - Repository `ayushYadav1107/Eco_loop_building_agents`
   - Branch `main`
   - Main file path `dashboard.py`
   - Python version **3.11 or newer** (under *Advanced settings*)
3. Deploy. First build takes a couple of minutes.

The deployed app opens on the summer week and the sidebar switches to winter —
both runs' results are committed, so nothing needs to be regenerated. Running
the closed loop itself still requires a local EnergyPlus and Ollama; the cloud
build has neither, which is exactly why the two requirements files are split.

## Quickstart

```bash
cp .env.example .env
# edit .env: ENERGYPLUS_DIR if not auto-detected, LLM model name if different

python main.py prepare          # stage + instrument an EnergyPlus example model
python main.py run-baseline     # reference run, native thermostat schedules
python main.py run-ai           # LLM closed-loop run (needs Ollama/vLLM running)
python main.py dashboard        # streamlit run dashboard.py
```

Or all at once:

```bash
python main.py run-all
```

By default both runs use EnergyPlus's `-D` (design-day-only) flag - a couple
of simulated days from the model's `SizingPeriod:DesignDay` objects, which is
fast enough for iterative demoing. Pass `--full-year` to run the complete
annual `RunPeriod` instead.

### Picking a different example model

```bash
python -m scripts.prepare_model --list                 # show every example IDF with a thermostat
python -m scripts.prepare_model --idf 5ZoneAirCooled.idf --epw USA_IL_Chicago-OHare.Intl.AP.725300_TMY3.epw
```

### Tool transport

`ECOLOOP_TOOL_TRANSPORT=mcp` (default) runs the real FastMCP HTTP server on a
daemon thread and drives it through a persistent async session
(`eco_loop/mcp_client.py`). Set `ECOLOOP_TOOL_TRANSPORT=direct` (or
`--transport direct`) to call the same tool functions in-process with no HTTP
hop - useful when iterating on prompt/tool logic without needing the protocol
round-trip.

### Inspecting the tools directly

```bash
python main.py serve-mcp
# then point an MCP client (Claude Desktop, MCP Inspector, `fastmcp dev ...`) at
# http://127.0.0.1:8848/mcp
```

## Configuration reference

All of the following live in `.env` (see `.env.example` for defaults):

| Variable | Purpose |
|---|---|
| `ENERGYPLUS_DIR` | EnergyPlus install root (contains `pyenergyplus/`) |
| `ECOLOOP_IDF` / `ECOLOOP_EPW` | Override the staged model paths |
| `ECOLOOP_LLM_BASE_URL` / `_API_KEY` / `_MODEL` | Local LLM endpoint (Ollama: `http://localhost:11434/v1`) |
| `ECOLOOP_CONTROL_INTERVAL_MIN` | How often the agent is consulted (15 or 60 simulated minutes) |
| `ECOLOOP_LLM_TIMEOUT_S` | Hard wall-clock budget per agent turn before falling back |
| `ECOLOOP_NON_BLOCKING_AGENT` | Run the agent off-thread instead of stalling the simulation |
| `ECOLOOP_DESIGN_DAY_ONLY` | Fast demo runs (`-D` flag) vs. full annual `RunPeriod` |
| `ECOLOOP_TOOL_TRANSPORT` | `mcp` (real FastMCP session) or `direct` (in-process) |
| `ECOLOOP_MCP_HOST` / `_PORT` | Where the FastMCP HTTP server listens |

## A note on PMV

Eco-Loop requests EnergyPlus's native `Zone Thermal Comfort Fanger Model PMV`
output, but most stock example models (including `5ZoneAirCooled.idf`) define
their `People` objects *without* enabling a thermal comfort model, so
EnergyPlus never generates that variable and logs:

```
** Warning ** The following Report Variables were requested but not generated
Key=*, VarName=ZONE THERMAL COMFORT FANGER MODEL PMV
```

This warning is harmless. `eco_loop/comfort.py` implements the ISO 7730 Fanger
PMV/PPD model directly and computes PMV from variables every model *does*
report (zone air temperature, mean radiant temperature, relative humidity),
with seasonal clothing assumptions. If the model does enable Fanger comfort,
the native EnergyPlus value is used instead. Either way the agent always has a
comfort signal - the closed loop does not depend on the IDF being authored a
particular way.

## Safety envelope

Every LLM-proposed setpoint passes through three independent layers before it
ever reaches `set_actuator_value` - schema/range validation, a minimum
heating/cooling deadband, and a per-interval rate limit - and a hard timeout
guarantees the simulation never stalls waiting on the model. Details in
[`ARCHITECTURE.md` §5](ARCHITECTURE.md#5-safety-envelope-defense-in-depth).
