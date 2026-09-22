from __future__ import annotations

from copy import deepcopy
import hashlib
from io import BytesIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PIL import Image, PngImagePlugin
import requests

from pipeline.webflow import (
    WebflowClient,
    WebflowError,
    build_field_data,
    download_image_fingerprint,
    expected_image_digests,
    has_pending_verification,
    pending_verification_urls,
    save_webflow_state,
    sync_post_to_webflow,
    verify_saved_images,
    verify_saved_item,
    verify_saved_post_body,
)


def png_bytes(colour: str, *, label: str = "") -> bytes:
    output = BytesIO()
    metadata = PngImagePlugin.PngInfo()
    if label:
        metadata.add_text("Description", label)
    Image.new("RGB", (4, 3), colour).save(output, format="PNG", pnginfo=metadata)
    return output.getvalue()


FIRST_URL = "https://cdn.prod.website-files.com/site/upload-a.png"
SECOND_URL = "https://cdn.prod.website-files.com/site/upload-b.png"
FIRST_COPY_URL = "https://cdn.prod.website-files.com/site/copied-a.png"
SECOND_COPY_URL = "https://cdn.prod.website-files.com/site/copied-b.png"
FIRST_BYTES = png_bytes("red")
SECOND_BYTES = png_bytes("blue")
POST = {
    "content": "<p>A post with two images.</p>",
    "url": "https://www.linkedin.com/feed/update/urn:li:share:123",
    "published_at": "2026-09-08T12:00:00",
    "images": [
        {
            "url": FIRST_URL,
            "filename": "2026-09-08_1.png",
            "alt": "Same alt text",
            "sha256": hashlib.sha256(FIRST_BYTES).hexdigest(),
        },
        {
            "url": SECOND_URL,
            "filename": "2026-09-08_2.png",
            "alt": "Same alt text",
            "sha256": hashlib.sha256(SECOND_BYTES).hexdigest(),
        },
    ],
}


class ImageResponse:
    def __init__(self, url: str, data: bytes):
        self.url = url
        self.data = data

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def raise_for_status(self):
        return None

    def iter_content(self, *, chunk_size):
        for offset in range(0, len(self.data), chunk_size):
            yield self.data[offset : offset + chunk_size]


class FakeSyncClient:
    def __init__(self, staged_change=None, live_change=None):
        self.events = []
        self.fields = {}
        self.staged_change = staged_change
        self.live_change = live_change
        self.is_live = False
        self.written_payloads = []

    def list_items(self):
        return [{"id": "item", "fieldData": deepcopy(self.fields)}] if self.fields else []

    def list_live_items(self):
        return [{"id": "item", "fieldData": deepcopy(self.fields)}] if self.is_live else []

    def create_item(self, field_data):
        self.events.append("create")
        self.written_payloads.append(deepcopy(field_data))
        self.fields = deepcopy(field_data)
        return {"id": "item"}

    def get_item(self, item_id):
        self.events.append("staged-read")
        fields = deepcopy(self.fields)
        if self.staged_change:
            self.staged_change(fields)
        return {"id": item_id, "fieldData": fields}

    def publish_item(self, _item_id):
        self.events.append("publish")
        self.is_live = True

    def update_item(self, item_id, field_data):
        self.events.append("update-staged")
        self.written_payloads.append(deepcopy(field_data))
        self.fields.update(deepcopy(field_data))
        return {"id": item_id}

    def update_live_item(self, item_id, field_data):
        self.events.append("update-live")
        self.written_payloads.append(deepcopy(field_data))
        self.fields.update(deepcopy(field_data))
        return {"id": item_id}

    def get_live_item(self, item_id):
        self.events.append("live-read")
        if not self.is_live:
            raise WebflowError("Webflow GET failed: 404 resource_not_found")
        fields = deepcopy(self.fields)
        if self.live_change:
            self.live_change(fields)
        return {"id": item_id, "fieldData": fields}


class WebflowImageTests(unittest.TestCase):
    def setUp(self):
        self.downloads = []
        self.state = {"items": {}}
        self.saved_states = []
        self.save_state = Mock(side_effect=lambda state: self.saved_states.append(deepcopy(state)))
        state_load = patch("pipeline.webflow.load_webflow_state", return_value=self.state)
        state_save = patch("pipeline.webflow.save_webflow_state", new=self.save_state)
        state_load.start()
        state_save.start()
        self.addCleanup(state_load.stop)
        self.addCleanup(state_save.stop)
        self.image_bytes = {
            FIRST_URL: FIRST_BYTES,
            SECOND_URL: SECOND_BYTES,
            FIRST_COPY_URL: FIRST_BYTES,
            SECOND_COPY_URL: SECOND_BYTES,
        }
        self.session = Mock()
        self.session.__enter__ = Mock(return_value=self.session)
        self.session.__exit__ = Mock(return_value=False)
        self.session.get.side_effect = self.download
        session_patch = patch("pipeline.webflow.requests.Session", return_value=self.session)
        session_patch.start()
        self.addCleanup(session_patch.stop)
        self.expected = build_field_data(POST)

    def download(self, url, **_kwargs):
        self.downloads.append(url)
        return ImageResponse(url, self.image_bytes[url])

    def copied_fields(self):
        saved = deepcopy(self.expected)
        for image in saved["post-images"] + [saved["main-image"], saved["thumbnail-image"]]:
            image["url"] = {
                FIRST_URL: FIRST_COPY_URL,
                SECOND_URL: SECOND_COPY_URL,
                FIRST_COPY_URL: FIRST_COPY_URL,
            }[image["url"]]
            image["fileId"] = "webflow-new-file-id"
        return saved

    def run_sync(self, client, *, post=None, publish=True, force=False):
        config = SimpleNamespace(
            webflow_api_token="token",
            webflow_collection_id="collection",
            webflow_publish=publish,
            force_webflow_sync=force,
        )
        with (
            patch("pipeline.webflow.WebflowClient", return_value=client),
            patch("pipeline.webflow.load_webflow_state", return_value=self.state),
            patch("pipeline.webflow.save_webflow_state", new=self.save_state) as save_state,
            patch("pipeline.webflow.time.sleep"),
        ):
            result = sync_post_to_webflow(post or POST, config)
        return result, save_state

    def test_original_filename_keeps_gallery_order_after_cdn_renaming(self):
        post = {**POST, "images": [POST["images"][1], POST["images"][0]]}
        fields = build_field_data(post)
        self.assertEqual([image["url"] for image in fields["post-images"]], [FIRST_URL, SECOND_URL])
        self.assertEqual(fields["main-image"]["url"], FIRST_URL)
        self.assertEqual(fields["thumbnail-image"]["url"], FIRST_URL)
        self.assertTrue(all(set(image) == {"url", "alt"} for image in fields["post-images"]))

    def test_missing_source_urls_and_malformed_sources_fail_instead_of_disappearing(self):
        for sources in (None, {}, [None], [{"local_path": "images/image.jpg"}], [{"alt": "missing"}]):
            with self.subTest(sources=sources), self.assertRaises(WebflowError):
                build_field_data({**POST, "images": sources})

    def test_missing_generated_url_fails_instead_of_publishing_without_hero(self):
        with self.assertRaises(WebflowError):
            build_field_data({**POST, "images": [], "generated_main_image": {"local_path": "images/generated/hero.png"}})

    def test_private_repository_and_local_image_urls_never_reach_cms(self):
        urls = [
            "https://raw.githubusercontent.com/GiacomoIono/linkedin-posts-clean/main/images/a.png",
            "https://github.com/GiacomoIono/linkedin-posts-clean/blob/main/images/a.png?raw=true",
            "https://github.com/GiacomoIono/linkedin-posts-clean/raw/main/images/a.png",
            "file:///Users/example/image.png",
            "/Users/example/image.png",
            "images/a.png",
            "https://localhost/image.png",
            "https://user:password@example.com/image.png",
        ]
        client = WebflowClient("token", "collection")
        for url in urls:
            with self.subTest(url=url), patch.object(client, "request") as request:
                fields = {"main-image": {"url": url, "alt": ""}}
                for method in (
                    lambda: client.create_item(fields),
                    lambda: client.update_item("item", fields),
                    lambda: client.update_live_item("item", fields),
                ):
                    with self.assertRaises(WebflowError):
                        method()
                request.assert_not_called()

    def test_source_digest_accepts_copied_urls_and_new_file_ids_without_source_download(self):
        verify_saved_images(self.copied_fields(), self.expected, image_digests=expected_image_digests(POST))
        self.assertEqual(self.downloads, [FIRST_COPY_URL, SECOND_COPY_URL])
        self.assertFalse(self.session.trust_env)
        for call in self.session.get.call_args_list:
            self.assertEqual(call.kwargs, {"timeout": 30, "stream": True})

    def test_matching_urls_still_require_publicly_accessible_valid_images(self):
        self.image_bytes[FIRST_URL] = b"<html>Sign in required</html>"
        with self.assertRaisesRegex(WebflowError, "not a valid, decodable image"):
            verify_saved_images(self.expected, self.expected, image_digests=expected_image_digests(POST))

    def test_copied_url_with_identical_pixels_accepts_encoding_metadata_change(self):
        self.image_bytes[FIRST_COPY_URL] = png_bytes("red", label="copied by Webflow")
        self.assertNotEqual(self.image_bytes[FIRST_COPY_URL], FIRST_BYTES)
        verify_saved_images(self.copied_fields(), self.expected, image_digests=expected_image_digests(POST))
        self.assertEqual(self.downloads, [FIRST_COPY_URL, FIRST_URL, SECOND_COPY_URL])

    def test_copied_urls_without_optional_digest_compare_source_content(self):
        verify_saved_images(self.copied_fields(), self.expected)
        self.assertEqual(self.downloads, [FIRST_COPY_URL, FIRST_URL, SECOND_COPY_URL, SECOND_URL])

    def test_swapped_gallery_files_fail_even_when_count_and_alts_match(self):
        saved = self.copied_fields()
        saved["post-images"][0]["url"] = SECOND_COPY_URL
        saved["post-images"][1]["url"] = FIRST_COPY_URL
        with self.assertRaisesRegex(WebflowError, "image identity or order"):
            verify_saved_images(saved, self.expected, image_digests=expected_image_digests(POST))

    def test_omitted_source_image_and_changed_alt_text_fail(self):
        saved = self.copied_fields()
        saved["post-images"].pop()
        with self.assertRaisesRegex(WebflowError, "image count"):
            verify_saved_images(saved, self.expected)
        saved = self.copied_fields()
        saved["post-images"][0]["alt"] = "Unrequested replacement alt"
        with self.assertRaisesRegex(WebflowError, "alt text"):
            verify_saved_images(saved, self.expected)

    def test_main_and_thumbnail_must_keep_the_first_source_image(self):
        for key in ("main-image", "thumbnail-image"):
            saved = self.copied_fields()
            saved[key] = {"url": SECOND_COPY_URL, "alt": "Same alt text"}
            with self.subTest(field=key), self.assertRaisesRegex(WebflowError, "image identity or order"):
                verify_saved_images(saved, self.expected, image_digests=expected_image_digests(POST))

    def test_missing_main_or_thumbnail_fails(self):
        for key in ("main-image", "thumbnail-image"):
            saved = deepcopy(self.expected)
            saved.pop(key)
            with self.subTest(field=key), self.assertRaisesRegex(WebflowError, "omitted"):
                verify_saved_images(saved, self.expected)

    def test_generated_only_requires_main_and_absent_gallery_and_thumbnail(self):
        post = {**POST, "images": [], "generated_main_image": POST["images"][0]}
        expected = build_field_data(post)
        verify_saved_images(expected, expected, image_digests=expected_image_digests(post))
        for key in ("post-images", "thumbnail-image"):
            saved = deepcopy(expected)
            saved[key] = [POST["images"][1]] if key == "post-images" else POST["images"][1]
            with self.subTest(field=key), self.assertRaisesRegex(WebflowError, "unexpected"):
                verify_saved_images(saved, expected)

    def test_download_network_failure_empty_file_and_limits_fail_closed(self):
        self.session.get.side_effect = requests.ConnectionError("offline")
        with self.assertRaisesRegex(WebflowError, "could not be downloaded publicly"):
            download_image_fingerprint(FIRST_URL)
        self.session.get.side_effect = self.download
        self.image_bytes[FIRST_URL] = b""
        with self.assertRaisesRegex(WebflowError, "empty"):
            download_image_fingerprint(FIRST_URL)
        self.image_bytes[FIRST_URL] = FIRST_BYTES
        with patch("pipeline.webflow.MAX_READBACK_IMAGE_BYTES", 10), self.assertRaisesRegex(WebflowError, "size limit"):
            download_image_fingerprint(FIRST_URL)
        with patch("pipeline.webflow.MAX_READBACK_IMAGE_PIXELS", 1), self.assertRaisesRegex(WebflowError, "pixel limit"):
            download_image_fingerprint(FIRST_URL)

    def test_invalid_and_conflicting_source_fingerprints_fail(self):
        for images in (
            [{**POST["images"][0], "sha256": "invalid"}],
            [POST["images"][0], {**POST["images"][0], "sha256": "0" * 64}],
        ):
            with self.subTest(images=images), self.assertRaises(WebflowError):
                expected_image_digests({**POST, "images": images})

    def test_changed_source_cannot_override_the_trusted_upload_fingerprint(self):
        self.image_bytes[FIRST_URL] = SECOND_BYTES
        saved = self.copied_fields()
        saved["post-images"][0]["url"] = SECOND_COPY_URL
        with self.assertRaisesRegex(WebflowError, "source fingerprint changed"):
            verify_saved_images(saved, self.expected, image_digests=expected_image_digests(POST))

    def test_staged_image_failure_stops_before_publish_and_success_state(self):
        client = FakeSyncClient(staged_change=lambda fields: fields["post-images"].pop())
        with self.assertRaisesRegex(WebflowError, "image count"):
            self.run_sync(client)
        self.assertEqual(client.events, ["create", "staged-read"])
        entry = self.state["items"][POST["url"]]
        self.assertEqual(entry["verification_pending"]["location"], "staged")
        self.assertNotIn("signature", entry)

    def test_staged_and_live_checks_download_each_unique_image_once(self):
        client = FakeSyncClient()
        result, save_state = self.run_sync(client)
        self.assertTrue(result["read_back_verified"])
        self.assertEqual(client.events, ["create", "staged-read", "publish", "live-read"])
        self.assertEqual(self.downloads, [FIRST_URL, SECOND_URL])
        self.assertEqual(save_state.call_count, 3)
        self.assertNotIn("verification_pending", self.state["items"][POST["url"]])

    def test_live_omissions_are_retried_and_never_recorded_as_verified(self):
        client = FakeSyncClient(live_change=lambda fields: fields["post-images"].pop())
        with self.assertRaisesRegex(WebflowError, "failed after 3 attempts.*image count"):
            self.run_sync(client)
        self.assertEqual(client.events.count("live-read"), 3)
        entry = self.state["items"][POST["url"]]
        self.assertEqual(entry["verification_pending"]["location"], "live")
        self.assertNotIn("signature", entry)

    def test_nonpublishing_run_still_requires_full_staged_verification(self):
        client = FakeSyncClient()
        result, _ = self.run_sync(client, publish=False)
        self.assertFalse(result["published"])
        self.assertTrue(result["read_back_verified"])
        self.assertEqual(client.events, ["create", "staged-read"])
        self.assertEqual(self.downloads, [FIRST_URL, SECOND_URL])

    def test_legacy_body_only_verifier_preserves_exact_html_contract(self):
        client = Mock()
        client.get_item.return_value = {"id": "item", "fieldData": {"post-body": POST["content"]}}
        verify_saved_post_body(client, "item", POST["content"], live=False)
        self.assertEqual(self.downloads, [])
        with self.assertRaisesRegex(WebflowError, "post body exactly"):
            verify_saved_post_body(client, "item", POST["content"] + " ", live=False)

    def test_live_retry_discards_wrong_bytes_and_accepts_the_fixed_download(self):
        client = Mock()
        client.get_live_item.return_value = {"id": "item", "fieldData": self.copied_fields()}

        def transient_download(url, **kwargs):
            if url == FIRST_COPY_URL:
                self.image_bytes[url] = SECOND_BYTES if url not in self.downloads else FIRST_BYTES
            return self.download(url, **kwargs)

        self.session.get.side_effect = transient_download
        cache = {}
        with patch("pipeline.webflow.time.sleep"):
            verify_saved_item(client, "item", self.expected, live=True, image_digests=expected_image_digests(POST), image_cache=cache)
        self.assertEqual(client.get_live_item.call_count, 2)
        self.assertEqual(self.downloads.count(FIRST_COPY_URL), 2)
        self.assertEqual(cache[FIRST_COPY_URL][0], hashlib.sha256(FIRST_BYTES).hexdigest())

    def test_failed_live_verification_recovers_read_only_next_run_without_forcing_sync(self):
        client = FakeSyncClient(live_change=lambda fields: fields["post-images"].pop())
        with self.assertRaises(WebflowError):
            self.run_sync(client)
        intended_signature = self.state["items"][POST["url"]]["verification_pending"]["signature"]
        client.live_change = None
        client.events.clear()
        result, _ = self.run_sync(client, post={"url": POST["url"], "content": "Incoming raw body is not the retry payload."})
        self.assertEqual(result, {"action": "verified_pending", "item_id": "item", "published": True, "read_back_verified": True})
        self.assertEqual(client.events, ["live-read"])
        self.assertEqual(len(client.written_payloads), 1)
        self.assertEqual(self.state["items"][POST["url"]]["signature"], intended_signature)
        self.assertNotIn("verification_pending", self.state["items"][POST["url"]])

    def test_pending_gallery_mismatch_repairs_the_same_item_without_creating_another(self):
        client = FakeSyncClient(live_change=lambda fields: fields["post-images"].pop())
        with self.assertRaises(WebflowError):
            self.run_sync(client)
        client.live_change = None
        client.fields["post-images"].pop()
        client.events.clear()
        result, _ = self.run_sync(client, post={"url": POST["url"]})
        self.assertEqual(result["action"], "repaired_pending")
        self.assertEqual(result["item_id"], "item")
        self.assertEqual(client.events.count("update-staged"), 1)
        self.assertEqual(client.events.count("publish"), 1)
        self.assertNotIn("create", client.events)
        self.assertEqual(client.fields["post-images"], self.expected["post-images"])
        self.assertNotIn("verification_pending", self.state["items"][POST["url"]])

    def test_pending_network_failure_keeps_intent_without_rewriting_or_publishing(self):
        client = FakeSyncClient(live_change=lambda fields: fields["post-images"].pop())
        with self.assertRaises(WebflowError):
            self.run_sync(client)
        client.live_change = None
        client.events.clear()
        pending_before = deepcopy(self.state)
        self.session.get.side_effect = requests.ConnectionError("temporary outage")
        with self.assertRaisesRegex(WebflowError, "could not be downloaded publicly"):
            self.run_sync(client, post={"url": POST["url"]})
        self.assertEqual(self.state, pending_before)
        self.assertEqual(client.events, ["live-read"] * 3)

    def test_staged_pending_verifies_then_finishes_intended_publish_without_rewrite(self):
        client = FakeSyncClient(staged_change=lambda fields: fields["post-images"].pop())
        with self.assertRaises(WebflowError):
            self.run_sync(client)
        client.staged_change = None
        client.events.clear()
        result, _ = self.run_sync(client, post={"url": POST["url"]})
        self.assertEqual(result["action"], "verified_pending")
        self.assertTrue(result["published"])
        self.assertEqual(client.events, ["staged-read", "publish", "live-read"])
        self.assertEqual(len(client.written_payloads), 1)

    def test_publish_failure_leaves_live_intent_and_retry_republishes_correct_staged_item(self):
        client = FakeSyncClient()
        real_publish = client.publish_item

        def failed_publish(_item_id):
            pending = self.state["items"][POST["url"]]["verification_pending"]
            self.assertEqual(pending["location"], "live")
            self.assertEqual(pending["expected_fields"], self.expected)
            raise WebflowError("Webflow POST failed: 503 temporary outage")

        client.publish_item = failed_publish
        with self.assertRaisesRegex(WebflowError, "503"):
            self.run_sync(client)
        self.assertEqual(self.saved_states[-1]["items"][POST["url"]]["verification_pending"]["location"], "live")
        client.publish_item = real_publish
        client.events.clear()
        result, _ = self.run_sync(client, post={"url": POST["url"]})
        self.assertTrue(result["read_back_verified"])
        self.assertEqual(client.events.count("publish"), 1)
        self.assertNotIn("update-staged", client.events)
        self.assertEqual(len(client.written_payloads), 1)

    def test_failed_live_patch_has_a_checkpoint_before_the_request(self):
        client = FakeSyncClient()
        client.fields = deepcopy(self.expected)
        client.is_live = True

        def failed_patch(item_id, field_data):
            pending = self.state["items"][POST["url"]]["verification_pending"]
            self.assertEqual(pending["item_id"], item_id)
            self.assertEqual(pending["expected_fields"], field_data)
            self.assertEqual(pending["location"], "live")
            raise WebflowError("Webflow PATCH failed: 403 permission denied")

        client.update_live_item = failed_patch
        with self.assertRaisesRegex(WebflowError, "403"):
            self.run_sync(client, force=True)
        self.assertIn("verification_pending", self.state["items"][POST["url"]])

    def test_pending_verification_does_not_skip_unapplied_metadata_changes(self):
        client = FakeSyncClient(live_change=lambda fields: fields["post-images"].pop())
        with self.assertRaises(WebflowError):
            self.run_sync(client)
        client.live_change = None
        client.fields["post-summary"] = "A value different from the saved write."
        client.events.clear()
        result, _ = self.run_sync(client, post={"url": POST["url"]})
        self.assertEqual(result["action"], "repaired_pending")
        self.assertEqual(client.fields["post-summary"], self.expected["post-summary"])

    def test_generated_only_patch_preserves_existing_gallery_and_thumbnail(self):
        post = {**POST, "images": [], "generated_main_image": POST["images"][1]}
        for live in (False, True):
            with self.subTest(live=live):
                self.state = {"items": {}}
                client = FakeSyncClient()
                client.fields = deepcopy(self.expected)
                client.is_live = live
                result, _ = self.run_sync(client, post=post, publish=False, force=live)
                self.assertTrue(result["read_back_verified"])
                self.assertEqual(client.fields["post-images"], self.expected["post-images"])
                self.assertEqual(client.fields["thumbnail-image"], self.expected["thumbnail-image"])
                self.assertEqual(client.fields["main-image"]["url"], SECOND_URL)
                self.assertNotIn("post-images", client.written_payloads[-1])
                self.assertNotIn("thumbnail-image", client.written_payloads[-1])

    def test_generated_only_create_keeps_gallery_and_thumbnail_empty(self):
        post = {**POST, "images": [], "generated_main_image": POST["images"][1]}
        client = FakeSyncClient()
        result, _ = self.run_sync(client, post=post)
        self.assertTrue(result["read_back_verified"])
        self.assertNotIn("post-images", client.fields)
        self.assertNotIn("thumbnail-image", client.fields)

    def test_pending_lookup_matches_source_and_rejects_wrong_collection_or_malformed_intent(self):
        client = FakeSyncClient(staged_change=lambda fields: fields["post-images"].pop())
        with self.assertRaises(WebflowError):
            self.run_sync(client)
        with patch("pipeline.webflow.load_webflow_state", return_value=self.state):
            self.assertTrue(has_pending_verification(SimpleNamespace(webflow_collection_id="collection"), POST["url"]))
            self.assertFalse(has_pending_verification(SimpleNamespace(webflow_collection_id="collection"), "https://linkedin.com/unrelated"))
            with self.assertRaisesRegex(WebflowError, "another collection"):
                has_pending_verification(SimpleNamespace(webflow_collection_id="different-collection"), POST["url"])
            self.state["items"][POST["url"]]["verification_pending"] = {}
            with self.assertRaises(WebflowError):
                has_pending_verification(SimpleNamespace(webflow_collection_id="collection"), POST["url"])

    def test_state_checkpoint_is_atomic_when_writing_the_replacement_fails(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "webflow_items.json"
            initial = {"items": {"saved": {"signature": "original"}}}
            with patch("pipeline.webflow.WEBFLOW_STATE_PATH", path):
                save_webflow_state(initial)
                with patch("pipeline.webflow.write_json", side_effect=OSError("disk full")):
                    with self.assertRaises(OSError):
                        save_webflow_state({"items": {"replacement": {}}})
            self.assertEqual(json.loads(path.read_text()), initial)
            self.assertEqual(list(Path(directory).glob("*.tmp")), [])

    def test_pending_sweep_includes_old_posts_and_validates_every_saved_intent(self):
        client = FakeSyncClient(staged_change=lambda fields: fields["post-images"].pop())
        with self.assertRaises(WebflowError):
            self.run_sync(client)
        older_url = "https://www.linkedin.com/feed/update/urn:li:share:older-post"
        older_entry = deepcopy(self.state["items"][POST["url"]])
        older_entry["verification_pending"]["expected_fields"]["linkedin-post-link"] = older_url
        self.state["items"][older_url] = older_entry
        self.state["items"]["https://linkedin.com/completed"] = {"signature": "already-done"}
        config = SimpleNamespace(webflow_collection_id="collection")
        with patch("pipeline.webflow.load_webflow_state", return_value=self.state):
            self.assertEqual(pending_verification_urls(config), [POST["url"], older_url])
            self.state["items"][older_url]["verification_pending"]["collection_id"] = "wrong-collection"
            with self.assertRaisesRegex(WebflowError, "another collection"):
                pending_verification_urls(config)

    def test_pending_missing_id_rebinds_unique_same_source_replacement_without_rewriting(self):
        client = FakeSyncClient(live_change=lambda fields: fields["post-images"].pop())
        with self.assertRaises(WebflowError):
            self.run_sync(client)
        pending = self.state["items"][POST["url"]]["verification_pending"]
        pending["item_id"] = "missing-old-item"
        intended_fields = deepcopy(pending["expected_fields"])
        client.live_change = None
        original_get = client.get_item
        original_get_live = client.get_live_item

        def staged_get(item_id):
            if item_id == "missing-old-item":
                raise WebflowError("Webflow GET failed: 404 resource_not_found")
            return original_get(item_id)

        def live_get(item_id):
            if item_id == "missing-old-item":
                raise WebflowError("Webflow GET failed: 404 resource_not_found")
            return original_get_live(item_id)

        client.get_item = staged_get
        client.get_live_item = live_get
        client.events.clear()
        result, _ = self.run_sync(client, post={"url": POST["url"]})
        self.assertEqual(result["action"], "verified_pending")
        self.assertEqual(result["item_id"], "item")
        self.assertEqual(client.fields, intended_fields)
        self.assertEqual(len(client.written_payloads), 1)
        self.assertNotIn("publish", client.events)
        self.assertNotIn("verification_pending", self.state["items"][POST["url"]])

    def test_missing_pending_id_with_zero_or_ambiguous_replacements_never_creates_or_repairs(self):
        client = FakeSyncClient(staged_change=lambda fields: fields["post-images"].pop())
        with self.assertRaises(WebflowError):
            self.run_sync(client)
        initial_state = deepcopy(self.state)
        for matches, pattern in (([], "no replacement"), ([{"id": "one", "fieldData": self.expected}, {"id": "two", "fieldData": self.expected}], "ambiguous")):
            with self.subTest(matches=matches):
                self.state = deepcopy(initial_state)
                self.state["items"][POST["url"]]["verification_pending"]["item_id"] = "missing"
                fake = Mock()
                fake.get_item.side_effect = WebflowError("Webflow GET failed: 404 resource_not_found")
                fake.get_live_item.side_effect = WebflowError("Webflow GET failed: 404 resource_not_found")
                fake.list_items.return_value = matches
                fake.list_live_items.return_value = []
                with self.assertRaisesRegex(WebflowError, pattern):
                    self.run_sync(fake, post={"url": POST["url"]})
                fake.create_item.assert_not_called()
                fake.update_item.assert_not_called()
                fake.update_live_item.assert_not_called()
                fake.publish_item.assert_not_called()
                self.assertEqual(self.state["items"][POST["url"]]["verification_pending"]["item_id"], "missing")

    def test_pending_existing_id_with_different_linkedin_url_is_never_repaired(self):
        client = FakeSyncClient(live_change=lambda fields: fields["post-images"].pop())
        with self.assertRaises(WebflowError):
            self.run_sync(client)
        client.live_change = None
        client.fields["linkedin-post-link"] = "https://linkedin.com/unrelated-post"
        client.events.clear()
        with self.assertRaisesRegex(WebflowError, "different item ID or LinkedIn URL"):
            self.run_sync(client, post={"url": POST["url"]})
        self.assertNotIn("update-staged", client.events)
        self.assertNotIn("update-live", client.events)
        self.assertEqual(len(client.written_payloads), 1)

    def test_lookup_rejects_partial_or_duplicate_pages_before_claiming_unique_source(self):
        client = WebflowClient("token", "collection")
        for responses in (
            [{"items": [{"id": "one"}]}],
            [{"items": [{"id": "one"}], "pagination": {"offset": 0, "limit": 100, "total": 2}}, {"items": [], "pagination": {"offset": 1, "limit": 100, "total": 2}}],
            [{"items": [{"id": "one"}, {"id": "one"}], "pagination": {"offset": 0, "limit": 100, "total": 2}}],
        ):
            with self.subTest(responses=responses), patch.object(client, "request", side_effect=responses):
                with self.assertRaises(WebflowError):
                    client.list_items()


if __name__ == "__main__":
    unittest.main()
