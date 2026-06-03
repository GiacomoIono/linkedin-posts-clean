from __future__ import annotations

import unittest
from unittest.mock import patch

from pipeline.linkedin import image_filename_sort_key
from pipeline.webflow import (
    AUTHOR_COLLECTION_ID,
    AUTHOR_ITEM_ID,
    WEBFLOW_PAYLOAD_VERSION,
    WebflowError,
    build_field_data,
    image_sequence,
    sync_post_to_webflow,
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

MULTI_IMAGE_POST = {
    **POST,
    "images": [
        {
            "url": "https://raw.githubusercontent.com/GiacomoIono/linkedin-posts-clean/refs/heads/main/images/2026-06-01_10.jpg",
            "alt": "Tenth image alt text",
        },
        {
            "url": "https://raw.githubusercontent.com/GiacomoIono/linkedin-posts-clean/refs/heads/main/images/2026-06-01_2.jpg",
            "alt": "Second image alt text",
        },
        {
            "url": "https://raw.githubusercontent.com/GiacomoIono/linkedin-posts-clean/refs/heads/main/images/2026-06-01_1.jpg",
            "alt": "First image alt text",
        },
    ],
}

SINGLE_DATE_IMAGE_POST = {
    **POST,
    "images": [
        {
            "url": "https://raw.githubusercontent.com/GiacomoIono/linkedin-posts-clean/refs/heads/main/images/2026-06-01.jpg",
            "alt": "Single image alt text",
        },
    ],
}


class WebflowPayloadTests(unittest.TestCase):
    def test_author_ids_match_webflow_author_collection(self) -> None:
        self.assertEqual(AUTHOR_COLLECTION_ID, "63250855178122e0e087d804")
        self.assertEqual(AUTHOR_ITEM_ID, "632508551781225a7587d893")

    def test_build_field_data_fills_exact_blog_post_fields_without_slug(self) -> None:
        field_data = build_field_data(POST)

        self.assertNotIn("slug", field_data)
        self.assertNotIn("headline", field_data)
        self.assertEqual(field_data["name"], POST["headline"])
        self.assertEqual(field_data["post-summary"], POST["description"])
        self.assertEqual(field_data["post-body"], POST["content"])
        self.assertEqual(field_data["post-images"], POST["images"])
        self.assertEqual(field_data["published-date"], "2026-05-31T10:30:00Z")
        self.assertEqual(field_data["linkedin-post-link"], POST["url"])
        self.assertEqual(field_data["author"], AUTHOR_ITEM_ID)

    def test_build_field_data_orders_post_images_and_reuses_first_image(self) -> None:
        field_data = build_field_data(MULTI_IMAGE_POST)

        expected_images = [
            {
                "url": "https://raw.githubusercontent.com/GiacomoIono/linkedin-posts-clean/refs/heads/main/images/2026-06-01_1.jpg",
                "alt": "First image alt text",
            },
            {
                "url": "https://raw.githubusercontent.com/GiacomoIono/linkedin-posts-clean/refs/heads/main/images/2026-06-01_2.jpg",
                "alt": "Second image alt text",
            },
            {
                "url": "https://raw.githubusercontent.com/GiacomoIono/linkedin-posts-clean/refs/heads/main/images/2026-06-01_10.jpg",
                "alt": "Tenth image alt text",
            },
        ]
        self.assertNotIn("slug", field_data)
        self.assertEqual(field_data["post-images"], expected_images)
        self.assertEqual(field_data["main-image"], expected_images[0])
        self.assertEqual(field_data["thumbnail-image"], expected_images[0])
        self.assertTrue(all("alt" in image for image in field_data["post-images"]))

    def test_build_field_data_handles_single_date_named_image(self) -> None:
        field_data = build_field_data(SINGLE_DATE_IMAGE_POST)

        expected_image = {
            "url": "https://raw.githubusercontent.com/GiacomoIono/linkedin-posts-clean/refs/heads/main/images/2026-06-01.jpg",
            "alt": "Single image alt text",
        }
        self.assertEqual(field_data["post-images"], [expected_image])
        self.assertEqual(field_data["main-image"], expected_image)
        self.assertEqual(field_data["thumbnail-image"], expected_image)
        self.assertIsNone(image_sequence(expected_image))

    def test_build_field_data_includes_only_known_optional_schema_fields(self) -> None:
        post = {
            **POST,
            "category": "category-id",
            "tags": ["tag-1", "tag-2"],
            "month": "month-id",
            "featured": True,
            "unknown-field": "ignored",
        }

        field_data = build_field_data(post)

        self.assertEqual(field_data["category"], "category-id")
        self.assertEqual(field_data["tags"], ["tag-1", "tag-2"])
        self.assertEqual(field_data["month"], "month-id")
        self.assertTrue(field_data["featured"])
        self.assertNotIn("unknown-field", field_data)

    def test_linkedin_date_named_image_is_not_treated_as_sequence(self) -> None:
        filenames = ["2026-06-01_2.jpg", "2026-06-01.jpg", "2026-06-01_1.jpg"]

        self.assertEqual(
            sorted(filenames, key=image_filename_sort_key),
            ["2026-06-01_1.jpg", "2026-06-01_2.jpg", "2026-06-01.jpg"],
        )

    def test_sync_skips_existing_live_item_without_using_local_state(self) -> None:
        class FakeClient:
            def __init__(self, _token, _collection_id):
                self.created = []
                self.updated = []
                self.updated_live = []
                self.published = []

            def update_item(self, item_id, field_data):
                self.updated.append((item_id, field_data))
                return {"id": item_id}

            def list_items(self):
                return []

            def list_live_items(self):
                return [{"id": "live-item", "fieldData": {"linkedin-post-link": POST["url"]}}]

            def update_live_item(self, item_id, field_data):
                self.updated_live.append((item_id, field_data))
                return {"id": item_id}

            def create_item(self, field_data):
                self.created.append(field_data)
                return {"id": "new-item"}

            def publish_item(self, item_id):
                self.published.append(item_id)

        config = type(
            "Config",
            (),
            {
                "webflow_api_token": "token",
                "webflow_collection_id": "collection",
                "webflow_publish": True,
                "force_webflow_sync": False,
            },
        )()
        state = {
            "items": {
                POST["url"]: {
                    "item_id": "deleted-staged-item",
                    "signature": "stale-signature",
                    "payload_version": WEBFLOW_PAYLOAD_VERSION - 1,
                    "published": True,
                }
            }
        }
        saved_states = []
        fake_client = FakeClient("token", "collection")

        with (
            patch("pipeline.webflow.WebflowClient", return_value=fake_client),
            patch("pipeline.webflow.load_webflow_state") as load_webflow_state,
            patch("pipeline.webflow.save_webflow_state", side_effect=saved_states.append),
        ):
            result = sync_post_to_webflow(POST, config)

        self.assertEqual(result, {"action": "skipped_existing_live_url", "item_id": "live-item", "published": True})
        load_webflow_state.assert_not_called()
        self.assertEqual(fake_client.updated, [])
        self.assertEqual(fake_client.updated_live, [])
        self.assertEqual(fake_client.created, [])
        self.assertEqual(fake_client.published, [])
        self.assertEqual(saved_states, [])

    def test_force_sync_updates_existing_live_item(self) -> None:
        class FakeClient:
            def __init__(self, _token, _collection_id):
                self.updated_live = []
                self.published = []

            def list_live_items(self):
                return [{"id": "live-item", "fieldData": {"linkedin-post-link": POST["url"]}}]

            def update_live_item(self, item_id, field_data):
                self.updated_live.append((item_id, field_data))
                return {"id": item_id}

            def publish_item(self, item_id):
                self.published.append(item_id)

        config = type(
            "Config",
            (),
            {
                "webflow_api_token": "token",
                "webflow_collection_id": "collection",
                "webflow_publish": True,
                "force_webflow_sync": True,
            },
        )()
        saved_states = []
        fake_client = FakeClient("token", "collection")

        with (
            patch("pipeline.webflow.WebflowClient", return_value=fake_client),
            patch("pipeline.webflow.load_webflow_state", return_value={"items": {}}),
            patch("pipeline.webflow.save_webflow_state", side_effect=saved_states.append),
        ):
            result = sync_post_to_webflow(POST, config)

        self.assertEqual(result, {"action": "updated_live", "item_id": "live-item", "published": True})
        self.assertEqual(fake_client.updated_live[0][0], "live-item")
        self.assertEqual(fake_client.updated_live[0][1]["linkedin-post-link"], POST["url"])
        self.assertEqual(fake_client.published, [])
        self.assertEqual(saved_states[0]["items"][POST["url"]]["item_id"], "live-item")

    def test_sync_recreates_item_when_live_leftover_cannot_be_updated(self) -> None:
        class FakeClient:
            def __init__(self, _token, _collection_id):
                self.unpublished = []
                self.created = []
                self.published = []

            def update_item(self, _item_id, _field_data):
                raise WebflowError("Webflow PATCH failed: 404 resource_not_found")

            def list_items(self):
                return []

            def list_live_items(self):
                return [{"id": "live-item", "fieldData": {"linkedin-post-link": POST["url"]}}]

            def update_live_item(self, _item_id, _field_data):
                raise WebflowError("Webflow PATCH live failed: 404 resource_not_found")

            def unpublish_live_item(self, item_id):
                self.unpublished.append(item_id)
                return {}

            def create_item(self, field_data):
                self.created.append(field_data)
                return {"id": "new-item"}

            def publish_item(self, item_id):
                self.published.append(item_id)

        config = type(
            "Config",
            (),
            {
                "webflow_api_token": "token",
                "webflow_collection_id": "collection",
                "webflow_publish": True,
                "force_webflow_sync": True,
            },
        )()
        state = {
            "items": {
                POST["url"]: {
                    "item_id": "deleted-staged-item",
                    "signature": "stale-signature",
                    "payload_version": WEBFLOW_PAYLOAD_VERSION - 1,
                    "published": True,
                }
            }
        }
        saved_states = []
        fake_client = FakeClient("token", "collection")

        with (
            patch("pipeline.webflow.WebflowClient", return_value=fake_client),
            patch("pipeline.webflow.load_webflow_state", return_value=state),
            patch("pipeline.webflow.save_webflow_state", side_effect=saved_states.append),
        ):
            result = sync_post_to_webflow(POST, config)

        self.assertEqual(result, {"action": "created", "item_id": "new-item", "published": True})
        self.assertEqual(fake_client.unpublished, ["live-item"])
        self.assertEqual(fake_client.created[0]["linkedin-post-link"], POST["url"])
        self.assertEqual(fake_client.published, ["new-item"])
        self.assertEqual(saved_states[0]["items"][POST["url"]]["item_id"], "new-item")

    def test_sync_explains_live_leftover_that_api_cannot_unpublish(self) -> None:
        class FakeClient:
            def __init__(self, _token, _collection_id):
                pass

            def update_item(self, _item_id, _field_data):
                raise WebflowError("Webflow PATCH failed: 404 resource_not_found")

            def list_items(self):
                return []

            def list_live_items(self):
                return [{"id": "live-item", "fieldData": {"linkedin-post-link": POST["url"]}}]

            def update_live_item(self, _item_id, _field_data):
                raise WebflowError("Webflow PATCH live failed: 404 resource_not_found")

            def unpublish_live_item(self, _item_id):
                raise WebflowError("Webflow DELETE live failed: 404 resource_not_found")

        config = type(
            "Config",
            (),
            {
                "webflow_api_token": "token",
                "webflow_collection_id": "collection",
                "webflow_publish": True,
                "force_webflow_sync": True,
            },
        )()
        state = {
            "items": {
                POST["url"]: {
                    "item_id": "deleted-staged-item",
                    "signature": "stale-signature",
                    "payload_version": WEBFLOW_PAYLOAD_VERSION - 1,
                    "published": True,
                }
            }
        }

        with (
            patch("pipeline.webflow.WebflowClient", return_value=FakeClient("token", "collection")),
            patch("pipeline.webflow.load_webflow_state", return_value=state),
        ):
            with self.assertRaisesRegex(WebflowError, "Publish the deletion in Webflow"):
                sync_post_to_webflow(POST, config)


if __name__ == "__main__":
    unittest.main()
