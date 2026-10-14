from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

print('[pipeline] importing cv2', flush=True)
import cv2
import numpy as np

print('[pipeline] importing torch', flush=True)
import torch
import torch.nn as nn
import torch.nn.functional as F
print('[pipeline] torch imported', flush=True)
from PIL import Image

print('[pipeline] importing skimage', flush=True)
from skimage.morphology import remove_small_objects
from .measurements import measure_mask
print('[pipeline] imports complete', flush=True)

IMAGENET_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
IMAGENET_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)


@dataclass(frozen=True)
class InferenceConfig:
    checkpoint: Path
    device: str = 'auto'
    threshold: float = 0.1
    tile_size: int = 448
    tile_overlap: float = 0.25
    min_component_px: int = 20


class ConvNeXtUPerNet(nn.Module):
    """Inference form of the ConvNeXt-Tiny + UPerNet checkpoint."""
    def __init__(self):
        super().__init__()
        from torchvision.models import convnext_tiny

        def norm(channels: int) -> nn.Module:
            # The checkpoint contains affine-only decoder normalization (no BN running stats).
            # GroupNorm is stable for the 1x1 pooled PSP branch at batch size one.
            return nn.GroupNorm(32, channels)

        self.encoder = convnext_tiny(weights=None).features
        self.psp = nn.ModuleList([
            nn.Sequential(nn.AdaptiveAvgPool2d(scale), nn.Sequential(
                nn.Conv2d(768, 256, 1, bias=False), norm(256), nn.ReLU(inplace=True)))
            for scale in (1, 2, 3, 6)
        ])
        self.psp_bottleneck = nn.Sequential(
            nn.Conv2d(1792, 256, 3, padding=1, bias=False), norm(256), nn.ReLU(inplace=True))
        self.laterals = nn.ModuleList([
            nn.Sequential(nn.Conv2d(ch, 256, 1, bias=False), norm(256))
            for ch in (96, 192, 384)
        ])
        self.fpn = nn.ModuleList([
            nn.Sequential(nn.Conv2d(256, 256, 3, padding=1, bias=False), norm(256))
            for _ in range(3)
        ])
        self.fuse = nn.Sequential(
            nn.Sequential(nn.Conv2d(1024, 256, 3, padding=1, bias=False), norm(256), nn.ReLU(inplace=True)),
            nn.Dropout2d(.1), nn.Conv2d(256, 1, 1))
        self.aux = nn.Sequential(
            nn.Sequential(nn.Conv2d(384, 128, 3, padding=1, bias=False), norm(128), nn.ReLU(inplace=True)),
            nn.Dropout2d(.1), nn.Conv2d(128, 1, 1))

    def forward(self, x):
        input_size = x.shape[-2:]
        features = []
        for i, layer in enumerate(self.encoder):
            x = layer(x)
            if i in (1, 3, 5, 7):
                features.append(x)
        c1, c2, c3, c4 = features
        pooled = [c4]
        for branch in self.psp:
            if c4.device.type == 'mps':
                # MPS adaptive pooling rejects non-divisible sizes (e.g. 14 -> 3).
                # Pooling is tiny here; do that operation on CPU, then keep the
                # learned projection and the rest of inference on the selected device.
                pooled_input = F.adaptive_avg_pool2d(c4.cpu(), branch[0].output_size).to(c4.device)
                branch_output = branch[1](pooled_input)
            else:
                branch_output = branch(c4)
            pooled.append(F.interpolate(branch_output, size=c4.shape[-2:], mode='bilinear', align_corners=False))
        top = self.psp_bottleneck(torch.cat(pooled, dim=1))
        laterals = [layer(feature) for layer, feature in zip(self.laterals, (c1, c2, c3))] + [top]
        for i in range(2, -1, -1):
            laterals[i] = laterals[i] + F.interpolate(laterals[i + 1], size=laterals[i].shape[-2:], mode='bilinear', align_corners=False)
        pyramid = [layer(feature) for layer, feature in zip(self.fpn, laterals[:3])] + [laterals[3]]
        target = pyramid[0].shape[-2:]
        pyramid = [pyramid[0]] + [F.interpolate(feature, size=target, mode='bilinear', align_corners=False) for feature in pyramid[1:]]
        logits = self.fuse(torch.cat(pyramid, dim=1))
        return F.interpolate(logits, size=input_size, mode='bilinear', align_corners=False)


def select_device(requested: str) -> torch.device:
    if requested != 'auto':
        return torch.device(requested)
    if torch.cuda.is_available():
        return torch.device('cuda')
    if torch.backends.mps.is_available():
        return torch.device('mps')
    return torch.device('cpu')


class CrackPredictor:
    def __init__(self, cfg: InferenceConfig):
        start = time.perf_counter()

        def log(message: str) -> None:
            print(f'[model +{time.perf_counter() - start:.1f}s] {message}', flush=True)

        log(f'Checking checkpoint: {cfg.checkpoint}')
        if not cfg.checkpoint.is_file():
            raise FileNotFoundError(f'Checkpoint not found: {cfg.checkpoint}')
        self.cfg = cfg
        log('Selecting device')
        self.device = select_device(cfg.device)
        log(f'Selected device: {self.device}')
        log('Reading checkpoint on CPU')
        checkpoint = torch.load(cfg.checkpoint, map_location='cpu', weights_only=False)
        if not isinstance(checkpoint, dict) or 'model' not in checkpoint:
            raise ValueError("Checkpoint must contain a 'model' state_dict")
        architecture = checkpoint.get('config', {}).get('model')
        log(f'Checkpoint architecture: {architecture}')
        if architecture == 'convnext_upernet':
            log('Creating ConvNeXt-Tiny UPerNet on CPU')
            self.model = ConvNeXtUPerNet()
        else:
            log('Creating U-Net ResNet34 on CPU')
            import segmentation_models_pytorch as smp
            self.model = smp.Unet('resnet34', encoder_weights=None, in_channels=3, classes=1)
        log('Loading state_dict')
        self.model.load_state_dict(checkpoint['model'])
        del checkpoint
        log(f'Moving model to {self.device}')
        self.model = self.model.to(self.device)
        self.model.eval()
        log('Ready')

    def _predict_tile(self, image: np.ndarray) -> np.ndarray:
        x = image.astype(np.float32) / 255.0
        x = (x - IMAGENET_MEAN) / IMAGENET_STD
        x = torch.from_numpy(x.transpose(2, 0, 1)).unsqueeze(0).to(self.device)
        with torch.inference_mode():
            return torch.sigmoid(self.model(x)).squeeze().float().cpu().numpy()

    def probability_map(self, image: np.ndarray) -> np.ndarray:
        h, w = image.shape[:2]
        tile = self.cfg.tile_size
        step = max(1, round(tile * (1 - self.cfg.tile_overlap)))
        ys = list(range(0, max(1, h - tile + 1), step))
        xs = list(range(0, max(1, w - tile + 1), step))
        if ys[-1] != max(0, h - tile):
            ys.append(max(0, h - tile))
        if xs[-1] != max(0, w - tile):
            xs.append(max(0, w - tile))
        padded = cv2.copyMakeBorder(image, 0, max(0, tile - h), 0, max(0, tile - w), cv2.BORDER_REFLECT_101)
        prob = np.zeros(padded.shape[:2], np.float32)
        weight = np.zeros_like(prob)
        window = np.outer(np.hanning(tile), np.hanning(tile)).astype(np.float32)
        window = np.maximum(window, 1e-3)
        for y in ys:
            for x in xs:
                p = self._predict_tile(padded[y:y + tile, x:x + tile])
                prob[y:y + tile, x:x + tile] += p * window
                weight[y:y + tile, x:x + tile] += window
        return (prob / np.maximum(weight, 1e-6))[:h, :w]

    def predict(self, image_path: Path, gsd_mm_per_px: float | None = None) -> tuple[np.ndarray, np.ndarray, dict]:
        image = np.asarray(Image.open(image_path).convert('RGB'))
        probability = self.probability_map(image)
        mask = probability >= self.cfg.threshold
        mask = remove_small_objects(mask, min_size=self.cfg.min_component_px)
        measurement = measure_mask(mask, gsd_mm_per_px)
        return image, mask.astype(np.uint8), measurement


def write_visuals(image: np.ndarray, mask: np.ndarray, measurement: dict, result_dir: Path) -> tuple[str, str]:
    result_dir.mkdir(parents=True, exist_ok=True)
    overlay = image.copy()
    overlay[mask.astype(bool)] = (255, 56, 56)
    blended = cv2.addWeighted(image, 0.62, overlay, 0.38, 0)
    for i, box in enumerate(measurement['boxes']):
        x, y, w, h = box['x'], box['y'], box['width'], box['height']
        cv2.rectangle(blended, (x, y), (x + w, y + h), (57, 220, 140), 2)
        cv2.putText(blended, f'Crack {i + 1}', (x, max(17, y - 6)), cv2.FONT_HERSHEY_SIMPLEX,
                    .55, (57, 220, 140), 2, cv2.LINE_AA)
    overlay_path, mask_path = result_dir / 'overlay.jpg', result_dir / 'mask.png'
    Image.fromarray(blended).save(overlay_path, quality=92)
    Image.fromarray(mask * 255).save(mask_path)
    return str(overlay_path), str(mask_path)
