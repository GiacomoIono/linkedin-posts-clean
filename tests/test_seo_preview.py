from __future__ import annotations

import argparse
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from pipeline.seo_preview import (
    MAX_PREVIEW_RUNS,
    generate_previews,
    load_saved_post,
    main,
    preview_run_count,
)


class SeoPreviewTests(unittest.TestCase):
    def test_load_saved_post_reads_the_existing_json_contract(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "post.json"
            path.write_text(
                json.dumps(
                    {
                        "content": "<p>A saved post about AI measurement.</p>",
                        "url": "https://www.linkedin.com/feed/update/example",
                    }
                ),
                encoding="utf-8",
            )

            post = load_saved_post(path)

        self.assertEqual(post["url"], "https://www.linkedin.com/feed/update/example")

    def test_generate_previews_calls_only_the_seo_generator(self) -> None:
        post = {"content": "<p>A saved post about AI measurement.</p>"}
        config = SimpleNamespace(openai_api_key="test-key", openai_model="gpt-5-nano")
        fake_client = object()
        generated = {
            "headline": "Can marketers still measure the AI customer journey?",
            "description": "A description grounded in the saved post body.",
        }

        with (
            patch("pipeline.seo_preview.OpenAI", return_value=fake_client) as openai,
            patch("pipeline.seo_preview.load_prompts", return_value={"seo_system": "system", "seo_user": "user"}),
            patch("pipeline.seo_preview.generate_seo", return_value=generated) as generate_seo,
        ):
            previews = generate_previews(post, config, runs=2)

        self.assertEqual(previews, [generated, generated])
        openai.assert_called_once_with(api_key="test-key")
        self.assertEqual(generate_seo.call_count, 2)
        for call in generate_seo.call_args_list:
            self.assertEqual(call.args[0], fake_client)
            self.assertEqual(call.args[1], config)
            self.assertEqual(call.args[2], "A saved post about AI measurement.")

    def test_main_prints_results_without_writing_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "post.json"
            path.write_text(
                json.dumps({"content": "<p>Saved content.</p>", "url": "https://example.com/post"}),
                encoding="utf-8",
            )
            config = SimpleNamespace(openai_api_key="test-key", openai_model="gpt-5-nano")
            preview = {
                "headline": "A sentence case title about AI measurement",
                "description": "A complete description based on the supplied post.",
            }
            output = StringIO()

            with (
                patch("pipeline.seo_preview.load_config", return_value=config),
                patch("pipeline.seo_preview.generate_previews", return_value=[preview]),
                patch.object(Path, "write_text") as write_text,
                redirect_stdout(output),
            ):
                status = main(["--post", str(path)])

        self.assertEqual(status, 0)
        write_text.assert_not_called()
        self.assertIn("Network calls: OpenAI only.", output.getvalue())
        self.assertIn("LinkedIn, Webflow and X calls: none.", output.getvalue())
        self.assertIn(preview["headline"], output.getvalue())

    def test_missing_openai_key_fails_before_creating_a_client(self) -> None:
        config = SimpleNamespace(openai_api_key="", openai_model="gpt-5-nano")

        with (
            patch("pipeline.seo_preview.OpenAI") as openai,
            self.assertRaisesRegex(RuntimeError, "OPENAI_API_KEY is missing"),
        ):
            generate_previews({"content": "Saved content."}, config, runs=1)

        openai.assert_not_called()

    def test_invalid_run_counts_are_rejected(self) -> None:
        for value in ("0", str(MAX_PREVIEW_RUNS + 1), "not-a-number"):
            with self.subTest(value=value), self.assertRaises(argparse.ArgumentTypeError):
                preview_run_count(value)

    def test_main_reports_an_invalid_saved_post(self) -> None:
        output = StringIO()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "post.json"
            path.write_text(json.dumps({"content": ""}), encoding="utf-8")
            with redirect_stderr(output):
                status = main(["--post", str(path)])

        self.assertEqual(status, 1)
        self.assertIn("Saved post has no content", output.getvalue())


if __name__ == "__main__":
    unittest.main()
