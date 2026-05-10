"""Safety monitor for the orchestrator.

The runtime policies (ACT / SmolVLA) have no awareness of safety constraints —
they execute whatever motion the FSM dispatches. The SafetyMonitor sits between
the FSM's *decision* and the dispatcher's *action*, and it watches every tick
while a skill is in flight.

Three layers of defense:

  1. Preconditions (gate) — checked BEFORE a skill is dispatched.
       e.g. don't insert bread while the toaster lever is down (hot surfaces),
       don't dispatch any skill if a human hand is visible in the workspace,
       don't dispatch if VLM confidence is low.

  2. Runtime watchdog — checked EVERY tick while a skill is running.
       e.g. if a hand enters the workspace mid-skill → E-STOP,
       if the lever goes down mid-insertion (toaster activated unexpectedly) → E-STOP,
       if VLM confidence collapses or JSON keeps failing to parse → HALT.

  3. Heartbeat — orchestrator main loop must call .heartbeat() each tick.
       If the loop stalls (>heartbeat_timeout_s), runtime check triggers an E-STOP
       so a hung orchestrator can never leave a robot in motion indefinitely.

Severities:
  - "wait"  — defer the decision; keep observing.
  - "halt"  — stop dispatching new skills; keep observing; don't kill in-flight.
  - "estop" — kill the in-flight skill subprocess immediately and enter SAFE_HALT.

Every violation is appended to an audit log keyed by timestamp, code, severity,
and the observation that triggered it. The team can post-mortem any incident.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class SafetyViolation:
    code: str
    message: str
    severity: str        # "wait" | "halt" | "estop"
    obs: dict[str, Any] = field(default_factory=dict)
    ts: float = field(default_factory=time.time)


@dataclass
class SafetyMonitor:
    low_confidence_limit: int = 3
    heartbeat_timeout_s: float = 5.0
    require_workspace_clear: bool = True
    log: list[SafetyViolation] = field(default_factory=list)

    _low_conf_streak: int = 0
    _last_heartbeat: float = field(default_factory=time.monotonic)
    _halted: bool = False

    # ─── lifecycle ────────────────────────────────────────────────────────

    def heartbeat(self) -> None:
        self._last_heartbeat = time.monotonic()

    def is_halted(self) -> bool:
        return self._halted

    def reset(self) -> None:
        """Operator acknowledgement to clear a halt. Audit log is preserved."""
        self._halted = False
        self._low_conf_streak = 0
        self._last_heartbeat = time.monotonic()

    # ─── checks ───────────────────────────────────────────────────────────

    def preflight(self, skill: str, obs: dict[str, Any]) -> list[SafetyViolation]:
        """Run before dispatching `skill`. Return list of blocking violations."""
        violations: list[SafetyViolation] = []

        if obs.get("human_hand_visible"):
            violations.append(SafetyViolation(
                "HAND_IN_WORKSPACE",
                "human hand visible in workspace; refusing to dispatch",
                "halt", obs))

        # Skill-specific invariants ───────────────────────────────────────
        if skill == "bread_to_toaster" and obs.get("lever_down"):
            violations.append(SafetyViolation(
                "HOT_TOASTER_BREAD_INSERT",
                "lever is down (toaster active) — refusing to insert bread into hot slot",
                "halt", obs))

        if skill == "lever_down" and not obs.get("bread_in_toaster"):
            violations.append(SafetyViolation(
                "PRESS_WITHOUT_BREAD",
                "refusing to press lever without bread confirmed in toaster",
                "halt", obs))

        # Low VLM confidence: defer, don't halt
        if obs.get("confidence") == "low" or obs.get("parse_error"):
            violations.append(SafetyViolation(
                "LOW_CONFIDENCE_PREFLIGHT",
                "VLM confidence too low to dispatch a skill safely",
                "wait", obs))

        if self.require_workspace_clear and obs.get("workspace_clear") is False:
            violations.append(SafetyViolation(
                "WORKSPACE_OBSTRUCTED",
                "unexpected obstruction in workspace",
                "halt", obs))

        for v in violations:
            self.log.append(v)
            if v.severity == "halt":
                self._halted = True
        return violations

    def runtime(self, obs: dict[str, Any], current_skill: str | None) -> list[SafetyViolation]:
        """Run every tick. While a skill is in flight, dangerous conditions ESTOP it."""
        violations: list[SafetyViolation] = []

        # Heartbeat staleness (loop is alive but check anyway as defense in depth)
        if time.monotonic() - self._last_heartbeat > self.heartbeat_timeout_s:
            violations.append(SafetyViolation(
                "HEARTBEAT_STALE",
                f"main loop heartbeat stale > {self.heartbeat_timeout_s}s",
                "estop", obs))

        # Hand entering workspace mid-skill is an immediate E-STOP
        if current_skill and obs.get("human_hand_visible"):
            violations.append(SafetyViolation(
                "HAND_DURING_SKILL",
                f"hand detected while {current_skill} is running",
                "estop", obs))

        # Lever activating during a bread-insertion is an immediate E-STOP
        if current_skill == "bread_to_toaster" and obs.get("lever_down"):
            violations.append(SafetyViolation(
                "LEVER_DOWN_DURING_INSERT",
                "lever went down while inserting bread — toaster active",
                "estop", obs))

        # VLM going blind: track streak; halt if persistent
        if obs.get("confidence") == "low" or obs.get("parse_error"):
            self._low_conf_streak += 1
        else:
            self._low_conf_streak = 0
        if self._low_conf_streak >= self.low_confidence_limit:
            violations.append(SafetyViolation(
                "VLM_BLIND",
                f"VLM confidence/parse failed for {self._low_conf_streak} ticks",
                "halt", obs))

        for v in violations:
            self.log.append(v)
            if v.severity in ("halt", "estop"):
                self._halted = True
        return violations

    # ─── reporting ────────────────────────────────────────────────────────

    def summary(self) -> dict[str, Any]:
        return {
            "halted": self._halted,
            "violations": len(self.log),
            "by_code": _counts(v.code for v in self.log),
            "by_severity": _counts(v.severity for v in self.log),
            "recent": [
                {"code": v.code, "severity": v.severity, "message": v.message,
                 "ts": v.ts}
                for v in self.log[-10:]
            ],
        }


def _counts(items) -> dict[str, int]:
    out: dict[str, int] = {}
    for x in items:
        out[x] = out.get(x, 0) + 1
    return out
