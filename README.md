# large-image-mcp

An MCP server that lets an LLM work with very large images (gigabyte-scale TIFFs, whole-slide scans, stitched microscopy, aerial imagery, etc.) without blowing up the context window.

It exposes two primitives:

- **`get_overview`** — returns a downscaled preview of the whole image so the model can reason about the scene.
- **`get_region`** — returns a full-resolution crop of a specific rectangle so the model can zoom in on details.

Coordinates are always expressed in the **original** image's pixel space — origin `(0, 0)` is the top-left pixel, `+x` goes right, `+y` goes down. The model can use overview pixels, multiply by the returned `scale`, and ask for a region in original coordinates.

Under the hood it uses [libvips](https://libvips.github.io/libvips/) (via `pyvips`). On first access to an image it bakes a tiled pyramidal TIFF sidecar, which makes every subsequent overview and crop fast.

## Installation

```sh
uv sync
```

`pyvips-binary` ships prebuilt libvips wheels for macOS (arm64 + x86_64), Linux, and Windows — no system package install required.

## Running

```sh
uv run python -m large_image_mcp --images-dir /path/to/images
```

Flags (all optional):

| Flag | Env | Default | Purpose |
| --- | --- | --- | --- |
| `--images-dir` | `LARGE_IMAGE_DIR` | cwd | Directory of images to serve. |
| `--cache-dir` | `LARGE_IMAGE_CACHE_DIR` | `<images-dir>/image-cache` | Where pyramids and cached overviews live. Each pyramid is roughly the same size as its source image, so expect the cache to be large — it's a visible directory on purpose so you notice the disk usage. |
| `--max-dimension` | `LARGE_IMAGE_MAX_DIMENSION` | `4096` | Hard cap on any side of a returned image. |
| `--log-level` | `LARGE_IMAGE_LOG_LEVEL` | `INFO` | Python logging level (logs go to stderr). |

The server speaks MCP over stdio.

## Claude Code

Register the server with one command (run from the project directory):

```sh
claude mcp add large-image -s user -- \
  uv --directory "$PWD" run large-image-mcp --images-dir "$PWD"
```

Scope options:
- `-s user` — available in every Claude Code session (shown above).
- `-s local` — only active when running `claude` inside this directory.
- `-s project` — commits the registration to `.mcp.json` in the repo so teammates pick it up too.

Confirm it's connected with `claude mcp list`; remove with `claude mcp remove large-image -s user`.

Once registered, ask Claude something like *"What's in stitched.tif? Zoom into the upper-right quadrant."*

### Claude Desktop

Add this block to `claude_desktop_config.json` (see the [MCP docs](https://modelcontextprotocol.io/quickstart/user) for the file location on your platform):

```json
{
  "mcpServers": {
    "large-image": {
      "command": "uv",
      "args": [
        "--directory", "/Users/johanoskarsson/Dev/large-image-mcp",
        "run", "large-image-mcp",
        "--images-dir", "/Users/johanoskarsson/Dev/large-image-mcp"
      ]
    }
  }
}
```

Restart Claude Desktop afterwards.

## Tool reference

### `list_images() -> list[ImageSummary]`
Enumerates image files in `--images-dir`. Each summary carries `name`, `width`, `height`, `channels`, `bits_per_sample`, `bytes_on_disk`, `pyramid_cached`, and cache paths. No pixel decode.

### `get_image_info(image) -> ImageInfo`
Same fields as above for a single image.

### `get_overview(image, max_dimension=2048, format="png", jpeg_quality=90)`
Returns a downscaled overview. First call per image builds a tiled pyramid sidecar (~10–60 s for a 1.3 GB source); subsequent calls with the same parameters are served from an on-disk cache. The accompanying text message reports the `scale` factor so coordinates in the overview can be mapped back to the original.

### `get_region(image, x, y, width, height, max_dimension=2048, format="png", jpeg_quality=95)`
Extracts `[x, x+width) × [y, y+height)` from the **original** image (coords in original pixels, top-left origin). If the region is larger than `max_dimension` on any side it is downscaled with Lanczos; pass `max_dimension=null` for native resolution (subject to the server-wide `--max-dimension` cap). Out-of-bounds requests are clamped and the text response flags the clip.

## Smoke testing

Run the MCP inspector against a live server:

```sh
npx @modelcontextprotocol/inspector \
  uv run python -m large_image_mcp --images-dir .
```

Then in the inspector UI: call `list_images`, then `get_overview("stitched.tif")`, then `get_region("stitched.tif", x=1000, y=1000, width=1000, height=1000)`.

## Development

```sh
uv sync
uv run pytest
```

Tests synthesize a small RGB TIFF on the fly, so they run in seconds and don't need the 1.3 GB sample file.

## Cache layout and invalidation

By default the cache lives at `<images-dir>/image-cache/`:

```
image-cache/
└── stitched.tif/
    └── <sha1[:16]>-<size>-<mtime_ns>/
        ├── pyramid.tif      # tiled pyramidal BigTIFF (~source-image size)
        └── overview-2048.png
```

The sidecar directory for each image is keyed by `(absolute path, file size, mtime)`. Modifying the source file (even `touch`) causes the old sidecar to be discarded and the pyramid rebuilt on the next call. Delete `image-cache/` any time to reclaim disk — the next request will rebuild what it needs.

## License

Apache License 2.0 — see [LICENSE](LICENSE).
