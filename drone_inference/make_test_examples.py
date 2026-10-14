"""Create a qualitative comparison board from selected held-out test images.

Run from the repository root:
    .venv/bin/python -m drone_inference.make_test_examples --device auto
"""
from __future__ import annotations

import argparse
import csv
from pathlib import Path

import numpy as np
import torch
from PIL import Image, ImageDraw, ImageFont

from .evaluate_checkpoints import image_and_mask
from .pipeline import CrackPredictor, InferenceConfig

ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = ROOT / "final_road_dataset_cleaned"
EVAL_ROOT = ROOT / "runs/evaluation_three_models"
OUTPUT = ROOT / "runs/slide_assets/test_set_comparison_clear/test_set_comparison.png"
CHECKPOINTS = {
    "unet": ROOT / "runs/20261013_112233_unet/best.pt",
    "convnext": ROOT / "runs/20261013_125533_convnext_upernet/best.pt",
}
EXAMPLES = [
    ("CRACK500_20160308_155737_1281_361.jpg", "Higher Dice: ConvNeXt-UPerNet | sharper image"),
    ("CRACK500_20160329_103800_361_1281.jpg", "Higher Dice: U-Net / ResNet34"),
]


def load_csv(path: Path) -> dict[str, dict]:
    with path.open(newline="", encoding="utf-8") as handle:
        return {row["sample_id"]: row for row in csv.DictReader(handle)}


def font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in ("/System/Library/Fonts/Supplemental/Arial.ttf",
                 "/System/Library/Fonts/Supplemental/Arial Bold.ttf"):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            pass
    return ImageFont.load_default(size=size)


def confusion_overlay(rgb: np.ndarray, pred: np.ndarray, target: np.ndarray) -> Image.Image:
    # Mutually exclusive colors: TP green, FP red, FN blue.
    colors = np.asarray(((46, 190, 140), (237, 75, 79), (68, 145, 245)), dtype=np.float32)
    tp = pred & target
    fp = pred & ~target
    fn = ~pred & target
    result = rgb.astype(np.float32).copy()
    for selected, color in zip((tp, fp, fn), colors):
        result[selected] = 0.46 * result[selected] + 0.54 * color
    return Image.fromarray(np.uint8(np.clip(result, 0, 255)))


def mask_overlay(rgb: np.ndarray, mask: np.ndarray, color=(0, 190, 125)) -> Image.Image:
    result = rgb.astype(np.float32).copy()
    result[mask] = 0.46 * result[mask] + 0.54 * np.asarray(color, dtype=np.float32)
    return Image.fromarray(np.uint8(np.clip(result, 0, 255)))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", choices=("auto", "mps", "cpu", "cuda"), default="auto")
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()

    manifest = load_csv(DATA_ROOT / "manifest.csv")
    scores = {model: load_csv(EVAL_ROOT / model / "test_per_image.csv")
              for model in CHECKPOINTS}
    selected = []
    for sample_id, label in EXAMPLES:
        row = manifest.get(sample_id)
        if not row or row["previous_split"].strip().lower() != "test":
            raise ValueError(f"{sample_id} is not in the held-out test split")
        if any(sample_id not in scores[model] for model in CHECKPOINTS):
            raise ValueError(f"Missing saved test metrics for {sample_id}")
        normalized, target = image_and_mask(DATA_ROOT, row)
        image = np.asarray(Image.open(DATA_ROOT / row["image_path"]).convert("RGB"))
        selected.append((sample_id, label, row, normalized, target, image))

    predictions: dict[str, dict[str, np.ndarray]] = {name: {} for name in CHECKPOINTS}
    for model_name, checkpoint in CHECKPOINTS.items():
        print(f"Predicting {model_name} on {len(selected)} held-out images", flush=True)
        predictor = CrackPredictor(InferenceConfig(checkpoint=checkpoint, device=args.device,
                                                   threshold=0.5))
        for sample_id, _, _, normalized, _, _ in selected:
            x = torch.from_numpy(normalized[None]).to(predictor.device)
            with torch.inference_mode():
                predictions[model_name][sample_id] = (torch.sigmoid(predictor.model(x))[0, 0]
                                                       .cpu().numpy() > 0.5)
        del predictor
        if torch.backends.mps.is_available():
            torch.mps.empty_cache()

    panel = 420
    gap = 24
    left = 48
    column_gap = 22
    columns = 4
    width = left * 2 + columns * panel + (columns - 1) * column_gap
    header_height = 270
    row_height = panel + 110
    height = header_height + len(selected) * row_height + 40
    canvas = Image.new("RGB", (width, height), "#f7f9fa")
    draw = ImageDraw.Draw(canvas)
    ink, muted = "#1d2c38", "#60717d"
    draw.text((left, 38), "Held-out Test Set | Same Images, Two Models", font=font(42), fill=ink)
    draw.text((left, 100), "Threshold 0.50 · no TTA · green = TP · red = FP · blue = FN",
              font=font(23), fill=muted)
    headers = ("Original image", "Ground-truth mask", "U-Net / ResNet34", "ConvNeXt-Tiny / UPerNet")
    for col, title in enumerate(headers):
        x = left + col * (panel + column_gap)
        draw.text((x, 190), title, font=font(22), fill=ink)

    for row_index, (sample_id, label, metadata, _, target, rgb) in enumerate(selected):
        y = header_height + row_index * row_height
        draw.rounded_rectangle((left, y, width - left, y + 38), radius=6, fill="#e8eef1")
        draw.text((left + 10, y + 7), f"{label}  |  {sample_id}  |  {metadata['source_dataset']}",
                  font=font(17), fill=ink)
        gt_overlay = mask_overlay(rgb, target)
        unet_overlay = confusion_overlay(rgb, predictions["unet"][sample_id], target)
        convnext_overlay = confusion_overlay(rgb, predictions["convnext"][sample_id], target)
        panels = [Image.fromarray(rgb), gt_overlay, unet_overlay, convnext_overlay]
        for col, picture in enumerate(panels):
            x = left + col * (panel + column_gap)
            picture = picture.resize((panel, panel), Image.Resampling.LANCZOS)
            canvas.paste(picture, (x, y + 48))
            draw.rectangle((x, y + 48, x + panel - 1, y + 48 + panel - 1), outline="#cbd5da", width=2)
        u_dice = float(scores["unet"][sample_id]["dice"])
        c_dice = float(scores["convnext"][sample_id]["dice"])
        draw.text((left + 2 * (panel + column_gap), y + 48 + panel + 10),
                  f"Dice {u_dice:.3f}", font=font(19), fill=ink)
        draw.text((left + 3 * (panel + column_gap), y + 48 + panel + 10),
                  f"Dice {c_dice:.3f}", font=font(19), fill=ink)

        winner = "convnext_upernet_better" if c_dice > u_dice else "unet_resnet34_better"
        case_dir = args.output.parent / winner / Path(sample_id).stem
        case_dir.mkdir(parents=True, exist_ok=True)
        Image.fromarray(rgb).save(case_dir / "original_image.png")
        gt_overlay.save(case_dir / "ground_truth.png")
        unet_overlay.save(case_dir / "unet_resnet34_prediction.png")
        convnext_overlay.save(case_dir / "convnext_upernet_prediction.png")
        Image.fromarray((target.astype(np.uint8) * 255)).save(case_dir / "ground_truth_binary_mask.png")
        Image.fromarray((predictions["unet"][sample_id].astype(np.uint8) * 255)).save(case_dir / "unet_resnet34_binary_mask.png")
        Image.fromarray((predictions["convnext"][sample_id].astype(np.uint8) * 255)).save(case_dir / "convnext_upernet_binary_mask.png")
        with (case_dir / "case_info.txt").open("w", encoding="utf-8") as handle:
            handle.write(f"Sample: {sample_id}\nSource: {metadata['source_dataset']}\n")
            handle.write(f"Split: held-out test\nThreshold: 0.50\nTTA: off\n")
            handle.write(f"U-Net Dice: {u_dice:.6f}\nConvNeXt-UPerNet Dice: {c_dice:.6f}\n")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(args.output)
    print(f"Saved comparison board: {args.output}", flush=True)
    print("Cases:", flush=True)
    for sample_id, label in EXAMPLES:
        folder = "convnext_upernet_better" if float(scores["convnext"][sample_id]["dice"]) > float(scores["unet"][sample_id]["dice"]) else "unet_resnet34_better"
        print(f"  {sample_id}: U-Net Dice {float(scores['unet'][sample_id]['dice']):.3f}; "
              f"ConvNeXt Dice {float(scores['convnext'][sample_id]['dice']):.3f} — {label}; "
              f"assets: {args.output.parent / folder / Path(sample_id).stem}", flush=True)


if __name__ == "__main__":
    main()
