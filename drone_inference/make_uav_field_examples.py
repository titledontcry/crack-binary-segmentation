"""Generate unlabelled real-UAV inference examples for visual review only.

Run from the repository root:
    .venv/bin/python -m drone_inference.make_uav_field_examples --device cpu
"""
from __future__ import annotations

import argparse
import gc
from pathlib import Path

import numpy as np
import torch
from PIL import Image, ImageDraw, ImageFont

from .pipeline import CrackPredictor, InferenceConfig

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "runs/slide_assets/uav_field_examples_20261014"
CHECKPOINTS = {
    "unet": ROOT / "runs/20261013_112233_unet/best.pt",
    "convnext_upernet": ROOT / "runs/20261013_125533_convnext_upernet/best.pt",
}
SAMPLES = [
    ROOT / "drone_inference/data/flight_8f2dcd7b0b60/originals/40f9dc90-c6c5-4fc9-867d-0e824fb30c81/crack_20261014T082248_000079.jpg",
    ROOT / "drone_inference/data/flight_8f2dcd7b0b60/originals/7cf275c0-d4e8-45b5-8f12-b5f135a8a3f8/crack_20261014T082253_000080.jpg",
    ROOT / "drone_inference/data/flight_aacf14b7048f/originals/30d9612d-fe17-436d-a755-464177a9d93a/crack_20261014T083800_000260.jpg",
]


def font(size: int):
    for path in ("/System/Library/Fonts/Supplemental/Arial.ttf",
                 "/System/Library/Fonts/Supplemental/Arial Bold.ttf"):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            pass
    return ImageFont.load_default(size=size)


def overlay(rgb: np.ndarray, mask: np.ndarray) -> Image.Image:
    result = rgb.astype(np.float32).copy()
    color = np.asarray((35, 205, 154), dtype=np.float32)
    result[mask] = 0.50 * result[mask] + 0.50 * color
    return Image.fromarray(np.uint8(np.clip(result, 0, 255)))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", choices=("auto", "cpu", "mps", "cuda"), default="auto")
    parser.add_argument("--threshold", type=float, default=0.5)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    if not 0 < args.threshold < 1:
        parser.error("--threshold must be between 0 and 1")
    missing = [str(path) for path in SAMPLES if not path.is_file()]
    if missing:
        parser.error("Missing UAV image(s): " + ", ".join(missing))
    missing_models = [str(path) for path in CHECKPOINTS.values() if not path.is_file()]
    if missing_models:
        parser.error("Missing checkpoint(s): " + ", ".join(missing_models))
    args.output.mkdir(parents=True, exist_ok=True)

    rows: dict[str, dict[str, Image.Image]] = {
        str(path): {"original": Image.open(path).convert("RGB")} for path in SAMPLES
    }
    for model_name, checkpoint in CHECKPOINTS.items():
        print(f"Running {model_name} on 3 full-resolution UAV images (threshold={args.threshold})", flush=True)
        predictor = CrackPredictor(InferenceConfig(checkpoint=checkpoint, device=args.device,
                                                   threshold=args.threshold))
        for index, path in enumerate(SAMPLES, 1):
            print(f"  {index}/3 {path.name}", flush=True)
            rgb, mask, measurement = predictor.predict(path)
            mask_bool = mask.astype(bool)
            rows[str(path)][model_name] = overlay(rgb, mask_bool)
            rows[str(path)][f"{model_name}_mask"] = Image.fromarray(mask.astype(np.uint8) * 255)
            rows[str(path)][f"{model_name}_pixels"] = measurement["crack_pixels"]
        del predictor
        gc.collect()
        if torch.backends.mps.is_available():
            torch.mps.empty_cache()

    # Presentation board: each row is one real image, same image across models.
    labels = ("Original UAV image", "U-Net / ResNet34 prediction", "ConvNeXt-Tiny / UPerNet prediction")
    panel_w, panel_h, gap, left = 500, 281, 22, 36
    row_h, top = panel_h + 88, 245
    width = left * 2 + panel_w * 3 + gap * 2
    height = top + row_h * len(SAMPLES) + 35
    board = Image.new("RGB", (width, height), "#f5f7f8")
    draw = ImageDraw.Draw(board)
    ink, muted = "#1d2c38", "#5f707b"
    draw.text((left, 38), "UAV Field Examples | Qualitative Inference", font=font(38), fill=ink)
    draw.text((left, 96), f"Captured 14 Oct 2026 · threshold {args.threshold:.2f} · no TTA · no ground-truth masks",
              font=font(21), fill=muted)
    draw.text((left, 132), "Green overlay = pixels predicted as crack; it does not indicate verified detections.",
              font=font(19), fill=muted)
    for col, label in enumerate(labels):
        draw.text((left + col * (panel_w + gap), 202), label, font=font(19), fill=ink)

    for row_idx, path in enumerate(SAMPLES):
        y = top + row_idx * row_h
        draw.text((left, y - 27), path.name, font=font(16), fill=muted)
        sample_rows = rows[str(path)]
        panels = (sample_rows["original"], sample_rows["unet"], sample_rows["convnext_upernet"])
        for col, panel in enumerate(panels):
            x = left + col * (panel_w + gap)
            preview = panel.resize((panel_w, panel_h), Image.Resampling.LANCZOS)
            board.paste(preview, (x, y))
            draw.rectangle((x, y, x + panel_w - 1, y + panel_h - 1), outline="#cbd4d9", width=2)

        folder = args.output / path.stem
        folder.mkdir(parents=True, exist_ok=True)
        sample_rows["original"].save(folder / "original_image.png")
        sample_rows["unet"].save(folder / "unet_resnet34_prediction_overlay.png")
        sample_rows["convnext_upernet"].save(folder / "convnext_upernet_prediction_overlay.png")
        sample_rows["unet_mask"].save(folder / "unet_resnet34_mask.png")
        sample_rows["convnext_upernet_mask"].save(folder / "convnext_upernet_mask.png")
        (folder / "README.txt").write_text(
            "Real UAV image captured on 2026-10-14. No ground-truth mask is available.\n"
            "These are qualitative model predictions only, not accuracy/evaluation results.\n"
            f"Threshold: {args.threshold:.2f}; TTA: off.\n"
            f"U-Net predicted crack pixels: {sample_rows['unet_pixels']}\n"
            f"ConvNeXt-UPerNet predicted crack pixels: {sample_rows['convnext_upernet_pixels']}\n",
            encoding="utf-8")

    args.output.mkdir(parents=True, exist_ok=True)
    board.save(args.output / "uav_examples_comparison.png")
    print(f"Saved board: {args.output / 'uav_examples_comparison.png'}", flush=True)
    print(f"Saved per-image folders: {args.output}", flush=True)


if __name__ == "__main__":
    main()
