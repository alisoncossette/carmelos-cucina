"""MakerMods Display ModBlock integration.

Carmelo's Cucina has no speaker. The robot speaks to Carmelo through:
  - the arm itself (gesture.py — wave_no, return_home)
  - the MakerMods Display ModBlock (this file — touchscreen prompts and alerts)

The Display gives us a real UI for an elderly user: large text, color-coded
status, tap-able Yes/No buttons. Far friendlier than a console prompt or
microphone-based voice for someone who may not be comfortable with either.

Two providers:
  stub      — logs renders to console; for development without the hardware.
  makermods — HTTP / SDK call to the actual ModBlock. PLACEHOLDER — wire up.

Color conventions:
  white  — neutral status ("Placing bread...")
  green  — success / progress ("Toast ready!")
  amber  — caution ("Pausing — please step back")
  red    — safety alert ("Hand detected — stopping")
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol

log = logging.getLogger(__name__)

Color = str   # "white" | "green" | "amber" | "red"


class DisplayClient(Protocol):
    def show(self, text: str, color: Color = "white") -> None: ...
    def show_yes_no(self, question: str, timeout_s: float = 30.0) -> bool | None: ...
    def show_alert(self, text: str) -> None: ...
    def clear(self) -> None: ...


# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class StubDisplayClient:
    """Logs what would be shown. Auto-answers yes_no with a default."""
    name: str = "stub"
    default_yes_no: bool = True

    def show(self, text: str, color: Color = "white") -> None:
        log.info("[DISPLAY %s color=%s] %s", self.name, color, text)

    def show_yes_no(self, question: str, timeout_s: float = 30.0) -> bool | None:
        log.info("[DISPLAY %s YES/NO] %s -> %s",
                 self.name, question, self.default_yes_no)
        return self.default_yes_no

    def show_alert(self, text: str) -> None:
        log.warning("[DISPLAY %s ALERT red] %s", self.name, text)

    def clear(self) -> None:
        log.info("[DISPLAY %s] cleared", self.name)


# ─────────────────────────────────────────────────────────────────────────────

class MakerModsDisplayClient:
    """MakerMods Display ModBlock client — PLACEHOLDER.

    The local team plugs the actual MakerMods SDK / HTTP call into the
    `_send` method below. The orchestrator only depends on the public
    interface (show / show_yes_no / show_alert / clear).

    Suggested integration: each ModBlock gets an auto-detected ID over USB-C.
    The MakerMods runtime exposes a local HTTP endpoint per device. Send
    JSON payloads describing the screen contents and (for yes_no) poll a
    response endpoint until a tap is recorded.
    """

    def __init__(
        self,
        device_id: str,
        endpoint: str = "http://localhost:8080",
        poll_interval_s: float = 0.2,
    ):
        self.device_id = device_id
        self.endpoint = endpoint.rstrip("/")
        self.poll_interval_s = poll_interval_s

    def show(self, text: str, color: Color = "white") -> None:
        self._send({"type": "text", "text": text, "color": color})

    def show_yes_no(self, question: str, timeout_s: float = 30.0) -> bool | None:
        token = self._send({"type": "yes_no", "question": question})
        return self._await_response(token, timeout_s)

    def show_alert(self, text: str) -> None:
        self._send({"type": "alert", "text": text, "color": "red"})

    def clear(self) -> None:
        self._send({"type": "clear"})

    # ── plug-in points ────────────────────────────────────────────────────

    def _send(self, payload: dict) -> str:
        """POST {endpoint}/devices/{device_id}/render with payload.
        Return a token used to poll for a response (for yes_no).

        TODO: replace with actual MakerMods SDK call.
        """
        log.info("[DISPLAY makermods STUB-SEND %s] %s", self.device_id, payload)
        return "stub-token"

    def _await_response(self, token: str, timeout_s: float) -> bool | None:
        """Poll {endpoint}/responses/{token} until a yes/no tap arrives or timeout.

        TODO: replace with actual MakerMods SDK call.
        """
        log.info("[DISPLAY makermods STUB-AWAIT %s] returning yes (stub)", token)
        return True


# ─────────────────────────────────────────────────────────────────────────────

def build(cfg: dict | None) -> DisplayClient:
    cfg = cfg or {}
    provider = cfg.get("provider", "stub")
    if provider == "stub":
        return StubDisplayClient(default_yes_no=bool(cfg.get("default_yes_no", True)))
    if provider == "makermods":
        return MakerModsDisplayClient(
            device_id=cfg.get("device_id", "display-0"),
            endpoint=cfg.get("endpoint", "http://localhost:8080"),
            poll_interval_s=float(cfg.get("poll_interval_s", 0.2)),
        )
    raise ValueError(f"unknown display provider: {provider}")
