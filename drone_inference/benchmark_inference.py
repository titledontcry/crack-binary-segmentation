"""Benchmark full-resolution inference latency for the two final checkpoints.

Measures model inference plus sliding-window stitching on the same UAV images.
Image decoding, model loading, mask post-processing, and visual export are
excluded. Run from the repository root:

    .venv/bin/python -m drone_inference.benchmark_inference --count 30
"""
from __future__ import annotations

import argparse
import csv
import gc
import json
import statistics
import time
from datetime import datetime
from pathlib import Path

import numpy as np
from PIL import Image

from .pipeline import CrackPredictor, InferenceConfig

ROOT = Path(__file__).resolve().parents[1]
CHECKPOINTS = {
    "unet": ROOT / "runs/20261013_112233_unet/best.pt",
    "convnext_upernet": ROOT / "runs/20261013_125533_convnext_upernet/best.pt",
}
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}


def collect_images(data_root: Path, count: int) -> list[Path]:
    paths = sorted(path for path in data_root.glob("*/originals/**/*")
                   if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES)
    if not paths:
        raise ValueError(f"No UAV images found under {data_root}")
    indices = np.linspace(0, len(paths) - 1, min(count, len(paths)), dtype=int)
    return [paths[index] for index in indices]


def synchronize(device) -> None:
    import torch

    if device.type == "mps":
        torch.mps.synchronize()
    elif device.type == "cuda":
        torch.cuda.synchronize(device)


def percentile(values: list[float], pct: float) -> float:
    return float(np.percentile(np.asarray(values), pct))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=ROOT / "drone_inference/data")
    parser.add_argument("--count", type=int, default=30)
    parser.add_argument("--warmup", type=int, default=3)
    parser.add_argument("--device", choices=("auto", "cpu", "mps", "cuda"), default="auto")
    parser.add_argument("--output", type=Path,
                        default=ROOT / "runs" / f"inference_benchmark_{datetime.now():%Y%m%d_%H%M%S}")
    args = parser.parse_args()
    if args.count < 1 or args.warmup < 0:
        parser.error("--count must be positive and --warmup cannot be negative")
    for checkpoint in CHECKPOINTS.values():
        if not checkpoint.is_file():
            parser.error(f"Missing checkpoint: {checkpoint}")

    images = collect_images(args.data, args.count)
    args.output.mkdir(parents=True, exist_ok=False)
    rows: list[dict] = []
    results: dict[str, dict] = {}

    for name, checkpoint in CHECKPOINTS.items():
        print(f"Loading {name}: {checkpoint}", flush=True)
        predictor = CrackPredictor(InferenceConfig(checkpoint=checkpoint, device=args.device))
        device = predictor.device

        # Warm up the backend, then measure the exact same decoded images.
        for path in images[:min(args.warmup, len(images))]:
            with Image.open(path) as opened:
                image = np.asarray(opened.convert("RGB"))
            predictor.probability_map(image)
            synchronize(device)

        elapsed_seconds: list[float] = []
        tile_counts: list[int] = []
        dimensions: set[str] = set()
        for index, path in enumerate(images, 1):
            with Image.open(path) as opened:
                image = np.asarray(opened.convert("RGB"))
            dimensions.add(f"{image.shape[1]}x{image.shape[0]}")
            h, w = image.shape[:2]
            tile = predictor.cfg.tile_size
            step = max(1, round(tile * (1 - predictor.cfg.tile_overlap)))
            ys = list(range(0, max(1, h - tile + 1), step))
            xs = list(range(0, max(1, w - tile + 1), step))
            if ys[-1] != max(0, h - tile):
                ys.append(max(0, h - tile))
            if xs[-1] != max(0, w - tile):
                xs.append(max(0, w - tile))

            synchronize(device)
            start = time.perf_counter()
            predictor.probability_map(image)
            synchronize(device)
            elapsed = time.perf_counter() - start
            elapsed_seconds.append(elapsed)
            tile_counts.append(len(xs) * len(ys))
            rows.append({"model": name, "image": str(path.relative_to(ROOT)),
                         "width_px": w, "height_px": h, "tiles": tile_counts[-1],
                         "inference_and_stitch_seconds": elapsed})
            print(f"  {index:>3}/{len(images)} {path.name}: {elapsed:.3f}s", flush=True)

        total = sum(elapsed_seconds)
        results[name] = {
            "checkpoint": str(checkpoint.relative_to(ROOT)),
            "device": str(device),
            "images": len(images),
            "image_dimensions": sorted(dimensions),
            "tiles_per_image": sorted(set(tile_counts)),
            "warmup_images": min(args.warmup, len(images)),
            "latency_seconds_median": statistics.median(elapsed_seconds),
            "latency_seconds_p95": percentile(elapsed_seconds, 95),
            "throughput_images_per_second": len(images) / total,
            "throughput_images_per_minute": 60 * len(images) / total,
            "mean_tiles_per_second": sum(tile_counts) / total,
            "mean_milliseconds_per_tile": 1000 * total / sum(tile_counts),
            "total_measured_seconds": total,
        }
        print(f"{name}: median {results[name]['latency_seconds_median']:.3f}s/image, "
              f"p95 {results[name]['latency_seconds_p95']:.3f}s, "
              f"{results[name]['throughput_images_per_second']:.3f} images/s", flush=True)
        del predictor
        gc.collect()
        try:
            import torch
            if torch.backends.mps.is_available():
                torch.mps.empty_cache()
        except (ImportError, AttributeError):
            pass

    with (args.output / "per_image.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    report = {
        "measurement": "Full-resolution probability map including tile preprocessing and overlap stitching; excludes image decoding, model loading, thresholding, mask cleanup, and disk output.",
        "data_root": str(args.data),
        "image_selection": "deterministic evenly spaced sample from sorted UAV original images",
        "input_images": [str(path.relative_to(ROOT)) for path in images],
        "models": results,
    }
    (args.output / "summary.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nSaved benchmark results: {args.output}", flush=True)


if __name__ == "__main__":
    main()
