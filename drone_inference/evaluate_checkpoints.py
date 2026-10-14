"""Evaluate the three saved crack models on the fixed manifest splits.

Test-only mode uses each checkpoint's pre-existing inference threshold, without
threshold tuning on the test set. Optional validation mode can select a frozen
threshold/TTA first. best_boat uses an older manifest and is reported as
exploratory because its training/test provenance is unknown.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import time
from pathlib import Path

import cv2
import numpy as np
import torch

from .compare_uav import MODELS, ROOT
from .pipeline import CrackPredictor, InferenceConfig


THRESHOLDS = [round(i / 100, 2) for i in range(5, 100, 5)]
SPLITS = {"validation", "test"}
COUNT_FIELDS = ("tp", "fp", "fn", "pred_near_gt", "gt_near_pred")
MEAN = np.array((0.485, 0.456, 0.406), np.float32)
STD = np.array((0.229, 0.224, 0.225), np.float32)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def rows_for(manifest: Path, split: str, limit: int | None) -> list[dict]:
    with manifest.open(newline="", encoding="utf-8") as handle:
        rows = [row for row in csv.DictReader(handle)
                if row["previous_split"].strip().lower() == split]
    if not rows:
        raise ValueError(f"No {split} rows in {manifest}")
    return rows[:limit] if limit else rows


def image_and_mask(root: Path, row: dict) -> tuple[np.ndarray, np.ndarray]:
    image = cv2.imread(str(root / row["image_path"]), cv2.IMREAD_COLOR)
    mask = cv2.imread(str(root / row["mask_path"]), cv2.IMREAD_GRAYSCALE)
    if image is None or mask is None:
        raise FileNotFoundError(f"Missing image or mask for {row['sample_id']}")
    if image.shape[:2] != mask.shape:
        raise ValueError(f"Image/mask size mismatch for {row['sample_id']}")
    rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB).astype(np.float32) / 255
    return ((rgb - MEAN) / STD).transpose(2, 0, 1), mask > 127


def probability(model: torch.nn.Module, x: torch.Tensor, with_tta: bool) -> tuple[np.ndarray, np.ndarray | None]:
    with torch.inference_mode():
        direct = torch.sigmoid(model(x).float())
        if not with_tta:
            return direct[:, 0].cpu().numpy(), None
        total = direct
        for axes in ((-1,), (-2,), (-2, -1)):
            total = total + torch.sigmoid(model(torch.flip(x, axes)).float()).flip(axes)
        return direct[:, 0].cpu().numpy(), (total / 4)[:, 0].cpu().numpy()


def counts(prob: np.ndarray, target: np.ndarray, threshold: float, tolerance: int) -> np.ndarray:
    pred = prob > threshold  # Matches the notebook's strict threshold comparison.
    tp = np.count_nonzero(pred & target)
    fp = np.count_nonzero(pred & ~target)
    fn = np.count_nonzero(~pred & target)
    if tolerance:
        kernel = np.ones((2 * tolerance + 1, 2 * tolerance + 1), np.uint8)
        near_gt = cv2.dilate(target.astype(np.uint8), kernel) > 0
        near_pred = cv2.dilate(pred.astype(np.uint8), kernel) > 0
    else:
        near_gt, near_pred = target, pred
    return np.array((tp, fp, fn, np.count_nonzero(pred & near_gt),
                     np.count_nonzero(target & near_pred)), dtype=np.int64)


def metrics(count: np.ndarray) -> dict[str, float]:
    tp, fp, fn, near_gt, near_pred = [float(value) for value in count]
    eps = 1e-9
    precision = tp / (tp + fp + eps)
    recall = tp / (tp + fn + eps)
    tol_precision = near_gt / (tp + fp + eps)
    tol_recall = near_pred / (tp + fn + eps)
    return {"iou": tp / (tp + fp + fn + eps),
            "dice": 2 * tp / (2 * tp + fp + fn + eps),
            "precision": precision, "recall": recall,
            "tol_precision": tol_precision, "tol_recall": tol_recall,
            "tol_f1": 2 * tol_precision * tol_recall / (tol_precision + tol_recall + eps)}


def write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def grouped(rows: list[dict], chosen: list[np.ndarray], column: str) -> list[dict]:
    groups: dict[str, list[np.ndarray]] = {}
    for row, count in zip(rows, chosen):
        groups.setdefault(row[column], []).append(count)
    return [{column: label, "n": len(values), **metrics(np.sum(values, axis=0))}
            for label, values in sorted(groups.items())]


def evaluate(predictor: CrackPredictor, rows: list[dict], data_root: Path,
             thresholds: list[float], with_tta: bool, tolerance: int, batch_size: int) -> dict:
    result = {"plain": {threshold: [] for threshold in thresholds}}
    if with_tta:
        result["tta"] = {threshold: [] for threshold in thresholds}
    started = time.perf_counter()
    for start in range(0, len(rows), batch_size):
        chunk = rows[start:start + batch_size]
        samples = [image_and_mask(data_root, row) for row in chunk]
        x = torch.from_numpy(np.stack([sample[0] for sample in samples])).to(predictor.device)
        direct, augmented = probability(predictor.model, x, with_tta)
        for index, (_, target) in enumerate(samples):
            for threshold in thresholds:
                result["plain"][threshold].append(counts(direct[index], target, threshold, tolerance))
                if with_tta:
                    result["tta"][threshold].append(counts(augmented[index], target, threshold, tolerance))
        if (start // batch_size + 1) % 10 == 0 or start + batch_size >= len(rows):
            print(f"  {min(start + batch_size, len(rows))}/{len(rows)} images, {time.perf_counter()-started:.0f}s", flush=True)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("smoke", "validation", "test"))
    parser.add_argument("--model", choices=("all", *MODELS), default="all")
    parser.add_argument("--dataset", type=Path, default=ROOT / "final_road_dataset_cleaned")
    parser.add_argument("--output", type=Path, default=ROOT / "runs/evaluation_three_models")
    parser.add_argument("--device", choices=("auto", "mps", "cpu", "cuda"), default="auto")
    parser.add_argument("--batch-size", type=int, default=2)
    parser.add_argument("--no-tta", action="store_true", help="Use plain predictions only during validation")
    parser.add_argument("--fixed-test", action="store_true", help="Test only, using pre-existing model thresholds without validation tuning")
    parser.add_argument("--limit", type=int, help="Smoke only: process this many validation images")
    args = parser.parse_args()
    if args.batch_size < 1 or (args.limit and args.stage != "smoke") or (args.fixed_test and args.stage != "test"):
        parser.error("Positive batch size required; --limit is for smoke only")
    manifest = args.dataset / "manifest.csv"
    if not manifest.is_file():
        parser.error(f"Missing {manifest}")
    split = "test" if args.stage == "test" else "validation"
    rows = rows_for(manifest, split, args.limit or 2 if args.stage == "smoke" else None)
    manifest_hash = sha256(manifest)
    selected_models = MODELS if args.model == "all" else {args.model: MODELS[args.model]}
    if args.stage == "test" and not args.fixed_test:
        missing = [name for name in selected_models if not (args.output / name / "val_selection.json").is_file()]
        if missing:
            parser.error(f"Run full validation first for: {', '.join(missing)}")
    summaries = []
    for name, (checkpoint, _) in selected_models.items():
        if not checkpoint.is_file():
            parser.error(f"Missing checkpoint: {checkpoint}")
        checkpoint_hash = sha256(checkpoint)
        folder = args.output / name
        if args.stage == "test" and (folder / "test_results.json").exists():
            parser.error(f"Test results already exist for {name}; preserve the one-time test: {folder}")
        if args.stage == "test" and not args.fixed_test:
            selection = json.loads((folder / "val_selection.json").read_text())
            if selection["checkpoint_sha256"] != checkpoint_hash or selection["manifest_sha256"] != manifest_hash:
                parser.error(f"Checkpoint or dataset changed after validation for {name}")
            thresholds = [selection["threshold"]]
            with_tta = bool(selection["tta"])
        elif args.stage == "test":
            thresholds = [MODELS[name][1]]
            with_tta = False
        else:
            thresholds = [0.5] if args.stage == "smoke" else THRESHOLDS
            with_tta = args.stage == "validation" and not args.no_tta
        print(f"{name}: {split}, {len(rows)} images, device={args.device}, TTA={with_tta}", flush=True)
        predictor = CrackPredictor(InferenceConfig(checkpoint=checkpoint, device=args.device))
        checkpoint_data = torch.load(checkpoint, map_location="cpu", weights_only=False)
        source_hash = checkpoint_data.get("manifest_sha256")
        exploratory = bool(source_hash and source_hash != manifest_hash) or name == "best_boat"
        tolerance = int(checkpoint_data.get("cfg", {}).get("tol_px", 2))
        result = evaluate(predictor, rows, args.dataset, thresholds, with_tta, tolerance, args.batch_size)
        del predictor, checkpoint_data
        if torch.backends.mps.is_available():
            torch.mps.empty_cache()
        if args.stage == "smoke":
            for mode, table in result.items():
                print(name, mode, metrics(np.sum(table[0.5], axis=0)), flush=True)
            continue
        folder.mkdir(parents=True, exist_ok=True)
        if args.stage == "validation":
            sweep = [{"mode": mode, "threshold": threshold,
                      **metrics(np.sum(values, axis=0))}
                     for mode, table in result.items() for threshold, values in table.items()]
            write_csv(folder / "threshold_sweep.csv", sweep)
            plain = max((row for row in sweep if row["mode"] == "plain"), key=lambda row: row["tol_f1"])
            augmented = max((row for row in sweep if row["mode"] == "tta"),
                            key=lambda row: row["tol_f1"]) if with_tta else None
            chosen = augmented if augmented and augmented["tol_f1"] > plain["tol_f1"] + 0.002 else plain
            selection = {"model": name, "checkpoint": str(checkpoint), "checkpoint_sha256": checkpoint_hash,
                         "manifest_sha256": manifest_hash, "checkpoint_manifest_sha256": source_hash,
                         "exploratory_provenance": exploratory, "split": "validation", "n": len(rows),
                         "threshold": chosen["threshold"], "tta": chosen["mode"] == "tta",
                         "tolerance_px": tolerance, "selection_metric": "tol_f1", "metrics": metrics(
                             np.sum(result[chosen["mode"]][chosen["threshold"]], axis=0))}
            (folder / "val_selection.json").write_text(json.dumps(selection, indent=2))
            print(f"  selected threshold={selection['threshold']:.2f}, TTA={selection['tta']}, tol-F1={selection['metrics']['tol_f1']:.4f}", flush=True)
        else:
            chosen = {"mode": "tta" if with_tta else "plain", "threshold": thresholds[0]}
        values = result[chosen["mode"]][chosen["threshold"]]
        summary = {"model": name, "split": split, "n": len(rows), "threshold": chosen["threshold"],
                   "tta": chosen["mode"] == "tta", "exploratory_provenance": exploratory,
                   "threshold_source": "pre-existing model setting" if args.fixed_test else "validation selection",
                   **metrics(np.sum(values, axis=0))}
        summaries.append(summary)
        write_csv(folder / f"{split}_by_source.csv", grouped(rows, values, "source_dataset"))
        write_csv(folder / f"{split}_by_class.csv", grouped(rows, values, "crack_area_class"))
        write_csv(folder / f"{split}_per_image.csv", [
            {"sample_id": row["sample_id"], "source_dataset": row["source_dataset"],
             "crack_area_class": row["crack_area_class"], **dict(zip(COUNT_FIELDS, value)),
             **metrics(value)} for row, value in zip(rows, values)])
        if args.stage == "test":
            (folder / "test_results.json").write_text(json.dumps(summary, indent=2))
        print(f"  {split}: IoU={summary['iou']:.4f}, Dice={summary['dice']:.4f}, tol-F1={summary['tol_f1']:.4f}", flush=True)
    if summaries:
        write_csv(args.output / f"{split}_comparison.csv", summaries)
        print(f"Saved {split} comparison: {args.output / f'{split}_comparison.csv'}", flush=True)


if __name__ == "__main__":
    main()
