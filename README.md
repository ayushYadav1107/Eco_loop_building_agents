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

## Verified results

End-to-end on EnergyPlus 26.1.0 + Ollama `llama3.2:3b`, `5ZoneAirCooled.idf`
with Chicago TMY3, design-day run, 60-minute control interval:

```
baseline HVAC : 80.88 kWh
AI-driven HVAC: 73.47 kWh
savings       : +7.41 kWh  (+9.2%)

agent turns   : 48   (48 real LLM decisions, 0 fallbacks, 0 timeouts)
PMV occupied  : mean -0.01,  9/11 intervals inside [-0.5, +0.5]
latency       : mean 6.4 s,  p50 4.9 s,  max 43.3 s
```

Reproduce with `python main.py run-all`. See
[`ARCHITECTURE.md` §6](ARCHITECTURE.md#6-measured-results-and-tuning-findings)
for the tuning findings behind these numbers - in particular why model choice
must be driven by available VRAM, and why the agent needs the *direction* of a
comfort fix rather than just the PMV value.

## Prerequisites

1. **EnergyPlus 9.5+** installed locally (https://energyplus.net/downloads).
   `config.py` auto-discovers common install locations
   (`C:\EnergyPlusV9-6-0`, `/Applications/EnergyPlus-9-6-0`,
   `/usr/local/EnergyPlus-9-6-0`); otherwise set `ENERGYPLUS_DIR` in `.env`.
2. **Python 3.9+** (the version EnergyPlus 9.5+ ships `pyenergyplus` bindings for).
3. **Ollama** (or vLLM) serving an OpenAI-compatible endpoint:
   ```bash
   ollama pull llama3.1:8b-instruct-q4_K_M
   ollama serve
   ```
4. `pip install -r requirements.txt`

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
