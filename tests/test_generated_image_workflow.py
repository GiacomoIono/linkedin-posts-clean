from __future__ import annotations

import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PRODUCTION_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "webflow_cms_pipeline.yml"
VALIDATION_WORKFLOW = (
    REPO_ROOT / ".github" / "workflows" / "validate_linkedin_fetch.yml"
)


class GeneratedImageWorkflowTests(unittest.TestCase):
    def test_production_commits_date_image_and_registry_before_webflow(self) -> None:
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
        self.assertNotIn("images/generated", workflow)

    def test_paid_smoke_artifact_uses_the_normal_date_filename(self) -> None:
        workflow = VALIDATION_WORKFLOW.read_text(encoding="utf-8")

        self.assertIn("path: images/2099-01-01.jpeg", workflow)
        self.assertNotIn("images/generated", workflow)


if __name__ == "__main__":
    unittest.main()
