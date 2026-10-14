"""Load the configured checkpoint and exit, without starting the API or queue."""
from __future__ import annotations

import time
from pathlib import Path

import yaml

from drone_inference.pipeline import CrackPredictor, InferenceConfig


def main() -> None:
    root = Path(__file__).resolve().parent
    config_path = root / "config.yaml"
    if not config_path.is_file():
        config_path = root / "config.example.yaml"
    config = yaml.safe_load(config_path.read_text())
    checkpoint = (root / config["checkpoint"]).resolve()
    print(f"Loading checkpoint: {checkpoint}", flush=True)
    started = time.perf_counter()
    predictor = CrackPredictor(InferenceConfig(
        checkpoint=checkpoint,
        device=config.get("device", "auto"),
        threshold=float(config.get("threshold", 0.1)),
        tile_size=int(config.get("tile_size", 448)),
        tile_overlap=float(config.get("tile_overlap", 0.25)),
        min_component_px=int(config.get("min_component_px", 20)),
    ))
    print(f"MODEL READY: device={predictor.device}, elapsed={time.perf_counter() - started:.1f}s", flush=True)


if __name__ == "__main__":
    main()
