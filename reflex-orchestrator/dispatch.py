"""Policy dispatcher.

Wraps `lerobot-record --policy.path=...` (or whatever subprocess invocation
the team's LeRobot version uses) so the FSM can fire-and-forget skills and
poll for completion.

Set `dry_run=True` to log commands without launching them — useful for
end-to-end orchestrator tests without the robot connected.
"""

from __future__ import annotations

import logging
import shlex
import subprocess
import time
from dataclasses import dataclass

log = logging.getLogger(__name__)


@dataclass
class SkillSpec:
    name: str
    policy_path: str
    timeout_s: int


class Dispatcher:
    def __init__(self, cmd_template: str, skills: dict[str, SkillSpec], dry_run: bool = False):
        self.cmd_template = cmd_template
        self.skills = skills
        self.dry_run = dry_run
        self._proc: subprocess.Popen | None = None
        self._started_at: float = 0.0
        self._current: str | None = None
        self._dry_run_until: float = 0.0

    def is_running(self) -> bool:
        if self.dry_run:
            return time.monotonic() < self._dry_run_until
        if self._proc is None:
            return False
        if self._proc.poll() is None:
            return True
        # process exited
        log.info("skill %s finished with code %s", self._current, self._proc.returncode)
        self._proc = None
        self._current = None
        return False

    def current(self) -> str | None:
        return self._current if self.is_running() else None

    def launch(self, skill_name: str) -> None:
        if self.is_running():
            raise RuntimeError(f"cannot launch {skill_name}; {self._current} still running")
        if skill_name not in self.skills:
            raise KeyError(f"unknown skill: {skill_name}")
        spec = self.skills[skill_name]

        cmd_str = self.cmd_template.format(policy_path=spec.policy_path, timeout_s=spec.timeout_s)
        argv = shlex.split(cmd_str)
        log.info("dispatch %s: %s", skill_name, cmd_str)

        if self.dry_run:
            self._current = skill_name
            self._dry_run_until = time.monotonic() + min(spec.timeout_s, 4)  # short fake duration
            return

        self._proc = subprocess.Popen(argv)
        self._started_at = time.monotonic()
        self._current = skill_name

    def kill(self) -> None:
        if self._proc and self._proc.poll() is None:
            log.warning("killing %s", self._current)
            self._proc.terminate()
            try:
                self._proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        self._proc = None
        self._current = None

    def maybe_timeout(self) -> bool:
        """Kill the running skill if it's exceeded its budget. Returns True if killed."""
        if self.dry_run:
            return False
        if not self._proc or not self._current:
            return False
        spec = self.skills[self._current]
        if time.monotonic() - self._started_at > spec.timeout_s + 5:
            log.warning("skill %s exceeded timeout, killing", self._current)
            self.kill()
            return True
        return False


def build(cfg: dict, dry_run: bool = False) -> Dispatcher:
    skills = {
        name: SkillSpec(name=name, policy_path=v["path"], timeout_s=int(v["timeout_s"]))
        for name, v in cfg["policies"].items()
    }
    return Dispatcher(cfg["dispatch"]["cmd_template"], skills, dry_run=dry_run)
