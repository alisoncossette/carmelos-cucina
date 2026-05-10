"""Robot gesture client.

Carmelo's Cucina has no speaker. The robot communicates safety events to its
elderly user *through motion* — a side-to-side wrist wave to mean "no / wait",
followed by a smooth return to home pose to signal "I'm standing by."

This module is the abstraction layer. The stub client just logs gestures so
the rest of the system can be tested end-to-end. The runmotion.ai client is
a placeholder — the team plugs in the actual SDK call where indicated.

Gesture vocabulary:
  wave_no       — universal "stop / I see you" signal at neutral elevation
  return_home   — smooth move to safe home pose; signals "standing by"
  ack           — brief nod ('I see you, acknowledged')
  thumbs_up     — success signal (toast popped)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol

log = logging.getLogger(__name__)


GESTURES = {
    "wave_no": "side-to-side wrist wave at neutral elevation — universal stop signal",
    "return_home": "smooth move to home pose — 'standing by'",
    "ack": "brief nod / dip — 'I see you, acknowledged'",
    "thumbs_up": "success signal — toast popped",
}


class GestureClient(Protocol):
    def execute(self, gesture: str) -> None: ...


# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class StubGestureClient:
    """Logs gesture invocations. Use for dry-run, tests, or until the
    runmotion.ai integration is wired up."""

    name: str = "stub"

    def execute(self, gesture: str) -> None:
        if gesture not in GESTURES:
            log.warning("[GESTURE %s] unknown gesture: %s", self.name, gesture)
            return
        log.info("[GESTURE %s] %-12s — %s", self.name, gesture, GESTURES[gesture])


# ─────────────────────────────────────────────────────────────────────────────

class RunMotionGestureClient:
    """runmotion.ai gesture client — PLACEHOLDER.

    Plug the actual SDK / HTTP call into `_play()` below. The orchestrator
    calls `execute(gesture_name)` and expects the call to block until the
    motion is done (or to time out cleanly).

    Suggested integration path:
      - import runmotion (or use requests against the HTTP API)
      - resolve `gesture` to a saved motion ID per `self.motion_ids`
      - dispatch with `self.arm_id` as the target arm
      - await completion / poll status
    """

    def __init__(
        self,
        api_key: str,
        arm_id: str = "right",
        motion_ids: dict[str, str] | None = None,
        timeout_s: float = 8.0,
    ):
        self.api_key = api_key
        self.arm_id = arm_id
        self.motion_ids = motion_ids or {
            # Fill these in with the actual motion IDs from your runmotion.ai project.
            "wave_no": "",
            "return_home": "",
            "ack": "",
            "thumbs_up": "",
        }
        self.timeout_s = timeout_s

    def execute(self, gesture: str) -> None:
        if gesture not in GESTURES:
            log.warning("[GESTURE runmotion] unknown gesture: %s", gesture)
            return
        motion_id = self.motion_ids.get(gesture, "")
        if not motion_id:
            log.warning("[GESTURE runmotion] no motion_id mapped for %s — falling back to log", gesture)
            log.info("[GESTURE runmotion(stub)] %s — %s", gesture, GESTURES[gesture])
            return
        try:
            self._play(motion_id)
        except Exception as e:  # noqa: BLE001 — never let a gesture failure crash the safety path
            log.exception("[GESTURE runmotion] play failed for %s (%s): %s",
                          gesture, motion_id, e)

    def _play(self, motion_id: str) -> None:
        """Replace this with the real runmotion.ai call.

        Likely shape:

            import requests
            r = requests.post(
                "https://api.runmotion.ai/v1/play",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={"motion_id": motion_id, "arm": self.arm_id},
                timeout=self.timeout_s,
            )
            r.raise_for_status()
        """
        raise NotImplementedError(
            "wire up runmotion.ai here — see _play docstring for the expected shape"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Composite responses — what the safety layer actually invokes
# ─────────────────────────────────────────────────────────────────────────────

def signal_pause_for_user(client: GestureClient) -> None:
    """The arm waves 'no' so the user knows it saw them, then returns home."""
    client.execute("wave_no")
    client.execute("return_home")


def signal_success(client: GestureClient) -> None:
    client.execute("thumbs_up")
    client.execute("return_home")


def signal_ack(client: GestureClient) -> None:
    client.execute("ack")


# ─────────────────────────────────────────────────────────────────────────────

def build(cfg: dict | None) -> GestureClient:
    cfg = cfg or {}
    provider = cfg.get("provider", "stub")
    if provider == "stub":
        return StubGestureClient(name=cfg.get("name", "stub"))
    if provider == "runmotion":
        return RunMotionGestureClient(
            api_key=cfg.get("api_key", ""),
            arm_id=cfg.get("arm_id", "right"),
            motion_ids=cfg.get("motion_ids", {}),
            timeout_s=float(cfg.get("timeout_s", 8.0)),
        )
    raise ValueError(f"unknown gesture provider: {provider}")
