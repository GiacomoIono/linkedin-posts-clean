from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from pipeline.config import NO_POSTS_FOUND_EXIT_CODE
from pipeline.prepare_image import prepare_latest_post_image, verify_latest_post_image

POST = {
    "content": "<p>A LinkedIn post without an image.</p>",
    "url": "https://www.linkedin.com/feed/update/urn:li:ugcPost:1234567890123456789",
    "published_at": "2026-08-25T08:00:00",
    "images": [],
}


def config(force_webflow_sync: bool = False):
    return SimpleNamespace(
        linkedin_access_token="linkedin-token",
        force_webflow_sync=force_webflow_sync,
    )


class PrepareImageTests(unittest.TestCase):
    def test_no_recent_post_exits_without_generation(self) -> None:
        with (
            patch(
                "pipeline.prepare_image.fetch_latest_linkedin_post", return_value=None
            ),
            patch("pipeline.prepare_image.generate_missing_main_image") as generate,
        ):
            exit_code = prepare_latest_post_image(config())

        self.assertEqual(exit_code, NO_POSTS_FOUND_EXIT_CODE)
        generate.assert_not_called()

    def test_source_image_skips_webflow_lookup_and_generation(self) -> None:
        post = {
            **POST,
            "images": [{"url": "https://example.com/source.jpg", "alt": ""}],
        }
        with (
            patch(
                "pipeline.prepare_image.fetch_latest_linkedin_post", return_value=post
            ),
            patch("pipeline.prepare_image.find_live_webflow_item") as find_live,
            patch("pipeline.prepare_image.generate_missing_main_image") as generate,
        ):
            exit_code = prepare_latest_post_image(config())

        self.assertEqual(exit_code, 0)
        find_live.assert_not_called()
        generate.assert_not_called()

    def test_existing_live_item_stops_before_paid_generation(self) -> None:
        with (
            patch(
                "pipeline.prepare_image.fetch_latest_linkedin_post", return_value=POST
            ),
            patch(
                "pipeline.prepare_image.find_live_webflow_item",
                return_value={"id": "live-item"},
            ),
            patch("pipeline.prepare_image.generate_missing_main_image") as generate,
        ):
            exit_code = prepare_latest_post_image(config())

        self.assertEqual(exit_code, 0)
        generate.assert_not_called()

    def test_missing_source_image_generates_after_duplicate_check(self) -> None:
        expected = {"action": "generated", "path": "images/generated/image.png"}
        with (
            patch(
                "pipeline.prepare_image.fetch_latest_linkedin_post", return_value=POST
            ),
            patch(
                "pipeline.prepare_image.find_live_webflow_item", return_value=None
            ) as find_live,
            patch(
                "pipeline.prepare_image.generate_missing_main_image",
                return_value=expected,
            ) as generate,
        ):
            exit_code = prepare_latest_post_image(config())

        self.assertEqual(exit_code, 0)
        find_live.assert_called_once_with(config(), POST["url"])
        generate.assert_called_once_with(POST, config())

    def test_linkedin_image_without_local_file_stops_before_generation(self) -> None:
        post = {**POST, "linkedin_has_image": True}
        with (
            patch(
                "pipeline.prepare_image.fetch_latest_linkedin_post", return_value=post
            ),
            patch("pipeline.prepare_image.find_live_webflow_item", return_value=None),
            patch("pipeline.prepare_image.generate_missing_main_image") as generate,
            self.assertRaisesRegex(RuntimeError, "LinkedIn reports"),
        ):
            prepare_latest_post_image(config())

        generate.assert_not_called()


class VerifyImageTests(unittest.TestCase):
    def test_no_recent_post_exits_without_verification(self) -> None:
        with (
            patch(
                "pipeline.prepare_image.fetch_latest_linkedin_post", return_value=None
            ),
            patch("pipeline.prepare_image.wait_for_generated_image_public") as verify,
        ):
            exit_code = verify_latest_post_image(config())

        self.assertEqual(exit_code, NO_POSTS_FOUND_EXIT_CODE)
        verify.assert_not_called()

    def test_source_image_skips_generated_url_verification(self) -> None:
        post = {
            **POST,
            "images": [{"url": "https://example.com/source.jpg", "alt": ""}],
        }
        with (
            patch(
                "pipeline.prepare_image.fetch_latest_linkedin_post", return_value=post
            ),
            patch("pipeline.prepare_image.wait_for_generated_image_public") as verify,
        ):
            exit_code = verify_latest_post_image(config())

        self.assertEqual(exit_code, 0)
        verify.assert_not_called()

    def test_missing_local_generated_image_skips_public_verification(self) -> None:
        with (
            patch(
                "pipeline.prepare_image.fetch_latest_linkedin_post", return_value=POST
            ),
            patch("pipeline.prepare_image.is_valid_prepared_png_file", return_value=False),
            patch("pipeline.prepare_image.wait_for_generated_image_public") as verify,
        ):
            exit_code = verify_latest_post_image(config())

        self.assertEqual(exit_code, 0)
        verify.assert_not_called()

    def test_prepared_generated_image_is_verified(self) -> None:
        image_config = config()
        with (
            patch(
                "pipeline.prepare_image.fetch_latest_linkedin_post", return_value=POST
            ),
            patch("pipeline.prepare_image.is_valid_prepared_png_file", return_value=True),
            patch("pipeline.prepare_image.wait_for_generated_image_public") as verify,
        ):
            exit_code = verify_latest_post_image(image_config)

        self.assertEqual(exit_code, 0)
        verify.assert_called_once_with(POST, image_config)


if __name__ == "__main__":
    unittest.main()
