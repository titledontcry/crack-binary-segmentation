"""Run the same recorded UAV images through three crack checkpoints.

This is an inference/visual-review check. UAV accuracy needs human reference masks.
Run from the repository root with: .venv/bin/python -m drone_inference.compare_uav
"""
from __future__ import annotations

import argparse
import gc
import json
from datetime import datetime
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageOps

from .pipeline import CrackPredictor, InferenceConfig, write_visuals


ROOT = Path(__file__).resolve().parents[1]
MODELS = {
    "unet": (ROOT / "runs/20261013_112233_unet/best.pt", 0.5),
    "convnext": (ROOT / "runs/20261013_125533_convnext_upernet/best.pt", 0.5),
    "best_boat": (ROOT / "runs/model/best_boat.pt", 0.1),
}


def image_files(session: Path, count: int) -> list[Path]:
    files = sorted(path for path in (session / "originals").glob("*/*")
                   if path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"})
    if not files:
        raise ValueError(f"No UAV images found in {session / 'originals'}")
    indices = np.linspace(0, len(files) - 1, min(count, len(files)), dtype=int)
    return [files[i] for i in indices]


def comparison_sheet(files: list[Path], output: Path) -> None:
    labels = ("Original UAV", "U-Net / ResNet34", "ConvNeXt / UPerNet", "Earlier best_boat")
    width, cell_height, header, gap = 470, 300, 31, 8
    sheet = Image.new("RGB", (width * 4 + gap * 5, len(files) * (cell_height + header + gap) + gap), "#172025")
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default(size=17)
    for row, source in enumerate(files):
        images = [source] + [output / name / source.stem / "overlay.jpg"
                             for name in ("unet", "convnext", "best_boat")]
        y = gap + row * (cell_height + header + gap)
        for col, (label, path) in enumerate(zip(labels, images)):
            x = gap + col * (width + gap)
            with Image.open(path) as opened:
                preview = ImageOps.contain(opened.convert("RGB"), (width, cell_height))
            sheet.paste(preview, (x + (width - preview.width) // 2, y + header + (cell_height - preview.height) // 2))
            draw.text((x + 6, y + 3), f"{label} | {source.name}"[:55], fill="white", font=font)
    sheet.save(output / "comparison.jpg", quality=88)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session", type=Path, default=ROOT / "drone_inference/data/20261010_134920")
    parser.add_argument("--count", type=int, default=4, help="Evenly spaced images from the recorded UAV session")
    parser.add_argument("--output", type=Path, default=ROOT / "runs" / f"uav_comparison_{datetime.now():%Y%m%d_%H%M%S}")
    parser.add_argument("--device", choices=("auto", "mps", "cpu", "cuda"), default="auto")
    parser.add_argument("--threshold-unet", type=float, default=MODELS["unet"][1])
    parser.add_argument("--threshold-convnext", type=float, default=MODELS["convnext"][1])
    parser.add_argument("--threshold-best-boat", type=float, default=MODELS["best_boat"][1])
    args = parser.parse_args()
    if args.count < 1:
        parser.error("--count must be positive")
    thresholds = {"unet": args.threshold_unet, "convnext": args.threshold_convnext,
                  "best_boat": args.threshold_best_boat}
    if any(not 0 < threshold < 1 for threshold in thresholds.values()):
        parser.error("Every threshold must be between 0 and 1")
    files = image_files(args.session, args.count)
    args.output.mkdir(parents=True, exist_ok=False)
    report = {"purpose": "Unlabelled UAV inference and visual review; not an accuracy evaluation",
              "source_session": str(args.session), "thresholds": thresholds, "images": []}
    for source in files:
        report["images"].append({"filename": source.name, "source": str(source), "models": {}})
    for name, (checkpoint, _) in MODELS.items():
        if not checkpoint.is_file():
            raise FileNotFoundError(checkpoint)
        print(f"Loading {name}: {checkpoint}", flush=True)
        predictor = CrackPredictor(InferenceConfig(checkpoint=checkpoint, device=args.device,
                                                   threshold=thresholds[name]))
        for entry, source in zip(report["images"], files):
            print(f"  {source.name}", flush=True)
            image, mask, measurement = predictor.predict(source)
            result_dir = args.output / name / source.stem
            write_visuals(image, mask, measurement, result_dir)
            entry["models"][name] = {"mask": str(result_dir / "mask.png"),
                                     "overlay": str(result_dir / "overlay.jpg"),
                                     "crack_pixels": measurement["crack_pixels"],
                                     "length_px": measurement["length_px"],
                                     "width_px_max": measurement["width_px_max"]}
        del predictor
        gc.collect()
        try:
            import torch
            if torch.backends.mps.is_available():
                torch.mps.empty_cache()
        except ImportError:
            pass
    comparison_sheet(files, args.output)
    (args.output / "summary.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"Saved visual comparison: {args.output / 'comparison.jpg'}", flush=True)


if __name__ == "__main__":
    main()
