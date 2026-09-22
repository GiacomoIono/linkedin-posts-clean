from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from pipeline.audit_image_hosts import (
    ImageAuditError,
    audit_collection,
    audit_items,
    fetch_complete_items,
    main,
    report_url,
    repository_dependent_url,
    srcset_urls,
)
from pipeline.webflow import WebflowError

RAW = "https://raw.githubusercontent.com/GiacomoIono/linkedin-posts-clean/main/images/test.png"
CDN = "https://cdn.prod.website-files.com/site/test.png"


def item(item_id="item", *, body="", **fields):
    return {"id": item_id, "fieldData": {"slug": item_id, "post-body": body, **fields}}


def page(items, *, total=None, offset=0, limit=100):
    return {"items": items, "pagination": {"total": len(items) if total is None else total, "offset": offset, "limit": limit}}


class ImageHostAuditTests(unittest.TestCase):
    def test_repository_url_forms_are_detected_without_flagging_other_repositories(self):
        urls = [
            RAW,
            RAW.replace("https:", ""),
            RAW.replace("raw.githubusercontent.com", "raw.github.com"),
            "https://github.com/GiacomoIono/linkedin-posts-clean/blob/main/images/test.png?raw=true",
            "https://cdn.jsdelivr.net/gh/GiacomoIono/linkedin-posts-clean@main/images/test.png",
            "https://fastly.jsdelivr.net/gh/GiacomoIono/linkedin-posts-clean@abc123/images/test.png",
            "https://GiacomoIono.github.io/linkedin-posts-clean/images/test.png",
            "https://media.githubusercontent.com/media/GiacomoIono/linkedin-posts-clean/main/images/test.png",
        ]
        for url in urls:
            with self.subTest(url=url):
                self.assertTrue(repository_dependent_url(url))
        for url in (
            CDN,
            RAW.replace("linkedin-posts-clean", "another-project"),
            RAW.replace("GiacomoIono", "somebody-else"),
            "https://github.com/openai/openai-python",
            "https://giacomoiono.github.io/another-project/image.png",
            "https://example.com/images/GiacomoIono/linkedin-posts-clean/image.png",
        ):
            with self.subTest(url=url):
                self.assertFalse(repository_dependent_url(url))

    def test_all_native_fields_are_counted_and_flagged(self):
        result = audit_items([item(**{
            "main-image": {"url": RAW},
            "thumbnail-image": {"url": CDN},
            "post-images": [{"url": CDN}, {"url": RAW}],
        })])
        self.assertTrue(result["complete"])
        self.assertEqual(result["native_image_references"], 4)
        self.assertEqual(result["image_host_counts"], {"cdn.prod.website-files.com": 2, "raw.githubusercontent.com": 2})
        self.assertEqual([row["field"] for row in result["image_dependencies"]], ["main-image", "post-images[1]"])

    def test_body_src_srcset_lazy_image_links_and_css_are_inspected(self):
        body = (
            f'<img src="{RAW}">'
            f'<picture><source srcset="{CDN} 1x, {RAW} 2x"></picture>'
            f'<img data-src="{RAW}" data-lazy-srcset="{RAW} 1x, {CDN} 2x">'
            f'<a href="{RAW}">Open image</a>'
            f'<div style="background-image:url(\'{RAW}\')"></div>'
            f'<style>.hero {{background:url("{RAW}")}}</style>'
            f'<a href="{RAW}"><img src="{CDN}"></a>'
        )
        result = audit_items([item(body=body)])
        self.assertTrue(result["complete"])
        self.assertEqual(result["embedded_image_references"], 11)
        self.assertEqual(len(result["image_dependencies"]), 8)
        self.assertEqual(result["repository_hyperlinks"], [])

    def test_text_repository_hyperlinks_are_separate_and_unrelated_citations_are_ignored(self):
        body = (
            '<a href="https://github.com/GiacomoIono/linkedin-posts-clean">Source repository</a>'
            '<a href="https://github.com/openai/openai-python">SDK documentation</a>'
            '<a href="https://raw.githubusercontent.com/other/project/main/README.md">Another source</a>'
        )
        result = audit_items([item(body=body)])
        self.assertTrue(result["complete"])
        self.assertEqual(result["image_dependencies"], [])
        self.assertEqual(result["embedded_image_references"], 0)
        self.assertEqual(len(result["repository_hyperlinks"]), 1)

    def test_webflow_lightbox_images_are_checked_without_executing_script(self):
        lightbox = json.dumps({"items": [{"type": "image", "url": RAW}]})
        result = audit_items([item(body=f'<script type="application/json" class="w-json">{lightbox}</script>')])
        self.assertTrue(result["complete"])
        self.assertEqual(result["image_dependencies"][0]["field"], "post-body.lightbox[url]")

    def test_invalid_native_images_relative_sources_and_lightbox_json_fail_audit(self):
        records = [
            item("missing-url", **{"main-image": {"fileId": "id"}}),
            item("malformed-gallery", **{"post-images": {"url": RAW}}),
            item("relative-image", body='<img src="images/local.png">'),
            item("broken-lightbox", body='<script class="w-json">invalid JSON</script>'),
            item("incomplete-lightbox", body='<script class="w-json">{"items":'),
        ]
        result = audit_items(records)
        self.assertFalse(result["complete"])
        self.assertEqual(len(result["errors"]), 5)

    def test_srcset_retains_urls_and_ignores_width_or_density_descriptors(self):
        self.assertEqual(srcset_urls(f"{RAW} 400w, {CDN} 800w"), [RAW, CDN])
        self.assertEqual(srcset_urls(f" {RAW}, {CDN} "), [RAW, CDN])
        self.assertEqual(srcset_urls("data:image/png;base64,abcd 1x, https://example.com/image.png 2x"), ["data:image/png;base64,abcd", "https://example.com/image.png"])

    def test_report_urls_remove_credentials_and_sensitive_query_values(self):
        value = report_url("https://user:password@github.com/GiacomoIono/linkedin-posts-clean/image.png?access_token=secret&raw=true#secret-fragment")
        self.assertNotIn("password", value)
        self.assertNotIn("secret", value)
        self.assertIn("access_token=REDACTED", value)
        self.assertIn("raw=true", value)

    def test_complete_pagination_visits_every_page_and_only_reads(self):
        client = Mock(collection_id="collection")
        client.request.side_effect = [page([item(str(i)) for i in range(100)], total=103), page([item(str(i)) for i in range(100, 103)], total=103, offset=100)]
        result = fetch_complete_items(client, live=True)
        self.assertEqual(len(result), 103)
        self.assertEqual([call.args for call in client.request.call_args_list], [("GET", "/collections/collection/items/live")] * 2)
        self.assertEqual([call.kwargs["params"]["offset"] for call in client.request.call_args_list], [0, 100])

    def test_incomplete_duplicate_missing_or_inconsistent_pagination_fails(self):
        broken_pages = [
            [{"items": []}],
            [page([{"fieldData": {}}])],
            [page([item("same"), item("same")])],
            [page([], total=10)],
            [page([item()], total=0)],
            [page([], offset=100)],
            [page([], limit=50)],
            [page([], total=True)],
            [page([item("a")], total=2), page([item("b")], total=3, offset=1)],
            [page([item("a")], total=2), page([item("a")], total=2, offset=1)],
        ]
        for responses in broken_pages:
            client = Mock(collection_id="collection")
            client.request.side_effect = responses
            with self.subTest(responses=responses), self.assertRaises(ImageAuditError):
                fetch_complete_items(client, live=False)

    def test_full_audit_checks_staged_and_live_and_reports_live_only_dependency(self):
        client = Mock(collection_id="collection")
        client.request.side_effect = [page([item(**{"main-image": {"url": CDN}})]), page([item(**{"main-image": {"url": RAW}})])]
        report = audit_collection(client)
        self.assertTrue(report["complete"])
        self.assertFalse(report["ready_for_private_images"])
        self.assertEqual(report["image_dependency_count"], 1)
        self.assertEqual(report["endpoints"]["live"]["items_audited"], 1)
        self.assertEqual([call.args[1] for call in client.request.call_args_list], ["/collections/collection/items", "/collections/collection/items/live"])

    def test_failed_endpoint_does_not_prevent_the_other_read_only_audit(self):
        client = Mock(collection_id="collection")
        client.request.side_effect = [WebflowError("Webflow GET failed: 403 private-server-body-secret"), page([])]
        report = audit_collection(client)
        self.assertFalse(report["complete"])
        self.assertFalse(report["ready_for_private_images"])
        self.assertTrue(report["endpoints"]["live"]["complete"])
        serialized = json.dumps(report)
        self.assertNotIn("private-server-body-secret", serialized)
        self.assertIn("403", serialized)

    def test_cli_writes_compact_report_and_fails_for_image_dependencies(self):
        config = SimpleNamespace(webflow_api_token="api-secret", webflow_collection_id="collection")
        client = Mock(collection_id="collection")
        client.request.side_effect = [page([item(body="PRIVATE FULL BODY TEXT", **{"main-image": {"url": RAW}})]), page([])]
        with TemporaryDirectory() as directory:
            output = Path(directory) / "audit.json"
            with patch("pipeline.audit_image_hosts.load_config", return_value=config), patch("pipeline.audit_image_hosts.WebflowClient", return_value=client):
                code = main(["--output", str(output)])
            report = output.read_text()
        self.assertEqual(code, 1)
        self.assertNotIn("api-secret", report)
        self.assertNotIn("PRIVATE FULL BODY TEXT", report)
        self.assertIn("image_dependencies", report)

    def test_clean_audit_returns_success_even_with_non_image_repository_link(self):
        client = Mock(collection_id="collection")
        client.request.side_effect = [page([item(body='<a href="https://github.com/GiacomoIono/linkedin-posts-clean">Code</a>', **{"main-image": {"url": CDN}})]), page([])]
        report = audit_collection(client)
        self.assertTrue(report["ready_for_private_images"])
        self.assertEqual(report["repository_hyperlink_count"], 1)


if __name__ == "__main__":
    unittest.main()
