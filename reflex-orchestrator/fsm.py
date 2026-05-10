"""Debounced finite state machine for the bread-in-toaster pipeline.

The FSM consumes VLM observations and emits at most one Action per tick:
  - Action.DISPATCH(skill) — launch a policy
  - Action.WAIT             — keep observing
  - Action.DONE             — terminal
  - Action.HALT             — terminal: safety monitor halted us

Debouncing: a candidate transition must be supported by `debounce_ticks`
consecutive consistent observations before it commits. This is what keeps a
single hallucinated frame from firing the wrong skill.

Safety integration: the FSM does NOT enforce safety preconditions itself —
that lives in safety.py. The orchestrator loop wraps every DISPATCH action
through the SafetyMonitor's preflight check before launching.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class State(str, Enum):
    IDLE = "IDLE"             # waiting for bread to appear
    PLACING = "PLACING"       # Policy A running
    PLACED = "PLACED"         # bread in toaster, lever up
    PRESSING = "PRESSING"     # Policy B running
    TOASTING = "TOASTING"     # lever down, waiting for pop
    DONE = "DONE"
    SAFE_HALT = "SAFE_HALT"   # safety monitor stopped us


class ActionKind(str, Enum):
    DISPATCH = "DISPATCH"
    WAIT = "WAIT"
    DONE = "DONE"
    HALT = "HALT"


@dataclass
class Action:
    kind: ActionKind
    skill: str | None = None
    reason: str = ""


@dataclass
class FSM:
    debounce_ticks: int = 3
    state: State = State.IDLE
    _candidate: State | None = None
    _candidate_count: int = 0
    history: list[tuple[State, dict]] = field(default_factory=list)

    def force_halt(self, reason: str = "") -> None:
        if self.state != State.SAFE_HALT:
            self.history.append((self.state, {"reason": reason}))
        self.state = State.SAFE_HALT
        self._candidate = None
        self._candidate_count = 0

    def tick(self, obs: dict[str, Any], skill_running: bool) -> Action:
        if self.state == State.SAFE_HALT:
            return Action(ActionKind.HALT, reason="safety halt")

        next_state = self._next_from(obs, skill_running)

        if next_state == self.state:
            self._candidate = None
            self._candidate_count = 0
            return self._action_for(self.state, skill_running)

        if next_state == self._candidate:
            self._candidate_count += 1
        else:
            self._candidate = next_state
            self._candidate_count = 1

        if self._candidate_count >= self.debounce_ticks:
            self.history.append((self.state, dict(obs)))
            self.state = next_state
            self._candidate = None
            self._candidate_count = 0
            return self._action_for(self.state, skill_running=False)

        return Action(ActionKind.WAIT,
                      reason=f"debouncing {self.state}->{next_state} ({self._candidate_count}/{self.debounce_ticks})")

    def _next_from(self, obs: dict[str, Any], skill_running: bool) -> State:
        bread_visible = obs.get("bread_visible", False)
        in_toaster   = obs.get("bread_in_toaster", False)
        lever_down   = obs.get("lever_down", False)
        popped       = obs.get("toast_popped", False)

        if popped:
            return State.DONE

        if self.state == State.IDLE:
            if bread_visible and not in_toaster:
                return State.PLACING
            return State.IDLE

        if self.state == State.PLACING:
            if not skill_running and in_toaster:
                return State.PLACED
            return State.PLACING

        if self.state == State.PLACED:
            if in_toaster and not lever_down:
                return State.PRESSING
            return State.PLACED

        if self.state == State.PRESSING:
            if not skill_running and lever_down:
                return State.TOASTING
            return State.PRESSING

        if self.state == State.TOASTING:
            return State.TOASTING

        return self.state

    def _action_for(self, state: State, skill_running: bool) -> Action:
        if skill_running:
            return Action(ActionKind.WAIT, reason=f"{state} skill in flight")
        if state == State.PLACING:
            return Action(ActionKind.DISPATCH, skill="bread_to_toaster",
                          reason="bread visible, not in toaster")
        if state == State.PRESSING:
            return Action(ActionKind.DISPATCH, skill="lever_down",
                          reason="bread placed, lever up")
        if state == State.DONE:
            return Action(ActionKind.DONE, reason="toast popped")
        return Action(ActionKind.WAIT, reason=f"in {state}")
