from __future__ import annotations

import unittest
from unittest.mock import call, patch

from pipeline.linkedin import image_filename_sort_key
from pipeline.webflow import (
    AUTHOR_COLLECTION_ID,
    AUTHOR_ITEM_ID,
    WEBFLOW_PAYLOAD_VERSION,
    WebflowClient,
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

GENERATED_MAIN_IMAGE_POST = {
    **POST,
    "images": [],
    "generated_main_image": {
        "url": "https://raw.githubusercontent.com/GiacomoIono/linkedin-posts-clean/abc123/"
        "images/generated/2026-05-31-hello-from-linkedin-a1b2c3d4e5.png",
        "alt": "Editorial illustration representing Hello from LinkedIn",
    },
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

    def test_build_field_data_passes_verified_linked_html_to_post_body_only(self) -> None:
        linked_html = (
            '<p>Revenue reached <a href="https://example.org/reports/revenue-2024">'
            "$10 billion in 2024</a>.</p>"
        )
        linked_post = {**POST, "content": linked_html}

        original_fields = build_field_data(POST)
        linked_fields = build_field_data(linked_post)

        self.assertEqual(linked_fields["post-body"], linked_html)
        self.assertEqual(
            {key: value for key, value in linked_fields.items() if key != "post-body"},
            {key: value for key, value in original_fields.items() if key != "post-body"},
        )

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

    def test_generated_fallback_is_sent_to_main_image_only(self) -> None:
        field_data = build_field_data(GENERATED_MAIN_IMAGE_POST)

        self.assertEqual(
            field_data["main-image"], GENERATED_MAIN_IMAGE_POST["generated_main_image"]
        )
        self.assertNotIn("post-images", field_data)
        self.assertNotIn("thumbnail-image", field_data)

    def test_generated_main_image_must_not_be_silently_skipped(self) -> None:
        field_data = build_field_data(GENERATED_MAIN_IMAGE_POST)
        client = WebflowClient("token", "collection")

        with patch.object(client, "request", return_value={}) as request:
            client.create_item(field_data)
            client.update_item("item-id", field_data)
            client.update_live_item("item-id", field_data)

        self.assertEqual(request.call_count, 3)
        self.assertTrue(
            all(
                call.kwargs["params"]["skipInvalidFiles"] == "false"
                for call in request.call_args_list
            )
        )

    def test_source_images_keep_existing_lenient_file_handling(self) -> None:
        field_data = build_field_data(POST)
        client = WebflowClient("token", "collection")

        with patch.object(client, "request", return_value={}) as request:
            client.create_item(field_data)

        self.assertEqual(request.call_args.kwargs["params"]["skipInvalidFiles"], "true")

    def test_read_back_uses_the_staged_and_live_item_endpoints(self) -> None:
        client = WebflowClient("token", "collection")

        with patch.object(client, "request", return_value={"id": "item-id"}) as request:
            client.get_item("item-id")
            client.get_live_item("item-id")

        self.assertEqual(
            [call.args for call in request.call_args_list],
            [
                ("GET", "/collections/collection/items/item-id"),
                ("GET", "/collections/collection/items/item-id/live"),
            ],
        )

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
                return [
                    {
                        "id": "live-item",
                        "fieldData": {"linkedin-post-link": POST["url"]},
                    }
                ]

            def update_live_item(self, item_id, field_data):
                self.updated_live.append((item_id, field_data))
                return {"id": item_id}

            def get_item(self, item_id):
                return {"id": item_id, "fieldData": {"post-body": POST["content"]}}

            def get_live_item(self, item_id):
                return {"id": item_id, "fieldData": {"post-body": POST["content"]}}

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
        saved_states = []
        fake_client = FakeClient("token", "collection")

        with (
            patch("pipeline.webflow.WebflowClient", return_value=fake_client),
            patch("pipeline.webflow.load_webflow_state") as load_webflow_state,
            patch(
                "pipeline.webflow.save_webflow_state", side_effect=saved_states.append
            ),
        ):
            result = sync_post_to_webflow(POST, config)

        self.assertEqual(
            result,
            {
                "action": "skipped_existing_live_url",
                "item_id": "live-item",
                "published": True,
            },
        )
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
                self.staged_reads = 0
                self.live_reads = 0

            def list_live_items(self):
                return [
                    {
                        "id": "live-item",
                        "fieldData": {"linkedin-post-link": POST["url"]},
                    }
                ]

            def update_live_item(self, item_id, field_data):
                self.updated_live.append((item_id, field_data))
                return {"id": item_id}

            def get_item(self, item_id):
                self.staged_reads += 1
                return {"id": item_id, "fieldData": {"post-body": POST["content"]}}

            def get_live_item(self, item_id):
                self.live_reads += 1
                return {"id": item_id, "fieldData": {"post-body": POST["content"]}}

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
            patch(
                "pipeline.webflow.save_webflow_state", side_effect=saved_states.append
            ),
        ):
            result = sync_post_to_webflow(POST, config)

        self.assertEqual(
            result,
            {
                "action": "updated_live",
                "item_id": "live-item",
                "published": True,
                "read_back_verified": True,
            },
        )
        self.assertEqual(fake_client.updated_live[0][0], "live-item")
        self.assertEqual(
            fake_client.updated_live[0][1]["linkedin-post-link"], POST["url"]
        )
        self.assertEqual(fake_client.published, [])
        self.assertEqual(fake_client.staged_reads, 0)
        self.assertEqual(fake_client.live_reads, 1)
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
                return [
                    {
                        "id": "live-item",
                        "fieldData": {"linkedin-post-link": POST["url"]},
                    }
                ]

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

            def get_item(self, item_id):
                return {"id": item_id, "fieldData": {"post-body": POST["content"]}}

            def get_live_item(self, item_id):
                return {"id": item_id, "fieldData": {"post-body": POST["content"]}}

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
            patch(
                "pipeline.webflow.save_webflow_state", side_effect=saved_states.append
            ),
        ):
            result = sync_post_to_webflow(POST, config)

        self.assertEqual(
            result,
            {
                "action": "created",
                "item_id": "new-item",
                "published": True,
                "read_back_verified": True,
            },
        )
        self.assertEqual(fake_client.unpublished, ["live-item"])
        self.assertEqual(fake_client.created[0]["linkedin-post-link"], POST["url"])
        self.assertEqual(fake_client.published, ["new-item"])
        self.assertEqual(saved_states[0]["items"][POST["url"]]["item_id"], "new-item")

    def test_sync_stops_before_publish_and_state_when_staged_body_read_back_differs(self) -> None:
        class FakeClient:
            def __init__(self, _token, _collection_id):
                self.published = []

            def list_live_items(self):
                return []

            def list_items(self):
                return []

            def create_item(self, _field_data):
                return {"id": "new-item"}

            def get_item(self, item_id):
                return {
                    "id": item_id,
                    "fieldData": {"post-body": "<p>Webflow changed the body.</p>"},
                }

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
        fake_client = FakeClient("token", "collection")

        with (
            patch("pipeline.webflow.WebflowClient", return_value=fake_client),
            patch("pipeline.webflow.load_webflow_state", return_value={"items": {}}),
            patch("pipeline.webflow.save_webflow_state") as save_state,
            self.assertRaisesRegex(WebflowError, "did not preserve the verified post body"),
        ):
            sync_post_to_webflow(POST, config)

        self.assertEqual(fake_client.published, [])
        save_state.assert_not_called()

    def test_sync_retries_live_read_back_after_publish(self) -> None:
        class FakeClient:
            def __init__(self, _token, _collection_id):
                self.live_reads = 0

            def list_live_items(self):
                return []

            def list_items(self):
                return []

            def create_item(self, _field_data):
                return {"id": "new-item"}

            def get_item(self, item_id):
                return {"id": item_id, "fieldData": {"post-body": POST["content"]}}

            def publish_item(self, _item_id):
                return {}

            def get_live_item(self, item_id):
                self.live_reads += 1
                body = (
                    "<p>Stale live body.</p>"
                    if self.live_reads == 1
                    else POST["content"]
                )
                return {"id": item_id, "fieldData": {"post-body": body}}

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
        fake_client = FakeClient("token", "collection")

        with (
            patch("pipeline.webflow.WebflowClient", return_value=fake_client),
            patch("pipeline.webflow.load_webflow_state", return_value={"items": {}}),
            patch("pipeline.webflow.save_webflow_state"),
            patch("pipeline.webflow.time.sleep") as sleep,
        ):
            result = sync_post_to_webflow(POST, config)

        self.assertTrue(result["read_back_verified"])
        self.assertEqual(fake_client.live_reads, 2)
        sleep.assert_called_once_with(1)

    def test_sync_explains_live_leftover_that_api_cannot_unpublish(self) -> None:
        class FakeClient:
            def __init__(self, _token, _collection_id):
                pass

            def update_item(self, _item_id, _field_data):
                raise WebflowError("Webflow PATCH failed: 404 resource_not_found")

            def list_items(self):
                return []

            def list_live_items(self):
                return [
                    {
                        "id": "live-item",
                        "fieldData": {"linkedin-post-link": POST["url"]},
                    }
                ]

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
            patch(
                "pipeline.webflow.WebflowClient",
                return_value=FakeClient("token", "collection"),
            ),
            patch("pipeline.webflow.load_webflow_state", return_value=state),
            self.assertRaisesRegex(WebflowError, "Publish the deletion in Webflow"),
        ):
            sync_post_to_webflow(POST, config)


if __name__ == "__main__":
    unittest.main()
