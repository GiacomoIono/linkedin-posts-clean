from __future__ import annotations

from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from pipeline.config import PipelineConfig
from pipeline.link_review import exception_details, record_link_review, redact_diagnostics


def config() -> PipelineConfig:
    return PipelineConfig(
        linkedin_access_token="linkedin-secret-value",
        openai_api_key="openai-secret-value",
        openai_model="test-model",
        webflow_api_token="webflow-secret-value",
        webflow_collection_id="collection-id",
        webflow_publish=True,
        force_webflow_sync=False,
    )


POST = {
    "content": "<p>Original text.</p>",
    "url": "https://www.linkedin.com/feed/update/urn:li:ugcPost:123",
    "published_at": "2026-09-21T08:00:00",
    "headline": "Original title",
}
AUDIT = {
    "manual_review_required": True,
    "decision": "manual_review_required",
    "fallback": "original_body",
    "attempts": [{"attempt": 1, "calls": [{"stage": "coverage", "output_text": "false"}]}],
    "errors": [{"stage": "coverage", "message": "unresolved claim"}],
}


class LinkReviewTests(unittest.TestCase):
    def test_recursive_sanitiser_removes_config_env_and_recognisable_tokens(self) -> None:
        raw = {
            "list": ["linkedin-secret-value", "openai-secret-value", "webflow-secret-value"],
            "env": {"nested": "custom-secret-value"},
            "body": 'Bearer hidden-credential sk-proj-abcdefghijklm ghp_abcdefghijklmn github_pat_abcdefghijklmn',
            "url": "https://example.org/?access_token=hidden-query&ok=1",
            "raw_response": '{"api_key": "unknown-raw-key", "password": "unknown-password"}',
        }
        with patch.dict(os.environ, {"CUSTOM_API_KEY": "custom-secret-value"}, clear=True):
            clean = redact_diagnostics(raw, config())
        serialised = json.dumps(clean)
        for secret in ("linkedin-secret-value", "openai-secret-value", "webflow-secret-value", "custom-secret-value", "hidden-credential", "sk-proj-abcdefghijklm", "ghp_abcdefghijklmn", "github_pat_abcdefghijklmn", "hidden-query", "unknown-raw-key", "unknown-password"):
            self.assertNotIn(secret, serialised)
        self.assertEqual(clean["url"], "https://example.org/?access_token=[REDACTED]&ok=1")
        self.assertEqual(raw["list"][0], "linkedin-secret-value")

    def test_partial_config_supported_for_linker_unit_tests(self) -> None:
        self.assertEqual(redact_diagnostics("key-secret", SimpleNamespace(openai_api_key="key-secret")), "[REDACTED]")

    def test_exception_details_capture_sanitised_traceback_and_api_context(self) -> None:
        try:
            raise RuntimeError("API failed: openai-secret-value")
        except RuntimeError as exc:
            exc.status_code = 429
            exc.request_id = "request-123"
            exc.body = {"error": "Bearer unknown-token"}
            exc.response = SimpleNamespace(text="webflow-secret-value")
            details = exception_details(exc, config())
        self.assertEqual(details["status_code"], 429)
        self.assertEqual(details["request_id"], "request-123")
        self.assertIn("test_exception_details_capture_sanitised_traceback", details["traceback"])
        self.assertNotIn("openai-secret-value", json.dumps(details))
        self.assertNotIn("unknown-token", json.dumps(details))
        self.assertEqual(details["response_text"], "[REDACTED]")

    def test_report_records_identity_attempt_and_webflow_outcome_in_both_locations(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            env = {
                "GITHUB_ACTIONS": "true", "GITHUB_REPOSITORY": "owner/repo",
                "GITHUB_RUN_ID": "321", "GITHUB_RUN_ATTEMPT": "2", "GITHUB_SHA": "sha",
                "RUNNER_TEMP": str(root / "runner"), "GITHUB_STEP_SUMMARY": str(root / "summary.md"),
            }
            with patch("pipeline.link_review.LINK_REVIEW_DIR", root / "reviews"), patch.dict(os.environ, env, clear=True), redirect_stdout(io.StringIO()) as output:
                report = record_link_review(POST, POST, AUDIT, config())
                first = json.loads(next((root / "reviews").glob("*.json")).read_text())
                self.assertEqual(first["webflow"]["status"], "pending")
                updated = record_link_review(POST, POST, AUDIT, config(), report=report, webflow_status={"item_id": "item-123", "published": True, "read_back_verified": True})
                second_report = record_link_review(POST, POST, AUDIT, config())
            self.assertNotEqual(updated["report_id"], second_report["report_id"])
            self.assertEqual(updated["source_url"], POST["url"])
            self.assertEqual(updated["original_html"], POST["content"])
            self.assertEqual(updated["final_html"], POST["content"])
            self.assertEqual(updated["model"], "test-model")
            self.assertEqual(updated["actions"]["attempt_url"], "https://github.com/owner/repo/actions/runs/321/attempts/2")
            self.assertEqual(updated["audit"]["attempts"], AUDIT["attempts"])
            self.assertEqual(updated["webflow"]["collection_id"], "collection-id")
            self.assertEqual(updated["webflow"]["item_id"], "item-123")
            self.assertEqual(updated["webflow"]["status"], "completed")
            self.assertNotIn("editor_url", updated["webflow"])
            for path in (root / "reviews", root / "runner" / "link-reviews"):
                persisted = json.loads((path / f"{updated['report_id']}.json").read_text())
                self.assertEqual(persisted, updated)
            self.assertIn("::warning title=Evidence links need manual review::", output.getvalue())
            self.assertIn("item-123", (root / "summary.md").read_text())

    def test_primary_report_write_failure_still_creates_runner_artifact(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            blocked = root / "blocked"
            blocked.write_text("file, not directory")
            with patch("pipeline.link_review.LINK_REVIEW_DIR", blocked), patch.dict(os.environ, {"RUNNER_TEMP": str(root)}, clear=True), redirect_stdout(io.StringIO()) as output:
                report = record_link_review(POST, POST, AUDIT, config())
            self.assertIsNotNone(report)
            self.assertTrue((root / "link-reviews" / f"{report['report_id']}.json").is_file())
            self.assertIn("could not be saved", output.getvalue())

    def test_report_total_write_failure_is_nonblocking(self) -> None:
        with patch("pipeline.link_review._persist_report", side_effect=OSError("unwritable")), redirect_stdout(io.StringIO()):
            self.assertIsNotNone(record_link_review(POST, POST, AUDIT, config()))

    def test_webflow_error_is_recorded_without_claiming_no_side_effects(self) -> None:
        with TemporaryDirectory() as directory, patch("pipeline.link_review.LINK_REVIEW_DIR", Path(directory)), patch.dict(os.environ, {}, clear=True), redirect_stdout(io.StringIO()):
            report = record_link_review(POST, POST, AUDIT, config(), webflow_error=RuntimeError("publish read-back failed"))
        self.assertEqual(report["webflow"]["status"], "failed")
        self.assertNotIn("published", report["webflow"])
        self.assertIn("publish read-back failed", report["webflow"]["error"]["message"])

    def test_untrusted_diagnostics_are_redacted_and_cannot_inject_workflow_commands(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            post = {**POST, "url": POST["url"] + "\n::error::injected openai-secret-value"}
            audit = {**AUDIT, "errors": [{"message": "\r\n::error::injected webflow-secret-value <script>alert(1)</script>"}]}
            with patch("pipeline.link_review.LINK_REVIEW_DIR", root / "reviews"), patch.dict(os.environ, {"GITHUB_ACTIONS": "true", "GITHUB_STEP_SUMMARY": str(root / "summary")}, clear=True), redirect_stdout(io.StringIO()) as output:
                report = record_link_review(post, post, audit, config())
            visible = output.getvalue()
            persisted = (root / "reviews" / f"{report['report_id']}.json").read_text()
            summary = (root / "summary").read_text()
            for text in (visible, persisted, summary):
                self.assertNotIn("openai-secret-value", text)
                self.assertNotIn("webflow-secret-value", text)
            self.assertNotIn("\n::error::", visible)
            self.assertIn("%0A::error::", visible)
            self.assertNotIn("<script>", summary)
            self.assertIn("&lt;script&gt;", summary)

    def test_success_report_does_not_warn(self) -> None:
        with TemporaryDirectory() as directory, patch("pipeline.link_review.LINK_REVIEW_DIR", Path(directory)), patch.dict(os.environ, {}, clear=True), redirect_stdout(io.StringIO()) as output:
            report = record_link_review(POST, POST, {"decision": "no_material_claims"}, config())
        self.assertFalse(report["manual_review_required"])
        self.assertNotIn("WARNING:", output.getvalue())

    def test_workflow_uploads_only_current_runner_reports_before_commit(self) -> None:
        workflow = (Path(__file__).resolve().parents[1] / ".github/workflows/webflow_cms_pipeline.yml").read_text()
        upload = workflow.index("- name: Upload evidence-link review reports")
        main = workflow.index("- name: Run Webflow CMS pipeline")
        commit = workflow.index("- name: Commit and push pipeline outputs")
        self.assertLess(main, upload)
        self.assertLess(upload, commit)
        upload_block = workflow[upload:commit]
        self.assertIn("if: ${{ always() }}", upload_block)
        self.assertIn("${{ runner.temp }}/link-reviews/*.json", upload_block)
        self.assertIn("continue-on-error: true", upload_block)


if __name__ == "__main__":
    unittest.main()
