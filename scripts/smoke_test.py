"""Smoke test that drives the image pipeline against stitched.tif end-to-end.

Run: ``uv run python scripts/smoke_test.py``

Exercises list/describe, pyramid build (first pass is the expensive one),
overview rendering, region extraction, clamp behavior, and max-dimension downscale.
"""

from __future__ import annotations

import logging
import sys
import time
from pathlib import Path

from large_image_mcp import cache, images

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("smoke")

IMAGES_DIR = Path(__file__).resolve().parent.parent
CACHE_DIR = IMAGES_DIR / ".cache" / "large-image-mcp"
IMAGE_NAME = "stitched.tif"


def main() -> int:
    if not (IMAGES_DIR / IMAGE_NAME).exists():
        log.error("expected %s in %s", IMAGE_NAME, IMAGES_DIR)
        return 1

    log.info("listing images in %s", IMAGES_DIR)
    for name in cache.list_image_names(IMAGES_DIR):
        log.info("  found: %s", name)

    ref = cache.resolve_image(IMAGE_NAME, IMAGES_DIR, CACHE_DIR)
    info = images.describe(ref)
    log.info(
        "describe: %dx%d, %d channels, %d bits, pyramid_cached=%s",
        info.width, info.height, info.channels, info.bits_per_sample, info.pyramid_cached,
    )

    log.info("ensuring pyramid (first-time build may take ~10-60s)")
    t0 = time.monotonic()
    images.ensure_pyramid(ref)
    log.info("pyramid ready in %.2fs", time.monotonic() - t0)

    log.info("rendering overview (max_dim=2048, png)")
    t0 = time.monotonic()
    ov = images.render_overview(ref, max_dimension=2048, fmt="png", quality=90)
    log.info(
        "overview: %dx%d, %d bytes, scale=%.6f, %.2fs",
        ov.width, ov.height, len(ov.data), ov.scale, time.monotonic() - t0,
    )
    (CACHE_DIR / "smoke_overview.png").write_bytes(ov.data)

    log.info("rendering overview again (should hit on-disk cache)")
    t0 = time.monotonic()
    ov2 = images.render_overview(ref, max_dimension=2048, fmt="png", quality=90)
    log.info("overview (cached): %.3fs, bytes_match=%s", time.monotonic() - t0, ov2.data == ov.data)

    log.info("rendering native-res 1000x1000 region at (1000, 1000)")
    t0 = time.monotonic()
    region = images.render_region(
        ref, x=1000, y=1000, width=1000, height=1000,
        max_dimension=None, fmt="png", quality=95,
    )
    log.info(
        "region: %dx%d, %d bytes, served_region=%s, clipped=%s, %.2fs",
        region.width, region.height, len(region.data),
        region.served_region, region.clipped, time.monotonic() - t0,
    )
    (CACHE_DIR / "smoke_region_1000x1000.png").write_bytes(region.data)

    log.info("rendering 10000x10000 region downscaled to 2048")
    t0 = time.monotonic()
    big = images.render_region(
        ref, x=5000, y=4000, width=10000, height=10000,
        max_dimension=2048, fmt="jpeg", quality=90,
    )
    log.info(
        "big region: %dx%d, %d bytes, scale=%.4f, %.2fs",
        big.width, big.height, len(big.data), big.scale, time.monotonic() - t0,
    )
    (CACHE_DIR / "smoke_region_big.jpg").write_bytes(big.data)

    log.info("rendering out-of-bounds region (should clamp)")
    clip = images.render_region(
        ref, x=info.width - 100, y=info.height - 100,
        width=500, height=500,
        max_dimension=None, fmt="png", quality=95,
    )
    log.info(
        "clamped region: %dx%d, clipped=%s, served_region=%s",
        clip.width, clip.height, clip.clipped, clip.served_region,
    )

    log.info("smoke test OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
