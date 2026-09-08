from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from pipeline.image_assets import local_image_path, prepare_post_images


class ImageAssetsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        (self.root / "images" / "generated").mkdir(parents=True)
        self.patch = patch("pipeline.image_assets.REPO_ROOT", self.root)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.config = SimpleNamespace()

    def image(self, name, alt="", generated=False):
        relative = f"images/{'generated/' if generated else ''}{name}"
        (self.root / relative).write_bytes(b"bytes validated by the uploader")
        return {"local_path": relative, "filename": name, "alt": alt}

    def upload(self, path, config):
        self.assertIs(config, self.config)
        return {"url": f"https://cdn.prod.website-files.com/site/id_{path.name}",
                "asset_id": "asset", "sha256": "fingerprint", "filename": path.name}

    def test_original_images_upload_in_order_before_any_generated_work(self):
        images = [self.image("2026-09-08_1.jpg", "First alt"), self.image("2026-09-08_2.png", "Second alt")]
        post = {"content": "<p>Original body</p>", "images": images,
                "generated_main_image": {"url": "https://example.com/stale.png"}}
        with patch("pipeline.image_assets.ensure_webflow_asset", side_effect=self.upload) as upload:
            result = prepare_post_images(post, self.config)
        self.assertEqual(result["content"], post["content"])
        self.assertEqual([i["filename"] for i in result["images"]], [i["filename"] for i in images])
        self.assertEqual([i["alt"] for i in result["images"]], ["First alt", "Second alt"])
        self.assertNotIn("generated_main_image", result)
        self.assertTrue(all("local_path" not in image for image in result["images"]))
        self.assertEqual(upload.call_count, 2)
        self.assertEqual(post["images"], images)

    def test_generated_image_is_kept_separate_from_source_gallery(self):
        generated = self.image("2026-09-08-generated.png", "Reviewed hero", True)
        post = {"content": "<p>Body</p>", "images": []}
        with patch("pipeline.image_assets.attach_generated_main_image", return_value={**post, "generated_main_image": generated}), \
             patch("pipeline.image_assets.ensure_webflow_asset", side_effect=self.upload) as upload:
            result = prepare_post_images(post, self.config)
        self.assertEqual(result["images"], [])
        self.assertEqual(result["generated_main_image"]["alt"], "Reviewed hero")
        self.assertNotIn("local_path", result["generated_main_image"])
        upload.assert_called_once()

    def test_missing_second_source_stops_before_upload_or_fallback(self):
        first = self.image("2026-09-08_1.jpg")
        second = {"local_path": "images/2026-09-08_2.jpg"}
        with patch("pipeline.image_assets.ensure_webflow_asset") as upload, \
             patch("pipeline.image_assets.attach_generated_main_image") as attach, \
             self.assertRaisesRegex(RuntimeError, "missing locally"):
            prepare_post_images({"images": [first, second]}, self.config)
        upload.assert_not_called()
        attach.assert_not_called()

    def test_upload_failure_stops_without_returning_partial_gallery(self):
        images = [self.image("2026-09-08_1.jpg"), self.image("2026-09-08_2.png")]
        first = self.upload(self.root / images[0]["local_path"], self.config)
        with patch("pipeline.image_assets.ensure_webflow_asset", side_effect=[first, RuntimeError("Upload failed")]), \
             self.assertRaisesRegex(RuntimeError, "Upload failed"):
            prepare_post_images({"images": images}, self.config)

    def test_git_urls_are_not_a_fallback_for_missing_local_files(self):
        with patch("pipeline.image_assets.ensure_webflow_asset") as upload, \
             self.assertRaisesRegex(RuntimeError, "local image file"):
            prepare_post_images({"images": [{"url": "https://raw.githubusercontent.com/GiacomoIono/linkedin-posts-clean/main/images/image.png"}]}, self.config)
        upload.assert_not_called()

    def test_invalid_gallery_records_fail_before_generation(self):
        for images in ({}, "image.png", [None], [{}]):
            with self.subTest(images=images), patch("pipeline.image_assets.attach_generated_main_image") as attach, \
                 self.assertRaises(RuntimeError):
                prepare_post_images({"images": images}, self.config)
            attach.assert_not_called()

    def test_paths_cannot_upload_env_or_wrong_image_role(self):
        for path in (".env", "images/../.env", "/tmp/image.png", "images/generated/image.png"):
            with self.subTest(path=path), self.assertRaises(RuntimeError):
                local_image_path({"local_path": path})

    def test_symlink_cannot_expose_files_outside_image_directory(self):
        (self.root / "private.png").write_bytes(b"private")
        (self.root / "images" / "2026-09-08.png").symlink_to(self.root / "private.png")
        with self.assertRaisesRegex(RuntimeError, "symlinks"):
            local_image_path({"local_path": "images/2026-09-08.png"})

    def test_filename_cannot_disagree_with_path(self):
        image = self.image("2026-09-08_1.png")
        image["filename"] = "2026-09-08_2.png"
        with self.assertRaisesRegex(RuntimeError, "filename"):
            local_image_path(image)


if __name__ == "__main__":
    unittest.main()
