from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image, UnidentifiedImageError

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
PREFERRED_IMAGE_DIMENSIONS = (1200, 675)
FALLBACK_IMAGE_DIMENSIONS = ((1024, 576), (960, 540), (800, 450))
MAX_GENERATED_IMAGE_BYTES = 800_000
MAX_RAW_IMAGE_BYTES = 30_000_000


def inspect_raw_image(image_bytes: bytes) -> dict[str, Any]:
    if not image_bytes or len(image_bytes) > MAX_RAW_IMAGE_BYTES:
        raise RuntimeError("OpenAI returned an empty or unexpectedly large raw image.")
    try:
        with Image.open(BytesIO(image_bytes)) as image:
            image.verify()
        with Image.open(BytesIO(image_bytes)) as image:
            image.load()
            width, height = image.size
            image_format = str(image.format or "").upper()
            mode = image.mode
            frame_count = int(getattr(image, "n_frames", 1))
    except (OSError, ValueError, UnidentifiedImageError) as exc:
        raise RuntimeError("OpenAI returned image data that Pillow cannot decode.") from exc

    if width < 512 or height < 512:
        raise RuntimeError("OpenAI returned a raw image that is too small for a blog main image.")
    if frame_count != 1:
        raise RuntimeError("OpenAI returned multiple image frames; exactly one image is required.")
    if image_format != "PNG":
        raise RuntimeError("OpenAI returned a raw image that is not PNG format.")
    if width * 9 != height * 16:
        raise RuntimeError("OpenAI returned a raw PNG that is not exact 16:9.")
    if mode not in {"RGB", "RGBA"}:
        raise RuntimeError("OpenAI returned a raw PNG with an unsupported color mode.")
    return {
        "format": image_format,
        "width": width,
        "height": height,
        "mode": mode,
        "bytes": len(image_bytes),
    }


def _encode_png(image: Image.Image, colors: int | None = None) -> bytes:
    output = BytesIO()
    prepared = image
    if colors is not None:
        prepared = image.quantize(
            colors=colors,
            method=Image.Quantize.MEDIANCUT,
            dither=Image.Dither.FLOYDSTEINBERG,
        )
    prepared.save(output, format="PNG", optimize=True, compress_level=9)
    return output.getvalue()


def prepare_blog_main_png(image_bytes: bytes) -> tuple[bytes, dict[str, int]]:
    inspect_raw_image(image_bytes)
    try:
        with Image.open(BytesIO(image_bytes)) as source:
            source.load()
            rgb_source = source.convert("RGB")
    except (OSError, ValueError, UnidentifiedImageError) as exc:
        raise RuntimeError("The reviewed raw image could not be prepared as a PNG.") from exc

    dimensions = (PREFERRED_IMAGE_DIMENSIONS, *FALLBACK_IMAGE_DIMENSIONS)
    color_counts: tuple[int | None, ...] = (None, 256, 192, 128, 96, 64, 48, 32)
    for width, height in dimensions:
        # Raw input is already exact 16:9, so resize without cropping or padding.
        resized = rgb_source.resize((width, height), Image.Resampling.LANCZOS)
        for colors in color_counts:
            png_bytes = _encode_png(resized, colors)
            if len(png_bytes) <= MAX_GENERATED_IMAGE_BYTES:
                return png_bytes, {
                    "width": width,
                    "height": height,
                    "bytes": len(png_bytes),
                }
    raise RuntimeError(
        "The reviewed image could not be compressed below "
        f"{MAX_GENERATED_IMAGE_BYTES:,} bytes as a 16:9 PNG."
    )


def png_dimensions(image_bytes: bytes) -> tuple[int, int] | None:
    if not image_bytes.startswith(PNG_SIGNATURE):
        return None
    try:
        with Image.open(BytesIO(image_bytes)) as image:
            image.verify()
        with Image.open(BytesIO(image_bytes)) as image:
            return image.size
    except (OSError, ValueError, UnidentifiedImageError):
        return None


def is_valid_prepared_png_bytes(image_bytes: bytes) -> bool:
    dimensions = png_dimensions(image_bytes)
    return bool(
        dimensions
        and dimensions[0] * 9 == dimensions[1] * 16
        and len(image_bytes) <= MAX_GENERATED_IMAGE_BYTES
    )


def is_valid_prepared_png_file(path: Path) -> bool:
    try:
        return path.suffix.lower() == ".png" and is_valid_prepared_png_bytes(path.read_bytes())
    except OSError:
        return False
