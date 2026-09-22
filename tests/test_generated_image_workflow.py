from __future__ import annotations

import os
import re
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PRODUCTION_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "webflow_cms_pipeline.yml"
VALIDATION_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "validate_linkedin_fetch.yml"


def workflow_step(name: str) -> str:
    workflow = PRODUCTION_WORKFLOW.read_text(encoding="utf-8")
    start = workflow.index(f"      - name: {name}\n")
    end = workflow.find("      - name:", start + 1)
    return workflow[start:] if end == -1 else workflow[start:end]


def step_script(name: str) -> str:
    step = workflow_step(name)
    match = re.search(r"^        run: \|\n((?:          .*\n|\n)+)", step, re.MULTILINE)
    if match is None:
        raise AssertionError(f"No multiline run script in {name}")
    return textwrap.dedent(match.group(1))


class GeneratedImageWorkflowTests(unittest.TestCase):
    def test_production_checks_config_and_saves_generated_files_before_pipeline(self) -> None:
        workflow = PRODUCTION_WORKFLOW.read_text(encoding="utf-8")
        preflight = workflow.index("Check required GitHub configuration before paid work")
        prepare = workflow.index("python -m pipeline.prepare_image")
        stage = workflow.index("data/generated_main_images.json")
        webflow = workflow.index("python -m pipeline.main")

        self.assertLess(preflight, prepare)
        self.assertLess(prepare, stage)
        self.assertLess(stage, webflow)
        pre_webflow = workflow[prepare:webflow]
        self.assertEqual(pre_webflow.count("images/generated"), 2)
        self.assertGreaterEqual(pre_webflow.count("data/generated_main_images.json"), 2)
        self.assertNotIn("IMAGE_PUBLIC_REF", workflow)
        self.assertNotIn("--verify-public", workflow)
        self.assertIn("github.ref == 'refs/heads/main'", workflow)
        self.assertIn("cancel-in-progress: false", workflow)

    def test_webflow_credentials_only_come_from_secrets(self) -> None:
        workflow = PRODUCTION_WORKFLOW.read_text(encoding="utf-8")
        self.assertNotIn("vars.WEBFLOW_READ_AND_WRITE_BLOG_POSTS", workflow)
        self.assertNotIn("vars.WEBFLOW_SITE_ID", workflow)
        self.assertEqual(workflow.count("WEBFLOW_API_TOKEN: ${{ secrets.WEBFLOW_READ_AND_WRITE_BLOG_POSTS }}"), 3)
        self.assertEqual(workflow.count("WEBFLOW_SITE_ID: ${{ secrets.WEBFLOW_SITE_ID }}"), 3)

    def test_preflight_rejects_missing_config_without_printing_credentials(self) -> None:
        environment = dict(os.environ, LINKEDIN_ACCESS_TOKEN="secret-linkedin-value",
                           OPENAI_API_KEY="secret-openai-value", WEBFLOW_API_TOKEN="secret-webflow-value",
                           WEBFLOW_SITE_ID="   ")
        result = subprocess.run(
            ["bash", "-c", step_script("Check required GitHub configuration before paid work")],
            env=environment, capture_output=True, text=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("WEBFLOW_SITE_ID", result.stdout)
        self.assertNotIn("secret-", result.stdout + result.stderr)
        environment["WEBFLOW_SITE_ID"] = "site-id"
        result = subprocess.run(
            ["bash", "-c", step_script("Check required GitHub configuration before paid work")],
            env=environment, capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_no_post_is_success_but_real_pipeline_failure_stays_failed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            command = Path(directory) / "python"
            command.write_text('#!/bin/sh\nexit "$TEST_PIPELINE_STATUS"\n', encoding="utf-8")
            command.chmod(0o755)
            for pipeline_status, expected in ((0, 0), (2, 0), (1, 1)):
                with self.subTest(pipeline_status=pipeline_status):
                    environment = dict(os.environ, PATH=directory + os.pathsep + os.environ["PATH"],
                                       TEST_PIPELINE_STATUS=str(pipeline_status))
                    result = subprocess.run(
                        ["bash", "-c", step_script("Run Webflow CMS pipeline")],
                        env=environment, capture_output=True, text=True,
                    )
                    self.assertEqual(result.returncode, expected, result.stderr)

        pipeline = workflow_step("Run Webflow CMS pipeline")
        recovery = workflow_step("Save Webflow recovery state after a pipeline failure")
        self.assertIn("id: pipeline", pipeline)
        self.assertNotIn("continue-on-error", pipeline)
        self.assertIn("!cancelled() && steps.pipeline.outcome == 'failure'", recovery)
        outputs = workflow_step("Commit and push pipeline outputs")
        self.assertIn("success() && steps.pipeline.outcome == 'success'", outputs)

    def test_recovery_artifact_contains_only_asset_and_cms_checkpoints(self) -> None:
        artifact = workflow_step("Keep Webflow recovery state as a failed-run artifact")
        self.assertIn("failure()", artifact)
        self.assertIn("name: webflow-recovery-state", artifact)
        self.assertIn("retention-days: 7", artifact)
        paths = artifact.split("          path: |\n", 1)[1].split("          if-no-files-found:", 1)[0]
        self.assertEqual([path.strip() for path in paths.splitlines() if path.strip()],
                         ["data/webflow_assets.json", "data/webflow_items.json"])

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


class AssetCacheRecoveryWorkflowTests(unittest.TestCase):
    """Execute the actual workflow shell against disposable local Git repositories."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.remote = self.root / "remote.git"
        self.checkout = self.root / "checkout"
        self.environment = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
        self.git("init", "--bare", "--initial-branch=main", str(self.remote), cwd=self.root)
        self.git("init", "--initial-branch=main", str(self.checkout), cwd=self.root)
        self.configure(self.checkout)
        (self.checkout / "data").mkdir()
        (self.checkout / "images").mkdir()
        (self.checkout / "data/webflow_assets.json").write_text('{"assets": {}}\n', encoding="utf-8")
        (self.checkout / "data/webflow_items.json").write_text('{"items": {}}\n', encoding="utf-8")
        (self.checkout / "data/pipeline_state.json").write_text("initial-state\n", encoding="utf-8")
        (self.checkout / "images/original.png").write_bytes(b"initial-image")
        self.git("add", ".")
        self.git("commit", "-m", "initial")
        self.git("remote", "add", "origin", str(self.remote))
        self.git("push", "origin", "HEAD:main")

    def git(self, *args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["git", *args], cwd=cwd or self.checkout, env=self.environment,
                              check=True, capture_output=True, text=True)

    def configure(self, checkout: Path) -> None:
        self.git("config", "user.name", "Workflow Test", cwd=checkout)
        self.git("config", "user.email", "workflow-test@example.invalid", cwd=checkout)
        self.git("config", "commit.gpgsign", "false", cwd=checkout)

    def execute(self, step: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["bash", "-c", step_script(step)], cwd=self.checkout,
                              env=self.environment, capture_output=True, text=True)

    def test_failed_pipeline_persists_only_upload_and_cms_recovery_state(self) -> None:
        (self.checkout / "data/webflow_assets.json").write_text('{"assets": {"upload": "hosted"}}\n', encoding="utf-8")
        pending_state = '{"items": {"post-url": {"verification_pending": {"item_id": "item-id"}}}}\n'
        (self.checkout / "data/webflow_items.json").write_text(pending_state, encoding="utf-8")
        (self.checkout / "data/pipeline_state.json").write_text("failed-run-state\n", encoding="utf-8")
        (self.checkout / "data/last_linkedin_post.json").write_text("partial-raw-post\n", encoding="utf-8")
        (self.checkout / "data/last_linkedin_post.enriched.json").write_text("partial-enriched-post\n", encoding="utf-8")
        (self.checkout / "images/original.png").write_bytes(b"unrelated-image-change")
        (self.checkout / "data/incomplete.json").write_text("untracked-output\n", encoding="utf-8")
        result = self.execute("Save Webflow recovery state after a pipeline failure")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        changed = self.git("diff-tree", "--no-commit-id", "--name-only", "-r", "origin/main").stdout.splitlines()
        self.assertEqual(changed, ["data/webflow_assets.json", "data/webflow_items.json"])
        self.assertEqual(self.git("show", "origin/main:data/webflow_items.json").stdout, pending_state)
        self.assertEqual(self.git("show", "origin/main:data/pipeline_state.json").stdout, "initial-state\n")
        self.assertEqual((self.checkout / "data/pipeline_state.json").read_text(), "failed-run-state\n")
        self.assertIn("data/incomplete.json", self.git("status", "--porcelain").stdout)
        self.assertIn("data/last_linkedin_post.json", self.git("status", "--porcelain").stdout)
        self.assertIn("data/last_linkedin_post.enriched.json", self.git("status", "--porcelain").stdout)

    def test_no_new_uploads_produces_no_commit(self) -> None:
        before = self.git("rev-parse", "HEAD").stdout
        result = self.execute("Save Webflow recovery state after a pipeline failure")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.git("rev-parse", "HEAD").stdout, before)

    def test_cms_recovery_is_saved_when_no_asset_cache_exists(self) -> None:
        self.git("rm", "data/webflow_assets.json")
        self.git("commit", "-m", "baseline without asset cache")
        self.git("push", "origin", "HEAD:main")
        (self.checkout / "data/webflow_items.json").write_text('{"items": {"post": {"verification_pending": {}}}}\n', encoding="utf-8")
        result = self.execute("Save Webflow recovery state after a pipeline failure")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        changed = self.git("diff-tree", "--no-commit-id", "--name-only", "-r", "origin/main").stdout.splitlines()
        self.assertEqual(changed, ["data/webflow_items.json"])

    def test_absent_recovery_files_produce_no_commit(self) -> None:
        self.git("rm", "data/webflow_assets.json", "data/webflow_items.json")
        self.git("commit", "-m", "baseline without recovery files")
        self.git("push", "origin", "HEAD:main")
        before = self.git("rev-parse", "HEAD").stdout
        result = self.execute("Save Webflow recovery state after a pipeline failure")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.git("rev-parse", "HEAD").stdout, before)

    def test_first_untracked_upload_cache_is_persisted(self) -> None:
        self.git("rm", "data/webflow_assets.json")
        self.git("commit", "-m", "baseline before first asset upload")
        self.git("push", "origin", "HEAD:main")
        (self.checkout / "data/webflow_assets.json").write_text('{"assets": {"first": "hosted"}}\n', encoding="utf-8")
        (self.checkout / "data/incomplete.json").write_text("untracked-output\n", encoding="utf-8")
        result = self.execute("Save Webflow recovery state after a pipeline failure")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        changed = self.git("diff-tree", "--no-commit-id", "--name-only", "-r", "origin/main").stdout.splitlines()
        self.assertEqual(changed, ["data/webflow_assets.json"])
        self.assertIn("first", self.git("show", "origin/main:data/webflow_assets.json").stdout)
        self.assertIn("data/incomplete.json", self.git("status", "--porcelain").stdout)

    def test_cache_conflict_fails_visibly_for_success_and_failure_paths(self) -> None:
        other = self.root / "other"
        self.git("clone", str(self.remote), str(other), cwd=self.root)
        self.configure(other)
        (other / "data/webflow_assets.json").write_text('{"assets": {"remote": "hosted"}}\n', encoding="utf-8")
        self.git("add", ".", cwd=other)
        self.git("commit", "-m", "competing upload", cwd=other)
        self.git("push", "origin", "HEAD:main", cwd=other)

        (self.checkout / "data/webflow_assets.json").write_text('{"assets": {"local": "hosted"}}\n', encoding="utf-8")
        for step in ("Save Webflow recovery state after a pipeline failure", "Commit and push pipeline outputs"):
            with self.subTest(step=step):
                # Each script needs a new local diff; the rejected local commit remains reviewable.
                with (self.checkout / "data/webflow_assets.json").open("a", encoding="utf-8") as cache:
                    cache.write("\n")
                result = self.execute(step)
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("::error::", result.stdout)
                self.assertNotIn("local", self.git("show", "origin/main:data/webflow_assets.json").stdout)


if __name__ == "__main__":
    unittest.main()
