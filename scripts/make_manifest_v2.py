"""Build manifest_v2.csv: fix UAV_Crack_Detection leakage found by phash_check.py.

UAV_Crack_Detection ships each photo ~3 times (original + flipped/rotated copies) and the
copies are spread across splits. This script:
  1. Takes pHash candidate pairs (dist <= 6) within UAV_Crack_Detection.
  2. Confirms true duplicates at pixel level: mean abs diff < MAE_THR after the best of
     8 dihedral transforms. Duplicate groups = connected components of confirmed pairs.
  3. Keeps one image per duplicate group (lowest image number).
  4. Re-splits UAV 80/10/10 by pHash group (so near-similar frames also stay together),
     stratified by crack_area_class. Other sources keep their original split.

Image/mask files are not touched; manifest_v2.csv points at the same paths.
"""
import re
from pathlib import Path

import cv2
import numpy as np
import pandas as pd
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components

ROOT = Path("/Users/attachatchannakorn/Documents/crack-binary-segmentation")
DATASET = ROOT / "Dataset/final_merged_dataset_v1"
PAIRS = ROOT / "reports/phash/phash_pairs.csv"
OUT = DATASET / "manifest_v2.csv"
SRC = "UAV_Crack_Detection"
DUP_DIST = 6
MAE_THR = 3.0
SPLIT_FRAC = {"train": 0.8, "validation": 0.1, "test": 0.1}
SEED = 42


def components(nodes, edges):
    ix = {n: i for i, n in enumerate(nodes)}
    if edges:
        a, b = zip(*((ix[x], ix[y]) for x, y in edges))
    else:
        a, b = (), ()
    g = coo_matrix((np.ones(len(a)), (a, b)), shape=(len(nodes),) * 2)
    _, lab = connected_components(g, directed=False)
    return dict(zip(nodes, lab))


def best_dihedral_mae(path_a, path_b):
    A = cv2.imread(str(DATASET / path_a)).astype(np.float32)
    B = cv2.imread(str(DATASET / path_b)).astype(np.float32)
    best = np.inf
    for flip in (False, True):
        base = np.fliplr(B) if flip else B
        for k in range(4):
            best = min(best, float(np.abs(A - np.rot90(base, k)).mean()))
    return best


def image_number(sample_id):
    return int(re.search(r"(\d+)", sample_id).group(1))


def main():
    df = pd.read_csv(DATASET / "manifest.csv")
    uav = df[df.source_dataset == SRC].copy()
    pairs = pd.read_csv(PAIRS)
    pairs = pairs[(pairs.source_a == SRC) & (pairs.source_b == SRC) & (pairs.dist <= DUP_DIST)]

    nodes = uav.sample_id.tolist()
    phash_group = components(nodes, list(zip(pairs.sample_a, pairs.sample_b)))

    pairs = pairs.assign(mae=[best_dihedral_mae(a, b) for a, b in zip(pairs.image_a, pairs.image_b)])
    confirmed = pairs[pairs.mae < MAE_THR]
    dup_group = components(nodes, list(zip(confirmed.sample_a, confirmed.sample_b)))

    uav["phash_group"] = uav.sample_id.map(phash_group)
    uav["dup_group"] = uav.sample_id.map(dup_group)
    uav["num"] = uav.sample_id.map(image_number)
    keep = uav.sort_values("num").groupby("dup_group").head(1)

    # Re-split by pHash group, stratified by the class of the group's first kept image.
    groups = keep.sort_values("num").groupby("phash_group").crack_area_class.first()
    rng = np.random.default_rng(SEED)
    assign = {}
    for _, g in groups.groupby(groups):
        ids = rng.permutation(g.index.to_numpy())
        n_val = round(len(ids) * SPLIT_FRAC["validation"])
        n_test = round(len(ids) * SPLIT_FRAC["test"])
        for i, gid in enumerate(ids):
            assign[gid] = "validation" if i < n_val else "test" if i < n_val + n_test else "train"
    keep = keep.assign(split=keep.phash_group.map(assign))

    # Paths stay the same (files are not moved); split column is the source of truth.
    cols = list(df.columns)
    v2 = pd.concat([df[df.source_dataset != SRC], keep[cols]]).sort_values(["split", "sample_id"])
    v2.to_csv(OUT, index=False)

    dropped = uav[~uav.sample_id.isin(keep.sample_id)]
    dropped[["sample_id", "split", "dup_group"]].to_csv(ROOT / "reports/phash/uav_dropped.csv", index=False)

    print(f"pHash pairs checked: {len(pairs)}, confirmed pixel duplicates: {len(confirmed)} "
          f"(rejected {len(pairs) - len(confirmed)})")
    print(f"UAV: {len(uav)} -> {len(keep)} kept, {len(dropped)} dropped; "
          f"{uav.phash_group.nunique()} pHash groups, {uav.dup_group.nunique()} duplicate groups")
    print(f"Wrote {OUT} ({len(v2)} rows)")
    print(pd.crosstab(v2.source_dataset == SRC, v2.split, margins=True).rename(index={True: SRC, False: "others"}))
    leak = keep.groupby("phash_group").split.nunique().gt(1).sum()
    print("UAV pHash groups spanning >1 split:", leak)


if __name__ == "__main__":
    main()
