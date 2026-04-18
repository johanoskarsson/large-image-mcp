"""Cache sidecar directory management keyed by (abs path, size, mtime)."""

from __future__ import annotations

import hashlib
import shutil
from dataclasses import dataclass
from pathlib import Path

SUPPORTED_EXTENSIONS = {".tif", ".tiff", ".png", ".jpg", ".jpeg", ".webp"}


@dataclass(frozen=True)
class ImageRef:
    """A resolved, validated reference to a source image and its cache sidecar."""

    name: str
    source_path: Path
    sidecar_dir: Path

    @property
    def pyramid_path(self) -> Path:
        return self.sidecar_dir / "pyramid.tif"

    def overview_path(self, max_dimension: int, fmt: str, quality: int) -> Path:
        suffix = "png" if fmt == "png" else f"jpg-q{quality}"
        return self.sidecar_dir / f"overview-{max_dimension}.{suffix}"


def _cache_key(source_path: Path) -> str:
    stat = source_path.stat()
    abs_path = str(source_path.resolve()).encode()
    digest = hashlib.sha1(abs_path).hexdigest()[:16]
    return f"{digest}-{stat.st_size}-{stat.st_mtime_ns}"


def resolve_image(
    name: str,
    images_dir: Path,
    cache_dir: Path,
) -> ImageRef:
    """Resolve `name` inside `images_dir`, prepare the matching sidecar directory.

    Purges any stale sidecar (different size or mtime) before returning.
    Raises ValueError for unknown files or path-traversal attempts.
    """
    images_root = images_dir.resolve()
    candidate = (images_root / name).resolve()

    try:
        candidate.relative_to(images_root)
    except ValueError as exc:
        raise ValueError(f"image {name!r} resolves outside the images directory") from exc

    if not candidate.is_file():
        raise ValueError(f"image {name!r} not found under {images_root}")

    if candidate.suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise ValueError(
            f"image {name!r} has unsupported extension; "
            f"expected one of {sorted(SUPPORTED_EXTENSIONS)}"
        )

    current_key = _cache_key(candidate)
    image_cache_root = cache_dir / candidate.name
    image_cache_root.mkdir(parents=True, exist_ok=True)

    for existing in image_cache_root.iterdir():
        if existing.name != current_key and existing.is_dir():
            shutil.rmtree(existing, ignore_errors=True)

    sidecar = image_cache_root / current_key
    sidecar.mkdir(parents=True, exist_ok=True)

    return ImageRef(name=candidate.name, source_path=candidate, sidecar_dir=sidecar)


def list_image_names(images_dir: Path) -> list[str]:
    """Return source-image filenames (not recursive) with supported extensions."""
    root = images_dir.resolve()
    names = [
        p.name
        for p in root.iterdir()
        if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS
    ]
    names.sort()
    return names
