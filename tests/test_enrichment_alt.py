from __future__ import annotations

import unittest

from pipeline.enrichment import explicit_context_alt_text, fallback_alt_text, has_missing_image_alt


class EnrichmentAltTests(unittest.TestCase):
    def test_has_missing_image_alt_detects_empty_alt_on_image_url(self) -> None:
        post = {"images": [{"url": "https://example.com/train.jpg", "alt": ""}]}

        self.assertTrue(has_missing_image_alt(post))

    def test_has_missing_image_alt_ignores_posts_without_images(self) -> None:
        post = {"images": []}

        self.assertFalse(has_missing_image_alt(post))

    def test_fallback_alt_prefers_explicit_picture_context(self) -> None:
        text = "A few travel notes. In the picture: the new shiny EuroCity Swiss train from Zurich to Milan."

        self.assertEqual(
            fallback_alt_text(text),
            "The new shiny EuroCity Swiss train from Zurich to Milan.",
        )

    def test_explicit_context_alt_is_empty_without_picture_context(self) -> None:
        text = "A few travel notes about trains and timing."

        self.assertEqual(explicit_context_alt_text(text), "")


if __name__ == "__main__":
    unittest.main()
