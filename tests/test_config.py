from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from pipeline.config import DEFAULT_OPENAI_IMAGE_MODEL, load_config


class ConfigTests(unittest.TestCase):
    def test_run_x_pipeline_defaults_to_false(self) -> None:
        with patch.dict(os.environ, {}, clear=True), patch("pipeline.config.load_dotenv"):
            config = load_config()

        self.assertFalse(config.run_x_pipeline)

    def test_run_x_pipeline_can_be_enabled(self) -> None:
        with patch.dict(os.environ, {"RUN_X_PIPELINE": "true"}, clear=True), patch("pipeline.config.load_dotenv"):
            config = load_config()

        self.assertTrue(config.run_x_pipeline)

    def test_image_generation_reuses_key_with_a_dedicated_model(self) -> None:
        with patch.dict(os.environ, {"OPENAI_API_KEY": "existing-key"}, clear=True), patch(
            "pipeline.config.load_dotenv"
        ):
            config = load_config()

        self.assertEqual(config.openai_api_key, "existing-key")
        self.assertEqual(config.openai_image_model, DEFAULT_OPENAI_IMAGE_MODEL)
        self.assertEqual(config.image_public_ref, "main")

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
