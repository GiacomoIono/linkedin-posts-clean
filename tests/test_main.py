from __future__ import annotations

from contextlib import ExitStack
import unittest
from unittest.mock import call, patch

from pipeline import main as pipeline_main
from pipeline.config import ENRICHED_POST_PATH, NO_POSTS_FOUND_EXIT_CODE, RAW_POST_PATH, PipelineConfig


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

WEBFLOW_STATUS = {
    "action": "created",
    "item_id": "item-id",
    "published": True,
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
    def test_main_runs_the_active_linkedin_to_webflow_flow(self) -> None:
        pipeline_config = config()
        patches = [
            patch("pipeline.main.ensure_directories"),
            patch("pipeline.main.load_config", return_value=pipeline_config),
            patch("pipeline.main.fetch_latest_linkedin_post", return_value=POST),
            patch("pipeline.main.find_live_webflow_item", return_value=None),
            patch("pipeline.main.enrich_post", return_value=ENRICHED_POST),
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
        self.assertEqual(
            mocks[6].call_args_list,
            [call(RAW_POST_PATH, POST), call(ENRICHED_POST_PATH, ENRICHED_POST)],
        )
        mocks[7].assert_called_once_with(
            POST,
            ENRICHED_POST,
            {"enrichment": "generated", "webflow": WEBFLOW_STATUS},
        )

    def test_main_returns_no_posts_code_without_writing(self) -> None:
        pipeline_config = config()
        patches = [
            patch("pipeline.main.ensure_directories"),
            patch("pipeline.main.load_config", return_value=pipeline_config),
            patch("pipeline.main.fetch_latest_linkedin_post", return_value=None),
            patch("pipeline.main.find_live_webflow_item"),
            patch("pipeline.main.enrich_post"),
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
            patch("pipeline.main.sync_post_to_webflow", return_value=WEBFLOW_STATUS) as sync_post,
            patch("pipeline.main.write_json"),
            patch("pipeline.main.save_pipeline_state"),
        ):
            exit_code = pipeline_main.main()

        self.assertEqual(exit_code, 0)
        enrich_post.assert_called_once_with(POST, pipeline_config)
        sync_post.assert_called_once_with(ENRICHED_POST, pipeline_config)


if __name__ == "__main__":
    unittest.main()
