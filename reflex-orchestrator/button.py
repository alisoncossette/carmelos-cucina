"""MakerMods Button ModBlock integration.

A physical button is the right input for an elderly user — no menus, no
typing, no shouting at a microphone. Carmelo's Cucina uses it for two things:

  short_press → "I've helped / I'm ready / yes acknowledge"
  long_press  → manual E-STOP — kills any in-flight skill immediately

Two providers:
  stub      — never reports presses. Safe default for development.
  makermods — HTTP / SDK call to the actual ModBlock. PLACEHOLDER — wire up.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Protocol

log = logging.getLogger(__name__)


ButtonEvent = str  # "press" | "long_press" | None


class ButtonClient(Protocol):
    def poll(self) -> ButtonEvent | None: ...


# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class StubButtonClient:
    """Returns scripted events from `script` then None forever.
    For tests; e.g. script=['press', None, None, 'long_press']."""
    name: str = "stub"
    script: list[ButtonEvent | None] = field(default_factory=list)
    _idx: int = 0

    def poll(self) -> ButtonEvent | None:
        if self._idx >= len(self.script):
            return None
        evt = self.script[self._idx]
        self._idx += 1
        if evt is not None:
            log.info("[BUTTON %s] %s", self.name, evt)
        return evt


# ─────────────────────────────────────────────────────────────────────────────

class MakerModsButtonClient:
    """MakerMods Button ModBlock client — PLACEHOLDER.

    Plug the actual SDK / HTTP call into `_read_state` below. The orchestrator
    polls `poll()` each tick and acts on the event.

    Long-press detection is done client-side: if the button stays pressed
    across `long_press_threshold_s`, emit 'long_press'; otherwise on release
    emit 'press'.
    """

    def __init__(
        self,
        device_id: str,
        endpoint: str = "http://localhost:8080",
        long_press_threshold_s: float = 1.5,
    ):
        self.device_id = device_id
        self.endpoint = endpoint.rstrip("/")
        self.long_press_threshold_s = long_press_threshold_s
        self._was_down = False
        self._down_since: float = 0.0

    def poll(self) -> ButtonEvent | None:
        is_down = self._read_state()
        now = time.monotonic()

        if is_down and not self._was_down:
            self._down_since = now
            self._was_down = True
            return None

        if is_down and self._was_down:
            if now - self._down_since >= self.long_press_threshold_s:
                # latch — only emit long_press once per hold
                self._down_since = float("inf")
                return "long_press"
            return None

        if not is_down and self._was_down:
            self._was_down = False
            held = now - self._down_since if self._down_since != float("inf") else 0
            if held > 0:
                return "press"
            return None

        return None

    # ── plug-in point ─────────────────────────────────────────────────────

    def _read_state(self) -> bool:
        """GET {endpoint}/devices/{device_id}/state -> {pressed: bool}.

        TODO: replace with actual MakerMods SDK call.
        """
        return False


# ─────────────────────────────────────────────────────────────────────────────

def build(cfg: dict | None) -> ButtonClient:
    cfg = cfg or {}
    provider = cfg.get("provider", "stub")
    if provider == "stub":
        return StubButtonClient(script=cfg.get("script", []))
    if provider == "makermods":
        return MakerModsButtonClient(
            device_id=cfg.get("device_id", "button-0"),
            endpoint=cfg.get("endpoint", "http://localhost:8080"),
            long_press_threshold_s=float(cfg.get("long_press_threshold_s", 1.5)),
        )
    raise ValueError(f"unknown button provider: {provider}")
