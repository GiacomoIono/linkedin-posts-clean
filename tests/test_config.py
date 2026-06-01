from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from pipeline.config import load_config


class ConfigTests(unittest.TestCase):
    def test_run_x_pipeline_defaults_to_false(self) -> None:
        with patch.dict(os.environ, {}, clear=True), patch("pipeline.config.load_dotenv"):
            config = load_config()

        self.assertFalse(config.run_x_pipeline)

    def test_run_x_pipeline_can_be_enabled(self) -> None:
        with patch.dict(os.environ, {"RUN_X_PIPELINE": "true"}, clear=True), patch("pipeline.config.load_dotenv"):
            config = load_config()

        self.assertTrue(config.run_x_pipeline)


if __name__ == "__main__":
    unittest.main()
