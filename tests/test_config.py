from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from pipeline.config import (
    DEFAULT_IMAGE_PUBLIC_REF,
    DEFAULT_OPENAI_IMAGE_MODEL,
    DEFAULT_OPENAI_MODEL,
    DEFAULT_WEBFLOW_COLLECTION_ID,
    GENERATED_IMAGE_DIR,
    IMAGE_DIR,
    load_config,
)


class ConfigTests(unittest.TestCase):
    def test_active_settings_have_safe_defaults(self) -> None:
        with patch.dict(os.environ, {}, clear=True), patch("pipeline.config.load_dotenv"):
            config = load_config()

        self.assertEqual(config.linkedin_access_token, "")
        self.assertEqual(config.openai_api_key, "")
        self.assertEqual(config.openai_model, DEFAULT_OPENAI_MODEL)
        self.assertEqual(config.openai_model, "gpt-5.6-sol")
        self.assertEqual(config.webflow_api_token, "")
        self.assertEqual(config.webflow_collection_id, DEFAULT_WEBFLOW_COLLECTION_ID)
        self.assertTrue(config.webflow_publish)
        self.assertFalse(config.force_webflow_sync)
        self.assertEqual(config.openai_image_model, DEFAULT_OPENAI_IMAGE_MODEL)
        self.assertEqual(config.image_public_ref, DEFAULT_IMAGE_PUBLIC_REF)
        self.assertEqual(GENERATED_IMAGE_DIR, IMAGE_DIR / "generated")

    def test_loads_active_settings_from_environment(self) -> None:
        environment = {
            "LINKEDIN_ACCESS_TOKEN": " linkedin-token ",
            "OPENAI_API_KEY": " openai-token ",
            "OPENAI_MODEL": "gpt-test",
            "WEBFLOW_API_TOKEN": " webflow-token ",
            "WEBFLOW_COLLECTION_ID": "collection-id",
            "WEBFLOW_PUBLISH": "false",
            "FORCE_WEBFLOW_SYNC": "yes",
        }
        with patch.dict(os.environ, environment, clear=True), patch("pipeline.config.load_dotenv"):
            config = load_config()

        self.assertEqual(config.linkedin_access_token, "linkedin-token")
        self.assertEqual(config.openai_api_key, "openai-token")
        self.assertEqual(config.openai_model, "gpt-test")
        self.assertEqual(config.webflow_api_token, "webflow-token")
        self.assertEqual(config.webflow_collection_id, "collection-id")
        self.assertFalse(config.webflow_publish)
        self.assertTrue(config.force_webflow_sync)

    def test_supports_the_github_actions_webflow_token_name(self) -> None:
        environment = {"WEBFLOW_READ_AND_WRITE_BLOG_POSTS": "actions-token"}
        with patch.dict(os.environ, environment, clear=True), patch("pipeline.config.load_dotenv"):
            config = load_config()

        self.assertEqual(config.webflow_api_token, "actions-token")

    def test_image_generation_reuses_key_with_a_dedicated_model(self) -> None:
        with patch.dict(os.environ, {"OPENAI_API_KEY": "existing-key"}, clear=True), patch(
            "pipeline.config.load_dotenv"
        ):
            config = load_config()

        self.assertEqual(config.openai_api_key, "existing-key")
        self.assertEqual(config.openai_image_model, DEFAULT_OPENAI_IMAGE_MODEL)
        self.assertEqual(config.image_public_ref, DEFAULT_IMAGE_PUBLIC_REF)

    def test_image_model_and_public_ref_can_be_overridden(self) -> None:
        environment = {
            "OPENAI_IMAGE_MODEL": "gpt-image-test",
            "IMAGE_PUBLIC_REF": "commit-sha",
        }
        with patch.dict(os.environ, environment, clear=True), patch("pipeline.config.load_dotenv"):
            config = load_config()

        self.assertEqual(config.openai_image_model, "gpt-image-test")
        self.assertEqual(config.image_public_ref, "commit-sha")


if __name__ == "__main__":
    unittest.main()
