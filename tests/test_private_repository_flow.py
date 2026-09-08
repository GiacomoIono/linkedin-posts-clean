"""Cross-module publishing contracts, with only external services replaced.

The test drives the real LinkedIn parser, local-file discovery, main orchestration,
Webflow Assets uploader, OpenAI enrichment adapter, CMS writer and read-back checks.
Every Requests session is intercepted before network access; unfamiliar URLs fail.
"""

from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from contextlib import ExitStack, redirect_stdout
from datetime import datetime, timezone
from io import BytesIO, StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import urlsplit

import requests
from PIL import Image

from pipeline import config, enrichment, generated_images, image_assets
from pipeline import image_generation, linkedin, main as pipeline_main, prepare_image, webflow, webflow_assets

SITE_ID = "111111111111111111111111"
COLLECTION_ID = "222222222222222222222222"
ITEM_ID = "333333333333333333333333"
POST_URL = "https://www.linkedin.com/feed/update/urn:li:ugcPost:9999999999999999999"
SOURCE_TEXT = "AI changes how customers discover products, creating new paths that marketers must understand."
SEO = {
    "headline": "Can marketers still measure the AI customer journey?",
    "description": "AI creates new paths to purchase, making attribution harder and forcing marketers to rethink how they measure influence.",
}


def image_bytes(colour: str, *, image_format: str = "PNG", size: tuple[int, int] = (64, 36)) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", size, colour).save(buffer, format=image_format)
    return buffer.getvalue()


def response(url: str, *, payload: dict | None = None, content: bytes = b"", status: int = 200) -> requests.Response:
    result = requests.Response()
    result.status_code = status
    result.url = url
    result.encoding = "utf-8"
    result._content = json.dumps(payload).encode() if payload is not None else content
    result._content_consumed = True
    result.headers["Content-Type"] = "application/json" if payload is not None else "image/png"
    return result


class FakeServices:
    """A stateful HTTP boundary; none of the pipeline's internal stages are faked."""

    def __init__(self, testcase: unittest.TestCase, captured_at: int) -> None:
        self.test = testcase
        self.captured_at = captured_at
        self.events: list[tuple[str, str]] = []
        self.assets: dict[str, dict] = {}
        self.public_files: dict[str, bytes] = {}
        self.verified_urls: set[str] = set()
        self.alt_inputs: list[str] = []
        self.alts: dict[str, str] = {}
        self.asset_creates = 0
        self.file_uploads = 0
        self.publish_calls = 0
        self.cms_writes: list[dict] = []
        self.item: dict | None = None
        self.live_item: dict | None = None
        self.corrupt_main_readback = False
        self.fail_live_readback = False
        self.no_recent_post = False
        self.fail_next_enrichment = False
        self.client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=self.seo)),
            responses=SimpleNamespace(create=self.alt),
        )

    def reject_repository_url(self, url: str) -> None:
        parsed = urlsplit(url)
        self.test.assertFalse(
            parsed.hostname in {"raw.githubusercontent.com", "github.com", "www.github.com", "media.githubusercontent.com"}
            and "/giacomoiono/linkedin-posts-clean/" in parsed.path.lower(),
            f"Private repository image URL escaped into an external request: {url}",
        )

    def linkedin_element(self) -> dict:
        return {
            "resourceName": "ugcPosts", "method": "CREATE",
            "resourceId": POST_URL.rsplit("/", 1)[-1], "capturedAt": self.captured_at,
            "processedAt": self.captured_at,
            "activity": {"specificContent": {"com.linkedin.ugc.ShareContent": {
                "shareCommentary": {"text": SOURCE_TEXT}, "shareMediaCategory": "NONE",
            }}},
        }

    def request(self, session: requests.Session, method: str, url: str, **kwargs) -> requests.Response:
        method = method.upper()
        self.reject_repository_url(url)
        self.events.append((method, url))
        parsed = urlsplit(url)
        if url == linkedin.LINKEDIN_CHANGE_LOG_URL and method == "GET":
            return response(url, payload={"elements": [] if self.no_recent_post else [self.linkedin_element()]})
        if parsed.hostname == "api.webflow.com":
            self.test.assertEqual(kwargs["headers"]["Authorization"], "Bearer fake-webflow-token")
            endpoint = parsed.path.removeprefix("/v2")
            if endpoint == f"/sites/{SITE_ID}/assets" and method == "POST":
                self.asset_creates += 1
                asset_id = f"{self.asset_creates:024x}"
                filename = kwargs["json"]["fileName"]
                asset_url = f"https://cdn.prod.website-files.com/{SITE_ID}/{asset_id}_{filename}"
                asset = {
                    "id": asset_id, "siteId": SITE_ID, "hostedUrl": asset_url,
                    "originalFileName": filename,
                    "uploadUrl": "https://s3.amazonaws.com/webflow-prod-assets",
                    "uploadDetails": {"key": f"{SITE_ID}/{asset_id}_{filename}", "policy": "fake-temporary-policy"},
                    "expected_md5": kwargs["json"]["fileHash"],
                }
                self.assets[asset_id] = asset
                return response(url, payload=asset)
            if endpoint.startswith("/assets/") and method == "GET":
                asset_id = endpoint.rsplit("/", 1)[-1]
                self.test.assertIn(asset_id, self.assets)
                return response(url, payload=self.assets[asset_id])
            base = f"/collections/{COLLECTION_ID}/items"
            if endpoint in {base, base + "/live"} and method == "GET":
                item = self.live_item if endpoint.endswith("/live") else self.item
                items = [copy.deepcopy(item)] if item else []
                offset = kwargs["params"]["offset"]
                limit = kwargs["params"]["limit"]
                return response(url, payload={
                    "items": items[offset:offset + limit],
                    "pagination": {"total": len(items), "offset": offset, "limit": limit},
                })
            if endpoint == base and method == "POST":
                self.test.assertEqual(kwargs["params"], {"skipInvalidFiles": "false"})
                fields = copy.deepcopy(kwargs["json"]["fieldData"])
                self.cms_writes.append(fields)
                self.item = {"id": ITEM_ID, "fieldData": self.cms_copy_images(fields)}
                return response(url, payload=self.item)
            if endpoint == base + "/publish" and method == "POST":
                self.test.assertEqual(kwargs["json"], {"itemIds": [ITEM_ID]})
                self.publish_calls += 1
                self.live_item = copy.deepcopy(self.item)
                return response(url, payload={"publishedItemIds": [ITEM_ID]})
            if endpoint in {base + f"/{ITEM_ID}", base + f"/{ITEM_ID}/live"} and method == "GET":
                if endpoint.endswith("/live") and self.fail_live_readback:
                    return response(url, payload={"error": "simulated read-back outage"}, status=503)
                saved = copy.deepcopy(self.live_item if endpoint.endswith("/live") else self.item)
                self.test.assertIsNotNone(saved)
                if self.corrupt_main_readback:
                    wrong_url = f"https://cdn.prod.website-files.com/{SITE_ID}/incorrect-main.png"
                    self.public_files[wrong_url] = image_bytes("magenta")
                    saved["fieldData"]["main-image"]["url"] = wrong_url
                return response(url, payload=saved)
        if url == "https://s3.amazonaws.com/webflow-prod-assets" and method == "POST":
            self.test.assertNotIn("Authorization", kwargs.get("headers", {}))
            details = dict(kwargs["data"])
            self.test.assertEqual(len(kwargs["files"]), 1)
            name, (filename, data, mime) = kwargs["files"][0]
            self.test.assertEqual(name, "file")
            asset_id = details["key"].rsplit("/", 1)[-1].split("_", 1)[0]
            asset = self.assets[asset_id]
            self.test.assertEqual(filename, asset["originalFileName"])
            self.test.assertEqual(hashlib.md5(data).hexdigest(), asset["expected_md5"])
            self.public_files[asset["hostedUrl"]] = data
            asset["uploaded_mime"] = mime
            self.file_uploads += 1
            return response(url, status=201)
        if parsed.hostname == "cdn.prod.website-files.com" and method == "GET":
            self.test.assertNotIn("Authorization", kwargs.get("headers", {}))
            self.test.assertIn(url, self.public_files, "Public image downloaded before file upload")
            if kwargs.get("allow_redirects") is False:
                self.verified_urls.add(url)
            return response(url, content=self.public_files[url])
        raise AssertionError(f"Unexpected external HTTP call: {method} {url}")

    def cms_copy_images(self, fields: dict) -> dict:
        """Webflow may clone uploads under different public URLs when saving CMS fields."""
        # JSON transport does not preserve Python aliases between gallery/main/thumbnail.
        copied = json.loads(json.dumps(fields))
        replacements: dict[str, str] = {}
        for key in webflow.WEBFLOW_IMAGE_FIELDS:
            values = copied.get(key)
            if not values:
                continue
            for image in values if isinstance(values, list) else [values]:
                url = image["url"]
                self.reject_repository_url(url)
                self.test.assertIn(url, self.verified_urls)
                if url not in replacements:
                    clone = f"https://cdn.prod.website-files.com/{SITE_ID}/cms-copy-{len(replacements) + 1}.png"
                    self.public_files[clone] = self.public_files[url]
                    replacements[url] = clone
                image["url"] = replacements[url]
        return copied

    def seo(self, **kwargs):
        self.events.append(("OPENAI", "seo"))
        if self.fail_next_enrichment:
            self.fail_next_enrichment = False
            raise RuntimeError("simulated downstream enrichment failure")
        return SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(content=json.dumps(SEO)), finish_reason="stop")], usage=None)

    def alt(self, **kwargs):
        inputs = [part["image_url"] for message in kwargs["input"]
                  for part in message["content"] if part["type"] == "input_image"]
        self.test.assertEqual(len(inputs), 1)
        url = inputs[0]
        self.reject_repository_url(url)
        self.test.assertIn(url, self.verified_urls, "OpenAI saw an image before hosted-byte verification")
        self.alt_inputs.append(url)
        self.events.append(("OPENAI", url))
        alt = f"A marketer examines customer discovery chart number {len(self.alt_inputs)} on a desk."
        self.alts[url] = alt
        return SimpleNamespace(output_text=alt)


class PrivateRepositoryFlowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.images = self.root / "images"
        self.generated = self.images / "generated"
        self.data = self.root / "data"
        self.generated.mkdir(parents=True)
        self.data.mkdir()
        self.now = datetime.now(timezone.utc)
        self.date = self.now.strftime("%Y-%m-%d")
        self.http = FakeServices(self, int(self.now.timestamp() * 1000))
        self.pipeline_config = config.PipelineConfig(
            linkedin_access_token="fake-linkedin-token", openai_api_key="fake-openai-token",
            openai_model="gpt-test", webflow_api_token="fake-webflow-token",
            webflow_collection_id=COLLECTION_ID, webflow_publish=True,
            force_webflow_sync=False, webflow_site_id=SITE_ID,
        )
        self.enrichment_inputs: list[dict] = []
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.output = self.stack.enter_context(redirect_stdout(StringIO()))
        locations = [
            (config, "REPO_ROOT", self.root), (config, "DATA_DIR", self.data),
            (config, "IMAGE_DIR", self.images), (config, "GENERATED_IMAGE_DIR", self.generated),
            (linkedin, "IMAGE_DIR", self.images), (image_assets, "REPO_ROOT", self.root),
            (image_generation, "GENERATED_IMAGE_DIR", self.generated),
            (generated_images, "GENERATED_IMAGE_MANIFEST_PATH", self.data / "generated_main_images.json"),
            (webflow_assets, "ASSET_MANIFEST_PATH", self.data / "webflow_assets.json"),
            (webflow, "WEBFLOW_STATE_PATH", self.data / "webflow_items.json"),
            (webflow, "WEBFLOW_LIVE_READBACK_DELAY_SECONDS", 0),
            (pipeline_main, "RAW_POST_PATH", self.data / "last_linkedin_post.json"),
            (pipeline_main, "ENRICHED_POST_PATH", self.data / "last_linkedin_post.enriched.json"),
            (pipeline_main, "PIPELINE_STATE_PATH", self.data / "pipeline_state.json"),
        ]
        for module, attribute, value in locations:
            self.stack.enter_context(patch.object(module, attribute, value))
        self.stack.enter_context(patch.object(pipeline_main, "load_config", return_value=self.pipeline_config))
        self.stack.enter_context(patch("requests.sessions.Session.request", autospec=True, side_effect=self.http.request))
        self.stack.enter_context(patch.object(enrichment, "OpenAI", return_value=self.http.client))
        self.stack.enter_context(patch.object(pipeline_main, "enrich_post", side_effect=self.observe_enrichment))
        self.stack.enter_context(patch.object(pipeline_main, "link_post_body", side_effect=lambda post, cfg: (post, {"links_added": 0})))
        self.generator = self.stack.enter_context(patch.object(
            image_generation, "generate_one_raw_image", side_effect=AssertionError("Unexpected paid image generation")))

    def observe_enrichment(self, post: dict, pipeline_config: config.PipelineConfig) -> dict:
        self.enrichment_inputs.append(copy.deepcopy(post))
        images = list(post.get("images", []))
        if post.get("generated_main_image"):
            images.append(post["generated_main_image"])
        self.assertTrue(images)
        for image in images:
            self.assertNotIn("local_path", image)
            self.http.reject_repository_url(image["url"])
            self.assertIn(image["url"], self.http.verified_urls)
        return enrichment.enrich_post(post, pipeline_config)

    def source(self, suffix: str = "", *, colour: str = "navy", extension: str = ".png", image_format: str = "PNG") -> Path:
        path = self.images / f"{self.date}{suffix}{extension}"
        path.write_bytes(image_bytes(colour, image_format=image_format))
        return path

    def successful_fields(self) -> dict:
        self.assertEqual(pipeline_main.main(), 0, self.output.getvalue())
        self.assertEqual(self.http.publish_calls, 1)
        self.assertEqual(len(self.http.cms_writes), 1)
        self.assertTrue((self.data / "pipeline_state.json").is_file())
        state = json.loads((self.data / "webflow_items.json").read_text())
        self.assertTrue(state["items"][POST_URL]["published"])
        self.generator.assert_not_called()
        # The first network operation is the real LinkedIn read, then duplicate detection.
        self.assertEqual(self.http.events[0], ("GET", linkedin.LINKEDIN_CHANGE_LOG_URL))
        self.assertEqual(self.http.events[1], ("GET", f"https://api.webflow.com/v2/collections/{COLLECTION_ID}/items/live"))
        return self.http.cms_writes[0]

    def test_single_original_uploads_before_real_vision_and_preserves_cms_roles(self) -> None:
        source = self.source(extension=".jpg", image_format="JPEG")
        original_bytes = source.read_bytes()
        fields = self.successful_fields()
        self.assertEqual(self.http.asset_creates, 1)
        self.assertEqual(self.http.file_uploads, 1)
        self.assertEqual(len(self.http.alt_inputs), 1)
        expected = {"url": self.http.alt_inputs[0], "alt": self.http.alts[self.http.alt_inputs[0]]}
        self.assertEqual(fields["post-images"], [expected])
        self.assertEqual(fields["main-image"], expected)
        self.assertEqual(fields["thumbnail-image"], expected)
        self.assertEqual(source.read_bytes(), original_bytes)
        # Actual CMS read-back verifies a different hosted URL serving identical bytes.
        self.assertNotEqual(self.http.item["fieldData"]["main-image"]["url"], expected["url"])

    def test_png_bytes_named_jpg_keep_original_identity_and_upload_correct_mime(self) -> None:
        source = self.source(extension=".jpg", image_format="PNG")
        original_bytes = source.read_bytes()
        fields = self.successful_fields()
        asset = next(iter(self.http.assets.values()))
        self.assertTrue(asset["originalFileName"].endswith(".png"))
        self.assertEqual(asset["uploaded_mime"], "image/png")
        self.assertEqual(self.enrichment_inputs[0]["images"][0]["filename"], source.name)
        self.assertEqual(self.http.public_files[fields["main-image"]["url"]], original_bytes)
        self.assertEqual(source.read_bytes(), original_bytes)

    def test_multiple_originals_preserve_numeric_order_through_upload_alt_and_cms(self) -> None:
        paths = [self.source("_10", colour="gold"), self.source("_2", colour="green"), self.source("_1", colour="navy")]
        fields = self.successful_fields()
        expected_names = [paths[2].name, paths[1].name, paths[0].name]
        self.assertEqual([item["filename"] for item in self.enrichment_inputs[0]["images"]], expected_names)
        self.assertEqual([image["url"] for image in fields["post-images"]], self.http.alt_inputs)
        self.assertEqual([image["alt"] for image in fields["post-images"]], [self.http.alts[url] for url in self.http.alt_inputs])
        self.assertEqual([self.http.public_files[image["url"]] for image in fields["post-images"]],
                         [paths[2].read_bytes(), paths[1].read_bytes(), paths[0].read_bytes()])
        self.assertEqual(fields["main-image"], fields["post-images"][0])
        self.assertEqual(fields["thumbnail-image"], fields["post-images"][0])
        self.assertEqual(self.http.asset_creates, 3)

    def test_registered_generated_hero_uploads_main_only_without_new_generation(self) -> None:
        post = linkedin.extract_post(self.http.linkedin_element())
        self.assertIsNotNone(post)
        filename = image_generation.generated_image_filename(post)
        raw_png = image_bytes("slategray", size=(1200, 675))
        (self.generated / filename).write_bytes(raw_png)
        reviewed_alt = "A researcher studies branching customer journeys in a quiet charcoal studio."
        generated_images.record_generated_image(
            post, filename, raw_png, renderer_model="gpt-image-2", planner_model="gpt-test",
            qa_model="gpt-test", concept={"scene": "A researcher studies customer journeys"},
            quality_review={"passed": True, "alt": reviewed_alt}, references=[],
            prompt="Prepared fixture for the previously reviewed hero", dimensions={"width": 1200, "height": 675},
        )
        manifest_before = (self.data / "generated_main_images.json").read_bytes()
        fields = self.successful_fields()
        self.assertNotIn("post-images", fields)
        self.assertNotIn("thumbnail-image", fields)
        self.assertEqual(fields["main-image"]["alt"], reviewed_alt)
        self.assertEqual(self.http.public_files[fields["main-image"]["url"]], raw_png)
        self.assertEqual(self.http.alt_inputs, [])
        self.assertEqual(self.http.asset_creates, 1)
        self.assertEqual((self.data / "generated_main_images.json").read_bytes(), manifest_before)

    def test_retry_after_enrichment_failure_reuses_uploaded_asset(self) -> None:
        self.source()
        self.http.fail_next_enrichment = True
        with self.assertRaisesRegex(RuntimeError, "simulated downstream enrichment failure"):
            pipeline_main.main()
        self.assertEqual(self.http.asset_creates, 1)
        self.assertEqual(self.http.file_uploads, 1)
        self.assertEqual(self.http.cms_writes, [])
        self.assertFalse((self.data / "pipeline_state.json").exists())
        manifest = json.loads((self.data / "webflow_assets.json").read_text())
        self.assertEqual([entry["status"] for entry in manifest["assets"].values()], ["ready"])
        self.assertNotIn("fake-temporary-policy", json.dumps(manifest))
        first_verified = set(self.http.verified_urls)
        self.http.verified_urls.clear()
        fields = self.successful_fields()
        self.assertEqual(self.http.asset_creates, 1)
        self.assertEqual(self.http.file_uploads, 1)
        self.assertEqual(self.http.verified_urls, first_verified)
        self.assertIn(fields["main-image"]["url"], first_verified)
        self.assertEqual(len(self.enrichment_inputs), 2)

    def test_wrong_saved_image_bytes_prevent_publish_and_success_state(self) -> None:
        self.source()
        self.http.corrupt_main_readback = True
        with self.assertRaisesRegex(webflow.WebflowError, "image identity or order"):
            pipeline_main.main()
        self.assertEqual(len(self.http.cms_writes), 1)
        self.assertEqual(self.http.publish_calls, 0)
        self.assertIsNone(self.http.live_item)
        state = json.loads((self.data / "webflow_items.json").read_text())
        entry = state["items"][POST_URL]
        self.assertEqual(entry["verification_pending"]["item_id"], ITEM_ID)
        self.assertEqual(entry["verification_pending"]["location"], "staged")
        self.assertNotIn("signature", entry)
        self.assertNotIn("published", entry)
        self.assertFalse((self.data / "pipeline_state.json").exists())
        self.assertTrue((self.data / "webflow_assets.json").is_file())

    def test_published_item_retries_pending_readback_before_generation_or_enrichment(self) -> None:
        source = self.source()
        self.http.fail_live_readback = True
        with self.assertRaisesRegex(webflow.WebflowError, "read-back failed"):
            pipeline_main.main()
        self.assertEqual(self.http.publish_calls, 1)
        pending = json.loads((self.data / "webflow_items.json").read_text())["items"][POST_URL]
        self.assertEqual(pending["verification_pending"]["location"], "live")
        self.assertNotIn("published", pending)
        self.assertFalse((self.data / "pipeline_state.json").exists())
        local_outputs = {path.name: path.read_bytes() for path in (
            self.data / "last_linkedin_post.json", self.data / "last_linkedin_post.enriched.json")}
        enrichment_count = len(self.enrichment_inputs)
        upload_count = self.http.file_uploads
        write_count = len(self.http.cms_writes)

        # Recovery uses the durable expected fields even if no source file remains locally.
        source.unlink()
        self.http.fail_live_readback = False
        with patch.object(prepare_image, "generate_missing_main_image",
                          side_effect=AssertionError("Pending verification must not generate again")) as generate:
            self.assertEqual(prepare_image.prepare_latest_post_image(self.pipeline_config), 0)
            generate.assert_not_called()
        self.assertEqual(pipeline_main.main(), 0)
        completed = json.loads((self.data / "webflow_items.json").read_text())["items"][POST_URL]
        self.assertNotIn("verification_pending", completed)
        self.assertTrue(completed["published"])
        self.assertEqual(completed["item_id"], ITEM_ID)
        self.assertEqual(len(self.enrichment_inputs), enrichment_count)
        self.assertEqual(self.http.file_uploads, upload_count)
        self.assertEqual(len(self.http.cms_writes), write_count)
        self.assertEqual(self.http.publish_calls, 1)
        self.assertEqual(local_outputs, {name: (self.data / name).read_bytes() for name in local_outputs})

    def test_pending_publication_recovers_after_its_linkedin_post_leaves_the_window(self) -> None:
        self.source()
        self.http.fail_live_readback = True
        with self.assertRaisesRegex(webflow.WebflowError, "read-back failed"):
            pipeline_main.main()
        pending = json.loads((self.data / "webflow_items.json").read_text())["items"][POST_URL]
        self.assertIn("verification_pending", pending)
        self.assertEqual(self.http.publish_calls, 1)
        enrichment_count = len(self.enrichment_inputs)
        upload_count = self.http.file_uploads
        write_count = len(self.http.cms_writes)
        event_count = len(self.http.events)

        # A's checkpoint remains actionable after A disappears from LinkedIn's 48-hour response.
        self.http.no_recent_post = True
        self.http.fail_live_readback = False
        self.assertEqual(pipeline_main.main(), 0)
        resumed_events = self.http.events[event_count:]
        self.assertEqual(resumed_events[0], (
            "GET", f"https://api.webflow.com/v2/collections/{COLLECTION_ID}/items/{ITEM_ID}/live"))
        self.assertEqual(resumed_events[-1], ("GET", linkedin.LINKEDIN_CHANGE_LOG_URL))
        completed = json.loads((self.data / "webflow_items.json").read_text())["items"][POST_URL]
        self.assertNotIn("verification_pending", completed)
        self.assertTrue(completed["published"])
        self.assertEqual(completed["item_id"], ITEM_ID)
        self.assertEqual(len(self.enrichment_inputs), enrichment_count)
        self.assertEqual(self.http.file_uploads, upload_count)
        self.assertEqual(len(self.http.cms_writes), write_count)
        self.assertEqual(self.http.publish_calls, 1)
        self.generator.assert_not_called()


if __name__ == "__main__":
    unittest.main()
