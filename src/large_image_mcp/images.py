"""Image open/pyramid/overview/region operations backed by libvips."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import pyvips

from .cache import ImageRef

log = logging.getLogger(__name__)

Format = Literal["png", "jpeg"]

MIME_TYPES: dict[Format, str] = {"png": "image/png", "jpeg": "image/jpeg"}

# libvips 'format' values that correspond to 8/16/32-bit depths.
_BITS_PER_SAMPLE = {
    "uchar": 8, "char": 8,
    "ushort": 16, "short": 16,
    "uint": 32, "int": 32,
    "float": 32, "double": 64,
}

_PYRAMID_TILE = 512


@dataclass(frozen=True)
class ImageInfo:
    name: str
    width: int
    height: int
    channels: int
    bits_per_sample: int
    bytes_on_disk: int
    pyramid_cached: bool
    source_path: str
    cache_dir: str | None


@dataclass(frozen=True)
class RenderedImage:
    data: bytes
    mime_type: str
    width: int
    height: int
    scale: float          # returned_longest / source_region_longest
    requested_region: tuple[int, int, int, int] | None  # (x, y, w, h) as asked
    served_region: tuple[int, int, int, int] | None     # (x, y, w, h) after clamping
    clipped: bool


def _open_source_header(path: Path) -> pyvips.Image:
    # access="sequential" is a hint; we only read header fields so it's effectively header-only.
    return pyvips.Image.new_from_file(str(path), access="sequential")


def describe(ref: ImageRef) -> ImageInfo:
    """Populate ImageInfo from the source file header without decoding pixels."""
    img = _open_source_header(ref.source_path)
    bits = _BITS_PER_SAMPLE.get(img.format, 8)
    return ImageInfo(
        name=ref.name,
        width=img.width,
        height=img.height,
        channels=img.bands,
        bits_per_sample=bits,
        bytes_on_disk=ref.source_path.stat().st_size,
        pyramid_cached=ref.pyramid_path.exists(),
        source_path=str(ref.source_path),
        cache_dir=str(ref.sidecar_dir),
    )


def ensure_pyramid(ref: ImageRef) -> Path:
    """Build a tiled pyramidal TIFF on first use; no-op if one already exists."""
    if ref.pyramid_path.exists():
        return ref.pyramid_path

    tmp = ref.pyramid_path.with_suffix(".tif.tmp")
    tmp.unlink(missing_ok=True)

    log.info("building pyramid for %s → %s", ref.source_path, ref.pyramid_path)
    t0 = time.monotonic()

    src = pyvips.Image.new_from_file(str(ref.source_path), access="sequential")
    src.tiffsave(
        str(tmp),
        tile=True,
        pyramid=True,
        compression="deflate",
        tile_width=_PYRAMID_TILE,
        tile_height=_PYRAMID_TILE,
        bigtiff=True,
    )
    tmp.replace(ref.pyramid_path)

    log.info("pyramid ready in %.1fs", time.monotonic() - t0)
    return ref.pyramid_path


def _encode(image: pyvips.Image, fmt: Format, quality: int) -> bytes:
    if fmt == "jpeg" and image.bands == 4:
        image = image[0:3]  # drop alpha; JPEG cannot carry it
    if fmt == "png":
        return image.pngsave_buffer(compression=6)
    return image.jpegsave_buffer(Q=quality)


def _apply_max_dim(image: pyvips.Image, max_dim: int | None) -> tuple[pyvips.Image, float]:
    if max_dim is None:
        return image, 1.0
    longest = max(image.width, image.height)
    if longest <= max_dim:
        return image, 1.0
    factor = max_dim / longest
    return image.resize(factor, kernel="lanczos3"), factor


def render_overview(
    ref: ImageRef,
    max_dimension: int,
    fmt: Format,
    quality: int,
) -> RenderedImage:
    """Return a downscaled overview. Memoized to disk per (max_dim, fmt, quality)."""
    ensure_pyramid(ref)
    cache_file = ref.overview_path(max_dimension, fmt, quality)

    source_longest = max(_pyramid_level0(ref).width, _pyramid_level0(ref).height)

    if cache_file.exists():
        data = cache_file.read_bytes()
        # Re-derive dimensions from the cached overview header.
        cached = pyvips.Image.new_from_buffer(data, "")
        scale = max(cached.width, cached.height) / source_longest
        return RenderedImage(
            data=data,
            mime_type=MIME_TYPES[fmt],
            width=cached.width,
            height=cached.height,
            scale=scale,
            requested_region=None,
            served_region=None,
            clipped=False,
        )

    thumb = pyvips.Image.thumbnail(str(ref.pyramid_path), max_dimension)
    data = _encode(thumb, fmt, quality)
    cache_file.write_bytes(data)

    scale = max(thumb.width, thumb.height) / source_longest
    return RenderedImage(
        data=data,
        mime_type=MIME_TYPES[fmt],
        width=thumb.width,
        height=thumb.height,
        scale=scale,
        requested_region=None,
        served_region=None,
        clipped=False,
    )


def render_region(
    ref: ImageRef,
    x: int,
    y: int,
    width: int,
    height: int,
    max_dimension: int | None,
    fmt: Format,
    quality: int,
) -> RenderedImage:
    """Extract [x, x+width) x [y, y+height) from the original, clamped to bounds."""
    if width <= 0 or height <= 0:
        raise ValueError("width and height must be positive")

    ensure_pyramid(ref)
    full = _pyramid_level0(ref)

    clamped_x = max(0, min(x, full.width))
    clamped_y = max(0, min(y, full.height))
    clamped_w = max(0, min(x + width, full.width) - clamped_x)
    clamped_h = max(0, min(y + height, full.height) - clamped_y)

    if clamped_w == 0 or clamped_h == 0:
        raise ValueError(
            f"requested region ({x},{y},{width},{height}) lies entirely outside "
            f"image bounds {full.width}x{full.height}"
        )

    clipped = (
        clamped_x != x
        or clamped_y != y
        or clamped_w != width
        or clamped_h != height
    )

    crop = full.crop(clamped_x, clamped_y, clamped_w, clamped_h)
    sized, scale = _apply_max_dim(crop, max_dimension)
    data = _encode(sized, fmt, quality)

    return RenderedImage(
        data=data,
        mime_type=MIME_TYPES[fmt],
        width=sized.width,
        height=sized.height,
        scale=scale,
        requested_region=(x, y, width, height),
        served_region=(clamped_x, clamped_y, clamped_w, clamped_h),
        clipped=clipped,
    )


_level0_cache: dict[str, tuple[float, pyvips.Image]] = {}


def _pyramid_level0(ref: ImageRef) -> pyvips.Image:
    """Return a random-access handle to pyramid page 0 (original resolution), memoized per process."""
    key = str(ref.pyramid_path)
    mtime = ref.pyramid_path.stat().st_mtime
    cached = _level0_cache.get(key)
    if cached is not None and cached[0] == mtime:
        return cached[1]
    handle = pyvips.Image.new_from_file(key, page=0, access="random")
    _level0_cache[key] = (mtime, handle)
    return handle
