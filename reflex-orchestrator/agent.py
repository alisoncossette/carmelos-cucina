"""Conversational agent for Carmelo's Cucina.

Proactively engages Carmelo (the elderly user):
  - On startup: asks if Carmelo would like a piece of toast.
  - On every FSM state transition: narrates intent ("Oo... we are toasting now").
  - When the robot has failed the same skill twice: asks Carmelo for help
    and keeps watching — the VLM detects when Carmelo finishes and the FSM
    resumes automatically. No "I helped" button or verbal ack required.

Four providers:
  console — prints prompts, reads stdin. Works on any laptop, no audio hardware.
  stub    — fully autonomous: yes-to-toast. For unattended testing.
  local   — pyttsx3 (offline TTS) + speech_recognition (STT). Robotic-sounding.
  eleven  — ElevenLabs cloud TTS. Warm, demo-grade voice. STT for offer_toast
            only (one-shot yes/no at startup); help requests are fire-and-forget
            because the VLM detects resolution.

Why a separate module: the safety layer talks to Carmelo through a body
gesture (gesture.py — wave_no). The agent talks to Carmelo through *language*.
Both are channels of the same idea — communicate intent so the user is never
surprised by what the robot does next.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from typing import Protocol

log = logging.getLogger(__name__)


class AgentClient(Protocol):
    def speak(self, text: str) -> None: ...
    def ask_yes_no(self, prompt: str) -> bool: ...
    def ask_for_help(self, message: str) -> bool: ...


# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ConsoleAgentClient:
    """Prompts on stdout, reads stdin. Hackathon-safe default."""
    name: str = "console"

    def speak(self, text: str) -> None:
        print(f"\n[CARMELO'S CUCINA] {text}\n")

    def ask_yes_no(self, prompt: str) -> bool:
        print(f"\n[CARMELO'S CUCINA] {prompt} (y/n)")
        try:
            ans = input("> ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return False
        return ans in {"y", "yes", "yeah", "sure", "ok", "okay", "please"}

    def ask_for_help(self, message: str) -> bool:
        print(f"\n[CARMELO'S CUCINA — needs help] {message}")
        print("Press Enter once you've helped (or type 'cancel' to abort).")
        try:
            ans = input("> ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return False
        return ans != "cancel"


@dataclass
class StubAgentClient:
    """Fully autonomous: always yes to toast, always cancels help. For tests."""
    name: str = "stub"

    def speak(self, text: str) -> None:
        log.info("[AGENT stub] would say: %s", text)

    def ask_yes_no(self, prompt: str) -> bool:
        log.info("[AGENT stub] %s -> yes", prompt)
        return True

    def ask_for_help(self, message: str) -> bool:
        log.info("[AGENT stub] help request: %s -> cancel", message)
        return False


@dataclass
class DisplayButtonAgentClient:
    """Routes prompts through the MakerMods Display + Button ModBlocks.

    speak()         -> render text on the touchscreen
    ask_yes_no()    -> render question with Yes/No taps; await tap
    ask_for_help()  -> red alert on display; wait for short_press to ack
                       (long_press = decline / abort)

    Falls back gracefully if a tap times out (display.show_yes_no returns None).
    """

    display: object  # DisplayClient
    button: object   # ButtonClient
    yes_no_timeout_s: float = 30.0

    def speak(self, text: str) -> None:
        self.display.show(text, color="white")

    def ask_yes_no(self, prompt: str) -> bool:
        result = self.display.show_yes_no(prompt, timeout_s=self.yes_no_timeout_s)
        if result is None:
            log.warning("display yes/no timed out; defaulting to no")
            return False
        return result

    def ask_for_help(self, message: str) -> bool:
        self.display.show_alert(message)
        # Poll the button for up to ~60s. short_press = help given; long_press = decline.
        deadline = __import__("time").monotonic() + 60.0
        while __import__("time").monotonic() < deadline:
            evt = self.button.poll()
            if evt == "press":
                return True
            if evt == "long_press":
                return False
            __import__("time").sleep(0.05)
        log.warning("help button wait timed out; defaulting to decline")
        return False


class ElevenLabsAgentClient:
    """ElevenLabs cloud TTS, optionally with mic+STT for the toast question.

    - speak() / ask_for_help() — text → ElevenLabs → audio file → afplay/ffplay/mpg123.
      Blocking: returns after playback completes so subsequent narration doesn't overlap.
    - ask_yes_no() — speaks the prompt, then listens via speech_recognition (when
      voice_input=True). Falls back to stdin if voice_input=False or STT is unavailable.
    - ask_for_help() is fire-and-forget by design: speaks the help request and returns
      True immediately. The orchestrator loop watches the VLM to detect resolution
      (e.g. lever_down becoming true) and resumes from there.

    Lazy imports so a missing elevenlabs / speech_recognition install does not break
    the rest of the orchestrator (only this provider).
    """

    def __init__(self, voice_id: str, api_key: str | None = None,
                 voice_input: bool = False, listen_timeout_s: float = 8.0,
                 model: str = "eleven_turbo_v2_5"):
        from elevenlabs.client import ElevenLabs  # lazy
        api_key = api_key or os.environ.get("ELEVENLABS_API_KEY")
        if not api_key:
            raise RuntimeError(
                "ELEVENLABS_API_KEY not set. Add it to .env or export it before running.")
        self._client = ElevenLabs(api_key=api_key)
        self.voice_id = voice_id
        self.model = model
        self.voice_input = voice_input
        self.listen_timeout_s = listen_timeout_s
        self._sr = None
        self._recognizer = None
        if voice_input:
            try:
                import speech_recognition as sr  # lazy
                self._sr = sr
                self._recognizer = sr.Recognizer()
                # Verify the mic chain works (pyaudio installed, device present,
                # not held exclusively by another app). Catching broadly here is
                # intentional: any failure to open the mic should degrade to
                # stdin rather than crash during the toast question.
                with sr.Microphone():
                    pass
            except Exception as e:  # noqa: BLE001
                log.warning("voice_input requested but mic unavailable (%s: %s); "
                            "falling back to stdin for yes/no",
                            type(e).__name__, e)
                self.voice_input = False
                self._sr = None
                self._recognizer = None

    def speak(self, text: str) -> None:
        log.info("[ELEVEN] %s", text)
        try:
            audio_iter = self._client.text_to_speech.convert(
                voice_id=self.voice_id, model_id=self.model, text=text,
                output_format="mp3_44100_128",
            )
            with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
                for chunk in audio_iter:
                    f.write(chunk)
                path = f.name
            _play_audio(path)
        except Exception as e:  # noqa: BLE001
            log.exception("ElevenLabs speak failed: %s — falling back to log-only", e)

    def ask_yes_no(self, prompt: str) -> bool:
        self.speak(prompt)
        if self.voice_input and self._sr is not None:
            for _ in range(2):
                text = self._listen()
                log.info("[ELEVEN heard] %r", text)
                if any(w in text for w in ("yes", "yeah", "yep", "sure", "okay", "please")):
                    return True
                if any(w in text for w in ("no", "nope", "not now", "stop")):
                    return False
                self.speak("Sorry, I didn't catch that. Could you say yes or no?")
            return False
        # Fallback: stdin
        try:
            ans = input("(speak/type y or n) > ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return False
        return ans in {"y", "yes", "yeah", "sure", "ok", "okay", "please"}

    def ask_for_help(self, message: str) -> bool:
        """Fire-and-forget: speak the help request, return immediately.
        The orchestrator watches the VLM for the world to change."""
        self.speak(message)
        return True

    def _listen(self) -> str:
        assert self._sr is not None and self._recognizer is not None
        with self._sr.Microphone() as src:
            self._recognizer.adjust_for_ambient_noise(src, duration=0.5)
            try:
                audio = self._recognizer.listen(src, timeout=self.listen_timeout_s)
            except self._sr.WaitTimeoutError:
                return ""
        try:
            return self._recognizer.recognize_google(audio).lower()
        except Exception as e:  # noqa: BLE001
            log.warning("STT failed: %s", e)
            return ""


def _play_audio(path: str) -> None:
    """Best-effort cross-platform audio playback for an mp3 file.

    macOS: afplay (built-in). Linux: mpg123 / ffplay. Windows: start (default app).
    If nothing is found, the agent stays silent and logs a warning.
    """
    for cmd, args in (
        ("afplay", [path]),
        ("ffplay", ["-nodisp", "-autoexit", "-loglevel", "quiet", path]),
        ("mpg123", ["-q", path]),
    ):
        if shutil.which(cmd):
            subprocess.run([cmd] + args, check=False)
            return
    if os.name == "nt":  # Windows fallback
        try:
            os.startfile(path)  # type: ignore[attr-defined]
            return
        except Exception:  # noqa: BLE001
            pass
    log.warning("no audio player found (afplay/ffplay/mpg123); voice agent is silent")


class LocalVoiceAgentClient:
    """pyttsx3 (TTS) + speech_recognition (STT). Lazy imports so the rest of
    the orchestrator doesn't break if those libs aren't installed."""

    def __init__(self, listen_timeout_s: float = 8.0):
        import pyttsx3
        import speech_recognition as sr
        self._tts = pyttsx3.init()
        self._sr = sr
        self._recognizer = sr.Recognizer()
        self.listen_timeout_s = listen_timeout_s

    def speak(self, text: str) -> None:
        log.info("[VOICE] %s", text)
        self._tts.say(text)
        self._tts.runAndWait()

    def _listen(self) -> str:
        with self._sr.Microphone() as src:
            self._recognizer.adjust_for_ambient_noise(src, duration=0.5)
            try:
                audio = self._recognizer.listen(src, timeout=self.listen_timeout_s)
            except self._sr.WaitTimeoutError:
                return ""
        try:
            return self._recognizer.recognize_google(audio).lower()
        except Exception as e:  # noqa: BLE001
            log.warning("STT failed: %s", e)
            return ""

    def ask_yes_no(self, prompt: str) -> bool:
        self.speak(prompt)
        for _ in range(2):
            text = self._listen()
            log.info("[VOICE heard] %r", text)
            if any(w in text for w in ("yes", "yeah", "yep", "sure", "okay", "please")):
                return True
            if any(w in text for w in ("no", "nope", "not now", "stop")):
                return False
            self.speak("Sorry, I didn't catch that. Could you say yes or no?")
        return False

    def ask_for_help(self, message: str) -> bool:
        self.speak(message)
        # No reliable yes/no needed — wait for any response, then assume help given.
        self._listen()
        return True


# ─────────────────────────────────────────────────────────────────────────────
# Composite intents — what the orchestrator actually invokes
# ─────────────────────────────────────────────────────────────────────────────

def offer_toast(client: AgentClient) -> bool:
    return client.ask_yes_no("Hello Carmelo. Would you like me to make you a piece of toast?")


def request_help(client: AgentClient, skill: str) -> None:
    """Fire-and-forget: speak the help request. The orchestrator watches the
    VLM to detect when Carmelo has finished the step — no verbal/button ack
    needed. Return value (if any) is intentionally ignored."""
    friendly = {
        "bread_to_toaster": "My fingers aren't quite getting that bread, Carmelo. Could you help me pop it in?",
        "lever_down":       "Carmelo, could you give the lever a press for me? I can't quite reach.",
    }.get(skill, f"Carmelo, could you help me with the {skill} step?")
    client.ask_for_help(friendly)


def thank_for_help(client: AgentClient) -> None:
    client.speak("Thank you, Carmelo. Let me carry on.")


def announce_done(client: AgentClient) -> None:
    client.speak("Your toast is ready, Carmelo. Be careful — it may be hot.")


def announce_paused(client: AgentClient) -> None:
    client.speak("I see you. I'm pausing so you can take over.")


# ─────────────────────────────────────────────────────────────────────────────
# Narration on FSM transitions — keeps Carmelo informed during the work.
# Keys are (from_state, to_state). Missing transitions are silent.
# Strings stay short so ElevenLabs latency (~1s) doesn't stack up.
# ─────────────────────────────────────────────────────────────────────────────

STATE_NARRATION: dict[tuple[str, str], str] = {
    ("IDLE", "PLACING"):       "Ah, there's the bread. Let me get that for you, Carmelo.",
    ("PLACING", "PLACED"):     "Bread's in. Now for the lever.",
    ("PLACED", "PRESSING"):    "Pressing the lever now.",
    ("PRESSING", "TOASTING"):  "Oo... we are toasting now. Not long until lunch!",
}


def narrate_transition(client: AgentClient, from_state: str, to_state: str) -> None:
    """Speak a single line if we have one for this transition. Silent otherwise."""
    line = STATE_NARRATION.get((from_state, to_state))
    if line:
        client.speak(line)


# ─────────────────────────────────────────────────────────────────────────────

def build(cfg: dict | None, display=None, button=None) -> AgentClient:
    cfg = cfg or {}
    provider = cfg.get("provider", "console")
    if provider == "console":
        return ConsoleAgentClient()
    if provider == "stub":
        return StubAgentClient()
    if provider == "local":
        return LocalVoiceAgentClient(float(cfg.get("listen_timeout_s", 8.0)))
    if provider == "eleven":
        voice_id = cfg.get("voice_id")
        if not voice_id:
            raise ValueError("agent.provider=eleven requires agent.voice_id in config")
        return ElevenLabsAgentClient(
            voice_id=str(voice_id),
            voice_input=bool(cfg.get("voice_input", False)),
            listen_timeout_s=float(cfg.get("listen_timeout_s", 8.0)),
            model=str(cfg.get("model", "eleven_turbo_v2_5")),
        )
    if provider == "display":
        if display is None or button is None:
            raise ValueError("agent provider=display requires display + button clients")
        return DisplayButtonAgentClient(display=display, button=button,
                                        yes_no_timeout_s=float(cfg.get("yes_no_timeout_s", 30.0)))
    raise ValueError(f"unknown agent provider: {provider}")
