from __future__ import annotations

import base64
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from pipeline.image_generation import (
    GENERATED_IMAGE_COMPRESSION,
    GENERATED_IMAGE_FORMAT,
    GENERATED_IMAGE_QUALITY,
    GENERATED_IMAGE_SIZE,
    PUBLIC_IMAGE_MAX_ATTEMPTS,
    attach_generated_main_image,
    generate_missing_main_image,
    generated_image_filename,
    generated_image_path,
    jpeg_dimensions,
    wait_for_generated_image_public,
)

POST = {
    "content": "<p>AI is changing how customers discover and evaluate products.</p>",
    "headline": "AI changes product discovery",
    "url": "https://www.linkedin.com/feed/update/urn:li:ugcPost:1234567890123456789",
    "published_at": "2026-08-25T08:00:00",
    "images": [],
}


def jpeg_bytes(width: int = 1536, height: int = 864) -> bytes:
    return b"".join(
        [
            b"\xff\xd8",
            b"\xff\xc0",
            b"\x00\x11",
            b"\x08",
            height.to_bytes(2, "big"),
            width.to_bytes(2, "big"),
            b"\x03\x01\x11\x00\x02\x11\x00\x03\x11\x00",
            b"\xff\xd9",
        ]
    )


def config(**overrides):
    values = {
        "openai_api_key": "existing-openai-key",
        "openai_image_model": "gpt-image-2",
        "image_public_ref": "main",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class ImageGenerationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.generated_directory = (
            Path(self.temporary_directory.name) / "images" / "generated"
        )
        self.generated_directory.mkdir(parents=True)
        self.directory_patch = patch(
            "pipeline.image_generation.GENERATED_IMAGE_DIR",
            self.generated_directory,
        )
        self.directory_patch.start()

    def tearDown(self) -> None:
        self.directory_patch.stop()
        self.temporary_directory.cleanup()

    def client_returning(self, image_bytes: bytes):
        generate = Mock(
            return_value=SimpleNamespace(
                data=[
                    SimpleNamespace(
                        b64_json=base64.b64encode(image_bytes).decode("ascii")
                    )
                ]
            )
        )
        return SimpleNamespace(images=SimpleNamespace(generate=generate)), generate

    def test_source_images_skip_generation(self) -> None:
        post = {
            **POST,
            "images": [{"url": "https://example.com/source.jpg", "alt": "Source"}],
        }
        client, generate = self.client_returning(jpeg_bytes())

        result = generate_missing_main_image(post, config(), client=client)

        self.assertEqual(result, {"action": "skipped_source_images"})
        generate.assert_not_called()
        self.assertEqual(list(self.generated_directory.iterdir()), [])

    def test_linkedin_image_without_local_source_does_not_generate(self) -> None:
        post = {**POST, "linkedin_has_image": True}
        client, generate = self.client_returning(jpeg_bytes())

        with self.assertRaisesRegex(RuntimeError, "LinkedIn reports"):
            generate_missing_main_image(post, config(), client=client)

        generate.assert_not_called()

    def test_generates_exactly_one_high_quality_16_by_9_jpeg(self) -> None:
        client, generate = self.client_returning(jpeg_bytes())

        result = generate_missing_main_image(POST, config(), client=client)

        self.assertEqual(result["action"], "generated")
        output = Path(result["path"])
        self.assertEqual(output.suffix, ".jpeg")
        self.assertEqual(output.read_bytes(), jpeg_bytes())
        self.assertEqual(jpeg_dimensions(output.read_bytes()), (1536, 864))
        generate.assert_called_once()
        kwargs = generate.call_args.kwargs
        self.assertEqual(kwargs["model"], "gpt-image-2")
        self.assertEqual(kwargs["n"], 1)
        self.assertEqual(kwargs["size"], GENERATED_IMAGE_SIZE)
        self.assertEqual(kwargs["quality"], GENERATED_IMAGE_QUALITY)
        self.assertEqual(kwargs["output_format"], GENERATED_IMAGE_FORMAT)
        self.assertEqual(kwargs["output_compression"], GENERATED_IMAGE_COMPRESSION)
        self.assertEqual(kwargs["background"], "opaque")
        self.assertIn("noir", kwargs["prompt"].lower())
        self.assertIn("exactly one", kwargs["prompt"].lower())
        self.assertIn("AI changes product discovery", kwargs["prompt"])
        self.assertIn("AI is changing how customers discover", kwargs["prompt"])
        self.assertNotIn("<p>", kwargs["prompt"])

    def test_reuses_valid_deterministic_image_without_another_api_call(self) -> None:
        output = generated_image_path(POST)
        output.write_bytes(jpeg_bytes())
        client, generate = self.client_returning(jpeg_bytes())

        result = generate_missing_main_image(POST, config(), client=client)

        self.assertEqual(result["action"], "reused")
        self.assertEqual(Path(result["path"]), output)
        generate.assert_not_called()

    def test_same_date_posts_have_different_filenames(self) -> None:
        other_post = {
            **POST,
            "url": "https://www.linkedin.com/feed/update/urn:li:ugcPost:9876543210987654321",
        }

        self.assertNotEqual(
            generated_image_filename(POST), generated_image_filename(other_post)
        )

    def test_wrong_dimensions_fail_closed_without_writing_output(self) -> None:
        client, _ = self.client_returning(jpeg_bytes(width=1024, height=1024))

        with self.assertRaisesRegex(RuntimeError, "wrong dimensions"):
            generate_missing_main_image(POST, config(), client=client)

        self.assertFalse(generated_image_path(POST).exists())

    def test_image_over_webflow_limit_fails_closed(self) -> None:
        oversized = jpeg_bytes()[:-2] + (b"0" * 4_000_000) + b"\xff\xd9"
        client, _ = self.client_returning(oversized)

        with self.assertRaisesRegex(RuntimeError, "4 MB limit"):
            generate_missing_main_image(POST, config(), client=client)

        self.assertFalse(generated_image_path(POST).exists())

    def test_generated_image_attaches_as_a_separate_main_image(self) -> None:
        generated_image_path(POST).write_bytes(jpeg_bytes())

        enriched = attach_generated_main_image(POST, config(image_public_ref="abc123"))

        self.assertEqual(enriched["images"], [])
        self.assertEqual(
            enriched["generated_main_image"]["url"],
            "https://raw.githubusercontent.com/GiacomoIono/linkedin-posts-clean/abc123/"
            "images/generated/2026-08-25-1234567890123456789.jpeg",
        )
        self.assertIn(
            "AI changes product discovery", enriched["generated_main_image"]["alt"]
        )

    def test_missing_generated_image_stops_before_webflow(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "image-preparation stage"):
            attach_generated_main_image(POST, config())

    def test_public_generated_image_must_be_the_expected_jpeg(self) -> None:
        request_get = Mock(
            return_value=SimpleNamespace(status_code=200, content=jpeg_bytes())
        )
        sleep = Mock()

        url = wait_for_generated_image_public(
            POST,
            config(image_public_ref="abc123"),
            request_get=request_get,
            sleep_fn=sleep,
        )

        self.assertIn("/abc123/images/generated/", url)
        request_get.assert_called_once_with(url, timeout=30)
        sleep.assert_not_called()

    def test_invalid_public_image_stops_before_webflow(self) -> None:
        request_get = Mock(
            return_value=SimpleNamespace(status_code=200, content=b"not a jpeg")
        )
        sleep = Mock()

        with self.assertRaisesRegex(RuntimeError, "Stopping before Webflow"):
            wait_for_generated_image_public(
                POST,
                config(image_public_ref="abc123"),
                request_get=request_get,
                sleep_fn=sleep,
            )

        self.assertEqual(request_get.call_count, PUBLIC_IMAGE_MAX_ATTEMPTS)
        self.assertEqual(sleep.call_count, PUBLIC_IMAGE_MAX_ATTEMPTS - 1)


if __name__ == "__main__":
    unittest.main()
