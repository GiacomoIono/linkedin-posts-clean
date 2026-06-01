from __future__ import annotations

from contextlib import ExitStack
import unittest
from unittest.mock import patch

from pipeline import main as pipeline_main
from pipeline.config import PipelineConfig


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

TWEET = {
    "content": "Hello from LinkedIn.",
    "url": POST["url"],
    "published_at": POST["published_at"],
    "images": [],
}


def config(run_x_pipeline: bool) -> PipelineConfig:
    return PipelineConfig(
        linkedin_access_token="linkedin-token",
        openai_api_key="openai-token",
        openai_model="gpt-test",
        webflow_api_token="webflow-token",
        webflow_collection_id="collection-id",
        webflow_publish=True,
        run_x_pipeline=run_x_pipeline,
        x_access_token="x-token",
        require_x_posting=False,
        force_webflow_sync=False,
        force_enrich=False,
        force_tweetify=False,
        force_x_post=False,
    )


class MainXPipelineTests(unittest.TestCase):
    def run_main(self, run_x_pipeline: bool):
        captured_statuses = []

        def save_pipeline_state(_latest_post, _enriched_post, statuses):
            captured_statuses.append(statuses)

        patches = [
            patch("pipeline.main.ensure_directories"),
            patch("pipeline.main.load_config", return_value=config(run_x_pipeline)),
            patch("pipeline.main.fetch_latest_linkedin_post", return_value=POST),
            patch("pipeline.main.load_existing_raw_post", return_value=None),
            patch("pipeline.main.load_existing_enriched_post", return_value=None),
            patch("pipeline.main.webflow_state_entry", return_value=None),
            patch("pipeline.main.already_synced_to_webflow", return_value=None),
            patch("pipeline.main.enrich_post", return_value=ENRICHED_POST),
            patch("pipeline.main.sync_post_to_webflow", return_value={"action": "created", "item_id": "item-id"}),
            patch("pipeline.main.write_json"),
            patch("pipeline.main.save_pipeline_state", side_effect=save_pipeline_state),
        ]

        with ExitStack() as stack:
            for item in patches:
                stack.enter_context(item)
            with (
                patch("pipeline.main.load_existing_tweet", return_value=None) as load_existing_tweet,
                patch("pipeline.main.generate_tweet", return_value=TWEET) as generate_tweet,
                patch("pipeline.main.post_to_x", return_value={"action": "posted"}) as post_to_x,
            ):
                exit_code = pipeline_main.main()

        return exit_code, captured_statuses[0], load_existing_tweet, generate_tweet, post_to_x

    def test_main_skips_x_pipeline_when_disabled(self) -> None:
        exit_code, statuses, load_existing_tweet, generate_tweet, post_to_x = self.run_main(False)

        self.assertEqual(exit_code, 0)
        self.assertEqual(statuses["tweetify"], "skipped_x_pipeline_disabled")
        self.assertEqual(statuses["x"], "skipped_x_pipeline_disabled")
        load_existing_tweet.assert_not_called()
        generate_tweet.assert_not_called()
        post_to_x.assert_not_called()

    def test_main_runs_existing_optional_x_pipeline_when_enabled(self) -> None:
        exit_code, statuses, load_existing_tweet, generate_tweet, post_to_x = self.run_main(True)

        self.assertEqual(exit_code, 0)
        self.assertEqual(statuses["tweetify"], "generated")
        self.assertEqual(statuses["x"], {"action": "posted"})
        load_existing_tweet.assert_called_once()
        generate_tweet.assert_called_once_with(ENRICHED_POST, config(True))
        post_to_x.assert_called_once_with(TWEET, config(True))


if __name__ == "__main__":
    unittest.main()
