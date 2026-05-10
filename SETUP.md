# Setup — SF team

This is the get-it-running guide for the team in SF with the SO-101. The orchestrator code is platform-agnostic; only [dispatch.py](reflex-orchestrator/dispatch.py) needs to live on the same machine as the robot. Follow the sections in order — each one ends in a check you can verify before moving on.

## What the orchestrator owns vs. what lerobot owns

This is the most important thing to internalise before you start, because it will save you debugging the wrong layer:

- **Orchestrator owns: the cameras.** Two `cv2.VideoCapture` device IDs in [config.yaml](reflex-orchestrator/config.yaml) under `cameras.wrist` and `cameras.env`. That's the only hardware this codebase touches directly.
- **Orchestrator does NOT own: the SO-101 arms.** [dispatch.py](reflex-orchestrator/dispatch.py) shells out to `lerobot-record --policy.path=... --record-time-s=...` and waits for the subprocess to exit. Serial ports, motor IDs, calibration files, follower/leader pairing, `--robot.type=...` flags — all of that lives in your existing lerobot setup, not here.

**Sanity rule:** if `lerobot-record --policy.path=<your-trained-policy> --record-time-s=5` runs end-to-end on the SF machine on its own, the orchestrator will drive it. If it doesn't, fix lerobot first — nothing in this repo will help.

If your lerobot invocation needs extra flags, edit `dispatch.cmd_template` in [config.yaml](reflex-orchestrator/config.yaml) — it's a plain Python format string with `{policy_path}` and `{timeout_s}` placeholders.

## 0. What you need

**Hardware**
- Dual-arm SO-101, powered and tethered to the host machine
- LeRobot CLI working — `lerobot-record --help` should print usage
- Two cameras: one wrist cam, one environment cam (any USB webcam OpenCV can open)
- Toaster + bread for the demo
- Speakers (laptop is fine) and a mic if you want the "would you like toast?" question

**Optional / stubbed for the hackathon build**
- MakerMods Display ModBlock — stubbed in [config.yaml](reflex-orchestrator/config.yaml)
- MakerMods Button ModBlock — stubbed in [config.yaml](reflex-orchestrator/config.yaml)
- runmotion.ai gestures — stubbed in [config.yaml](reflex-orchestrator/config.yaml)

**Accounts / keys**
- ElevenLabs API key (free tier works for the demo) — needed for `--voice` and `--full` modes

**Host machine**
- Python 3.10+
- macOS, Linux, or Windows. SF demo machine is assumed Linux/Mac.
- GPU strongly recommended for the VLM (`device: cuda` in config). CPU works but is slow.

## 1. Clone and install

```bash
git clone https://github.com/alisoncossette/carmelos-cucina.git
cd carmelos-cucina/reflex-orchestrator

python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

pip install -r requirements.txt
```

If `pyaudio` fails to build:
- macOS: `brew install portaudio`, then `pip install pyaudio` again
- Linux (Debian/Ubuntu): `sudo apt install portaudio19-dev`, then retry
- If you don't need the mic question, set `agent.voice_input: false` in [config.yaml](reflex-orchestrator/config.yaml) and skip pyaudio entirely

Audio playback for ElevenLabs:
- macOS: nothing to install, uses built-in `afplay`
- Linux: `sudo apt install mpg123` (or `ffmpeg`)

**Check:** `python -c "import torch, transformers, elevenlabs, cv2, yaml; print('ok')"` prints `ok`.

## 2. Secrets

```bash
cp .env.example .env
```

Edit `.env` and paste your ElevenLabs key:

```
ELEVENLABS_API_KEY=sk_...
```

`.env` is in `.gitignore` — never commit it.

**Check:** `python -c "from dotenv import load_dotenv; load_dotenv(); import os; print('key set:', bool(os.environ.get('ELEVENLABS_API_KEY')))"` prints `key set: True`.

## 3. Unit tests (no hardware needed)

```bash
python test_fsm.py
python test_safety.py
```

**Check:** both scripts exit 0 with no failures. If anything fails, stop here and ping Allison — the FSM/safety logic is the spine of the system.

## 4. Voice smoke test (no robot, no cameras)

This walks every state transition with a scripted VLM and plays ElevenLabs through your speakers. No robot, no cameras needed. Validates that your audio path and API key work.

```bash
python run.py --config config.yaml --voice --dry-run --stub-vlm
```

You should hear:
1. *"Carmelo, would you like some toast?"* (mic listens — say "yes" or skip with `--stub-agent`)
2. Narration through PLACING → PLACED → PRESSING → TOASTING → DONE
3. *"Your toast is ready, Carmelo."*

**Check:** voice plays cleanly end-to-end. If audio fails: ElevenLabs key wrong, or `mpg123`/`afplay` missing.

If you don't want the mic prompt, swap in `--stub-agent` for the offer-toast question or set `agent.voice_input: false` in [config.yaml](reflex-orchestrator/config.yaml).

## 5. Camera + VLM smoke test (cameras, still no robot)

First, find your camera device IDs. A throwaway script:

```python
import cv2
for i in range(4):
    cap = cv2.VideoCapture(i)
    ok, _ = cap.read()
    print(i, "ok" if ok else "no")
    cap.release()
```

Edit [config.yaml](reflex-orchestrator/config.yaml) `cameras.wrist` and `cameras.env` to the IDs that worked.

Then capture a few representative frames (bread on counter, bread in toaster, lever down, hand in workspace) into a directory and run:

```bash
python test_vlm.py --frames-dir ./test_frames --provider smolvlm
```

For each image you should see parsed JSON with fields like `bread_visible`, `bread_in_toaster`, `lever_down`, `human_hand_visible`, `confidence`. Sanity-check that the VLM is reading the world correctly. If confidence is consistently low, lighting / framing needs work before the live demo.

**Check:** SmolVLM returns sensible state readings on your representative frames. If the model is too jumpy, try `--provider qwen2vl` (uncomment `qwen-vl-utils` in `requirements.txt` first).

## 6. Robot dry-run (robot connected, lerobot-record NOT actually run)

```bash
python run.py --config config.yaml --voice --dry-run
```

This uses real cameras and real VLM but the dispatcher only *logs* the `lerobot-record` command instead of running it. Walk through the kitchen scene with bread + toaster — watch the logs and listen to narration. Confirm the FSM advances through states as the world changes.

**Check:** state transitions happen at sensible moments (e.g. PLACED only after bread is actually visible in the toaster). No spurious E-STOPs.

## 7. Policy paths

Before going live, point the policies at whatever's actually trained on the SF machine. In [config.yaml](reflex-orchestrator/config.yaml):

```yaml
policies:
  bread_to_toaster:
    path: ajkoder/smolvla-bread-toaster      # or local ACT checkpoint dir
    timeout_s: 30
  lever_down:
    path: ajkoder/smolvla-toaster-on         # or local ACT checkpoint dir
    timeout_s: 15
```

ACT checkpoints are preferred for runtime (less jumpy than SmolVLA on tight pick-and-place). Either family works — `policies.*.path` accepts a HF repo or a local directory.

**Check:** `lerobot-record --policy.path=<your-path> --record-time-s=5` runs end-to-end on its own (without the orchestrator). If this fails, the orchestrator can't help — fix the lerobot side first.

## 8. Live demo

```bash
python run.py --config config.yaml --voice         # headline demo
python run.py --config config.yaml --full          # adds spoken safety announcements
```

Demo scenarios are listed in [the README](README.md#demoable-scenarios) — happy path, lever-help handoff, hand-in-workspace E-STOP, lever-pressed-mid-insertion E-STOP, camera covered, help timeout.

## Common gotchas

| Symptom | Fix |
|---|---|
| `lerobot-record: command not found` | Activate the venv that has lerobot installed, or add it to PATH. Verify with `which lerobot-record`. |
| ElevenLabs silent, no error | Audio backend missing. macOS: built-in. Linux: `sudo apt install mpg123`. |
| `ELEVENLABS_API_KEY not set` | `.env` not loaded. Confirm you ran from `reflex-orchestrator/` so `load_dotenv()` finds it. |
| Camera opens then returns empty frames | Wrong device ID. Re-run the cv2 probe in step 5. |
| VLM `confidence: low` everywhere | Lighting, framing, or the wrist cam isn't actually pointed at the workspace. Capture frames and inspect with `test_vlm.py`. |
| FSM never advances past IDLE | VLM isn't seeing `bread_visible: true`. Check frames; try `--stub-vlm` to confirm the rest of the loop works. |
| Spurious `HAND_DURING_SKILL` E-STOPs | The wrist cam is seeing the gripper as a hand. Tweak the VLM prompt in [vlm.py](reflex-orchestrator/vlm.py) or move the wrist cam. |
| `pyaudio` install fails | See step 1, or just disable the mic (`agent.voice_input: false`). |
| GPU OOM on SmolVLM | Set `vlm.device: cpu` in config — slow but works. Or switch to a smaller backend. |

## Who to ping

- **FSM, safety logic, agent flow, voice tone:** Allison (remote, VT)
- **Robot, cameras, lerobot-record, policy paths:** SF team

The orchestrator runs anywhere — Allison can iterate on tone/pacing remotely with `--voice --dry-run --stub-vlm` and ship the same code to the demo machine.
