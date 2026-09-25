from __future__ import annotations

from contextlib import ExitStack
from copy import deepcopy
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import call, patch

from pipeline import main as pipeline_main
from pipeline.config import ENRICHED_POST_PATH, NO_POSTS_FOUND_EXIT_CODE, RAW_POST_PATH, PipelineConfig
from pipeline.link_review import record_link_review


POST = {
    "content": "<p>Hello from LinkedIn.</p>",
    "url": "https://www.linkedin.com/feed/update/urn:li:share:1234567890",
    "published_at": "2026-06-01T08:00:00",
    "images": [],
}

ENRICHED_POST = {
    **POST,
    "headline": "Hello from LinkedIn",
    "description": "A short description",
}

ATTACHED_POST = {
    **ENRICHED_POST,
    "generated_main_image": {
        "url": "https://example.com/generated-main.jpeg",
        "alt": "Generated fallback image",
    },
}

LINKED_POST = {
    **ATTACHED_POST,
    "content": '<p><a href="https://example.org/evidence">Hello from LinkedIn</a>.</p>',
}

LINK_AUDIT = {
    "decision": "links",
    "links_added": 1,
    "proposals_reviewed": 1,
    "rejected_candidates": 0,
    "links": [
        {
            "anchor_text": "Hello from LinkedIn",
            "source_url": "https://example.org/evidence",
            "source_title": "Evidence",
            "source_type": "official_primary",
        }
    ],
}

WEBFLOW_STATUS = {
    "action": "created",
    "item_id": "item-id",
    "published": True,
    "read_back_verified": True,
}


def config(*, force_webflow_sync: bool = False) -> PipelineConfig:
    return PipelineConfig(
        linkedin_access_token="linkedin-token",
        openai_api_key="openai-token",
        openai_model="gpt-test",
        webflow_api_token="webflow-token",
        webflow_collection_id="collection-id",
        webflow_publish=True,
        force_webflow_sync=force_webflow_sync,
    )


class MainPipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.review = patch("pipeline.main.record_link_review", return_value={"report_id": "test"}).start()
        self.addCleanup(patch.stopall)

    def test_main_runs_the_active_linkedin_to_webflow_flow(self) -> None:
        pipeline_config = config()
        patches = [
            patch("pipeline.main.ensure_directories"),
            patch("pipeline.main.load_config", return_value=pipeline_config),
            patch("pipeline.main.fetch_latest_linkedin_post", return_value=POST),
            patch("pipeline.main.find_live_webflow_item", return_value=None),
            patch("pipeline.main.enrich_post", return_value=ENRICHED_POST),
            patch("pipeline.main.attach_generated_main_image", return_value=ATTACHED_POST),
            patch("pipeline.main.source_images", return_value=[]),
            patch("pipeline.main.link_post_body", return_value=(LINKED_POST, LINK_AUDIT)),
            patch("pipeline.main.sync_post_to_webflow", return_value=WEBFLOW_STATUS),
            patch("pipeline.main.write_json"),
            patch("pipeline.main.save_pipeline_state"),
        ]

        with ExitStack() as stack:
            mocks = [stack.enter_context(item) for item in patches]
            exit_code = pipeline_main.main()

        self.assertEqual(exit_code, 0)
        mocks[0].assert_called_once_with()
        mocks[1].assert_called_once_with()
        mocks[2].assert_called_once_with("linkedin-token")
        mocks[3].assert_called_once_with(pipeline_config, POST["url"])
        mocks[4].assert_called_once_with(POST, pipeline_config)
        mocks[5].assert_called_once_with(ENRICHED_POST, pipeline_config)
        mocks[6].assert_called_once_with(POST)
        mocks[7].assert_called_once_with(ATTACHED_POST, pipeline_config)
        mocks[8].assert_called_once_with(LINKED_POST, pipeline_config)
        self.assertEqual(
            mocks[9].call_args_list,
            [call(RAW_POST_PATH, POST), call(ENRICHED_POST_PATH, LINKED_POST)],
        )
        mocks[10].assert_called_once_with(
            POST,
            LINKED_POST,
            {
                "enrichment": "generated",
                "image": "generated_main_image",
                "links": LINK_AUDIT,
                "webflow": WEBFLOW_STATUS,
            },
        )

    def test_main_returns_no_posts_code_without_writing(self) -> None:
        pipeline_config = config()
        patches = [
            patch("pipeline.main.ensure_directories"),
            patch("pipeline.main.load_config", return_value=pipeline_config),
            patch("pipeline.main.fetch_latest_linkedin_post", return_value=None),
            patch("pipeline.main.find_live_webflow_item"),
            patch("pipeline.main.enrich_post"),
            patch("pipeline.main.attach_generated_main_image"),
            patch("pipeline.main.source_images"),
            patch("pipeline.main.link_post_body"),
            patch("pipeline.main.sync_post_to_webflow"),
            patch("pipeline.main.write_json"),
            patch("pipeline.main.save_pipeline_state"),
        ]

        with ExitStack() as stack:
            mocks = [stack.enter_context(item) for item in patches]
            exit_code = pipeline_main.main()

        self.assertEqual(exit_code, NO_POSTS_FOUND_EXIT_CODE)
        mocks[2].assert_called_once_with("linkedin-token")
        for mock in mocks[3:]:
            mock.assert_not_called()

    def test_main_stops_before_local_or_remote_writes_for_existing_live_item(self) -> None:
        pipeline_config = config()
        patches = [
            patch("pipeline.main.ensure_directories"),
            patch("pipeline.main.load_config", return_value=pipeline_config),
            patch("pipeline.main.fetch_latest_linkedin_post", return_value=POST),
            patch("pipeline.main.find_live_webflow_item", return_value={"id": "live-item"}),
            patch("pipeline.main.enrich_post"),
            patch("pipeline.main.attach_generated_main_image"),
            patch("pipeline.main.source_images"),
            patch("pipeline.main.link_post_body"),
            patch("pipeline.main.sync_post_to_webflow"),
            patch("pipeline.main.write_json"),
            patch("pipeline.main.save_pipeline_state"),
        ]

        with ExitStack() as stack:
            mocks = [stack.enter_context(item) for item in patches]
            exit_code = pipeline_main.main()

        self.assertEqual(exit_code, 0)
        mocks[3].assert_called_once_with(pipeline_config, POST["url"])
        for mock in mocks[4:]:
            mock.assert_not_called()

    def test_force_webflow_sync_continues_when_live_item_exists(self) -> None:
        pipeline_config = config(force_webflow_sync=True)

        with (
            patch("pipeline.main.ensure_directories"),
            patch("pipeline.main.load_config", return_value=pipeline_config),
            patch("pipeline.main.fetch_latest_linkedin_post", return_value=POST),
            patch("pipeline.main.find_live_webflow_item", return_value={"id": "live-item"}),
            patch("pipeline.main.enrich_post", return_value=ENRICHED_POST) as enrich_post,
            patch("pipeline.main.attach_generated_main_image", return_value=ATTACHED_POST) as attach_image,
            patch("pipeline.main.source_images", return_value=[]),
            patch("pipeline.main.link_post_body", return_value=(LINKED_POST, LINK_AUDIT)) as link_body,
            patch("pipeline.main.sync_post_to_webflow", return_value=WEBFLOW_STATUS) as sync_post,
            patch("pipeline.main.write_json"),
            patch("pipeline.main.save_pipeline_state"),
        ):
            exit_code = pipeline_main.main()

        self.assertEqual(exit_code, 0)
        enrich_post.assert_called_once_with(POST, pipeline_config)
        attach_image.assert_called_once_with(ENRICHED_POST, pipeline_config)
        link_body.assert_called_once_with(ATTACHED_POST, pipeline_config)
        sync_post.assert_called_once_with(LINKED_POST, pipeline_config)

    def test_main_stops_before_webflow_when_generated_image_is_not_prepared(self) -> None:
        pipeline_config = config()

        with (
            patch("pipeline.main.ensure_directories"),
            patch("pipeline.main.load_config", return_value=pipeline_config),
            patch("pipeline.main.fetch_latest_linkedin_post", return_value=POST),
            patch("pipeline.main.find_live_webflow_item", return_value=None),
            patch("pipeline.main.write_json") as write_json,
            patch("pipeline.main.enrich_post", return_value=ENRICHED_POST),
            patch(
                "pipeline.main.attach_generated_main_image",
                side_effect=RuntimeError("image-preparation stage"),
            ),
            patch("pipeline.main.source_images") as source_images,
            patch("pipeline.main.link_post_body") as link_body,
            patch("pipeline.main.sync_post_to_webflow") as sync_post,
            patch("pipeline.main.save_pipeline_state") as save_state,
            self.assertRaisesRegex(RuntimeError, "image-preparation stage"),
        ):
            pipeline_main.main()

        write_json.assert_called_once_with(RAW_POST_PATH, POST)
        source_images.assert_not_called()
        link_body.assert_not_called()
        sync_post.assert_not_called()
        save_state.assert_not_called()

    def test_main_publishes_original_body_and_reports_unexpected_linking_error(self) -> None:
        pipeline_config = config()

        with (
            patch("pipeline.main.ensure_directories"),
            patch("pipeline.main.load_config", return_value=pipeline_config),
            patch("pipeline.main.fetch_latest_linkedin_post", return_value=POST),
            patch("pipeline.main.find_live_webflow_item", return_value=None),
            patch("pipeline.main.write_json") as write_json,
            patch("pipeline.main.enrich_post", return_value=ENRICHED_POST),
            patch("pipeline.main.attach_generated_main_image", return_value=ATTACHED_POST),
            patch("pipeline.main.source_images", return_value=[]),
            patch(
                "pipeline.main.link_post_body",
                side_effect=RuntimeError("authoritative-link stage"),
            ),
            patch("pipeline.main.sync_post_to_webflow", return_value=WEBFLOW_STATUS) as sync_post,
            patch("pipeline.main.save_pipeline_state") as save_state,
        ):
            self.assertEqual(pipeline_main.main(), 0)

        self.assertEqual(write_json.call_args_list, [call(RAW_POST_PATH, POST), call(ENRICHED_POST_PATH, ATTACHED_POST)])
        sync_post.assert_called_once_with(ATTACHED_POST, pipeline_config)
        audit = save_state.call_args.args[2]["links"]
        self.assertTrue(audit["manual_review_required"])
        self.assertEqual(audit["fallback"], "original_body")
        self.assertEqual(audit["errors"][0]["type"], "RuntimeError")
        self.assertIn("authoritative-link stage", audit["errors"][0]["message"])
        self.assertEqual(self.review.call_count, 2)

    def _run_with_linker(self, linker, *, webflow_error=None):
        original = deepcopy(ATTACHED_POST)
        with (
            patch("pipeline.main.ensure_directories"),
            patch("pipeline.main.load_config", return_value=config()),
            patch("pipeline.main.fetch_latest_linkedin_post", return_value=POST),
            patch("pipeline.main.find_live_webflow_item", return_value=None),
            patch("pipeline.main.write_json"),
            patch("pipeline.main.enrich_post", return_value=ENRICHED_POST),
            patch("pipeline.main.attach_generated_main_image", return_value=original),
            patch("pipeline.main.source_images", return_value=[]),
            patch("pipeline.main.link_post_body", side_effect=linker),
            patch("pipeline.main.sync_post_to_webflow", return_value=WEBFLOW_STATUS, side_effect=webflow_error) as sync,
            patch("pipeline.main.save_pipeline_state") as state,
        ):
            exit_code = pipeline_main.main()
        return exit_code, original, sync, state

    def test_linker_mutation_before_exception_cannot_corrupt_fallback(self) -> None:
        def broken(post, _config):
            post["content"] = "corrupt"
            post["generated_main_image"]["url"] = "corrupt"
            raise ValueError("linker defect")

        code, original, sync, state = self._run_with_linker(broken)
        self.assertEqual(code, 0)
        self.assertEqual(original, ATTACHED_POST)
        sync.assert_called_once_with(ATTACHED_POST, config())
        self.assertTrue(state.call_args.args[2]["links"]["manual_review_required"])

    def test_linker_cannot_modify_non_body_fields_even_when_it_returns(self) -> None:
        def broken(post, _config):
            post["generated_main_image"]["url"] = "corrupt"
            return post, LINK_AUDIT

        code, _, sync, state = self._run_with_linker(broken)
        self.assertEqual(code, 0)
        sync.assert_called_once_with(ATTACHED_POST, config())
        self.assertIn("outside the post body", state.call_args.args[2]["links"]["errors"][0]["message"])

    def test_manual_review_result_always_uses_defensive_original_copy(self) -> None:
        def result(post, _config):
            post["content"] = "corrupt"
            return post, {**LINK_AUDIT, "manual_review_required": True}

        code, _, sync, _ = self._run_with_linker(result)
        self.assertEqual(code, 0)
        sync.assert_called_once_with(ATTACHED_POST, config())

    def test_reporting_failure_does_not_block_cms(self) -> None:
        self.review.side_effect = OSError("read-only report directory")
        code, _, sync, _ = self._run_with_linker(lambda *_: (LINKED_POST, LINK_AUDIT))
        self.assertEqual(code, 0)
        sync.assert_called_once_with(LINKED_POST, config())

    def test_webflow_failure_remains_a_failure_and_updates_report(self) -> None:
        error = RuntimeError("real CMS failure")
        with self.assertRaisesRegex(RuntimeError, "real CMS failure"):
            self._run_with_linker(lambda *_: (LINKED_POST, LINK_AUDIT), webflow_error=error)
        self.assertIs(self.review.call_args.kwargs["webflow_error"], error)

    def test_keyboard_interrupt_is_not_suppressed(self) -> None:
        def interrupted(*_):
            raise KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            self._run_with_linker(interrupted)

    def test_real_report_survives_linker_failure_and_records_successful_cms_item(self) -> None:
        def broken(post, _config):
            post["content"] = "corrupted body"
            raise RuntimeError("network error exposing openai-token")

        self.review.side_effect = record_link_review
        with TemporaryDirectory() as directory, patch("pipeline.link_review.LINK_REVIEW_DIR", Path(directory)), patch.dict(os.environ, {}, clear=True):
            code, _, sync, _ = self._run_with_linker(broken)
            files = list(Path(directory).glob("*.json"))
            self.assertEqual(len(files), 1)
            text = files[0].read_text()
            report = json.loads(text)
        self.assertEqual(code, 0)
        sync.assert_called_once_with(ATTACHED_POST, config())
        self.assertTrue(report["manual_review_required"])
        self.assertEqual(report["original_html"], ATTACHED_POST["content"])
        self.assertEqual(report["final_html"], ATTACHED_POST["content"])
        self.assertEqual(report["webflow"]["item_id"], WEBFLOW_STATUS["item_id"])
        self.assertTrue(report["webflow"]["published"])
        self.assertNotIn("openai-token", text)


if __name__ == "__main__":
    unittest.main()
