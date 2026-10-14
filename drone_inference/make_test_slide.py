"""Build a presentation-ready comparison using one held-out test image.

Example: .venv/bin/python -m drone_inference.make_test_slide --device mps
"""
from __future__ import annotations

import argparse
import csv
from pathlib import Path

import cv2
import numpy as np
import torch
from PIL import Image, ImageDraw, ImageFont

from .evaluate_checkpoints import image_and_mask, probability
from .pipeline import CrackPredictor, InferenceConfig


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SAMPLE = "DeepCrack_IMG27-10.jpg"
CHECKPOINTS = {
    "U-Net / ResNet34": ROOT / "runs/20261013_112233_unet/best.pt",
    "ConvNeXt-Tiny / UPerNet-style": ROOT / "runs/20261013_125533_convnext_upernet/best.pt",
}
FONT_PATH = "/System/Library/Fonts/Supplemental/Arial.ttf"


def font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    try:
        return ImageFont.truetype(FONT_PATH, size)
    except OSError:
        return ImageFont.load_default(size=size)


def overlay(image: np.ndarray, mask: np.ndarray, color: tuple[int, int, int]) -> Image.Image:
    result = image.copy().astype(np.float32)
    result[mask] = 0.35 * result[mask] + 0.65 * np.asarray(color, dtype=np.float32)
    return Image.fromarray(np.uint8(np.clip(result, 0, 255)))


def lookup_row(manifest: Path, sample_id: str) -> dict:
    with manifest.open(newline="", encoding="utf-8") as handle:
        matches = [row for row in csv.DictReader(handle) if row["sample_id"] == sample_id]
    if len(matches) != 1:
        raise ValueError(f"Expected one manifest entry for {sample_id}, found {len(matches)}")
    row = matches[0]
    if row["previous_split"].strip().lower() != "test":
        raise ValueError(f"{sample_id} is not in the held-out test split")
    return row


def per_image_metrics(model: str, sample_id: str) -> dict:
    path = ROOT / "runs/evaluation_three_models" / model / "test_per_image.csv"
    with path.open(newline="", encoding="utf-8") as handle:
        matches = [row for row in csv.DictReader(handle) if row["sample_id"] == sample_id]
    if len(matches) != 1:
        raise ValueError(f"Missing test evaluation for {model}: {sample_id}")
    return matches[0]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample-id", default=DEFAULT_SAMPLE)
    parser.add_argument("--device", choices=("auto", "mps", "cpu", "cuda"), default="auto")
    parser.add_argument("--output", type=Path, default=ROOT / "runs/slide_assets/experimental_design_road_comparison.png")
    args = parser.parse_args()

    data_root = ROOT / "final_road_dataset_cleaned"
    row = lookup_row(data_root / "manifest.csv", args.sample_id)
    normalized, target = image_and_mask(data_root, row)
    bgr = cv2.imread(str(data_root / row["image_path"]), cv2.IMREAD_COLOR)
    if bgr is None:
        raise FileNotFoundError(data_root / row["image_path"])
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)

    panels = [Image.fromarray(rgb), overlay(rgb, target, (27, 180, 123))]
    captions = ["Original road image", "Human reference mask"]
    scores = ["Held-out test image", "Green = annotated crack"]
    for index, (label, checkpoint) in enumerate(CHECKPOINTS.items()):
        predictor = CrackPredictor(InferenceConfig(checkpoint=checkpoint, device=args.device, threshold=0.5))
        x = torch.from_numpy(normalized[None]).to(predictor.device)
        direct, _ = probability(predictor.model, x, with_tta=False)
        mask = direct[0] > 0.5
        panels.append(overlay(rgb, mask, (226, 91, 55)))
        captions.append(label)
        score = per_image_metrics(("unet", "convnext")[index], args.sample_id)
        scores.append(f"IoU {float(score['iou']):.3f}    Dice {float(score['dice']):.3f}")
        del predictor
        if torch.backends.mps.is_available():
            torch.mps.empty_cache()

    canvas = Image.new("RGB", (1920, 1080), "#ffffff")
    draw = ImageDraw.Draw(canvas)
    ink, muted, accent = "#1d2c38", "#61717c", "#d46b3c"
    draw.text((72, 76), "Same road image, two segmentation models", font=font(53), fill=ink)
    draw.text((74, 148), f"Held-out test set | Source: {row['source_dataset']} | {args.sample_id}",
              font=font(25), fill=muted)
    draw.line((72, 204, 1848, 204), fill="#d8e0e5", width=2)

    panel_width, gap, left, top = 420, 27, 72, 294
    for index, (picture, title, score) in enumerate(zip(panels, captions, scores)):
        x = left + index * (panel_width + gap)
        draw.text((x, 247), title, font=font(27), fill=ink)
        canvas.paste(picture.resize((panel_width, panel_width), Image.Resampling.LANCZOS), (x, top))
        draw.rectangle((x, top, x + panel_width - 1, top + panel_width - 1), outline="#d8e0e5", width=2)
        draw.text((x, 748), score, font=font(25), fill=accent if index >= 2 else muted)

    draw.line((72, 868, 1848, 868), fill="#d8e0e5", width=2)
    draw.text((72, 909), "Both models: same test image, 448 x 448 input, threshold 0.50, no TTA.",
              font=font(25), fill=ink)
    draw.text((72, 950), "Illustrative example only; use full 589-image test results for overall performance.",
              font=font(24), fill=muted)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    panel_dir = args.output.parent / "road_comparison"
    panel_dir.mkdir(parents=True, exist_ok=True)
    for name, picture in zip(("01_original", "02_reference_mask", "03_unet", "04_convnext_upernet"), panels):
        picture.save(panel_dir / f"{name}.png")
    canvas.save(args.output)
    print(f"Saved {args.output}", flush=True)
    print(f"Saved four individual panels: {panel_dir}", flush=True)


if __name__ == "__main__":
    main()
