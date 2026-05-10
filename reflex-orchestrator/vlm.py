"""VLM clients. Swappable behind a thin interface.

Each client implements `observe(frames: dict[str, np.ndarray]) -> dict`,
returning a structured scene-state JSON the FSM and SafetyMonitor consume.

Schema:
    {
        # task progress
        "bread_visible": bool,
        "bread_in_toaster": bool,
        "lever_down": bool,
        "toast_popped": bool,

        # safety + presence (Carmelo's Cucina is built for an elderly user)
        "human_hand_visible": bool,    # hand IN the workspace = collision risk
        "user_present": bool,          # user (Carmelo) is in frame, watching/standing nearby
        "workspace_clear": bool,       # no unexpected obstructions

        "confidence": "low" | "med" | "high",
        "raw": str          # raw model text for debugging
    }
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Protocol

import numpy as np


SCENE_PROMPT = """You are a scene-state observer for Carmelo's Cucina, a kitchen robot built to assist an elderly person ("Carmelo"). Two cameras are attached: a wrist camera and a fixed environment camera. Look at both.

Report TASK STATE:
- bread_visible: is a slice of bread visible in either view?
- bread_in_toaster: is bread inside the toaster slot?
- lever_down: is the toaster lever pressed down (toaster ON)?
- toast_popped: has the toast popped up out of the toaster?

Report SAFETY + PRESENCE — these gate dispatch and can pause the robot:
- human_hand_visible: is a human hand, finger, or arm INSIDE the active workspace (near the bread, toaster, or arm path)? (be conservative — if unsure, true)
- user_present: is the user visible in either frame (standing nearby, supervising, watching)? This is normal and expected — NOT a safety event by itself.
- workspace_clear: is the workspace free of unexpected obstructions?

Report CONFIDENCE:
- confidence: "low" if blurry/occluded/dark, "high" if both views are clear, "med" otherwise.

Respond with ONE JSON object on a single line, no prose, no markdown fences:
{"bread_visible":<bool>,"bread_in_toaster":<bool>,"lever_down":<bool>,"toast_popped":<bool>,"human_hand_visible":<bool>,"user_present":<bool>,"workspace_clear":<bool>,"confidence":"<low|med|high>"}
"""


_BOOL_KEYS = ("bread_visible", "bread_in_toaster", "lever_down", "toast_popped",
              "human_hand_visible", "user_present")


class VLMClient(Protocol):
    def observe(self, frames: dict[str, np.ndarray]) -> dict[str, Any]: ...


def _parse_json(text: str) -> dict[str, Any]:
    """Defensive parse. On failure, return SAFE DEFAULTS — assume hand in
    workspace and workspace obstructed, so the SafetyMonitor refuses to act."""
    match = re.search(r"\{.*?\}", text, re.DOTALL)
    safe_default = {
        "bread_visible": False, "bread_in_toaster": False,
        "lever_down": False, "toast_popped": False,
        "human_hand_visible": True,    # fail-safe
        "user_present": True,          # fail-safe (assume Carmelo is there)
        "workspace_clear": False,      # fail-safe
        "confidence": "low",
        "raw": text, "parse_error": True,
    }
    if not match:
        return safe_default
    try:
        obj = json.loads(match.group(0))
    except json.JSONDecodeError:
        return safe_default

    obj["raw"] = text
    obj.setdefault("confidence", "med")
    for key in _BOOL_KEYS:
        obj.setdefault(key, False)
        obj[key] = bool(obj[key])
    obj["workspace_clear"] = bool(obj.get("workspace_clear", True))
    return obj


# ─────────────────────────────────────────────────────────────────────────────

class SmolVLMClient:
    def __init__(self, model_id: str = "HuggingFaceTB/SmolVLM-Instruct", device: str = "cuda"):
        from transformers import AutoProcessor, AutoModelForVision2Seq
        import torch

        self._torch = torch
        self.device = device
        self.processor = AutoProcessor.from_pretrained(model_id)
        self.model = AutoModelForVision2Seq.from_pretrained(
            model_id,
            torch_dtype=torch.bfloat16 if device == "cuda" else torch.float32,
        ).to(device)
        self.model.eval()

    def observe(self, frames: dict[str, np.ndarray]) -> dict[str, Any]:
        from PIL import Image

        images = [Image.fromarray(_to_rgb(f)) for f in frames.values()]
        content = [{"type": "image"} for _ in images] + [{"type": "text", "text": SCENE_PROMPT}]
        messages = [{"role": "user", "content": content}]
        prompt = self.processor.apply_chat_template(messages, add_generation_prompt=True)
        inputs = self.processor(text=prompt, images=images, return_tensors="pt").to(self.device)

        with self._torch.no_grad():
            out = self.model.generate(**inputs, max_new_tokens=180, do_sample=False)
        text = self.processor.batch_decode(out, skip_special_tokens=True)[0]
        text = text.split("Assistant:")[-1] if "Assistant:" in text else text
        return _parse_json(text)


class Qwen2VLClient:
    def __init__(self, model_id: str = "Qwen/Qwen2-VL-7B-Instruct", device: str = "cuda"):
        from transformers import AutoProcessor, Qwen2VLForConditionalGeneration
        import torch

        self._torch = torch
        self.device = device
        self.processor = AutoProcessor.from_pretrained(model_id)
        self.model = Qwen2VLForConditionalGeneration.from_pretrained(
            model_id,
            torch_dtype=torch.bfloat16 if device == "cuda" else torch.float32,
        ).to(device)
        self.model.eval()

    def observe(self, frames: dict[str, np.ndarray]) -> dict[str, Any]:
        from PIL import Image

        images = [Image.fromarray(_to_rgb(f)) for f in frames.values()]
        content = [{"type": "image", "image": img} for img in images]
        content.append({"type": "text", "text": SCENE_PROMPT})
        messages = [{"role": "user", "content": content}]
        text_prompt = self.processor.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
        inputs = self.processor(text=[text_prompt], images=images, return_tensors="pt").to(self.device)

        with self._torch.no_grad():
            out = self.model.generate(**inputs, max_new_tokens=180, do_sample=False)
        trimmed = out[:, inputs.input_ids.shape[1]:]
        text = self.processor.batch_decode(trimmed, skip_special_tokens=True)[0]
        return _parse_json(text)


@dataclass
class StubVLMClient:
    """Returns a scripted sequence of observations. For dry-run testing."""
    script: list[dict[str, Any]] | None = None

    def __post_init__(self):
        if self.script is None:
            base = {"workspace_clear": True, "human_hand_visible": False,
                    "user_present": True, "confidence": "high"}
            # Timing notes (assuming poll_hz=1.5 → ~0.67s/tick, dry_run fake skill = 4s = 6 ticks):
            #   ticks 0–2:  bread visible (FSM debounce → PLACING; bread_to_toaster dispatches)
            #   ticks 3–8:  bread_in_toaster=True while bread skill is in flight (NO lever_down here —
            #               that would trip LEVER_DOWN_DURING_INSERT safety)
            #   tick  9:    bread skill done → PLACED → PRESSING; lever_down policy dispatches
            #   ticks 9–14: lever_down=True while lever skill in flight
            #   tick 15:    lever skill done → TOASTING
            #   ticks 16+:  toast_popped=True → DONE
            placed = {**base, "bread_visible": True, "bread_in_toaster": True}
            lever  = {**placed, "lever_down": True}
            popped = {**base, "bread_visible": True, "toast_popped": True}
            self.script = (
                [{**base, "bread_visible": True}] * 3   # PLACING debounce
                + [placed] * 7                          # bread skill in flight
                + [lever] * 7                           # lever skill in flight + transition
                + [popped] * 4                          # toast popped → DONE
            )
        self._idx = 0

    def observe(self, frames: dict[str, np.ndarray]) -> dict[str, Any]:
        obs = self.script[min(self._idx, len(self.script) - 1)].copy()
        obs["raw"] = "[stub]"
        self._idx += 1
        return obs


def _to_rgb(arr: np.ndarray) -> np.ndarray:
    if arr.ndim == 3 and arr.shape[2] == 3:
        return arr[:, :, ::-1].copy()
    return arr


def build(cfg: dict) -> VLMClient:
    provider = cfg.get("provider", "smolvlm")
    if provider == "stub":
        return StubVLMClient()
    if provider == "smolvlm":
        return SmolVLMClient(cfg.get("model_id", "HuggingFaceTB/SmolVLM-Instruct"),
                             cfg.get("device", "cuda"))
    if provider == "qwen2vl":
        return Qwen2VLClient(cfg.get("model_id", "Qwen/Qwen2-VL-7B-Instruct"),
                             cfg.get("device", "cuda"))
    raise ValueError(f"unknown VLM provider: {provider}")
