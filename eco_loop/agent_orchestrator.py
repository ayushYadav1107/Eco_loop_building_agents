"""
Module 3 - the cognitive loop.

`Agent.decide()` is called once per control interval from the EnergyPlus
callback thread (see `sim_env._on_control_interval`). It:

  1. builds a compact system + user prompt from the current `BuildingState`,
  2. runs the OpenAI-compatible tool-calling loop against a local LLM
     (Ollama / vLLM), executing whatever MCP tools the model requests,
  3. enforces a hard wall-clock budget across the *whole* turn so a slow local
     model can never stall the simulation,
  4. returns an `AgentDecision` whose `command` has already passed
     `SetpointCommand` validation - or a baseline-fallback decision if the
     model timed out, errored, or never called `apply_hvac_setpoints`.
"""
from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional

from openai import OpenAI

from config import POLICY, SETTINGS
from eco_loop.mcp_client import build_bridge
from eco_loop.mcp_tools import OPENAI_TOOL_SPECS
from eco_loop.schemas import AgentDecision, BuildingState, SetpointCommand

SYSTEM_PROMPT = f"""You are Eco-Loop, an autonomous HVAC supervisory controller for a single \
commercial building running inside an EnergyPlus simulation. You make one \
decision per control interval ({SETTINGS.control_interval_min} simulated minutes).

OBJECTIVE
Minimise HVAC electrical energy and its carbon impact (grid intensity varies \
through the day - see get_grid_carbon_forecast) while keeping every occupied \
zone's Predicted Mean Vote (PMV) inside [{POLICY.pmv_low}, {POLICY.pmv_high}] \
(ISO 7730 / ASHRAE-55). Comfort is a hard constraint, not a nice-to-have: a \
PMV excursion is a failure even if it saves energy. When the building is \
unoccupied you may set back aggressively toward the policy limits.

HOW TO DECIDE, EVERY TURN
The user message already contains the current building snapshot. In most turns \
that is all you need, so go straight to the decision.

REQUIRED: every turn must end with exactly one apply_hvac_setpoints call. That \
is the only action that changes anything - a turn without it is a wasted turn \
and the building keeps its previous setpoints.

Optional, only when the snapshot is genuinely not enough (you have a limited \
number of tool calls per turn, so spend them carefully):
- get_recent_history - if you need the trend or whether your last change worked.
- get_grid_carbon_forecast - if you are weighing pre-cooling before a dirty peak.
- get_current_building_state - if you need a fresher reading than the snapshot.
- read_error_logs - ONLY if a reading looks physically impossible (e.g. PMV \
near +/-3, temperature jumping >5C in one interval). Never call it speculatively.

HOW SETPOINTS MOVE PMV (this is the core physics - get it right)
The cooling setpoint is the temperature the zone is held DOWN to. Raising it
lets the zone get warmer; lowering it forces more cooling.
- PMV below -0.5 (too_cold): occupants are over-cooled. RAISE cooling_sp. This
  is the win-win case - it fixes comfort AND cuts energy, because you are
  paying to overcool. Never respond to "too_cold" by lowering cooling_sp.
- PMV above +0.5 (too_warm): LOWER cooling_sp to cool more. This costs energy;
  spend it, comfort is the hard constraint.
- PMV already within [-0.5, +0.5]: you have room to optimise. Nudge cooling_sp
  UP for energy/carbon savings, stopping before PMV would exceed +0.5.
Rule of thumb for this building in cooling season: PMV is near neutral around a
25 C zone temperature, and each 1 C of cooling setpoint is worth roughly 0.3
PMV. A cooling setpoint below 23 C is almost always both uncomfortably cold and
wasteful.

HARD RULES
- Setpoints must stay within the ranges reported by get_control_policy. Values \
outside that range are rejected automatically - do not push against the edge \
expecting it to clamp, choose a value already inside the range.
- cooling_sp must exceed heating_sp by at least the minimum deadband reported \
by get_control_policy, or the command is rejected outright.
- Movement is rate-limited server-side; do not attempt a large jump in one turn.
- If any zone is already outside the PMV band, prioritise moving the setpoint \
toward comfort this turn over further energy savings.
- If you are uncertain or the state looks corrupted, call apply_hvac_setpoints \
with the current active setpoints unchanged rather than guessing.
- You must end every turn with exactly one apply_hvac_setpoints call. Do not \
answer in prose only.
"""


class Agent:
    """Owns the LLM client, the MCP tool bridge, and the tool-execution loop."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        timeout_s: Optional[float] = None,
        max_tool_rounds: Optional[int] = None,
        tool_transport: Optional[str] = None,
    ) -> None:
        self.model = model or SETTINGS.llm_model
        self.timeout_s = float(timeout_s or SETTINGS.llm_timeout_s)
        self.max_tool_rounds = int(max_tool_rounds or SETTINGS.llm_max_tool_rounds)
        self.temperature = SETTINGS.llm_temperature

        self.client = OpenAI(
            base_url=base_url or SETTINGS.llm_base_url,
            api_key=api_key or SETTINGS.llm_api_key,
            timeout=self.timeout_s,
            max_retries=0,  # a local retry would blow the interval's time budget
        )
        self.bridge = build_bridge(tool_transport)
        self._previous_command: Optional[SetpointCommand] = None

        self.stats = {"turns": 0, "llm_ok": 0, "fallbacks": 0, "timeouts": 0, "errors": 0}

    # ------------------------------------------------------------------ #
    def decide(self, state: BuildingState) -> AgentDecision:
        """Blocking entry point called once per control interval."""
        self.stats["turns"] += 1
        t0 = time.monotonic()
        deadline = t0 + self.timeout_s

        try:
            command, tool_calls = self._run_tool_loop(state, deadline)
        except _Timeout:
            self.stats["timeouts"] += 1
            return self._fallback(state, t0, "llm turn exceeded timeout budget", [])
        except Exception as exc:
            self.stats["errors"] += 1
            return self._fallback(state, t0, f"agent error: {exc}", [])

        latency = time.monotonic() - t0
        if command is None:
            self.stats["fallbacks"] += 1
            return self._fallback(state, t0, "model did not call apply_hvac_setpoints", tool_calls)

        self.stats["llm_ok"] += 1
        self._previous_command = command
        return AgentDecision(
            timestamp=state.timestamp,
            sim_minutes=state.sim_minutes,
            command=command,
            source="llm",
            latency_s=round(latency, 2),
            tool_calls=tool_calls,
            model=self.model,
            state_digest=state.to_prompt_dict(),
        )

    # ------------------------------------------------------------------ #
    def _run_tool_loop(self, state: BuildingState, deadline: float):
        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    "Start of control interval. Current raw snapshot for reference "
                    f"(call the tools for the authoritative, live view):\n"
                    f"{json.dumps(state.to_prompt_dict())}"
                ),
            },
        ]

        tool_calls_made: List[str] = []
        final_command: Optional[SetpointCommand] = None

        for _round in range(self.max_tool_rounds):
            remaining = deadline - time.monotonic()
            if remaining <= 0.5:
                raise _Timeout()

            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                tools=OPENAI_TOOL_SPECS,
                tool_choice="auto",
                temperature=self.temperature,
                timeout=max(0.5, remaining),
            )
            choice = response.choices[0]
            msg = choice.message
            messages.append(msg.model_dump(exclude_none=True))

            calls = getattr(msg, "tool_calls", None) or []
            if not calls:
                # Model answered in prose without acting - nudge it once, then give up.
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "You must call apply_hvac_setpoints to finish this turn. "
                            "Call it now with your decision."
                        ),
                    }
                )
                continue

            for call in calls:
                if time.monotonic() > deadline:
                    raise _Timeout()

                name = call.function.name
                try:
                    args = json.loads(call.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}

                remaining = max(0.5, deadline - time.monotonic())
                result = self.bridge.call(name, args, timeout=min(8.0, remaining))
                tool_calls_made.append(name)

                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": json.dumps(result, default=str)[:4000],
                    }
                )

                if name == "apply_hvac_setpoints" and result.get("accepted"):
                    staged = result.get("staged_setpoints_c", {})
                    final_command = SetpointCommand(
                        cooling_sp=staged.get("cooling", args.get("cooling_sp")),
                        heating_sp=staged.get("heating", args.get("heating_sp")),
                        reason=str(args.get("reason", ""))[:400],
                    )

            if final_command is not None:
                break

        if final_command is None:
            # The model spent its whole exploration budget on read-only tools
            # without ever committing (very common with small quantized models,
            # which follow "gather context" instructions literally). Close the
            # turn by *forcing* the decision tool rather than falling back.
            final_command = self._force_decision(messages, deadline, tool_calls_made)

        return final_command, tool_calls_made

    def _force_decision(
        self,
        messages: List[Dict[str, Any]],
        deadline: float,
        tool_calls_made: List[str],
    ) -> Optional[SetpointCommand]:
        """Last-chance call with `tool_choice` pinned to apply_hvac_setpoints."""
        remaining = deadline - time.monotonic()
        if remaining <= 1.0:
            return None

        messages = messages + [
            {
                "role": "user",
                "content": (
                    "Time to decide. Using what you have already gathered, call "
                    "apply_hvac_setpoints now with your chosen cooling_sp and "
                    "heating_sp and a one-sentence reason."
                ),
            }
        ]
        forced = {"type": "function", "function": {"name": "apply_hvac_setpoints"}}

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                tools=OPENAI_TOOL_SPECS,
                tool_choice=forced,
                temperature=self.temperature,
                timeout=max(1.0, remaining),
            )
        except Exception:
            # Some servers reject a pinned tool_choice; retry letting it choose.
            remaining = deadline - time.monotonic()
            if remaining <= 1.0:
                return None
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    tools=OPENAI_TOOL_SPECS,
                    tool_choice="auto",
                    temperature=self.temperature,
                    timeout=max(1.0, remaining),
                )
            except Exception:
                return None

        calls = getattr(response.choices[0].message, "tool_calls", None) or []
        for call in calls:
            if call.function.name != "apply_hvac_setpoints":
                continue
            try:
                args = json.loads(call.function.arguments or "{}")
            except json.JSONDecodeError:
                continue
            remaining = max(0.5, deadline - time.monotonic())
            result = self.bridge.call("apply_hvac_setpoints", args, timeout=min(8.0, remaining))
            tool_calls_made.append("apply_hvac_setpoints")
            if result.get("accepted"):
                staged = result.get("staged_setpoints_c", {})
                return SetpointCommand(
                    cooling_sp=staged.get("cooling", args.get("cooling_sp")),
                    heating_sp=staged.get("heating", args.get("heating_sp")),
                    reason=str(args.get("reason", ""))[:400],
                )
        return None

    # ------------------------------------------------------------------ #
    def _fallback(self, state: BuildingState, t0: float, reason: str, tool_calls: List[str]) -> AgentDecision:
        """Hold the last accepted command, or the schedule baseline if none exists.

        This is the "prevent simulation halting" guarantee the brief asks for:
        whatever goes wrong upstream, this method always returns a
        policy-valid command in bounded time.
        """
        command = self._previous_command or SetpointCommand.baseline(reason)
        command = SetpointCommand(
            cooling_sp=command.cooling_sp,
            heating_sp=command.heating_sp,
            reason=reason,
            confidence=0.0,
        )
        return AgentDecision(
            timestamp=state.timestamp,
            sim_minutes=state.sim_minutes,
            command=command,
            source="fallback",
            latency_s=round(time.monotonic() - t0, 2),
            tool_calls=tool_calls,
            model=self.model,
            error=reason,
            state_digest=state.to_prompt_dict(),
        )

    def close(self) -> None:
        self.bridge.close()


class _Timeout(Exception):
    """Internal signal: the wall-clock budget for this turn is exhausted."""
