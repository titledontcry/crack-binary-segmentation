"""Near-duplicate check across the merged dataset using perceptual hashing (pHash).

pHash: grayscale -> 32x32 -> DCT -> top-left 8x8 -> bit = coeff > median -> 64-bit hash.
Each image is also hashed under the 8 dihedral transforms (rot90 x flip), so rotated or
flipped copies are caught. Pair distance = min Hamming distance over those transforms.

Outputs (in --out):
  phash.csv            per-image hash (identity transform)
  phash_pairs.csv      all pairs with distance <= --max-dist
  phash_report.md      summary
"""
import argparse
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import cv2
import numpy as np
import pandas as pd

DATASET = Path("/Users/attachatchannakorn/Documents/crack-binary-segmentation/Dataset/final_merged_dataset_v1")

# Crops cut from the same parent photo share a prefix; used to tell expected
# within-photo similarity apart from real duplicates.
PARENT_PATTERNS = {
    "CRACK500": r"^(CRACK500_\d+_\d+)_",
    "Rissbilder": r"^(Rissbilder_for_Florian_[^_]+)_",
    "Volker": r"^(Volker_[^_]+)_",
    "noncrack": r"^(noncrack_.*?_\d+)_\d+\.jpg",
}


def parent_id(sample_id: str, source: str) -> str:
    pat = PARENT_PATTERNS.get(source)
    if pat:
        m = re.match(pat, sample_id)
        if m:
            return m.group(1)
    return sample_id


def dct_hash(gray32: np.ndarray) -> int:
    dct = cv2.dct(gray32.astype(np.float32))[:8, :8]
    bits = (dct > np.median(dct)).flatten()
    return int(np.packbits(bits).view(">u8")[0])


def phash_all_transforms(path: Path) -> list[int]:
    img = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    g = cv2.resize(img, (32, 32), interpolation=cv2.INTER_AREA)
    hashes = []
    for flip in (False, True):
        base = np.fliplr(g) if flip else g
        for k in range(4):
            hashes.append(dct_hash(np.ascontiguousarray(np.rot90(base, k))))
    return hashes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-dist", type=int, default=10)
    ap.add_argument("--dup-dist", type=int, default=6, help="distance treated as near-duplicate")
    ap.add_argument("--out", default="reports/phash")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    df = pd.read_csv(DATASET / "manifest.csv")
    df["parent"] = [parent_id(s, src) for s, src in zip(df.sample_id, df.source_dataset)]

    with ThreadPoolExecutor(8) as ex:
        H = np.array(list(ex.map(phash_all_transforms, (DATASET / p for p in df.image_path))), dtype=np.uint64)
    df["phash"] = [f"{h:016x}" for h in H[:, 0]]
    df[["sample_id", "split", "source_dataset", "parent", "phash"]].to_csv(out / "phash.csv", index=False)

    # Compare identity hash of i against all 8 transform hashes of j (j > i).
    n = len(df)
    rows = []
    for i in range(n - 1):
        d = np.bitwise_count(H[i + 1:, :] ^ H[i, 0]).min(axis=1)
        js = np.nonzero(d <= args.max_dist)[0]
        for j in js:
            rows.append((i, i + 1 + j, int(d[j])))

    pairs = pd.DataFrame(rows, columns=["i", "j", "dist"])
    a, b = df.iloc[pairs.i].reset_index(drop=True), df.iloc[pairs.j].reset_index(drop=True)
    pairs = pd.DataFrame({
        "dist": pairs.dist,
        "sample_a": a.sample_id, "split_a": a.split, "source_a": a.source_dataset,
        "sample_b": b.sample_id, "split_b": b.split, "source_b": b.source_dataset,
        "cross_split": a.split.values != b.split.values,
        "cross_source": a.source_dataset.values != b.source_dataset.values,
        "same_parent": (a.parent.values == b.parent.values),
        "image_a": a.image_path, "image_b": b.image_path,
    }).sort_values("dist")
    pairs.to_csv(out / "phash_pairs.csv", index=False)

    dup = pairs[pairs.dist <= args.dup_dist]
    lines = [
        "# pHash near-duplicate report", "",
        f"- Images: {n:,}",
        f"- Pairs with distance <= {args.max_dist}: {len(pairs):,}",
        f"- Near-duplicate pairs (distance <= {args.dup_dist}): {len(dup):,}",
        f"  - cross-split: {int(dup.cross_split.sum()):,}",
        f"  - cross-source: {int(dup.cross_source.sum()):,}",
        f"  - same parent photo: {int(dup.same_parent.sum()):,}",
        "", "## Distance histogram (all pairs <= max-dist)", "",
        "| dist | pairs | cross-split | cross-source |", "|---:|---:|---:|---:|",
    ]
    for d, g in pairs.groupby("dist"):
        lines.append(f"| {d} | {len(g):,} | {int(g.cross_split.sum()):,} | {int(g.cross_source.sum()):,} |")
    lines += ["", f"## Near-duplicates by source pair (distance <= {args.dup_dist})", "",
              "| source A | source B | pairs | cross-split |", "|---|---|---:|---:|"]
    if len(dup):
        sp = dup.assign(s1=np.minimum(dup.source_a, dup.source_b), s2=np.maximum(dup.source_a, dup.source_b))
        for (s1, s2), g in sp.groupby(["s1", "s2"]):
            lines.append(f"| {s1} | {s2} | {len(g):,} | {int(g.cross_split.sum()):,} |")
    (out / "phash_report.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
