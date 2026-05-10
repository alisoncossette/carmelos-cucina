# Carmelo's Cucina

> **A safety-first kitchen companion for an elderly user.**
> Built on a dual-arm SO-101 with MakerMods ModBlocks. Submitted to the MakerMods hackathon.

**Demo video:** https://youtu.be/jTSO_XpUEP8

MakerMods says explicitly: *"AI automations are for convenience only, not safety or security-critical use cases."* **Carmelo's Cucina is the safety layer that makes the MakerMods convenience stack deployable around vulnerable users.** That's the whole project.

## How

A vision-language model orchestrates two LeRobot policies through a debounced finite state machine. Around that core, four layers protect Carmelo (the elderly user):

1. **SafetyMonitor** — preflight gate, runtime watchdog, heartbeat E-STOP, fail-safe defaults.
2. **AgentClient** — proactive conversation via ElevenLabs cloud TTS. Asks Carmelo if he wants toast at startup, **narrates each state transition** in a warm voice ("Oo... we are toasting now. Not long until lunch!"), and asks Carmelo for help when a policy fails — *without* requiring a verbal or button acknowledgement. The VLM watches for the world to change (e.g. `lever_down: true`) and the FSM resumes automatically.
3. **GestureClient** — when the robot pauses, the arm waves "no" and returns home (via runmotion.ai). Embodied feedback as a backup channel. *Stub by default for the hackathon — voice covers the same intents.*
4. **DisplayClient** — the MakerMods Display ModBlock (multi-color touchscreen) is wired as a stub. Voice handles the demo.

The **MakerMods Button ModBlock** is also stubbed. A manual E-STOP via long-press is supported by the orchestrator; it's just not connected to physical hardware in this build. Voice + VLM cover the user-input side for the demo.

## Architecture

```
                                                    ┌─ Policy A: bread → toaster
  wrist cam ─┐                                      │
             ├─► VLM (1–2 Hz) ─► JSON state ──► FSM─┤
  env cam   ─┘   task + safety + presence    │      │
                                             │      └─ Policy B: lever down (toaster on)
                              ┌──────────────┴──────────────┐
                              ▼              ▼              ▼
                       SafetyMonitor    AgentClient    GestureClient
                       preflight gate   ElevenLabs TTS  (stub for demo)
                       runtime watchdog narrates state
                       heartbeat ESTOP  asks for help
                                        VLM detects resolve
```

The agent is a full perceive-decide-act loop, not just a TTS wrapper. It:

- **Perceives** via the VLM (cameras + scene understanding).
- **Decides** via the FSM (which skill to run next).
- **Acts** through the dispatcher (launches `lerobot-record` policies) and through speech (ElevenLabs).
- **Adapts** — when a policy fails repeatedly, it asks for help and keeps watching. When the VLM sees the world change to the expected state, it resumes. No verbal/button ack needed.

## Why ACT, not pi / SmolVLA, at the runtime layer

`pi` and `SmolVLA` were jumpy on the manipulation task — language-conditioned VLAs underperform on tight pick-and-place. We switched the runtime policy to **ACT (action chunking transformer)**, which is purpose-built for muscle-memory tasks. The VLM stays as the *supervisor*, not the actor: it decides *which* skill to run; ACT handles execution. Either family slots in via `policies.*.path`.

## Why this matters for Carmelo

The team's own training shows the policies aren't perfect — Policy B sometimes doesn't fully depress the toaster lever, and Policy A occasionally misses the bread slot. Without an orchestrator, the system would either:
- silently fail (toast never starts), or
- retry forever (annoying, possibly unsafe).

With Carmelo's Cucina:

1. The VLM verifies the world state after each skill (`lever_down == true`? `bread_in_toaster == true`?).
2. If verification fails, FSM dispatches the skill again — up to `max_skill_attempts` times.
3. After the cap, the robot **says (in a warm voice):** *"Carmelo, could you give the lever a press for me? I can't quite reach."*
4. The orchestrator **stops dispatching that skill** and just keeps watching. The VLM observes Carmelo helping; when it sees `lever_down: true`, the FSM transitions naturally to TOASTING, the robot says *"Thank you, Carmelo. Let me carry on,"* and continues.
5. If Carmelo doesn't help within `help_timeout_s` (default 15s), the robot re-asks once. After another `help_timeout_s` with no resolution, it speaks a graceful exit (*"I'm sorry, Carmelo. I'll stop here for now."*) and halts.

The key design choice: **the human-handoff doesn't require a verbal "I'm done" or button press.** The same VLM that drove the original verification drives the resume. One sensing system, one source of truth for "did the world reach the expected state?"

This is what graceful degradation looks like for assistive robotics: the robot does what it can, asks for help on what it can't, and trusts its own eyes to know when help arrived.

## Safety layer (`safety.py`)

### Preflight gate (before dispatch)

| Code | Trigger | Severity |
|---|---|---|
| `HAND_IN_WORKSPACE` | hand in workspace per VLM | halt |
| `HOT_TOASTER_BREAD_INSERT` | inserting bread while lever down (toaster active) | halt |
| `PRESS_WITHOUT_BREAD` | pressing lever with no bread confirmed | halt |
| `LOW_CONFIDENCE_PREFLIGHT` | VLM confidence low or JSON parse failed | wait |
| `WORKSPACE_OBSTRUCTED` | unexpected object in workspace | halt |

### Runtime watchdog (every tick)

| Code | Trigger | Severity |
|---|---|---|
| `HAND_DURING_SKILL` | hand visible while a policy is running | **E-STOP** |
| `LEVER_DOWN_DURING_INSERT` | toaster activates mid-insertion | **E-STOP** |
| `MANUAL_ESTOP` | operator long-pressed the physical button | **E-STOP** |
| `VLM_BLIND` | low-confidence streak exceeds limit | halt |
| `HEARTBEAT_STALE` | main loop hasn't ticked within budget | **E-STOP** |

### Fail-safe defaults

If the VLM emits invalid JSON, the parser returns `human_hand_visible=true`, `workspace_clear=false`, `confidence="low"` — the safest possible interpretation. The system halts rather than acting on garbage.

## MakerMods integration

| Module | Used for | File | Status |
|---|---|---|---|
| Robot arm (SO-101) | execution | `dispatch.py` | wired via `lerobot-record` |
| Display | (replaced by voice for this build) | `display.py` | stub |
| Button | (replaced by voice + VLM for this build) | `button.py` | stub |

The Display + Button placeholders remain in place as clean HTTP/SDK call sites — they can be activated later by editing one provider line in `config.yaml`. For the hackathon, voice (ElevenLabs) is the primary interaction channel.

## Setup

```bash
pip install -r requirements.txt

# Voice agent needs one secret. Copy the example and paste your key.
cp .env.example .env
# Edit .env and set ELEVENLABS_API_KEY=...
```

On macOS, audio playback uses the built-in `afplay` — no extra install. On Linux, install `mpg123` or `ffmpeg`. STT for the toast question needs `portaudio` on macOS: `brew install portaudio` before `pip install` if pyaudio fails.

## Operating modes

Three CLI flags pick common config combinations. They override `agent.provider` and the help fallback behaviour.

| flag | agent | help on policy failure | safety announcements | use when |
|---|---|---|---|---|
| `--basic` | stub (silent) | **disabled** — robot halts on repeated failure | none | quickest functional test, no API key needed |
| `--voice` | **ElevenLabs** | fire-and-forget; VLM detects resolution | none | the headline demo |
| `--full` | **ElevenLabs** | fire-and-forget; VLM detects resolution | spoken ("I see you. I'm pausing.") | full multimodal demo |

If no mode flag is given, the orchestrator uses `agent.provider` as set in `config.yaml`.

## Quick start

```bash
# Unit tests
python test_fsm.py
python test_safety.py

# Voice smoke test — no robot, scripted VLM, hear ElevenLabs through your laptop speakers.
# Walks through every state transition + the help flow.
python run.py --config config.yaml --voice --dry-run --stub-vlm

# Quietest end-to-end (no API key required) — same scripted walk, no voice.
python run.py --config config.yaml --basic --dry-run --stub-vlm

# Live demo (robot connected, lerobot CLI on PATH, ELEVENLABS_API_KEY set)
python run.py --config config.yaml --voice         # headline demo
python run.py --config config.yaml --full          # adds spoken safety announcements
```

## Voice setup

To swap the ElevenLabs voice (no code change):

1. Browse https://elevenlabs.io/voice-library and preview voices.
2. Click a voice → click the **ID** button → copy the ID (looks like `EXAVITQu4vr4xnSDxMaL`).
3. In `config.yaml`:
   ```yaml
   agent:
     voice_id: <paste-here>
   ```
4. Re-run. Done.

The default in `config.yaml` is **Sarah** — a warm, mature female voice. Customise to taste.

Tone direction for the chosen voice should be: warm, a little playful, addresses Carmelo by name. The narration lines live in `agent.py:STATE_NARRATION` and the help-request copy in `agent.py:request_help()` — both are easy to tweak.

## Demoable scenarios

Easy to show on stage (use `--voice` or `--full`):

- **Carmelo says "no" to toast at startup** (into the mic) → robot says "Okay. Maybe later." and exits without moving.
- **Happy path** → robot narrates each step in a warm voice: *"Ah, there's the bread. Let me get that for you, Carmelo."* → *"Bread's in. Now for the lever."* → *"Oo... we are toasting now. Not long until lunch!"* → *"Your toast is ready, Carmelo. Be careful — it may be hot."*
- **The toaster-on policy fails to fully depress the lever twice** → robot says: *"Carmelo, could you give the lever a press for me? I can't quite reach."* Carmelo presses the lever. VLM observes `lever_down: true`. Robot says *"Thank you, Carmelo. Let me carry on,"* and continues to TOASTING. **No button, no verbal ack — the VLM is the resume signal.**
- **Wave a hand near the toaster mid-skill** → arm halts (`HAND_DURING_SKILL` E-STOP). In `--full` mode, the robot also says *"I see you. I'm pausing so you can take over."*
- **Press the lever down manually mid-insertion** → `LEVER_DOWN_DURING_INSERT` E-STOPs before the arm collides with a hot slot.
- **Cover the camera** → confidence collapses → after 3 ticks, `VLM_BLIND` halts.
- **Help timeout** — if Carmelo doesn't help within 15s, the robot re-asks once. Another 15s with no resolution and the robot speaks a graceful exit (*"I'm sorry, Carmelo. I'll stop here for now."*) and halts.

## States

| State | Meaning |
|---|---|
| `IDLE` | waiting for bread to appear |
| `PLACING` | Policy A running |
| `PLACED` | bread in toaster, lever up |
| `PRESSING` | Policy B running |
| `TOASTING` | lever down, waiting for pop |
| `DONE` | toast popped — terminal |
| `SAFE_HALT` | safety halt OR Carmelo declined help; requires reset |

## Files

| File | Purpose |
|---|---|
| `run.py` | Entry point. Wires camera → VLM → safety → FSM → dispatcher + gesture + display + button + agent. |
| `vlm.py` | Swappable VLM (SmolVLM default, Qwen2-VL fallback, stub for tests). |
| `fsm.py` | Debounced state machine. |
| `safety.py` | Preflight gate + runtime watchdog + audit log. |
| `gesture.py` | Embodied feedback via runmotion.ai. wave_no / return_home / thumbs_up. |
| `display.py` | MakerMods Display ModBlock client. |
| `button.py` | MakerMods Button ModBlock client (E-STOP + ack). |
| `agent.py` | Conversational layer. Console / stub / local-voice / **ElevenLabs** / display. Narration + help flow live here. |
| `camera.py` | `FrameSource` abstraction. Webcam (cv2) or saved-frames dir. |
| `dispatch.py` | Subprocess wrapper around `lerobot-record --policy.path=...`. |
| `config.yaml` | Single source of truth for all knobs. |

## Models & datasets

- **Policy A — bread → toaster:** `ajkoder/smolvla-bread-toaster` · dataset `ajkoder/bread_in_toaster_v4`
- **Policy B — lever down (toaster on):** `ajkoder/smolvla-toaster-on` · dataset `ajkoder/toaster_on_v1`
- ACT checkpoints trained on the same datasets are preferred for runtime — set `policies.*.path` to the local ACT checkpoint directory.

## Hackathon notes

- Built remotely (Allison, VT) + locally (team in SF on the robot). The orchestrator runs anywhere; only `dispatch.py` needs to be on the same machine as the SO-101.
- All three external integrations (runmotion.ai, MakerMods Display, MakerMods Button) are abstracted behind clean stub providers, so the orchestrator works end-to-end without any of the hardware wired up. **Voice (ElevenLabs) is the primary interaction channel for this build** — the stub providers remain as fallback / future extension points.
- **For remote validation** (no robot needed): `python run.py --voice --dry-run --stub-vlm` exercises every state transition, every narration line, the offer-toast STT, and the help flow. The voice plays through your laptop speakers. Iterate on tone and pacing from anywhere; ship the same code to the demo machine.
