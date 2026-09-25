from __future__ import annotations

from contextlib import ExitStack, redirect_stdout
from copy import deepcopy
from io import StringIO
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from pipeline import main as pipeline_main
from pipeline.config import PROMPTS_PATH, PipelineConfig
from pipeline.enrichment import load_prompts


def model_response(payload: dict[str, object], *, opened: str | None = None) -> SimpleNamespace:
    output = []
    if opened:
        output = [
            SimpleNamespace(
                type="web_search_call",
                status="completed",
                action=SimpleNamespace(type="search", sources=[]),
            ),
            SimpleNamespace(
                type="web_search_call",
                status="completed",
                action=SimpleNamespace(type="open_page", url=opened),
            ),
        ]
    return SimpleNamespace(output_text=json.dumps(payload), output=output, status="completed")


class LinkReviewIntegrationTests(unittest.TestCase):
    """Keep real linking, report persistence and pipeline state in the same execution."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="link-review-integration-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.data = self.root / "data"
        self.reviews = self.data / "link_reviews"
        self.runner = self.root / "runner"
        self.summary = self.root / "summary.md"
        self.raw_path = self.data / "raw.json"
        self.enriched_path = self.data / "enriched.json"
        self.state_path = self.data / "state.json"
        self.config = PipelineConfig(
            linkedin_access_token="integration-linkedin-token",
            openai_api_key="integration-openai-token",
            openai_model="gpt-test",
            webflow_api_token="integration-webflow-token",
            webflow_collection_id="integration-collection",
            webflow_publish=True,
            force_webflow_sync=False,
        )
        self.post = {
            "content": (
                "<h2>Research &amp; revenue</h2>\n"
                "<p>Revenue reached $10 billion in 2024.</p>\n"
                "<p>The study included 12,000 participants.</p>\n"
                '<p>Existing <a href="https://example.org/reports/prior" target="_blank">source</a>.</p>'
            ),
            "url": "https://www.linkedin.com/feed/update/integration-example",
            "published_at": "2026-09-21T08:00:00",
            "images": [],
        }
        self.enriched = {**deepcopy(self.post), "headline": "Research results", "description": "Original summary."}
        self.attached = {
            **deepcopy(self.enriched),
            "generated_main_image": {"url": "https://images.example.org/hero.png", "alt": "Editorial image"},
        }
        self.webflow_status = {
            "action": "created",
            "item_id": "integration-item-id",
            "published": True,
            "read_back_verified": True,
        }
        self.proposal = {
            "anchor_text": "$10 billion in 2024",
            "claim_text": "Revenue reached $10 billion in 2024.",
            "source_url": "https://example.org/reports/revenue-2024",
            "source_title": "Official revenue report",
            "source_type": "official_primary",
        }

    def run_pipeline(self, responses: list[object]) -> dict[str, object]:
        create = Mock(side_effect=responses)
        client = SimpleNamespace(responses=SimpleNamespace(create=create))
        output = StringIO()
        patches = [
            # Keep actual ensure_directories, writes and state updates inside the fixture.
            patch("pipeline.config.DATA_DIR", self.data),
            patch("pipeline.config.IMAGE_DIR", self.root / "images"),
            patch("pipeline.config.GENERATED_IMAGE_DIR", self.root / "images/generated"),
            patch("pipeline.main.RAW_POST_PATH", self.raw_path),
            patch("pipeline.main.ENRICHED_POST_PATH", self.enriched_path),
            patch("pipeline.main.PIPELINE_STATE_PATH", self.state_path),
            patch("pipeline.link_review.LINK_REVIEW_DIR", self.reviews),
            patch("pipeline.main.load_config", return_value=self.config),
            patch("pipeline.main.fetch_latest_linkedin_post", return_value=deepcopy(self.post)),
            patch("pipeline.main.find_live_webflow_item", return_value=None),
            patch("pipeline.main.enrich_post", return_value=deepcopy(self.enriched)),
            patch("pipeline.main.attach_generated_main_image", return_value=deepcopy(self.attached)),
            patch("pipeline.linking.OpenAI", return_value=client),
        ]
        with ExitStack() as stack:
            stack.enter_context(patch.dict(os.environ, {
                "RUNNER_TEMP": str(self.runner),
                "GITHUB_STEP_SUMMARY": str(self.summary),
                "GITHUB_ACTIONS": "true",
                "GITHUB_REPOSITORY": "example/pipeline",
                "GITHUB_RUN_ID": "12345",
                "GITHUB_RUN_ATTEMPT": "1",
            }, clear=True))
            for item in patches:
                stack.enter_context(item)
            sync = stack.enter_context(patch(
                "pipeline.main.sync_post_to_webflow", return_value=self.webflow_status
            ))
            stack.enter_context(redirect_stdout(output))
            exit_code = pipeline_main.main()

        self.assertEqual(exit_code, 0)
        sync.assert_called_once_with(self.attached, self.config)
        self.assertEqual(json.loads(self.raw_path.read_text()), self.post)
        self.assertEqual(json.loads(self.enriched_path.read_text()), self.attached)
        reports = list(self.reviews.glob("*.json"))
        self.assertEqual(len(reports), 1, "Webflow completion must update the initial report")
        report = json.loads(reports[0].read_text())
        runner_copy = self.runner / "link-reviews" / reports[0].name
        self.assertEqual(json.loads(runner_copy.read_text()), report)
        self.assertEqual(report["source_url"], self.post["url"])
        self.assertEqual(report["original_html"], self.attached["content"])
        self.assertEqual(report["final_html"], self.attached["content"])
        self.assertTrue(report["manual_review_required"])
        self.assertEqual(report["webflow"], {
            "collection_id": self.config.webflow_collection_id,
            "status": "completed",
            **self.webflow_status,
        })
        self.assertEqual(report["actions"]["run_url"], "https://github.com/example/pipeline/actions/runs/12345")
        state = json.loads(self.state_path.read_text())
        self.assertEqual(state["statuses"]["links"], report["audit"])
        self.assertTrue(state["statuses"]["links"]["manual_review_required"])
        self.assertEqual(state["statuses"]["webflow"], self.webflow_status)
        self.assertIn("integration-item-id", self.summary.read_text())
        self.assertIn("Evidence links need manual review", output.getvalue())
        self.assertEqual(
            {path for path in self.root.rglob("*") if path.is_file()},
            {self.raw_path, self.enriched_path, self.state_path, reports[0], runner_copy, self.summary},
        )
        for token in (self.config.openai_api_key, self.config.linkedin_access_token, self.config.webflow_api_token):
            self.assertNotIn(token, reports[0].read_text())
            self.assertNotIn(token, output.getvalue())
        return {"report": report, "model_create": create}

    def test_repeated_negative_coverage_still_uploads_original_and_keeps_both_reviews(self) -> None:
        research = model_response({"decision": "links", "links": [self.proposal]}, opened=self.proposal["source_url"])
        verdict = model_response({"verdicts": [{
            "proposal_id": "link_1",
            "source_url": self.proposal["source_url"],
            "supports_claim": True,
            "authoritative": True,
            "reason": "The official report supports this revenue and reporting period.",
        }]}, opened=self.proposal["source_url"])
        objections = [{
            "claim_text": "The study included 12,000 participants.",
            "anchor_text": "12,000 participants",
            "source_url": "https://research.example.edu/studies/participant-count",
            "reason": reason,
        } for reason in (
            "The participant count lacks a link; the opened methods section confirms it.",
            "The correction still omitted the participant count link supported by the methods section.",
        )]
        checks = [model_response({"complete": False, "objections": [objection]}, opened=objection["source_url"])
                  for objection in objections]

        result = self.run_pipeline([research, verdict, checks[0], research, verdict, checks[1]])

        result["model_create"].assert_called()
        self.assertEqual(result["model_create"].call_count, 6)
        audit = result["report"]["audit"]
        self.assertEqual(audit["corrections_attempted"], 1)
        self.assertEqual(audit["fallback"], "original_body")
        self.assertEqual(audit["links_added"], 0)
        self.assertEqual(len(audit["attempts"]), 2)
        for attempt, objection in zip(audit["attempts"], objections):
            self.assertEqual(attempt["coverage"], {"complete": False, "objections": [objection]})
            self.assertIn(f'<a href="{self.proposal["source_url"]}">', attempt["candidate_html"])
        self.assertEqual(audit["errors"][-1]["stage"], "coverage")

    def check_optional_prompt_failure(self, *, missing: bool) -> None:
        prompt_document = json.loads(PROMPTS_PATH.read_text())
        profile = prompt_document["linkedin_post_enrichment"][0]
        if missing:
            profile.pop("link_coverage_verify_system")
        else:
            profile["link_coverage_verify_system"] = {"invalid": "prompt shape"}
        with tempfile.TemporaryDirectory(prefix="link-prompt-fixture-") as directory:
            prompt_path = Path(directory) / "prompts.json"
            prompt_path.write_text(json.dumps(prompt_document), encoding="utf-8")
            with (
                patch("pipeline.enrichment.PROMPTS_PATH", prompt_path),
                patch.dict(os.environ, {"LINKEDIN_PROMPT_PROFILE": ""}),
            ):
                # The ordinary loader used by required SEO/image stages stays usable.
                prompts = load_prompts()
                for key in (
                    "seo_system", "seo_user", "alt_system", "alt_user",
                    "image_system", "image_user", "image_qa_system", "image_qa_user",
                ):
                    self.assertIsInstance(prompts[key], str, key)
                    self.assertTrue(prompts[key].strip(), key)
                self.assertNotIn("link_coverage_verify_system", prompts)
                # The same file then fails inside real optional linking and is reported.
                result = self.run_pipeline([])

        result["model_create"].assert_not_called()
        audit = result["report"]["audit"]
        self.assertEqual(audit["attempts"], [])
        self.assertEqual(audit["errors"][0]["stage"], "setup")
        self.assertIn("link_coverage_verify_system", audit["errors"][0]["message"])

    def test_missing_optional_link_prompt_does_not_block_required_pipeline(self) -> None:
        self.check_optional_prompt_failure(missing=True)

    def test_invalid_optional_link_prompt_does_not_block_required_pipeline(self) -> None:
        self.check_optional_prompt_failure(missing=False)

    def test_model_timeout_still_uploads_original_and_saves_error_details(self) -> None:
        result = self.run_pipeline([TimeoutError("Model API timed out during evidence research")])

        self.assertEqual(result["model_create"].call_count, 1)
        audit = result["report"]["audit"]
        self.assertEqual(audit["corrections_attempted"], 0)
        self.assertEqual(audit["fallback"], "original_body")
        self.assertEqual(audit["errors"][0]["stage"], "research")
        self.assertEqual(audit["errors"][0]["type"], "TimeoutError")
        self.assertIn("Model API timed out", audit["errors"][0]["message"])
        self.assertIn("TimeoutError", audit["errors"][0]["traceback"])
        self.assertEqual(audit["attempts"][0]["calls"][0]["error"]["type"], "TimeoutError")


if __name__ == "__main__":
    unittest.main()
