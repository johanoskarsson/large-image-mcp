"""Exercise cache resolution, pyramid build, overview caching, and region extraction."""

from __future__ import annotations

import io
import os
import time
from pathlib import Path

import pyvips
import pytest

from large_image_mcp import cache, images
from tests.conftest import TEST_HEIGHT, TEST_WIDTH


def _decode(data: bytes) -> pyvips.Image:
    return pyvips.Image.new_from_buffer(data, "")


def test_describe_reads_header(images_dir: Path, cache_dir: Path) -> None:
    ref = cache.resolve_image("tiny.tif", images_dir, cache_dir)
    info = images.describe(ref)
    assert info.width == TEST_WIDTH
    assert info.height == TEST_HEIGHT
    assert info.channels == 3
    assert info.bits_per_sample == 8
    assert info.pyramid_cached is False


def test_pyramid_build_is_idempotent(images_dir: Path, cache_dir: Path) -> None:
    ref = cache.resolve_image("tiny.tif", images_dir, cache_dir)
    p1 = images.ensure_pyramid(ref)
    mtime1 = p1.stat().st_mtime_ns
    p2 = images.ensure_pyramid(ref)
    assert p1 == p2
    assert p2.stat().st_mtime_ns == mtime1  # not rebuilt


def test_overview_respects_max_dimension(images_dir: Path, cache_dir: Path) -> None:
    ref = cache.resolve_image("tiny.tif", images_dir, cache_dir)
    rendered = images.render_overview(ref, max_dimension=256, fmt="png", quality=90)
    assert rendered.mime_type == "image/png"
    assert max(rendered.width, rendered.height) == 256
    assert rendered.scale == pytest.approx(256 / TEST_WIDTH, rel=1e-3)

    # Second call is served from disk cache — same bytes back.
    rendered2 = images.render_overview(ref, max_dimension=256, fmt="png", quality=90)
    assert rendered2.data == rendered.data


def test_overview_different_formats_cache_independently(
    images_dir: Path, cache_dir: Path
) -> None:
    ref = cache.resolve_image("tiny.tif", images_dir, cache_dir)
    png = images.render_overview(ref, 256, "png", 90)
    jpeg = images.render_overview(ref, 256, "jpeg", 90)
    assert png.mime_type == "image/png"
    assert jpeg.mime_type == "image/jpeg"
    assert png.data != jpeg.data


def test_region_native_resolution(images_dir: Path, cache_dir: Path) -> None:
    ref = cache.resolve_image("tiny.tif", images_dir, cache_dir)
    rendered = images.render_region(
        ref, x=100, y=50, width=200, height=150,
        max_dimension=None, fmt="png", quality=90,
    )
    decoded = _decode(rendered.data)
    assert decoded.width == 200
    assert decoded.height == 150
    assert rendered.clipped is False
    assert rendered.scale == 1.0
    assert rendered.served_region == (100, 50, 200, 150)


def test_region_pixel_values_match_gradient(images_dir: Path, cache_dir: Path) -> None:
    ref = cache.resolve_image("tiny.tif", images_dir, cache_dir)
    rendered = images.render_region(
        ref, x=300, y=200, width=4, height=4,
        max_dimension=None, fmt="png", quality=90,
    )
    decoded = _decode(rendered.data)
    # Top-left pixel of the crop should be source pixel (300, 200):
    # R = x % 256 = 300 % 256 = 44; G = y % 256 = 200; B = 128.
    r, g, b = decoded(0, 0)
    assert r == 44
    assert g == 200
    assert b == 128


def test_region_clamps_and_reports(images_dir: Path, cache_dir: Path) -> None:
    ref = cache.resolve_image("tiny.tif", images_dir, cache_dir)
    rendered = images.render_region(
        ref, x=TEST_WIDTH - 100, y=TEST_HEIGHT - 50, width=500, height=500,
        max_dimension=None, fmt="png", quality=90,
    )
    assert rendered.clipped is True
    assert rendered.served_region == (TEST_WIDTH - 100, TEST_HEIGHT - 50, 100, 50)


def test_region_fully_outside_rejected(images_dir: Path, cache_dir: Path) -> None:
    ref = cache.resolve_image("tiny.tif", images_dir, cache_dir)
    with pytest.raises(ValueError):
        images.render_region(
            ref, x=TEST_WIDTH + 100, y=0, width=10, height=10,
            max_dimension=None, fmt="png", quality=90,
        )


def test_region_applies_max_dimension(images_dir: Path, cache_dir: Path) -> None:
    ref = cache.resolve_image("tiny.tif", images_dir, cache_dir)
    rendered = images.render_region(
        ref, x=0, y=0, width=800, height=400,
        max_dimension=200, fmt="png", quality=90,
    )
    assert max(rendered.width, rendered.height) == 200
    assert rendered.scale == pytest.approx(200 / 800, rel=1e-3)


def test_cache_invalidates_on_mtime_change(images_dir: Path, cache_dir: Path) -> None:
    ref = cache.resolve_image("tiny.tif", images_dir, cache_dir)
    images.ensure_pyramid(ref)
    first_sidecar = ref.sidecar_dir
    assert first_sidecar.exists()

    # Bump mtime on the source — next resolve should allocate a new sidecar and
    # purge the old one.
    future = time.time() + 10
    os.utime(ref.source_path, (future, future))

    ref2 = cache.resolve_image("tiny.tif", images_dir, cache_dir)
    assert ref2.sidecar_dir != first_sidecar
    assert not first_sidecar.exists()
    assert not ref2.pyramid_path.exists()  # fresh sidecar, pyramid rebuilds on demand


def test_rejects_path_traversal(images_dir: Path, cache_dir: Path) -> None:
    with pytest.raises(ValueError):
        cache.resolve_image("../etc/passwd", images_dir, cache_dir)


def test_rejects_unsupported_extension(tmp_path: Path, cache_dir: Path) -> None:
    d = tmp_path / "imgs"
    d.mkdir()
    (d / "data.bin").write_bytes(b"not an image")
    with pytest.raises(ValueError):
        cache.resolve_image("data.bin", d, cache_dir)


def test_list_image_names(images_dir: Path) -> None:
    (images_dir / "readme.txt").write_text("ignore me")
    (images_dir / "other.png").write_bytes(b"")
    names = cache.list_image_names(images_dir)
    assert "tiny.tif" in names
    assert "other.png" in names
    assert "readme.txt" not in names
