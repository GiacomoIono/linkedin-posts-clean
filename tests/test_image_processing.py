from __future__ import annotations

import random
import unittest
from io import BytesIO

from PIL import Image

from pipeline.image_processing import (
    MAX_GENERATED_IMAGE_BYTES,
    inspect_raw_image,
    is_valid_prepared_png_bytes,
    png_dimensions,
    prepare_blog_main_png,
)


def source_png(width: int = 1536, height: int = 864) -> bytes:
    image = Image.new("RGB", (width, height), (80, 90, 100))
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def source_jpeg(width: int = 1536, height: int = 864) -> bytes:
    image = Image.new("RGB", (width, height), (80, 90, 100))
    output = BytesIO()
    image.save(output, format="JPEG")
    return output.getvalue()


def textured_source_png(width: int = 1200, height: int = 675) -> bytes:
    randomizer = random.Random(123)
    pixels = bytearray()
    for y in range(height):
        for x in range(width):
            base = x * 180 // width + y * 50 // height
            variation = randomizer.randrange(-3, 4)
            pixels.extend(
                (
                    (base + variation) % 256,
                    (base * 3 // 4 + variation) % 256,
                    (base // 2 + variation) % 256,
                )
            )
    image = Image.frombytes("RGB", (width, height), bytes(pixels))
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


class ImageProcessingTests(unittest.TestCase):
    def test_generated_image_cap_is_800_000_bytes(self) -> None:
        self.assertEqual(MAX_GENERATED_IMAGE_BYTES, 800_000)

    def test_raw_image_is_inspected_before_preparation(self) -> None:
        inspection = inspect_raw_image(source_png())

        self.assertEqual(inspection["format"], "PNG")
        self.assertEqual((inspection["width"], inspection["height"]), (1536, 864))
        self.assertGreater(inspection["bytes"], 0)

    def test_invalid_or_too_small_raw_image_is_rejected(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "cannot decode"):
            inspect_raw_image(b"not an image")
        with self.assertRaisesRegex(RuntimeError, "too small"):
            inspect_raw_image(source_png(320, 320))

    def test_three_by_two_raw_png_is_rejected_before_preparation(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "not exact 16:9"):
            inspect_raw_image(source_png(1536, 1024))

    def test_exact_16_by_9_raw_jpeg_is_rejected_before_preparation(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "not PNG"):
            inspect_raw_image(source_jpeg(1536, 864))

    def test_preparation_outputs_one_preferred_exact_16_by_9_png_under_cap(self) -> None:
        output, metadata = prepare_blog_main_png(source_png())

        self.assertTrue(is_valid_prepared_png_bytes(output))
        self.assertEqual(png_dimensions(output), (1200, 675))
        self.assertEqual(metadata["width"] * 9, metadata["height"] * 16)
        self.assertEqual(metadata["bytes"], len(output))
        self.assertLessEqual(len(output), MAX_GENERATED_IMAGE_BYTES)

    def test_larger_cap_preserves_preferred_rgb_quality_above_old_limit(self) -> None:
        output, metadata = prepare_blog_main_png(textured_source_png())

        self.assertGreater(len(output), 200_000)
        self.assertLessEqual(len(output), MAX_GENERATED_IMAGE_BYTES)
        self.assertEqual((metadata["width"], metadata["height"]), (1200, 675))
        with Image.open(BytesIO(output)) as image:
            self.assertEqual(image.mode, "RGB")

    def test_non_png_and_non_16_by_9_output_is_not_accepted(self) -> None:
        self.assertFalse(is_valid_prepared_png_bytes(b"not a png"))
        self.assertFalse(is_valid_prepared_png_bytes(source_png(1200, 800)))


if __name__ == "__main__":
    unittest.main()
