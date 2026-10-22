"""Procedural sky textures (equirectangular) so scenes never need an external image."""
import numpy as np
from PIL import Image


def starfield(path, width=2048, height=1024, density=0.0009, seed=7):
    rng = np.random.default_rng(seed)
    img = np.zeros((height, width, 3), dtype=np.float32)
    img[:] = (0.005, 0.006, 0.02)
    n = int(width * height * density)
    ys = rng.integers(0, height, n)
    xs = rng.integers(0, width, n)
    mag = rng.power(0.25, n)  # most stars are dim
    tint = rng.uniform(0.75, 1.0, (n, 3)) * np.array([0.9, 0.95, 1.0])
    for x, y, m, t in zip(xs, ys, mag, tint):
        b = 0.25 + 0.75 * m
        img[y, x] = np.maximum(img[y, x], t * b)
        if m > 0.93:  # a few bright ones get a soft halo
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                img[(y + dy) % height, (x + dx) % width] += t * 0.25
    Image.fromarray((np.clip(img, 0, 1) * 255).astype(np.uint8)).save(path)
