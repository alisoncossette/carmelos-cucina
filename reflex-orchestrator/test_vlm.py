"""Standalone VLM smoke test.

  python test_vlm.py --frames-dir ./test_frames
  python test_vlm.py --frames-dir ./test_frames --provider qwen2vl

Loads each image in --frames-dir, runs the VLM once, prints the parsed JSON.
Use this BEFORE plugging the orchestrator into the robot to confirm the VLM
gives sane state readings on representative frames.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2

import vlm as vlm_mod


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--frames-dir", required=True)
    ap.add_argument("--provider", default="smolvlm", choices=["smolvlm", "qwen2vl", "stub"])
    ap.add_argument("--device", default="cuda")
    args = ap.parse_args()

    client = vlm_mod.build({"provider": args.provider, "device": args.device})

    frames_dir = Path(args.frames_dir)
    images = sorted(p for p in frames_dir.iterdir()
                    if p.suffix.lower() in {".jpg", ".jpeg", ".png"})
    if not images:
        print(f"no images in {frames_dir}")
        return 1

    for img_path in images:
        img = cv2.imread(str(img_path))
        if img is None:
            print(f"skip {img_path}: cannot read")
            continue
        # Use the same image for both wrist+env in this smoke test;
        # in production you pass actual wrist + env frames.
        obs = client.observe({"wrist": img, "env": img})
        print(f"\n{img_path.name}")
        print(json.dumps({k: v for k, v in obs.items() if k != "raw"}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
