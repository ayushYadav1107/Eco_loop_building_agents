# Eco-Loop Building Agents - System Architecture

## 1. Overview

Eco-Loop closes the loop between a running EnergyPlus simulation and a locally
hosted LLM. Every `N` simulated minutes (15 or 60, configurable), the building's
aggregated state - zone temperatures, humidity, PMV, HVAC electric demand, grid
carbon intensity - is handed to the LLM through a set of MCP tools. The model
reasons over that state, calls tools to gather more context if needed, and
finishes by calling `apply_hvac_setpoints`, whose validated output is written
back into the simulation's actuators before the next timestep.

```
 EnergyPlus (C runtime)                 Python process
┌─────────────────────────┐            ┌──────────────────────────────────────┐
│ pyenergyplus.api         │            │ eco_loop.sim_env.EnergyPlusSimulation │
│                          │  callback  │                                      │
│ end-zone-timestep ───────┼───────────▶│  _Accumulator (per-interval buffer)   │
│  (every sub-hourly step) │            │        │                             │
│                          │            │        ▼ every control interval      │
│ actuators re-asserted ◀──┼────────────┼── BuildingStateBus.publish_state()   │
│  every timestep          │            │        │                             │
└─────────────────────────┘            │        ▼                             │
                                        │  agent_orchestrator.Agent.decide()    │
                                        │        │  chat.completions.create()   │
                                        │        ▼                             │
                                        │  ┌───────────────────────────────┐   │
                                        │  │ FastMCP server (daemon thread) │   │
                                        │  │  get_current_building_state    │   │
                                        │  │  get_recent_history             │   │
                                        │  │  read_error_logs                │   │
                                        │  │  get_grid_carbon_forecast       │   │
                                        │  │  get_control_policy             │   │
                                        │  │  apply_hvac_setpoints  ─────────┼───┼─▶ BuildingStateBus.stage_command()
                                        │  └───────────────────────────────┘   │
                                        └──────────────────────────────────────┘
                                                       ▲
                                                       │ OpenAI-compatible API
                                                       │ (http://localhost:11434/v1)
                                        ┌──────────────────────────┐
                                        │ Ollama / vLLM             │
                                        │ Llama 3 8B / Mistral       │
                                        └──────────────────────────┘
```

Three threads, one shared blackboard:

1. **The EnergyPlus thread** - runs `run_energyplus()`; owns every C-API call.
2. **The FastMCP HTTP server thread** - serves tool calls over streamable HTTP.
3. **The agent's own call** happens synchronously *on the EnergyPlus thread*
   (by default): `Agent.decide()` blocks the callback until it returns.

The only object all three touch is `eco_loop.state_bus.BUS`, a single
`threading.RLock`-guarded blackboard. The simulation thread is the sole writer
of building state; the MCP tool `apply_hvac_setpoints` is the sole writer of
pending commands. Neither thread ever blocks waiting on the other's lock for
more than a few microseconds.

## 2. Tool-Calling Architecture: bridging the LLM and the C simulation loop

FastMCP (`eco_loop/mcp_tools.py`) exposes six tools, each a thin, validated
wrapper around `BuildingStateBus`:

| Tool | Direction | Purpose |
|---|---|---|
| `get_current_building_state` | read | Latest aggregated sensor snapshot |
| `get_recent_history` | read | Last N intervals + past decisions (trend detection) |
| `get_grid_carbon_forecast` | read | Diurnal carbon-intensity curve, for pre-cooling |
| `get_control_policy` | read | The hard setpoint bounds, so the model doesn't guess |
| `read_error_logs` | read | Tail of `eplusout.err`, bounded (see §4) |
| `apply_hvac_setpoints` | **write** | The only path from LLM output to an actuator |

**Why a real MCP server and not just Python function calls?** The brief asks
for FastMCP specifically because it makes the tool boundary a genuine protocol
boundary: the schema the LLM sees (`OPENAI_TOOL_SPECS` in `mcp_tools.py`) is
the same JSON-Schema FastMCP would serve to Claude Desktop, an MCP Inspector,
or any other MCP client - swapping the cognitive engine for a hosted model
later requires no changes to the tool layer. It also means the security
boundary (`SetpointCommand` validation) lives on the *server* side, not
scattered through client code, so it cannot be bypassed by a different prompt.

**Why does the simulation thread never call FastMCP directly?** Because
`fastmcp`'s client is asyncio-native and `pyenergyplus` callbacks are
synchronous, C-invoked functions with no event loop of their own.
`eco_loop/mcp_client.py`'s `MCPToolBridge` runs a private `asyncio` event loop
on its own daemon thread, opens one long-lived MCP session for the whole
simulation (paying the handshake cost once, not once per control interval),
and exposes a plain blocking `call(name, args, timeout)` that the agent
orchestrator invokes like a normal function. `ECOLOOP_TOOL_TRANSPORT=direct`
in `.env` swaps this for `DirectToolBridge`, which calls the same tool
functions in-process with zero IPC - useful for latency-sensitive demos or
when FastMCP's HTTP transport is unavailable; the tool *contracts and
validation* are identical either way, since both paths ultimately call the
functions in `mcp_tools.TOOL_REGISTRY`.

**Why is `apply_hvac_setpoints` the only write path?** Everything downstream
of the LLM is untrusted input. `apply_hvac_setpoints` constructs a
`pydantic.BaseModel` (`eco_loop.schemas.SetpointCommand`) with field and
model validators enforcing:
- absolute range (`ControlPolicy.cooling_sp_min/max`, `heating_sp_min/max`),
- minimum heating/cooling deadband (prevents simultaneous heat+cool "fighting"),
- (at the simulation layer) a maximum per-interval rate of change
  (`SetpointCommand.rate_limited`), which stops thermostat hunting even if the
  model asks for a large swing.

A command that fails validation is rejected with `accepted: false` and a
reason string; the previously active setpoints stay in force. The model sees
the rejection and can retry - it never has a path to write an invalid value
into `set_actuator_value`.

### What the agent actually modifies (and what it does not)

This is worth stating explicitly, because there are two very different ways to
"let an LLM change a building model" and they produce different artifacts.

**What Eco-Loop does: live actuator injection.** The agent never edits an
`.idf` file. EnergyPlus runs *inside the Python process*, and the agent's
decisions are written straight into the running solver through the Data
Exchange API - `set_actuator_value` on the `Zone Temperature Control /
Cooling Setpoint` and `Heating Setpoint` actuators, re-asserted on **every**
zone timestep (EnergyPlus actuator overrides do not latch). The very next
timestep's heat balance is solved using the setpoint the LLM just chose. There
is no file rewrite, no re-run, and no restart anywhere in the loop.

**What it deliberately does not do: iterative `.idf` regeneration.** The
alternative pattern - have the LLM rewrite the model file and launch a fresh
simulation each time - cannot close a loop *within* a simulation. It can only
compare whole runs after the fact, so it cannot respond to a zone drifting out
of comfort at 14:00 on day 3. Runtime injection is what makes this a genuine
closed loop rather than a batch parameter sweep.

The practical consequence is that the "modified building models" this project
produces are:

| Artifact | What it is |
|---|---|
| `assets/model.idf` | The instrumented baseline - the source example model plus the `Output:Variable` / `Output:Meter` / `Output:SQLite` requests Eco-Loop needs. Committed. |
| `assets/model_MMDD_MMDD.idf` | Runtime-generated variants where `main.py --start/--end` rewrote the `RunPeriod` for a representative-period study. Committed. |
| `outputs/*/agent_decisions.jsonl` | **The actual record of what the agent changed** - one JSON line per control interval with the chosen setpoints, the model's stated reason, which tools it called, decision latency, and whether the turn was a real LLM decision or a fallback. Committed. |

If you want to see "what the AI did to the building", `agent_decisions.jsonl`
is the file to read - it is the moment-by-moment control trail, and it is what
the dashboard's PMV chart and decision table are built from. The baseline run
writes no such file by construction: it is the same model with the agent
detached, so the native thermostat schedules run untouched and the comparison
isolates exactly one variable.

## 3. Prompt Latency Management

An LLM call inside an EnergyPlus callback is a hard real-time constraint: the
C runtime is blocked on the Python call returning. Three layers keep that
bounded:

1. **Interval batching (throttling).** The callback fires every sub-hourly
   zone timestep, but `_Accumulator` only aggregates - it does not call the
   agent. `EnergyPlusSimulation._on_control_interval` fires once every
   `control_interval_min` simulated minutes (15 or 60; `ECOLOOP_CONTROL_INTERVAL_MIN`
   in `.env`). This is the single biggest latency lever: it turns "one LLM call
   per zone-timestep" (could be thousands per day) into "one LLM call per
   quarter-hour of simulated time" (96/day), amortizing inference cost across
   real building-physics dynamics that do not change meaningfully faster than
   that.
2. **Hard wall-clock deadline.** `Agent.decide()` computes `deadline =
   time.monotonic() + ECOLOOP_LLM_TIMEOUT_S` once, at the start of the turn,
   and every subsequent LLM call and every tool call is given only the
   *remaining* budget (`self.client...create(..., timeout=remaining)`,
   `self.bridge.call(..., timeout=min(8.0, remaining))`). The tool-execution
   loop (`_run_tool_loop`) also caps the number of reasoning rounds
   (`ECOLOOP_LLM_MAX_TOOL_ROUNDS`, default 4) so a model stuck calling
   read-only tools in a loop cannot spin forever even inside the time budget.
3. **Fallback to the last-known-good command.** If the deadline is exceeded,
   the model errors, or it returns prose without ever calling
   `apply_hvac_setpoints`, `Agent._fallback()` returns immediately with the
   **previously accepted command** (or the schedule baseline on the very first
   turn), tagged `source="fallback"` and `confidence=0.0` for later audit in
   the dashboard's decision trail. `sim_env._actuate()` re-asserts whatever
   command is currently active on *every* timestep regardless, so a slow or
   failed agent turn degrades gracefully to "hold current setpoints" rather
   than ever halting or corrupting the simulation.
4. **Local inference quantization (deployment-time lever).** The loop is
   inference-engine-agnostic (`OpenAI(base_url=..., api_key=...)` against
   Ollama or vLLM). For interactive-latency demos, a 4-bit/5-bit GGUF quant of
   an 8B model (`ollama pull llama3.1:8b-instruct-q4_K_M`) comfortably clears a
   15-25s turn budget on a single consumer GPU; the timeout/fallback machinery
   above is what makes that a *tuning* choice rather than a correctness
   requirement.
5. **Optional non-blocking mode** (`ECOLOOP_NON_BLOCKING_AGENT=true` /
   `--non-blocking`). Runs `Agent.decide()` on a background thread via a
   single-worker `ThreadPoolExecutor`; the simulation thread never waits - it
   applies whichever decision (if any) finished since the last interval and
   keeps the previous command active otherwise. Trades one interval of
   staleness for zero simulation slowdown; useful for design-day sweeps where
   wall-clock throughput matters more than per-interval reactivity.

## 4. Log Handling: bounded-context `read_error_logs`

`eplusout.err` grows for the entire run and can reach megabytes on a model
with many `** Warning **` lines (out-of-range schedules, unmet-hours notices,
etc.). Handing that file to an LLM verbatim would blow the context window and
add unbounded latency to a call that is supposed to be a rare diagnostic aid,
not the main data path.

`mcp_tools.read_error_logs(lines=20)`:

- **Never loads the whole file.** `_tail_file()` seeks to
  `max(0, filesize - lines * 256 - 2048)` bytes from the end and reads forward,
  so cost is `O(lines)`, not `O(file size)`, even for a multi-MB `.err` file.
- **Hard-caps `lines` at 60** (`_MAX_ERR_LINES`) and each returned line at 220
  characters (`_MAX_LINE_CHARS`) - a worst-case response is a few KB, not
  megabytes, regardless of what the model requests.
- **Tolerates a file EnergyPlus still holds open.** On Windows in particular, a
  concurrent read against a file another process is actively writing can raise
  `OSError`; the tool catches that and returns `{"available": false, "reason":
  ...}` instead of raising into the MCP layer (which would surface as a tool
  error to the model - handled gracefully, but wasted a turn) or, worse,
  propagating into the EnergyPlus callback.
- **Reports severity counts, not just raw text**
  (`severe_or_fatal_in_tail`, `warnings_in_tail`), so the model can decide
  whether the tail it received actually needs deeper attention without having
  to re-parse `** Severe  **` / `** Warning **` markers itself.
- **The system prompt explicitly rate-limits its own use**: "Only call
  `read_error_logs` if a reading looks physically impossible... it costs
  context, so do not call it speculatively." Combined with the per-turn
  `ECOLOOP_LLM_MAX_TOOL_ROUNDS` cap, a model cannot turn log-reading into an
  unbounded context sink even if it tries.

## 5. Safety envelope (defense in depth)

Three independent layers stand between an LLM output and the simulation:

1. **Schema validation** (`SetpointCommand` field/model validators) - rejects
   out-of-policy or physically inconsistent commands outright.
2. **Rate limiting** (`SetpointCommand.rate_limited`) - clamps *accepted*
   commands to `POLICY.max_step_per_interval` degC of movement from the
   previous command, applied unconditionally in `sim_env._on_control_interval`
   regardless of what the model or the MCP tool already validated.
3. **Timeout fallback** (`Agent._fallback`) - guarantees a policy-valid command
   is always returned in bounded time, so a hung or misbehaving model degrades
   to "hold the last good setpoint," never to an unhandled exception in the
   EnergyPlus callback.

None of these three depend on the LLM behaving well; they hold even against an
adversarial or simply broken model response.

## 6. Measured results and tuning findings

Validated end-to-end on EnergyPlus 26.1.0 + Ollama, `5ZoneAirCooled.idf` with
Chicago TMY3 weather, design-day run (2 days), 60-minute control interval,
`llama3.2:3b`:

| Metric | Baseline | AI-driven |
|---|---|---|
| HVAC electricity | 80.88 kWh | **73.47 kWh (-9.2%)** |
| Agent turns | - | 48 |
| Real LLM decisions | - | **48/48 (0 fallbacks, 0 timeouts)** |
| PMV during occupancy | - | mean -0.01, **9/11 intervals in band** |
| Agent latency | - | mean 6.4 s, p50 4.9 s, max 43.3 s |

Three findings from getting there are worth recording, because each is a trap
that any similar LLM-in-the-loop system will hit:

**Model size must be chosen against VRAM, not benchmark quality.** On a 4 GB
RTX 4050, `llama3.1:8b` (5.6 GB resident) is split 25% GPU / 75% CPU by Ollama
and takes ~18 s per tool call; `llama3.2:3b` (2.6 GB) fits entirely in VRAM and
takes ~4.5 s - a 4x speedup that decides whether the per-turn deadline is
achievable at all. This is the concrete form the "local inference quantization"
lever from §3 takes in practice.

**A prompt that mandates exploration will starve the decision.** The first
working run produced only 4/48 real decisions. Every one of the 44 fallbacks
had the identical tool sequence - `get_current_building_state`,
`get_recent_history`, `get_grid_carbon_forecast` - because the system prompt
listed those as numbered steps and the round budget (`max_tool_rounds`) ran out
before the model reached the decision step. Two changes fixed it: exploration
tools were demoted to explicitly optional, and `Agent._force_decision()` now
closes any turn that used its whole budget without committing, re-calling the
model with `tool_choice` pinned to `apply_hvac_setpoints`. Result: 48/48.

**A small model needs the direction of the fix, not just the metric.** Given
PMV = -1.0 and a `too_cold` label, `llama3.2:3b` reliably *lowered* the cooling
setpoint to 22 C - simultaneously the coldest and the most energy-intensive
choice available, and the reason the first complete run came in 8% *worse* than
baseline. The model had the number and the label but not the causal link to an
action. Encoding the physics explicitly - both as a prompt section ("PMV below
-0.5 means over-cooled: RAISE cooling_sp, this fixes comfort AND cuts energy")
and as a computed `comfort_action_hint` field on every observation
(`schemas.BuildingState.comfort_action_hint`) - flipped the same model from
-8.0% to +9.2%. Worth noting *why* this is a win-win: with summer clothing
(clo = 0.5, met = 1.2) ISO 7730 puts comfort-neutral near a 25 C zone
temperature, so the baseline's 23.9 C setpoint was already over-cooling. The
agent's job was to find that, and it could not until the observation said which
way to move.

## 7. Data flow summary for the dashboard

- `sim_env.py` runs EnergyPlus with `-r` (ReadVarsESO), producing
  `eplusout.csv`, and `idf_utils.instrument_idf` injects
  `Output:SQLite,SimpleAndTabular;` so `eplusout.sql` exists as a fallback.
- `BuildingStateBus.record_decision` appends one JSON line per control
  interval to `outputs/ai/agent_decisions.jsonl` - the ground truth for the
  dashboard's PMV scatter and decision trail (setpoints, reason, latency,
  fallback vs. LLM source).
- `eco_loop/eplus_outputs.py` parses the ReadVarsESO CSV header format
  (`Key:Variable [Unit](Frequency)`) into tidy per-zone/per-facility series;
  `dashboard.py` (Streamlit) joins those with the decision log for the
  dual-axis energy chart, the PMV boundary plot, and the headline % savings
  metric.
