"""Mask measurements shared by AI inference and human corrections."""
import math
import cv2
import numpy as np
from skimage.morphology import skeletonize


def measure_mask(mask: np.ndarray, gsd_mm_per_px: float | None) -> dict:
    mask = np.asarray(mask, dtype=bool)
    skeleton = skeletonize(mask)
    orth = (skeleton[:, 1:] & skeleton[:, :-1]).sum() + (skeleton[1:, :] & skeleton[:-1, :]).sum()
    diag = (skeleton[1:, 1:] & skeleton[:-1, :-1]).sum() + (skeleton[1:, :-1] & skeleton[:-1, 1:]).sum()
    length_px = float(orth + math.sqrt(2) * diag)
    distance = cv2.distanceTransform(mask.astype(np.uint8), cv2.DIST_L2, 5)
    widths = 2 * distance[skeleton]
    _, _, stats, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8), connectivity=8)
    boxes = [{'x': int(x), 'y': int(y), 'width': int(w), 'height': int(h), 'area_px': int(area)}
             for x, y, w, h, area in stats[1:]]
    result = {'crack_pixels': int(mask.sum()), 'component_count': len(boxes),
              'boxes': sorted(boxes, key=lambda box: box['area_px'], reverse=True),
              'length_px': length_px, 'width_px_mean': float(widths.mean()) if len(widths) else 0.0,
              'width_px_max': float(widths.max()) if len(widths) else 0.0}
    if gsd_mm_per_px and gsd_mm_per_px > 0:
        result.update(gsd_mm_per_px=float(gsd_mm_per_px), length_mm=length_px*gsd_mm_per_px,
                      width_mm_mean=result['width_px_mean']*gsd_mm_per_px,
                      width_mm_max=result['width_px_max']*gsd_mm_per_px)
    return result
