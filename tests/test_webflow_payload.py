from __future__ import annotations

import unittest
from unittest.mock import patch

from pipeline.main import already_synced_to_webflow
from pipeline.utils import post_hash
from pipeline.webflow import (
    AUTHOR_COLLECTION_ID,
    AUTHOR_ITEM_ID,
    AUTHOR_NAME,
    WEBFLOW_PAYLOAD_VERSION,
    build_field_data,
)


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
    def test_author_ids_match_webflow_author_collection(self) -> None:
        self.assertEqual(AUTHOR_COLLECTION_ID, "63250855178122e0e087d804")
        self.assertEqual(AUTHOR_ITEM_ID, "632508551781225a7587d893")

    def test_build_field_data_passes_linkedin_url_and_image_alt_tag(self) -> None:
        collection = {
            "fields": [
                {"slug": "slug", "displayName": "Slug", "type": "PlainText", "isRequired": True},
                {"slug": "linkedin-url", "displayName": "LinkedIn URL", "type": "Link"},
                {"slug": "image-alt-tag", "displayName": "Image Alt Tag", "type": "PlainText"},
                {"slug": "featured-image", "displayName": "Featured Image", "type": "Image"},
                {"slug": "images", "displayName": "Images", "type": "MultiImage"},
            ]
        }

        field_data = build_field_data(POST, collection)

        self.assertNotIn("slug", field_data)
        self.assertEqual(field_data["linkedin-url"], POST["url"])
        self.assertEqual(field_data["image-alt-tag"], POST["images"][0]["alt"])
        self.assertEqual(field_data["featured-image"], POST["images"][0])
        self.assertEqual(field_data["images"], POST["images"])

    def test_build_field_data_fallback_includes_linkedin_url_and_alt_tag(self) -> None:
        field_data = build_field_data(POST, {"fields": []})

        self.assertNotIn("slug", field_data)
        self.assertEqual(field_data["author"], AUTHOR_NAME)
        self.assertEqual(field_data["linkedin-url"], POST["url"])
        self.assertEqual(field_data["images-alt-tag"], POST["images"][0]["alt"])
        self.assertEqual(field_data["image"], POST["images"][0])
        self.assertEqual(field_data["images"], POST["images"])

    def test_build_field_data_fills_expected_webflow_fields_without_slug(self) -> None:
        collection = {
            "fields": [
                {"slug": "slug", "displayName": "Slug", "type": "PlainText", "isRequired": True},
                {"slug": "headline", "displayName": "Headline", "type": "PlainText"},
                {"slug": "post-summery", "displayName": "Post Summery", "type": "PlainText"},
                {"slug": "post-body", "displayName": "Post Body", "type": "RichText"},
                {"slug": "post-images", "displayName": "Post Images", "type": "MultiImage"},
                {"slug": "published-date", "displayName": "Published Date", "type": "DateTime"},
                {"slug": "linkedin-post-link", "displayName": "LinkedIn Post Link", "type": "Link"},
                {"slug": "author", "displayName": "Author", "type": "Reference"},
            ]
        }

        field_data = build_field_data(POST, collection)

        self.assertNotIn("slug", field_data)
        self.assertEqual(field_data["headline"], POST["headline"])
        self.assertEqual(field_data["post-summery"], POST["description"])
        self.assertEqual(field_data["post-body"], POST["content"])
        self.assertEqual(field_data["post-images"], POST["images"])
        self.assertEqual(field_data["published-date"], "2026-05-31T10:30:00Z")
        self.assertEqual(field_data["linkedin-post-link"], POST["url"])
        self.assertEqual(field_data["author"], AUTHOR_ITEM_ID)

    def test_build_field_data_uses_author_name_for_plain_text_author_field(self) -> None:
        collection = {
            "fields": [
                {"slug": "author", "displayName": "Author", "type": "PlainText"},
            ]
        }

        field_data = build_field_data(POST, collection)

        self.assertEqual(field_data["author"], AUTHOR_NAME)

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
