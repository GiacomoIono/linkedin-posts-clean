from __future__ import annotations

import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PRODUCTION_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "webflow_cms_pipeline.yml"
VALIDATION_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "validate_linkedin_fetch.yml"


class GeneratedImageWorkflowTests(unittest.TestCase):
    def test_production_commits_generated_folder_and_manifest_before_webflow(self) -> None:
        workflow = PRODUCTION_WORKFLOW.read_text(encoding="utf-8")
        prepare = workflow.index("python -m pipeline.prepare_image")
        stage = workflow.index("data/generated_main_images.json")
        pin = workflow.index("IMAGE_PUBLIC_REF=$(git rev-parse HEAD)")
        verify = workflow.index("python -m pipeline.prepare_image --verify-public")
        webflow = workflow.index("python -m pipeline.main")

        self.assertLess(prepare, stage)
        self.assertLess(stage, pin)
        self.assertLess(pin, verify)
        self.assertLess(verify, webflow)
        pre_webflow = workflow[prepare:webflow]
        self.assertEqual(pre_webflow.count("images/generated"), 2)
        self.assertGreaterEqual(pre_webflow.count("data/generated_main_images.json"), 2)

    def test_sol_is_pinned_for_prepare_enrichment_and_manual_smoke(self) -> None:
        production = PRODUCTION_WORKFLOW.read_text(encoding="utf-8")
        validation = VALIDATION_WORKFLOW.read_text(encoding="utf-8")

        self.assertEqual(production.count("OPENAI_MODEL: gpt-5.6-sol"), 2)
        self.assertIn("OPENAI_MODEL: gpt-5.6-sol", validation)
        self.assertNotIn("gpt-5-nano", production)
        self.assertNotIn("gpt-5-nano", validation)
        self.assertIn("OPENAI_IMAGE_MODEL: gpt-image-2", production)
        self.assertIn("OPENAI_IMAGE_MODEL: gpt-image-2", validation)

    def test_paid_smoke_artifact_uses_generated_png_path(self) -> None:
        workflow = VALIDATION_WORKFLOW.read_text(encoding="utf-8")

        self.assertIn("path: images/generated/2099-01-01-*.png", workflow)


if __name__ == "__main__":
    unittest.main()
