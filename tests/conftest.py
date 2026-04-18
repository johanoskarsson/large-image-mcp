"""Synthesize a small RGB TIFF so tests don't need the real 1.35 GB file."""

from __future__ import annotations

import os
from pathlib import Path

import pyvips
import pytest

TEST_WIDTH = 1024
TEST_HEIGHT = 768


def _build_test_tiff(path: Path) -> None:
    # A horizontal gradient in R, vertical in G, constant B — makes every coordinate
    # visually distinct so region extractions can be verified by pixel value.
    xyz = pyvips.Image.xyz(TEST_WIDTH, TEST_HEIGHT)
    x_ramp = (xyz[0] % 256).cast("uchar")
    y_ramp = (xyz[1] % 256).cast("uchar")
    blue = (x_ramp * 0 + 128).cast("uchar")
    rgb = x_ramp.bandjoin([y_ramp, blue]).copy(interpretation="srgb")
    rgb.tiffsave(str(path), compression="none")


@pytest.fixture
def images_dir(tmp_path: Path) -> Path:
    d = tmp_path / "images"
    d.mkdir()
    _build_test_tiff(d / "tiny.tif")
    return d


@pytest.fixture
def cache_dir(tmp_path: Path) -> Path:
    d = tmp_path / "cache"
    d.mkdir()
    return d
