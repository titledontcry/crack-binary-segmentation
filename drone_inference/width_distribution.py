"""Report the real distribution of AI max crack width in an inspection DB.

Read-only; it does not modify the database or images.

    python -m drone_inference.width_distribution
    python -m drone_inference.width_distribution --db /path/to/inspection.sqlite3
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    index = (len(values) - 1) * fraction
    lower, upper = int(index), min(int(index) + 1, len(values) - 1)
    return values[lower] + (values[upper] - values[lower]) * (index - lower)


def distribution(db_path: Path) -> dict[str, float | int | None]:
    con = sqlite3.connect(db_path)
    try:
        rows = con.execute(
            "SELECT measurement FROM images WHERE status = 'done' AND measurement IS NOT NULL"
        )
        widths = []
        for (raw,) in rows:
            try:
                value = json.loads(raw).get("width_px_max")
            except (TypeError, json.JSONDecodeError):
                continue
            if isinstance(value, (int, float)):
                widths.append(float(value))
    finally:
        con.close()
    widths.sort()
    return {
        "count": len(widths),
        "min": min(widths) if widths else None,
        "median": percentile(widths, 0.50),
        "p75": percentile(widths, 0.75),
        "p90": percentile(widths, 0.90),
        "max": max(widths) if widths else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=Path(__file__).with_name("data") / "inspection.sqlite3")
    args = parser.parse_args()
    if not args.db.is_file():
        parser.error("database not found: %s" % args.db)
    print(json.dumps(distribution(args.db), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
