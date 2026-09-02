from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from pipeline.config import DEFAULT_OPENAI_MODEL, DEFAULT_WEBFLOW_COLLECTION_ID, load_config


class ConfigTests(unittest.TestCase):
    def test_active_settings_have_safe_defaults(self) -> None:
        with patch.dict(os.environ, {}, clear=True), patch("pipeline.config.load_dotenv"):
            config = load_config()

        self.assertEqual(config.linkedin_access_token, "")
        self.assertEqual(config.openai_api_key, "")
        self.assertEqual(DEFAULT_OPENAI_MODEL, "gpt-5.6")
        self.assertEqual(config.openai_model, DEFAULT_OPENAI_MODEL)
        self.assertEqual(config.webflow_api_token, "")
        self.assertEqual(config.webflow_collection_id, DEFAULT_WEBFLOW_COLLECTION_ID)
        self.assertTrue(config.webflow_publish)
        self.assertFalse(config.force_webflow_sync)

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


if __name__ == "__main__":
    unittest.main()
