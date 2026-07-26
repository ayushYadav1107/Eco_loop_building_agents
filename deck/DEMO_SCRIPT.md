# Eco-Loop — 3-Minute PoC Demonstration Script

The brief asks the video to show **data transferring live from EnergyPlus to the
LLM, and the control actions updating the model automatically**. That is the
thing to prove on camera; the dashboard is the payoff, not the proof.

**Total: 3:00.** Timings are cumulative.

---

## Before you hit record

| Check | Command / action |
|---|---|
| Ollama running with the model warm | `ollama run llama3.2:3b "ready"` then close — first call otherwise costs ~10 s of dead air |
| Baseline results already on disk | `outputs/baseline/eplusout.csv` exists (ship the committed one) |
| Terminal font size | Bump to ~18 pt — the per-interval lines must be readable at 720p |
| Dashboard pre-loaded | Run `python main.py dashboard` in a second terminal, leave the tab open |
| Screen layout | Terminal left ~60%, browser right ~40%, or alt-tab between full screens |
| Close | Slack, notifications, anything that can pop a toast |

Record at **1080p**. Do a silent dry run first — the AI run takes ~6 minutes,
so you will record it and **cut**, not stream it live.

---

## 0:00 – 0:20 · The problem, in one breath

**Show:** Slide 1 of the deck (title + the −8.5% / 336-336 callouts).

> "Buildings are about 40% of global energy. Building management systems still
> run on fixed clock schedules — they can't react to weather, occupancy or grid
> carbon. Eco-Loop replaces that schedule with an agent: EnergyPlus for the
> physics, a local open-source LLM for the judgement, and the Model Context
> Protocol carrying every reading and every command between them."

*Cut on the word "between them" — don't linger.*

---

## 0:20 – 0:40 · The architecture, once

**Show:** Slide 3 — the closed-loop diagram.

> "Sensors are read every zone timestep. Once an hour of simulated time the
> aggregated state goes to the model through six MCP tools. It returns
> setpoints, they pass a validation gate, and they're injected straight into the
> running solver — no file rewrite, no restart. The loop closes inside one
> simulation."

*Trace the loop with the cursor while you say it: EnergyPlus → MCP → LLM →
gate → back into EnergyPlus.*

---

## 0:40 – 1:40 · The live loop — **this is the money shot**

**Show:** Terminal. Run:

```bash
python main.py run-ai --start 07-15 --end 07-21
```

Let the per-interval lines scroll. Each line is one complete closed loop:

```
[ai 07-17 09:00] OAT= 24.9C PMV=-1.02 kW= 2.09 -> cool=24.8 heat=19.2 (llm, 1.3s)
```

> "Every line here is one full cycle. On the left, data coming *out* of
> EnergyPlus — outdoor temperature, thermal comfort, HVAC power. On the right,
> the setpoints the model just sent *back in*, and how long it took to decide.
> `llm` means the model actually made the call. Watch the setpoints change as
> the building warms up."

**Point at one line and read it aloud.** Then:

> "That's 168 of these across a simulated week, and 336 across both seasons —
> every single one a real model decision. Zero fallbacks, zero timeouts, zero
> actuation errors."

**Editing note:** record the full ~6 minutes, then speed-ramp the middle to
4–8× so viewers see the counter climbing. Keep the first ~10 seconds and the
last ~5 seconds at normal speed so individual lines are legible.

---

## 1:40 – 2:05 · Proof it is really the agent driving

**Show:** `outputs/ai/agent_decisions.jsonl` — either `code` it, or scroll the
Agent-behaviour section of the dashboard.

> "Every decision is logged with the model's own stated reasoning. Here it says
> 'raise cooling setpoint — this fixes comfort and saves energy'. Here it says
> 'hold — trimming further would push occupants out of the comfort band'. That's
> the agent reasoning about the trade-off, not a rule firing."

*Highlight one `"reason"` string on screen. One is enough — don't read three.*

---

## 2:05 – 2:40 · The result

**Show:** Dashboard, top of page → scroll slowly to the per-zone chart.

> "Against the identical building on its native schedule: 8.5% less HVAC energy
> across a summer week. And comfort didn't pay for it — mean PMV moved from
> −0.43 to −0.02, *closer* to thermal neutral than the baseline. Per zone the
> agent holds 85 to 96% of occupied time inside the comfort band."

Then switch the sidebar to **Winter week**:

> "Same loop, heating season, no code change — 3.7%."

*The theme toggle is a nice half-second flourish if you have room. Skip it if
you're over time.*

---

## 2:40 – 3:00 · Honesty + close

**Show:** Slide 6 (limitation + reproduce commands), or the terminal.

> "One honest limitation: a single setpoint pair drives five differently-loaded
> zones, so the strictest worst-zone metric is lower than the per-zone numbers.
> Per-zone setpoints are the next step — the actuator handles are already there.
> Everything is committed and runs at temperature zero, so anyone can clone the
> repo and reproduce these exact figures. Thank you."

---

## What NOT to show

- The EnergyPlus install, `pip install`, or any setup — assume it works.
- `main.py prepare` — boring, and it isn't the loop.
- Code files scrolling. Judges cannot read code at video speed.
- The MCP server starting up. Say "over MCP", show the diagram instead.
- More than one reasoning string, more than one chart per point.

## If you overrun

Cut in this order: the winter switch (0:10), the theme toggle (0:05), then
shorten the architecture beat to one sentence (0:10).

## The single most important frame

If a judge watches only ten seconds, make it the scrolling
`[ai …] OAT= … PMV= … -> cool= … heat= … (llm, 1.3s)` lines. That one frame
proves the feedback loop, the reasoning and the forward injection at once —
which is exactly what the brief asks the video to demonstrate.
