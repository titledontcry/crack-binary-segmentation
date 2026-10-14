"""Make slide-ready Optuna optimization-history plots from saved trial CSVs.

Example:
  .venv/bin/python -m drone_inference.plot_optuna \
    --unet runs/optuna_unet.csv --convnext runs/optuna_convnext_upernet.csv
"""
from __future__ import annotations

import argparse
import csv
import math
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


ROOT = Path(__file__).resolve().parents[1]
MODELS = (("U-Net / ResNet34", "unet", "#d67542"),
          ("ConvNeXt-Tiny / UPerNet-style", "convnext_upernet", "#397ea3"))


def read_trials(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    if not rows or not {"number", "value", "state"}.issubset(rows[0]):
        raise ValueError(f"Expected Optuna trials CSV with number, value and state: {path}")
    trials = []
    for row in rows:
        if not row["value"]:
            continue
        trials.append({"number": int(row["number"]), "value": float(row["value"]),
                       "state": row["state"].upper()})
    if not trials or not any(row["state"] == "COMPLETE" for row in trials):
        raise ValueError(f"No completed trials with objective values in {path}")
    return sorted(trials, key=lambda row: row["number"])


def draw(ax: plt.Axes, title: str, trials: list[dict], color: str,
         y_limits: tuple[float, float]) -> None:
    completed = [row for row in trials if row["state"] == "COMPLETE"]
    pruned = [row for row in trials if row["state"] == "PRUNED"]
    ax.scatter([row["number"] for row in completed], [row["value"] for row in completed],
               s=95, color=color, label="Completed trial", zorder=3)
    if pruned:
        ax.scatter([row["number"] for row in pruned], [row["value"] for row in pruned],
                   s=80, marker="x", linewidths=2, color="#82909a", label="Pruned (intermediate)", zorder=3)
    completed_by_number = {row["number"]: row["value"] for row in completed}
    best_so_far, x, y = float("-inf"), [], []
    for row in trials:
        if row["number"] in completed_by_number:
            best_so_far = max(best_so_far, completed_by_number[row["number"]])
        if best_so_far != float("-inf"):
            x.append(row["number"])
            y.append(best_so_far)
    ax.step(x, y, where="post", color="#263644", linewidth=2.5, label="Best completed score")
    best = max(completed, key=lambda row: row["value"])
    ax.annotate(f"Best: trial {best['number']}  |  {best['value']:.4f}",
                (best["number"], best["value"]), xytext=(12, 18), textcoords="offset points",
                fontsize=11, color="#263644", fontweight="bold")
    ax.set(title=title, xlabel="Optuna trial", ylabel="Validation tolerance-F1")
    ax.set_xticks([row["number"] for row in trials])
    ax.set_xlim(-0.5, max(row["number"] for row in trials) + 2)
    ax.set_ylim(*y_limits)
    ax.grid(alpha=0.15)
    ax.legend(loc="lower right", frameon=False)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--unet", type=Path, default=ROOT / "runs/optuna_unet.csv")
    parser.add_argument("--convnext", type=Path, default=ROOT / "runs/optuna_convnext_upernet.csv")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "runs/slide_assets/optuna")
    args = parser.parse_args()
    inputs = {"unet": args.unet, "convnext_upernet": args.convnext}
    missing = [str(path) for path in inputs.values() if not path.is_file()]
    if missing:
        parser.error("Missing Optuna trial files: " + ", ".join(missing))
    trial_sets = {key: read_trials(path) for key, path in inputs.items()}
    values = [row["value"] for rows in trial_sets.values() for row in rows]
    y_limits = (max(0, math.floor((min(values) - 0.015) * 100) / 100),
                min(1, math.ceil((max(values) + 0.025) * 100) / 100))
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for title, key, color in MODELS:
        fig, ax = plt.subplots(figsize=(12, 6.75), dpi=160)
        draw(ax, title, trial_sets[key], color, y_limits)
        fig.suptitle("Optuna optimization history", fontsize=21, fontweight="bold", color="#263644")
        fig.text(0.08, 0.025, "10 trials/model · up to 6 epochs/trial · validation tolerance-F1 (±2 px)",
                 fontsize=10, color="#667783")
        fig.tight_layout(rect=(0, 0.055, 1, 0.94))
        output = args.output_dir / f"optuna_{key}.png"
        fig.savefig(output, facecolor="white")
        plt.close(fig)
        print(f"Saved {output}")


if __name__ == "__main__":
    main()
