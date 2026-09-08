from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from pipeline.config import NO_POSTS_FOUND_EXIT_CODE
from pipeline.prepare_image import prepare_latest_post_image

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
    def setUp(self) -> None:
        pending_check = patch("pipeline.prepare_image.has_pending_verification", return_value=False)
        self.has_pending_verification = pending_check.start()
        self.addCleanup(pending_check.stop)

    def test_pending_verification_skips_paid_generation_and_live_lookup(self) -> None:
        self.has_pending_verification.return_value = True
        with (
            patch("pipeline.prepare_image.fetch_latest_linkedin_post", return_value=POST),
            patch("pipeline.prepare_image.find_live_webflow_item") as find_live,
            patch("pipeline.prepare_image.generate_missing_main_image") as generate,
        ):
            self.assertEqual(prepare_latest_post_image(config()), 0)
        find_live.assert_not_called()
        generate.assert_not_called()

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
            "images": [{"local_path": "images/2026-08-25.jpg", "filename": "2026-08-25.jpg", "alt": ""}],
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

    def test_linkedin_media_signal_without_dated_source_still_generates(self) -> None:
        post = {**POST, "linkedin_has_image": True}
        with (
            patch(
                "pipeline.prepare_image.fetch_latest_linkedin_post", return_value=post
            ),
            patch("pipeline.prepare_image.find_live_webflow_item", return_value=None),
            patch(
                "pipeline.prepare_image.generate_missing_main_image",
                return_value={"action": "generated"},
            ) as generate,
        ):
            exit_code = prepare_latest_post_image(config())

        self.assertEqual(exit_code, 0)
        generate.assert_called_once_with(post, config())


if __name__ == "__main__":
    unittest.main()
