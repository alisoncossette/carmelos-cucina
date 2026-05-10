"""Carmelo's Cucina orchestrator entry point.

  python run.py --config config.yaml              # use config as-is
  python run.py --config config.yaml --basic      # policies only, stub agent (no voice, no help)
  python run.py --config config.yaml --voice      # ElevenLabs voice + fire-and-forget help
  python run.py --config config.yaml --full       # voice + spoken safety announcements

  python run.py --config config.yaml --dry-run    # log subprocess cmds, don't launch lerobot
  python run.py --config config.yaml --stub-vlm   # use scripted VLM responses
  python run.py --config config.yaml --stub-agent # force the conversational agent to stub
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

import yaml

# Load .env (ELEVENLABS_API_KEY etc.) before any module that reads os.environ.
# Silent no-op if python-dotenv isn't installed or .env doesn't exist.
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass

import agent as agent_mod
import button as button_mod
import camera as camera_mod
import dispatch as dispatch_mod
import display as display_mod
import gesture as gesture_mod
import vlm as vlm_mod
from agent import (announce_done, announce_paused, narrate_transition,
                   offer_toast, request_help, thank_for_help)
from fsm import FSM, ActionKind, State
from gesture import signal_pause_for_user, signal_success
from safety import SafetyMonitor, SafetyViolation


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.yaml")

    # Operating modes — mutually exclusive shortcuts for common config combos.
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--basic", action="store_true",
                      help="Policies only. Stub agent (no voice). No help-on-failure: "
                           "robot halts if a skill fails repeatedly. Safety still on.")
    mode.add_argument("--voice", action="store_true",
                      help="ElevenLabs voice agent. Narrates state transitions. "
                           "Fire-and-forget help when a policy fails; VLM detects resolution.")
    mode.add_argument("--full", action="store_true",
                      help="Everything in --voice plus spoken announcements for safety "
                           "events (e.g. when a human hand triggers a pause).")

    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--stub-vlm", action="store_true")
    ap.add_argument("--stub-agent", action="store_true")
    ap.add_argument("--max-ticks", type=int, default=10_000)
    ap.add_argument("--log-level", default="INFO")
    args = ap.parse_args()

    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    log = logging.getLogger("orchestrator")

    cfg = yaml.safe_load(Path(args.config).read_text())
    if args.stub_vlm:
        cfg["vlm"]["provider"] = "stub"
    if args.stub_agent:
        cfg.setdefault("agent", {})["provider"] = "stub"

    # Mode flags override agent.provider. --voice / --full also imply the new
    # help flow (fire-and-forget + VLM-resume + timeout) — see help loop below.
    # --basic disables the help flow entirely so a repeated failure just halts.
    help_fallback_enabled = True
    narrate_safety = False
    if args.basic:
        cfg.setdefault("agent", {})["provider"] = "stub"
        help_fallback_enabled = False
    elif args.voice:
        cfg.setdefault("agent", {})["provider"] = "eleven"
    elif args.full:
        cfg.setdefault("agent", {})["provider"] = "eleven"
        narrate_safety = True

    vlm = vlm_mod.build(cfg["vlm"])
    cams = None if args.stub_vlm else camera_mod.build(cfg["cameras"])
    dispatcher = dispatch_mod.build(cfg, dry_run=args.dry_run)
    gest = gesture_mod.build(cfg.get("gesture"))
    display = display_mod.build(cfg.get("display"))
    button = button_mod.build(cfg.get("button"))
    agent = agent_mod.build(cfg.get("agent"), display=display, button=button)
    fsm = FSM(debounce_ticks=int(cfg["fsm"]["debounce_ticks"]))
    safety = SafetyMonitor(
        low_confidence_limit=int(cfg["safety"]["low_confidence_limit"]),
        heartbeat_timeout_s=float(cfg["safety"]["heartbeat_timeout_s"]),
        require_workspace_clear=bool(cfg["safety"]["require_workspace_clear"]),
    )
    max_attempts = int(cfg["fsm"].get("max_skill_attempts", 2))
    help_timeout_s = float(cfg.get("agent", {}).get("help_timeout_s", 15.0))
    poll_dt = 1.0 / float(cfg["vlm"]["poll_hz"])

    log.info("Carmelo's Cucina up. poll_dt=%.2fs debounce=%d dry_run=%s stub_vlm=%s help_fallback=%s",
             poll_dt, fsm.debounce_ticks, args.dry_run, args.stub_vlm, help_fallback_enabled)

    if not offer_toast(agent):
        agent.speak("Okay. Maybe later.")
        log.info("Carmelo declined; exiting.")
        return 0
    agent.speak("Wonderful. Let me get started.")

    skill_attempts: dict[str, int] = {}
    last_state = fsm.state
    pause_signaled = False

    # Fire-and-forget help state. When a skill has failed `max_attempts` times,
    # the agent speaks the help request and we *stop dispatching* that skill.
    # The VLM keeps observing; when it sees the expected world state (e.g.
    # lever_down=true), the FSM transitions normally and we clear awaiting_help.
    awaiting_help_since: float | None = None
    help_reprompts: int = 0
    help_skill: str | None = None

    try:
        for tick in range(args.max_ticks):
            t0 = time.monotonic()
            safety.heartbeat()

            # Physical button — long_press is a manual E-STOP that any user can hit.
            btn_evt = button.poll()
            if btn_evt == "long_press":
                log.error("MANUAL E-STOP via button")
                dispatcher.kill()
                safety.log.append(SafetyViolation(
                    "MANUAL_ESTOP", "operator long-pressed the button", "estop"))
                safety._halted = True

            frames = cams.read() if cams else {}
            obs = vlm.observe(frames)
            current = dispatcher.current()

            for v in safety.runtime(obs, current):
                log.warning("SAFETY %s [%s]: %s", v.severity.upper(), v.code, v.message)
                if v.severity == "estop" and current:
                    log.error("E-STOP — killing %s", current)
                    dispatcher.kill()

            if safety.is_halted():
                fsm.force_halt(reason="safety monitor halted")
                if not pause_signaled:
                    log.info("signaling pause to Carmelo via gesture + display")
                    signal_pause_for_user(gest)
                    if narrate_safety:
                        announce_paused(agent)
                    pause_signaled = True
            else:
                pause_signaled = False

            if fsm.state != last_state:
                # Narrate transitions for Carmelo's benefit (silent if no line maps).
                narrate_transition(agent, last_state.value, fsm.state.value)
                # If help was outstanding and the FSM moved on, the VLM saw the
                # resolution — thank Carmelo and clear awaiting state.
                if awaiting_help_since is not None:
                    thank_for_help(agent)
                    awaiting_help_since = None
                    help_reprompts = 0
                    help_skill = None
                last_state = fsm.state
                skill_attempts.clear()

            action = fsm.tick(obs, skill_running=dispatcher.is_running())

            log.info("tick=%d state=%s skill=%s vlm=%s action=%s",
                     tick, fsm.state.value, dispatcher.current(),
                     {k: obs.get(k) for k in
                      ("bread_visible", "bread_in_toaster", "lever_down",
                       "toast_popped", "human_hand_visible", "user_present", "confidence")},
                     f"{action.kind.value} {action.skill or ''} ({action.reason})")

            # While awaiting human help, keep observing but DON'T dispatch the
            # failing skill again. Re-prompt once at help_timeout_s; halt at 2x.
            if awaiting_help_since is not None:
                elapsed = time.monotonic() - awaiting_help_since
                if elapsed > 2 * help_timeout_s:
                    log.warning("help timeout exceeded (%.0fs) — halting", elapsed)
                    agent.speak("I'm sorry, Carmelo. I'll stop here for now.")
                    fsm.force_halt(reason="help timeout")
                    break
                if elapsed > help_timeout_s and help_reprompts == 0 and help_skill:
                    log.info("re-prompting help for %s after %.0fs", help_skill, elapsed)
                    request_help(agent, help_skill)
                    help_reprompts = 1

            if action.kind == ActionKind.DISPATCH and action.skill:
                # Suppress dispatch while waiting for human help on this skill.
                if awaiting_help_since is not None:
                    pass
                else:
                    blockers = safety.preflight(action.skill, obs)
                    if blockers:
                        for v in blockers:
                            log.warning("PREFLIGHT BLOCKED [%s] %s: %s",
                                        v.severity.upper(), v.code, v.message)
                    else:
                        skill_attempts[action.skill] = skill_attempts.get(action.skill, 0) + 1
                        n = skill_attempts[action.skill]
                        log.info("attempt %d/%d for %s", n, max_attempts, action.skill)

                        if n > max_attempts:
                            if help_fallback_enabled:
                                log.warning("skill %s failed %d times — asking Carmelo for help",
                                            action.skill, n - 1)
                                signal_pause_for_user(gest)
                                request_help(agent, action.skill)
                                awaiting_help_since = time.monotonic()
                                help_reprompts = 0
                                help_skill = action.skill
                            else:
                                log.error("skill %s failed %d times — halting (no help fallback)",
                                          action.skill, n - 1)
                                fsm.force_halt(reason=f"{action.skill} exceeded attempts")
                                break
                        else:
                            dispatcher.launch(action.skill)
            elif action.kind == ActionKind.DONE:
                log.info("PIPELINE COMPLETE — toast is ready")
                signal_success(gest)
                announce_done(agent)
                break
            elif action.kind == ActionKind.HALT:
                log.error("SAFETY HALT — orchestrator stopping")
                break

            dispatcher.maybe_timeout()

            elapsed = time.monotonic() - t0
            time.sleep(max(0.0, poll_dt - elapsed))
    except KeyboardInterrupt:
        log.info("interrupted")
    finally:
        dispatcher.kill()
        if cams:
            cams.close()

    print("\n=== safety summary ===")
    print(json.dumps(safety.summary(), indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
