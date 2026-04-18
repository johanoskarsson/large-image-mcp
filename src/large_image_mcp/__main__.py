"""Entrypoint: ``python -m large_image_mcp [options]``."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from .server import build_server


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="large-image-mcp",
        description="MCP server that serves overviews and sub-regions of very large images.",
    )
    parser.add_argument(
        "--images-dir",
        default=os.environ.get("LARGE_IMAGE_DIR", os.getcwd()),
        help="Directory containing the image files to serve (default: $LARGE_IMAGE_DIR or cwd).",
    )
    parser.add_argument(
        "--cache-dir",
        default=os.environ.get("LARGE_IMAGE_CACHE_DIR"),
        help=(
            "Directory for pyramid and overview caches "
            "(default: $LARGE_IMAGE_CACHE_DIR or <images-dir>/image-cache). "
            "Expect one pyramid TIFF per source image, similar in size to the source."
        ),
    )
    parser.add_argument(
        "--max-dimension",
        type=int,
        default=int(os.environ.get("LARGE_IMAGE_MAX_DIMENSION", "4096")),
        help="Hard cap on returned image dimensions, any side (default: 4096).",
    )
    parser.add_argument(
        "--log-level",
        default=os.environ.get("LARGE_IMAGE_LOG_LEVEL", "INFO"),
        help="Python logging level (default: INFO).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)

    logging.basicConfig(
        level=args.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )

    images_dir = Path(args.images_dir).expanduser().resolve()
    if not images_dir.is_dir():
        raise SystemExit(f"--images-dir {images_dir} is not a directory")

    cache_dir = (
        Path(args.cache_dir).expanduser().resolve()
        if args.cache_dir
        else images_dir / "image-cache"
    )

    if args.max_dimension <= 0:
        raise SystemExit("--max-dimension must be positive")

    server = build_server(images_dir, cache_dir, args.max_dimension)
    server.run()


if __name__ == "__main__":
    main()
