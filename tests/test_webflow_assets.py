from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from dataclasses import replace
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

import requests
from PIL import Image

from pipeline.config import PipelineConfig
from pipeline import webflow_assets as assets

SITE = "63250855178122098387d7ef"
OTHER_SITE = "63250855178122098387d7e0"
ASSET = "64358b9544249dc43d37d2b7"
OTHER_ASSET = "64358b9544249dc43d37d2b8"
PUBLIC_URL = f"https://cdn.prod.website-files.com/{SITE}/{ASSET}_image.png"
UPLOAD_URL = "https://s3.amazonaws.com/webflow-prod-assets"
UPLOAD_DETAILS = {
    "acl": "public-read", "bucket": "webflow-prod-assets",
    "X-Amz-Algorithm": "AWS4-HMAC-SHA256", "X-Amz-Credential": "private-credential",
    "X-Amz-Date": "20260908T090000Z", "key": f"{SITE}/{ASSET}_image.png",
    "Policy": "private-policy", "X-Amz-Signature": "private-signature",
    "success_action_status": "201", "content-type": "image/png",
    "Cache-Control": "max-age=31536000, must-revalidate",
}


def png(colour: str = "navy", dimensions: tuple[int, int] = (30, 20)) -> bytes:
    output = BytesIO()
    Image.new("RGB", dimensions, colour).save(output, format="PNG")
    return output.getvalue()


def response(status: int = 200, *, payload: object = None, body: bytes = b"", headers: dict | None = None) -> requests.Response:
    result = requests.Response()
    result.status_code = status
    result.headers.update(headers or {})
    result._content = json.dumps(payload).encode() if payload is not None else body
    result._content_consumed = True
    return result


def created(asset_id: str = ASSET, url: str = PUBLIC_URL) -> dict:
    return {"id": asset_id, "uploadUrl": UPLOAD_URL, "hostedUrl": url,
            "uploadDetails": UPLOAD_DETAILS.copy(), "contentType": "image/png"}


def metadata(asset_id: str = ASSET, url: str = PUBLIC_URL, site: str = SITE) -> dict:
    return {"id": asset_id, "hostedUrl": url, "siteId": site, "contentType": "image/png"}


class WebflowAssetsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.directory_path = Path(self.directory.name)
        self.manifest = self.directory_path / "webflow_assets.json"
        self.path = self.directory_path / "original_name.png"
        self.data = png()
        self.path.write_bytes(self.data)
        self.sha256 = hashlib.sha256(self.data).hexdigest()
        self.config = PipelineConfig(
            linkedin_access_token="", openai_api_key="", openai_model="unused",
            webflow_api_token="private-api-token", webflow_collection_id="unused",
            webflow_publish=False, force_webflow_sync=False, webflow_site_id=SITE,
        )
        self.manifest_patch = patch.object(assets, "ASSET_MANIFEST_PATH", self.manifest)
        self.manifest_patch.start()
        self.addCleanup(self.manifest_patch.stop)
        self.sleep_patch = patch.object(assets.time, "sleep")
        self.sleep = self.sleep_patch.start()
        self.addCleanup(self.sleep_patch.stop)

    def cache(self, *, status: str = "ready", asset_id: str = ASSET) -> None:
        self.manifest.write_text(json.dumps({"version": 1, "assets": {
            f"{SITE}:{self.sha256}": {"asset_id": asset_id, "site_id": SITE,
            "sha256": self.sha256, "url": PUBLIC_URL, "filename": "old-name.png", "status": status}
        }}))

    def upload_responses(self, *, asset_id: str = ASSET, site: str = SITE, data: bytes | None = None) -> list:
        return [response(payload=created(asset_id)), response(201),
                response(payload=metadata(asset_id, site=site)), response(body=data or self.data)]

    def test_real_prepared_multipart_preserves_exact_form_fields_and_file_last(self) -> None:
        sent = []
        results = iter(self.upload_responses())

        def send(session, request, **kwargs):
            sent.append(request)
            return next(results)

        with patch.object(requests.Session, "send", autospec=True, side_effect=send):
            result = assets.ensure_webflow_asset(self.path, self.config)

        self.assertEqual(result, {"url": PUBLIC_URL, "asset_id": ASSET,
                                 "sha256": self.sha256, "filename": self.path.name})
        create_payload = json.loads(sent[0].body)
        self.assertEqual(create_payload["fileHash"], hashlib.md5(self.data).hexdigest())
        self.assertLess(len(create_payload["fileName"]), 100)
        self.assertEqual(sent[0].headers["Authorization"], "Bearer private-api-token")
        multipart = sent[1].body
        for key, value in UPLOAD_DETAILS.items():
            self.assertIn(f'name="{key}"\r\n\r\n{value}'.encode(), multipart)
            self.assertLess(multipart.index(f'name="{key}"'.encode()), multipart.index(b'name="file"'))
        self.assertIn(self.data, multipart)
        self.assertIn(b"Content-Type: image/png", multipart)
        self.assertNotIn("Authorization", sent[1].headers)
        self.assertNotIn("Authorization", sent[3].headers)
        self.assertEqual(sent[3].url, PUBLIC_URL)
        manifest = self.manifest.read_text()
        self.assertEqual(json.loads(manifest)["assets"][f"{SITE}:{self.sha256}"]["status"], "ready")
        for secret in ("private-api-token", "private-credential", "private-policy", "private-signature", "uploadDetails"):
            self.assertNotIn(secret, manifest)

    def test_cached_asset_is_verified_without_new_upload_and_keeps_callers_filename(self) -> None:
        self.cache()
        with patch.object(assets.requests, "request", side_effect=[response(payload=metadata()), response(body=self.data)]) as request:
            result = assets.ensure_webflow_asset(self.path, self.config)
        self.assertEqual(request.call_count, 2)
        self.assertTrue(all(call.args[0] == "GET" for call in request.call_args_list))
        self.assertEqual(result["filename"], self.path.name)

    def test_pending_successful_upload_is_recovered_without_uploading_again(self) -> None:
        self.cache(status="pending")
        with patch.object(assets.requests, "request", side_effect=[response(payload=metadata()), response(body=self.data)]) as request:
            assets.ensure_webflow_asset(self.path, self.config)
        self.assertEqual(request.call_count, 2)
        self.assertEqual(json.loads(self.manifest.read_text())["assets"][f"{SITE}:{self.sha256}"]["status"], "ready")

    def test_changed_bytes_are_uploaded_and_previous_cache_entry_is_preserved(self) -> None:
        self.cache()
        self.path.write_bytes(png("red"))
        with patch.object(assets.requests, "request", side_effect=self.upload_responses(asset_id=OTHER_ASSET, data=self.path.read_bytes())):
            result = assets.ensure_webflow_asset(self.path, self.config)
        entries = json.loads(self.manifest.read_text())["assets"]
        self.assertEqual(len(entries), 2)
        self.assertNotEqual(result["sha256"], self.sha256)
        self.assertIn(f"{SITE}:{self.sha256}", entries)

    def test_same_bytes_on_another_site_do_not_reuse_cached_asset(self) -> None:
        self.cache()
        with patch.object(assets.requests, "request", side_effect=self.upload_responses(site=OTHER_SITE)) as request:
            assets.ensure_webflow_asset(self.path, replace(self.config, webflow_site_id=OTHER_SITE))
        self.assertEqual(request.call_args_list[0].args[1], f"{assets.API_ROOT}/sites/{OTHER_SITE}/assets")
        self.assertEqual(len(json.loads(self.manifest.read_text())["assets"]), 2)

    def test_deleted_cached_asset_is_replaced(self) -> None:
        self.cache()
        with patch.object(assets.requests, "request", side_effect=[response(404), *self.upload_responses(asset_id=OTHER_ASSET)]):
            result = assets.ensure_webflow_asset(self.path, self.config)
        self.assertEqual(result["asset_id"], OTHER_ASSET)

    def test_unfinished_pending_asset_fails_without_creating_duplicates(self) -> None:
        self.cache(status="pending")
        outputs = [response(payload=metadata()), response(404), response(404), response(404)]
        with patch.object(assets.requests, "request", side_effect=outputs) as request:
            with self.assertRaisesRegex(RuntimeError, "no duplicate upload was attempted"):
                assets.ensure_webflow_asset(self.path, self.config)
        self.assertTrue(all(call.args[0] == "GET" for call in request.call_args_list))

    def test_wrong_public_bytes_in_cache_fail_without_blind_recreation(self) -> None:
        self.cache()
        outputs = [response(payload=metadata()), *[response(body=png("red")) for _ in range(3)]]
        with patch.object(assets.requests, "request", side_effect=outputs) as request:
            with self.assertRaisesRegex(RuntimeError, "no duplicate upload was attempted"):
                assets.ensure_webflow_asset(self.path, self.config)
        self.assertTrue(all(call.args[0] == "GET" for call in request.call_args_list))

    def test_public_readiness_retries_missing_file_before_accepting_upload(self) -> None:
        outputs = self.upload_responses()[:-1] + [response(403), response(404), response(body=self.data)]
        with patch.object(assets.requests, "request", side_effect=outputs):
            assets.ensure_webflow_asset(self.path, self.config)
        self.assertEqual(self.sleep.call_count, 2)

    def test_metadata_without_real_file_fails_and_retains_pending_record(self) -> None:
        outputs = self.upload_responses()[:-1] + [response(404), response(404), response(404)]
        with patch.object(assets.requests, "request", side_effect=outputs), self.assertRaisesRegex(RuntimeError, "does not match"):
            assets.ensure_webflow_asset(self.path, self.config)
        self.assertEqual(json.loads(self.manifest.read_text())["assets"][f"{SITE}:{self.sha256}"]["status"], "pending")

    def test_pending_asset_is_checkpointed_before_upload_failure_without_credentials(self) -> None:
        def upload_failure(method, url, **kwargs):
            if url == f"{assets.API_ROOT}/sites/{SITE}/assets":
                return response(payload=created())
            checkpoint = json.loads(self.manifest.read_text())["assets"][f"{SITE}:{self.sha256}"]
            self.assertEqual(checkpoint["status"], "pending")
            self.assertEqual(checkpoint["asset_id"], ASSET)
            return response(403, body=b"private-policy private-signature")

        with patch.object(assets.requests, "request", side_effect=upload_failure):
            with self.assertRaises(RuntimeError) as context:
                assets.ensure_webflow_asset(self.path, self.config)
        for secret in ("private-policy", "private-signature"):
            self.assertNotIn(secret, str(context.exception))
            self.assertNotIn(secret, self.manifest.read_text())

    def test_atomic_checkpoint_failure_preserves_previous_manifest(self) -> None:
        self.cache()
        original = self.manifest.read_bytes()
        with patch.object(assets.os, "replace", side_effect=OSError("disk unavailable")):
            with self.assertRaises(OSError):
                assets._checkpoint("new-key", {"status": "pending"})
        self.assertEqual(self.manifest.read_bytes(), original)
        self.assertEqual(list(self.directory_path.glob(".webflow-assets-*.tmp")), [])

    def test_api_deduplicated_asset_without_upload_details_still_checks_public_file(self) -> None:
        outputs = [response(payload={"id": ASSET, "hostedUrl": PUBLIC_URL}), response(payload=metadata()), response(body=self.data)]
        with patch.object(assets.requests, "request", side_effect=outputs) as request:
            assets.ensure_webflow_asset(self.path, self.config)
        self.assertEqual(request.call_count, 3)

    def test_rate_limit_retry_after_and_safe_upload_network_retry_are_bounded(self) -> None:
        outputs = [response(429, headers={"Retry-After": "7"}), response(payload=created()),
                   requests.ConnectionError("private-api-token"), response(201),
                   response(payload=metadata()), response(body=self.data)]
        with patch.object(assets.requests, "request", side_effect=outputs):
            assets.ensure_webflow_asset(self.path, self.config)
        self.assertEqual([call.args[0] for call in self.sleep.call_args_list], [7, 1])

    def test_ambiguous_create_failure_is_not_retried_or_leaked(self) -> None:
        with patch.object(assets.requests, "request", side_effect=requests.ConnectionError("private-api-token signed-url")) as request:
            with self.assertRaises(RuntimeError) as context:
                assets.ensure_webflow_asset(self.path, self.config)
        self.assertEqual(request.call_count, 1)
        self.assertIn("inspect Assets before rerunning", str(context.exception))
        self.assertNotIn("private-api-token", str(context.exception))
        self.assertNotIn("signed-url", str(context.exception))
        intent = next(iter(json.loads(self.manifest.read_text())["assets"].values()))
        self.assertEqual(intent["status"], "creating")
        self.assertIn("upload_filename", intent)
        self.assertNotIn("asset_id", intent)

    def test_intent_is_durable_before_create_request(self) -> None:
        def inspect_before_create(method, url, **kwargs):
            intent = next(iter(json.loads(self.manifest.read_text())["assets"].values()))
            self.assertEqual(intent["status"], "creating")
            self.assertEqual(intent["upload_filename"], kwargs["json"]["fileName"])
            raise requests.ConnectionError("timeout")

        with patch.object(assets.requests, "request", side_effect=inspect_before_create):
            with self.assertRaises(RuntimeError):
                assets.ensure_webflow_asset(self.path, self.config)

    def create_uncertain_intent(self) -> dict:
        with patch.object(assets.requests, "request", side_effect=requests.Timeout("lost response")):
            with self.assertRaises(RuntimeError):
                assets.ensure_webflow_asset(self.path, self.config)
        return next(iter(json.loads(self.manifest.read_text())["assets"].values()))

    def test_next_run_reconciles_every_asset_page_and_reuses_verified_file(self) -> None:
        intent = self.create_uncertain_intent()
        found = {**metadata(), "originalFileName": intent["upload_filename"]}
        unrelated = {**metadata(OTHER_ASSET), "originalFileName": "different.png"}
        outputs = [response(payload={"assets": [unrelated], "pagination": {"total": 2}}),
                   response(payload={"assets": [found], "pagination": {"total": 2}}),
                   response(payload=metadata()), response(body=self.data)]
        with patch.object(assets.requests, "request", side_effect=outputs) as request:
            result = assets.ensure_webflow_asset(self.path, self.config)
        self.assertEqual(result["asset_id"], ASSET)
        self.assertTrue(all(call.args[0] == "GET" for call in request.call_args_list))
        self.assertEqual(request.call_args_list[1].kwargs["params"]["offset"], 1)
        self.assertEqual(next(iter(json.loads(self.manifest.read_text())["assets"].values()))["status"], "ready")

    def test_next_run_missing_or_duplicate_reconciliation_never_posts(self) -> None:
        for matched_ids in ([], [ASSET, OTHER_ASSET]):
            with self.subTest(matched_ids=matched_ids):
                self.manifest.unlink(missing_ok=True)
                intent = self.create_uncertain_intent()
                candidates = [{**metadata(asset_id), "originalFileName": intent["upload_filename"]} for asset_id in matched_ids]
                page = {"assets": candidates, "pagination": {"total": len(candidates)}}
                with patch.object(assets.requests, "request", return_value=response(payload=page)) as request:
                    with self.assertRaises(RuntimeError):
                        assets.ensure_webflow_asset(self.path, self.config)
                self.assertTrue(all(call.args[0] == "GET" for call in request.call_args_list))
                self.assertEqual(next(iter(json.loads(self.manifest.read_text())["assets"].values()))["status"], "creating")

    def test_reconciled_metadata_without_uploaded_file_remains_pending(self) -> None:
        intent = self.create_uncertain_intent()
        page = {"assets": [{**metadata(), "originalFileName": intent["upload_filename"]}], "pagination": {"total": 1}}
        outputs = [response(payload=page), response(payload=metadata()), response(404), response(404), response(404)]
        with patch.object(assets.requests, "request", side_effect=outputs) as request:
            with self.assertRaisesRegex(RuntimeError, "no duplicate upload was attempted"):
                assets.ensure_webflow_asset(self.path, self.config)
        self.assertTrue(all(call.args[0] == "GET" for call in request.call_args_list))
        record = next(iter(json.loads(self.manifest.read_text())["assets"].values()))
        self.assertEqual((record["status"], record["asset_id"]), ("pending", ASSET))

    def test_incomplete_reconciliation_cannot_be_misread_as_no_existing_asset(self) -> None:
        self.create_uncertain_intent()
        for page in ({"assets": []}, {"assets": [], "pagination": {"total": 2}},
                     {"assets": [], "pagination": {"total": "0"}}):
            with self.subTest(page=page), patch.object(assets.requests, "request", return_value=response(payload=page)) as request:
                with self.assertRaisesRegex(RuntimeError, "reconciliation"):
                    assets.ensure_webflow_asset(self.path, self.config)
            self.assertTrue(all(call.args[0] == "GET" for call in request.call_args_list))

    def test_changing_or_overlapping_pages_do_not_prove_unique_reconciliation(self) -> None:
        intent = self.create_uncertain_intent()
        candidate = {**metadata(), "originalFileName": intent["upload_filename"]}
        first_page = {"assets": [candidate], "pagination": {"total": 2, "offset": 0}}
        for second_page in (
            {"assets": [candidate], "pagination": {"total": 2, "offset": 1}},
            {"assets": [metadata(OTHER_ASSET)], "pagination": {"total": 3, "offset": 1}},
            {"assets": [metadata(OTHER_ASSET)], "pagination": {"total": 2, "offset": 0}},
        ):
            with self.subTest(second_page=second_page):
                outputs = [response(payload=first_page), response(payload=second_page)]
                with patch.object(assets.requests, "request", side_effect=outputs) as request:
                    with self.assertRaisesRegex(RuntimeError, "reconciliation"):
                        assets.ensure_webflow_asset(self.path, self.config)
                self.assertTrue(all(call.args[0] == "GET" for call in request.call_args_list))
                self.assertEqual(next(iter(json.loads(self.manifest.read_text())["assets"].values()))["status"], "creating")

    def test_definite_create_rejection_allows_new_request_after_config_is_fixed(self) -> None:
        with patch.object(assets.requests, "request", return_value=response(403)):
            with self.assertRaises(RuntimeError):
                assets.ensure_webflow_asset(self.path, self.config)
        intent = next(iter(json.loads(self.manifest.read_text())["assets"].values()))
        self.assertEqual(intent["status"], "create_rejected")
        with patch.object(assets.requests, "request", side_effect=self.upload_responses()) as request:
            assets.ensure_webflow_asset(self.path, self.config)
        self.assertEqual(request.call_args_list[0].args[0], "POST")

    def test_response_missing_url_still_preserves_returned_asset_id_for_recovery(self) -> None:
        with patch.object(assets.requests, "request", return_value=response(payload={"id": ASSET})):
            with self.assertRaisesRegex(RuntimeError, "missing asset URL"):
                assets.ensure_webflow_asset(self.path, self.config)
        record = next(iter(json.loads(self.manifest.read_text())["assets"].values()))
        self.assertEqual((record["status"], record["asset_id"]), ("pending", ASSET))

    def test_create_server_error_is_not_retried(self) -> None:
        with patch.object(assets.requests, "request", return_value=response(500)) as request:
            with self.assertRaisesRegex(RuntimeError, "inspect Assets before rerunning"):
                assets.ensure_webflow_asset(self.path, self.config)
        self.assertEqual(request.call_count, 1)

    def test_missing_asset_read_permission_does_not_blindly_upload_duplicates(self) -> None:
        self.cache()
        with patch.object(assets.requests, "request", return_value=response(403, body=b"private-api-token")) as request:
            with self.assertRaisesRegex(RuntimeError, "Assets read/write"):
                assets.ensure_webflow_asset(self.path, self.config)
        self.assertEqual(request.call_count, 1)

    def test_public_server_failure_does_not_trigger_new_asset_creation(self) -> None:
        self.cache()
        outputs = [response(payload=metadata()), response(503), response(503), response(503)]
        with patch.object(assets.requests, "request", side_effect=outputs) as request:
            with self.assertRaisesRegex(RuntimeError, "HTTP 503"):
                assets.ensure_webflow_asset(self.path, self.config)
        self.assertTrue(all(call.args[0] == "GET" for call in request.call_args_list))

    def test_corrupt_manifest_is_preserved_without_network_writes(self) -> None:
        for content in ("not json", "[]", '{"version": 99, "assets": {}}'):
            with self.subTest(content=content):
                self.manifest.write_text(content)
                with patch.object(assets.requests, "request") as request, self.assertRaisesRegex(RuntimeError, "manifest"):
                    assets.ensure_webflow_asset(self.path, self.config)
                request.assert_not_called()
                self.assertEqual(self.manifest.read_text(), content)

    def test_bad_images_and_missing_file_fail_before_any_network_call(self) -> None:
        cases = [("missing.png", None), ("bad.png", b"<html>not an image</html>"),
                 ("big.png", b"x" * (assets.MAX_ASSET_BYTES + 1)),
                 ("truncated.png", self.data[:40])]
        for filename, data in cases:
            with self.subTest(filename=filename):
                path = self.directory_path / filename
                if data is not None:
                    path.write_bytes(data)
                with patch.object(assets.requests, "request") as request, self.assertRaises(ValueError):
                    assets.ensure_webflow_asset(path, self.config)
                request.assert_not_called()

    def test_jpeg_and_webp_images_are_supported(self) -> None:
        for image_format, suffix, mime in (("JPEG", ".jpeg", "image/jpeg"), ("WEBP", ".webp", "image/webp")):
            with self.subTest(image_format=image_format):
                output = BytesIO()
                Image.new("RGB", (30, 20), "navy").save(output, format=image_format)
                path = self.directory_path / ("source" + suffix)
                path.write_bytes(output.getvalue())
                with patch.object(assets.requests, "request", side_effect=self.upload_responses(data=output.getvalue())) as request:
                    assets.ensure_webflow_asset(path, self.config)
                self.assertEqual(request.call_args_list[1].kwargs["files"][0][1][2], mime)

    def test_iphone_multi_picture_jpeg_keeps_all_mpo_bytes_and_uses_jpeg_mime(self) -> None:
        output = BytesIO()
        Image.new("RGB", (30, 20), "red").save(
            output, format="MPO", save_all=True,
            append_images=[Image.new("RGB", (30, 20), "blue")],
        )
        data = output.getvalue()
        path = self.directory_path / "2026-09-08_1.jpg"
        path.write_bytes(data)
        self.assertEqual(assets._inspect_image(data)[0], "MPO")
        with patch.object(assets.requests, "request", side_effect=self.upload_responses(data=data)) as request:
            result = assets.ensure_webflow_asset(path, self.config)
        remote_filename, sent_bytes, mime = request.call_args_list[1].kwargs["files"][0][1]
        self.assertTrue(remote_filename.endswith(".jpg"))
        self.assertEqual(mime, "image/jpeg")
        self.assertEqual(sent_bytes, data)
        self.assertEqual(path.read_bytes(), data)
        self.assertEqual(result["filename"], path.name)

    def test_misnamed_source_keeps_local_bytes_and_original_name_but_corrects_remote_format(self) -> None:
        for image_format, local_suffix, remote_suffix, mime in (
            ("PNG", ".jpeg", ".png", "image/png"),
            ("PNG", ".jpg", ".png", "image/png"),
            ("WEBP", ".jpg", ".webp", "image/webp"),
            ("WEBP", ".png", ".webp", "image/webp"),
            ("JPEG", ".png", ".jpg", "image/jpeg"),
        ):
            with self.subTest(image_format=image_format, local_suffix=local_suffix):
                self.manifest.unlink(missing_ok=True)
                output = BytesIO()
                Image.new("RGB", (30, 20), "navy").save(output, format=image_format)
                original_bytes = output.getvalue()
                path = self.directory_path / ("2026-09-08_2" + local_suffix)
                path.write_bytes(original_bytes)
                with patch.object(assets.requests, "request", side_effect=self.upload_responses(data=original_bytes)) as request:
                    result = assets.ensure_webflow_asset(path, self.config)
                sent_name = request.call_args_list[0].kwargs["json"]["fileName"]
                self.assertTrue(sent_name.endswith(remote_suffix))
                self.assertEqual(request.call_args_list[1].kwargs["files"][0][1], (sent_name, original_bytes, mime))
                self.assertEqual(path.read_bytes(), original_bytes)
                self.assertEqual(result["filename"], path.name)
                self.assertEqual(result["sha256"], hashlib.sha256(original_bytes).hexdigest())
                cached = next(iter(json.loads(self.manifest.read_text())["assets"].values()))
                self.assertEqual(cached["filename"], path.name)

    def test_long_filenames_are_safe_deterministic_and_original_name_is_returned(self) -> None:
        long_path = self.directory_path / (("a" * 120) + ".png")
        long_path.write_bytes(self.data)
        with patch.object(assets.requests, "request", side_effect=self.upload_responses()) as request:
            result = assets.ensure_webflow_asset(long_path, self.config)
        sent_name = request.call_args_list[0].kwargs["json"]["fileName"]
        self.assertLess(len(sent_name), 100)
        self.assertTrue(sent_name.endswith(self.sha256[:16] + ".png"))
        self.assertEqual(result["filename"], long_path.name)

    def test_no_auth_and_no_site_fail_before_network(self) -> None:
        for config in (replace(self.config, webflow_api_token=""), replace(self.config, webflow_site_id=""), replace(self.config, webflow_site_id="../invalid")):
            with patch.object(assets.requests, "request") as request, self.assertRaises(ValueError):
                assets.ensure_webflow_asset(self.path, config)
            request.assert_not_called()

    def test_untrusted_destinations_and_signed_public_urls_are_rejected(self) -> None:
        for url in ("http://cdn.prod.website-files.com/a.png", "https://evil.example/a.png",
                    "https://s3.amazonaws.com/other-bucket/a.png", "https://website-files.com.evil.example/a.png",
                    PUBLIC_URL + "?X-Amz-Signature=private", "https://user:pass@cdn.prod.website-files.com/a.png"):
            with self.subTest(url=url), self.assertRaises(RuntimeError):
                assets._allowed_url(url)
        for url in (PUBLIC_URL, "https://other-bucket.s3.amazonaws.com/a.png"):
            with self.subTest(upload_url=url), self.assertRaises(RuntimeError):
                assets._allowed_url(url, upload=True)

    def test_api_redirect_is_not_followed(self) -> None:
        with patch.object(assets.requests, "request", return_value=response(302, headers={"Location": "https://evil.example"})) as request:
            with self.assertRaisesRegex(RuntimeError, "HTTP 302"):
                assets.ensure_webflow_asset(self.path, self.config)
        self.assertFalse(request.call_args.kwargs["allow_redirects"])

    def test_wrong_site_metadata_fails_without_public_request(self) -> None:
        self.cache()
        with patch.object(assets.requests, "request", return_value=response(payload=metadata(site=OTHER_SITE))) as request:
            with self.assertRaisesRegex(RuntimeError, "requested asset/site"):
                assets.ensure_webflow_asset(self.path, self.config)
        self.assertEqual(request.call_count, 1)


if __name__ == "__main__":
    unittest.main()
