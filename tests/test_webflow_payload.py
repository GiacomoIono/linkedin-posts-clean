from __future__ import annotations

import unittest
from unittest.mock import patch

from pipeline.main import already_synced_to_webflow
from pipeline.utils import post_hash
from pipeline.webflow import WEBFLOW_PAYLOAD_VERSION, build_field_data


POST = {
    "content": "<p>Hello from LinkedIn.</p>",
    "url": "https://www.linkedin.com/feed/update/urn:li:share:1234567890",
    "published_at": "2026-05-31T10:30:00",
    "headline": "Hello from LinkedIn",
    "description": "A short description",
    "images": [
        {
            "url": "https://example.com/image.jpg",
            "alt": "A chart showing steady revenue growth",
        }
    ],
}


class WebflowPayloadTests(unittest.TestCase):
    def test_build_field_data_passes_linkedin_url_and_image_alt_tag(self) -> None:
        collection = {
            "fields": [
                {"slug": "linkedin-url", "displayName": "LinkedIn URL", "type": "Link"},
                {"slug": "image-alt-tag", "displayName": "Image Alt Tag", "type": "PlainText"},
                {"slug": "featured-image", "displayName": "Featured Image", "type": "Image"},
                {"slug": "images", "displayName": "Images", "type": "MultiImage"},
            ]
        }

        field_data = build_field_data(POST, collection)

        self.assertEqual(field_data["linkedin-url"], POST["url"])
        self.assertEqual(field_data["image-alt-tag"], POST["images"][0]["alt"])
        self.assertEqual(field_data["featured-image"], POST["images"][0])
        self.assertEqual(field_data["images"], POST["images"])

    def test_build_field_data_fallback_includes_linkedin_url_and_alt_tag(self) -> None:
        field_data = build_field_data(POST, {"fields": []})

        self.assertEqual(field_data["linkedin-url"], POST["url"])
        self.assertEqual(field_data["images-alt-tag"], POST["images"][0]["alt"])
        self.assertEqual(field_data["image"], POST["images"][0])
        self.assertEqual(field_data["images"], POST["images"])

    def test_existing_webflow_item_is_current_only_after_payload_version_backfill(self) -> None:
        current_entry = {
            "item_id": "item-123",
            "signature": post_hash(POST),
            "payload_version": WEBFLOW_PAYLOAD_VERSION,
            "published": True,
        }
        stale_entry = dict(current_entry)
        stale_entry.pop("payload_version")

        with patch("pipeline.main.load_webflow_state", return_value={"items": {POST["url"]: stale_entry}}):
            self.assertIsNone(already_synced_to_webflow(POST))

        with patch("pipeline.main.load_webflow_state", return_value={"items": {POST["url"]: current_entry}}):
            self.assertEqual(already_synced_to_webflow(POST), current_entry)


if __name__ == "__main__":
    unittest.main()
