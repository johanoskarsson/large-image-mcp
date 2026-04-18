"""FastMCP server exposing large-image tools over stdio."""

from __future__ import annotations

import logging
from dataclasses import asdict
from pathlib import Path
from typing import Literal

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.utilities.types import Image as FastMCPImage

from . import cache, images

log = logging.getLogger(__name__)


def _format_region_summary(rendered: images.RenderedImage) -> str:
    lines = [f"returned {rendered.width}x{rendered.height} {rendered.mime_type}"]
    if rendered.served_region is not None:
        sx, sy, sw, sh = rendered.served_region
        lines.append(f"region served: x={sx} y={sy} width={sw} height={sh}")
    if rendered.clipped and rendered.requested_region is not None:
        rx, ry, rw, rh = rendered.requested_region
        lines.append(
            f"note: requested region (x={rx} y={ry} width={rw} height={rh}) "
            "was clipped to the image bounds"
        )
    if rendered.scale != 1.0:
        lines.append(
            f"downscaled by factor {rendered.scale:.4f} to fit max_dimension; "
            "multiply returned coordinates by 1/scale to map back to original pixels"
        )
    return "\n".join(lines)


def _format_overview_summary(rendered: images.RenderedImage, info: images.ImageInfo) -> str:
    return (
        f"overview of {info.name}: source {info.width}x{info.height}, "
        f"returned {rendered.width}x{rendered.height} {rendered.mime_type}, "
        f"scale={rendered.scale:.6f} (multiply overview pixels by 1/scale to get "
        f"coordinates in the original image)"
    )


def build_server(
    images_dir: Path,
    cache_dir: Path,
    server_max_dimension: int,
) -> FastMCP:
    images_dir = images_dir.resolve()
    cache_dir = cache_dir.resolve()
    cache_dir.mkdir(parents=True, exist_ok=True)

    mcp = FastMCP(
        "large-image-mcp",
        instructions=(
            "Serves overviews and full-resolution sub-regions of very large images.\n"
            "Coordinate system: origin (0,0) is the top-left pixel of the ORIGINAL "
            "image; +x is right, +y is down; units are original-image pixels.\n"
            "Typical flow: list_images -> get_overview to see the whole scene -> "
            "get_region with coordinates in the original image to inspect details."
        ),
    )

    def _cap(max_dim: int | None) -> int | None:
        if max_dim is None:
            return None
        return min(max_dim, server_max_dimension)

    @mcp.tool()
    def list_images() -> list[dict]:
        """List all image files in the configured images directory.

        Returns a list of summaries with dimensions, channels, bit depth, file size,
        and whether a pyramid cache has been built. Does not decode pixels.
        """
        out = []
        for name in cache.list_image_names(images_dir):
            try:
                ref = cache.resolve_image(name, images_dir, cache_dir)
                info = images.describe(ref)
                out.append(asdict(info))
            except Exception as exc:
                log.warning("failed to describe %s: %s", name, exc)
                out.append({"name": name, "error": str(exc)})
        return out

    @mcp.tool()
    def get_image_info(image: str) -> dict:
        """Return dimensions, channels, bit depth, and cache status for one image.

        Args:
            image: Filename (as returned by list_images) inside the images directory.
        """
        ref = cache.resolve_image(image, images_dir, cache_dir)
        return asdict(images.describe(ref))

    @mcp.tool()
    def get_overview(
        image: str,
        max_dimension: int = 2048,
        format: Literal["png", "jpeg"] = "png",
        jpeg_quality: int = 90,
    ) -> list:
        """Return a downscaled overview of the full image.

        Builds a tiled pyramid cache on first access (one-time cost).
        Subsequent calls with the same parameters are served from disk cache.

        Args:
            image: Filename inside the images directory.
            max_dimension: Longest side (pixels) of the returned overview. Clamped
                to the server's configured maximum. Default 2048.
            format: 'png' for lossless, 'jpeg' for smaller payload. Default 'png'.
            jpeg_quality: 1-100, only used when format='jpeg'. Default 90.

        Returns image + a text note describing the scale factor so the caller can
        map overview pixels back to original image coordinates.
        """
        if max_dimension <= 0:
            raise ValueError("max_dimension must be positive")
        if format == "jpeg" and not (1 <= jpeg_quality <= 100):
            raise ValueError("jpeg_quality must be between 1 and 100")

        ref = cache.resolve_image(image, images_dir, cache_dir)
        effective_max = _cap(max_dimension) or max_dimension
        rendered = images.render_overview(ref, effective_max, format, jpeg_quality)
        info = images.describe(ref)

        return [
            _format_overview_summary(rendered, info),
            FastMCPImage(data=rendered.data, format=format),
        ]

    @mcp.tool()
    def get_region(
        image: str,
        x: int,
        y: int,
        width: int,
        height: int,
        max_dimension: int | None = 2048,
        format: Literal["png", "jpeg"] = "png",
        jpeg_quality: int = 95,
    ) -> list:
        """Extract a rectangular region from the ORIGINAL image at full fidelity.

        Coordinate system: (0,0) is the top-left pixel of the original image.
        +x goes right, +y goes down. Units are original-image pixels.

        Args:
            image: Filename inside the images directory.
            x: Left edge of region in original-image pixel coordinates.
            y: Top edge of region in original-image pixel coordinates.
            width: Region width in original-image pixels.
            height: Region height in original-image pixels.
            max_dimension: If set, downscale so the longest returned side is at most
                this many pixels. Set to null for native resolution (subject to the
                server's configured hard cap). Default 2048.
            format: 'png' for lossless crops, 'jpeg' for smaller payload. Default 'png'.
            jpeg_quality: 1-100, only used when format='jpeg'. Default 95.

        Out-of-bounds requests are clamped to the image with a note in the text
        response. Returns image + a text note describing the region actually served
        and any applied downscale factor.
        """
        if max_dimension is not None and max_dimension <= 0:
            raise ValueError("max_dimension must be positive or null")
        if format == "jpeg" and not (1 <= jpeg_quality <= 100):
            raise ValueError("jpeg_quality must be between 1 and 100")

        ref = cache.resolve_image(image, images_dir, cache_dir)
        effective_max = _cap(max_dimension)
        rendered = images.render_region(
            ref, x, y, width, height, effective_max, format, jpeg_quality
        )

        return [
            _format_region_summary(rendered),
            FastMCPImage(data=rendered.data, format=format),
        ]

    return mcp
