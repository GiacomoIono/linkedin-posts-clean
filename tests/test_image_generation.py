from __future__ import annotations

import base64
import hashlib
import json
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PIL import Image

from pipeline.generated_images import load_generated_image_manifest, record_generated_image
from pipeline.image_generation import (
    BACKGROUND_STATUS_TIMEOUT_SECONDS,
    GENERATED_IMAGE_TIMEOUT_SECONDS,
    PUBLIC_IMAGE_MAX_ATTEMPTS,
    RAW_GENERATION_FORMAT,
    RAW_GENERATION_QUALITY,
    RAW_GENERATION_SIZE,
    attach_generated_main_image,
    generate_missing_main_image,
    generated_image_filename,
    generated_image_path,
    wait_for_generated_image_public,
)
from pipeline.image_processing import (
    MAX_GENERATED_IMAGE_BYTES,
    is_valid_prepared_png_bytes,
    png_dimensions,
    prepare_blog_main_png,
)
from pipeline.image_references import STYLE_REFERENCES, reference_manifest, validated_style_references
from pipeline.webflow import build_field_data

POST = {
    "content": "<p>AI is changing how customers discover and evaluate products.</p>",
    "headline": "AI changes product discovery",
    "url": "https://www.linkedin.com/feed/update/urn:li:ugcPost:1234567890123456789",
    "published_at": "2026-08-25T08:00:00",
    "images": [],
}

CONCEPT = {
    "use_case": "illustration-story",
    "central_claim": "AI changes how customers discover products.",
    "tension": "A marketer tries to understand a shifting discovery path.",
    "audience": "marketing leaders",
    "emotional_register": "focused uncertainty",
    "motifs": ["marketer", "fragmenting path", "search light"],
    "scene": "A marketer follows a path that reorganizes beneath their feet.",
    "backdrop": "A restrained studio opening into branching information paths.",
    "subject": "One expressive marketer moving carefully through the changing path.",
    "mood": "thoughtful tension with readable shadows",
    "reference_ids": ["03", "01", "10"],
    "alt": "A marketer follows a shifting information path through a charcoal-toned studio.",
}

PASSING_REVIEW = {
    "article_fit": 90,
    "human_editorial_resonance": 88,
    "thumbnail_clarity": 86,
    "house_style_match": 92,
    "technical_cleanliness": 90,
    "material_defect": False,
    "issues": [],
    "passed": True,
    "rationale": "The human story and changing path are clear.",
    "alt": "A cautious marketer crosses a path that shifts into branching product-discovery routes.",
}


def png_bytes(width: int = 1536, height: int = 864, color=(80, 90, 100)) -> bytes:
    image = Image.new("RGB", (width, height), color)
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def chat_json(payload: dict) -> SimpleNamespace:
    message = SimpleNamespace(content=json.dumps(payload))
    choice = SimpleNamespace(message=message, finish_reason="stop")
    return SimpleNamespace(choices=[choice], usage=None)


def config(**overrides):
    values = {
        "openai_api_key": "existing-openai-key",
        "openai_model": "gpt-5.6-sol",
        "openai_image_model": "gpt-image-2",
        "image_public_ref": "main",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class ImageGenerationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        root = Path(self.temporary_directory.name)
        self.generated_directory = root / "images" / "generated"
        self.generated_directory.mkdir(parents=True)
        self.manifest_path = root / "data" / "generated_main_images.json"
        self.reference_directory = root / "assets" / "blog-main-image-style"
        self.reference_directory.mkdir(parents=True)
        for index, reference in enumerate(STYLE_REFERENCES):
            reference.path.name
            (self.reference_directory / reference.filename).write_bytes(
                png_bytes(512, 512, (30 + index, 40 + index, 50 + index))
            )

        self.patches = [
            patch("pipeline.image_generation.GENERATED_IMAGE_DIR", self.generated_directory),
            patch("pipeline.image_generation.BACKGROUND_IMAGE_POLL_SECONDS", 0.0),
            patch("pipeline.generated_images.GENERATED_IMAGE_MANIFEST_PATH", self.manifest_path),
            patch("pipeline.image_references.STYLE_REFERENCE_DIR", self.reference_directory),
        ]
        for active_patch in self.patches:
            active_patch.start()

    def tearDown(self) -> None:
        for active_patch in reversed(self.patches):
            active_patch.stop()
        self.temporary_directory.cleanup()

    def fake_client(
        self,
        image_bytes: bytes | None = None,
        review: dict | None = None,
        concept: dict | None = None,
    ) -> tuple[SimpleNamespace, Mock, Mock, Mock]:
        completed_image = base64.b64encode(image_bytes or png_bytes()).decode("ascii")
        create = Mock(
            return_value=SimpleNamespace(
                id="resp-image-test",
                status="in_progress",
                output=[],
                error=None,
            )
        )
        retrieve = Mock(
            return_value=SimpleNamespace(
                id="resp-image-test",
                status="completed",
                output=[
                    SimpleNamespace(
                        type="image_generation_call",
                        status="completed",
                        result=completed_image,
                    )
                ],
                error=None,
            )
        )
        generate = Mock()
        completions = Mock(
            side_effect=[
                chat_json(concept or CONCEPT),
                chat_json(review or PASSING_REVIEW),
            ]
        )
        client = SimpleNamespace(
            responses=SimpleNamespace(create=create, retrieve=retrieve),
            images=SimpleNamespace(generate=generate),
            chat=SimpleNamespace(completions=SimpleNamespace(create=completions)),
        )
        return client, create, generate, completions

    def write_registered_image(self, post: dict | None = None) -> tuple[Path, bytes]:
        chosen_post = post or POST
        final_bytes, dimensions = prepare_blog_main_png(png_bytes())
        output = generated_image_path(chosen_post)
        output.write_bytes(final_bytes)
        references = validated_style_references(CONCEPT["reference_ids"])
        record_generated_image(
            chosen_post,
            output.name,
            final_bytes,
            renderer_model="gpt-image-2",
            planner_model="gpt-5.6-sol",
            qa_model="gpt-5.6-sol",
            concept=CONCEPT,
            quality_review={**PASSING_REVIEW, "weighted_score": 89.0},
            references=reference_manifest(references),
            prompt="one prompt",
            dimensions=dimensions,
        )
        return output, final_bytes

    def test_source_images_skip_every_openai_call(self) -> None:
        post = {**POST, "images": [{"url": "https://example.com/source.jpg", "alt": "Source"}]}
        client, edit, generate, completions = self.fake_client()

        result = generate_missing_main_image(post, config(), client=client)

        self.assertEqual(result, {"action": "skipped_source_images"})
        edit.assert_not_called()
        generate.assert_not_called()
        completions.assert_not_called()
        self.assertEqual(list(self.generated_directory.iterdir()), [])

    def test_linkedin_media_signal_without_dated_source_still_generates(self) -> None:
        client, edit, generate, completions = self.fake_client()

        result = generate_missing_main_image(
            {**POST, "linkedin_has_image": True}, config(), client=client
        )

        self.assertEqual(result["action"], "generated")
        edit.assert_called_once()
        generate.assert_not_called()
        self.assertEqual(completions.call_count, 2)

    def test_generates_once_with_exactly_three_references_then_reviews_and_saves_png(self) -> None:
        client, edit, generate, completions = self.fake_client()

        result = generate_missing_main_image(POST, config(), client=client)

        output = Path(result["path"])
        self.assertEqual(result["action"], "generated")
        self.assertEqual(output.parent, self.generated_directory)
        self.assertEqual(output.name, generated_image_filename(POST))
        self.assertEqual(output.suffix, ".png")
        self.assertEqual(png_dimensions(output.read_bytes()), (1200, 675))
        self.assertLessEqual(output.stat().st_size, MAX_GENERATED_IMAGE_BYTES)
        edit.assert_called_once()
        generate.assert_not_called()
        self.assertEqual(completions.call_count, 2)

        create_kwargs = edit.call_args.kwargs
        self.assertEqual(create_kwargs["model"], "gpt-5.6-sol")
        self.assertIs(create_kwargs["background"], True)
        self.assertIs(create_kwargs["store"], False)
        self.assertEqual(create_kwargs["max_tool_calls"], 1)
        self.assertIs(create_kwargs["parallel_tool_calls"], False)
        self.assertEqual(create_kwargs["tool_choice"], {"type": "image_generation"})

        tool = create_kwargs["tools"][0]
        self.assertEqual(tool["model"], "gpt-image-2")
        self.assertEqual(tool["action"], "generate")
        self.assertEqual(tool["size"], RAW_GENERATION_SIZE)
        self.assertEqual(tool["size"], "1536x864")
        self.assertEqual(tool["quality"], RAW_GENERATION_QUALITY)
        self.assertEqual(tool["output_format"], RAW_GENERATION_FORMAT)
        self.assertEqual(tool["output_format"], "png")
        self.assertEqual(tool["partial_images"], 0)
        self.assertNotIn("input_fidelity", tool)

        content = create_kwargs["input"][0]["content"]
        self.assertIn("Input images: Image 1: style reference", content[0]["text"])
        self.assertIn("Text: none", content[0]["text"])
        self.assertIn("speech bubbles", content[0]["text"])
        reference_content = content[1:]
        self.assertEqual(len(reference_content), 3)
        self.assertTrue(all(item["detail"] == "original" for item in reference_content))
        reference_bytes = [
            base64.b64decode(item["image_url"].partition(",")[2])
            for item in reference_content
        ]
        expected_references = validated_style_references(CONCEPT["reference_ids"])
        self.assertEqual(
            reference_bytes, [item.path.read_bytes() for item in expected_references]
        )
        client.responses.retrieve.assert_called_once_with(
            "resp-image-test", timeout=BACKGROUND_STATUS_TIMEOUT_SECONDS
        )

        self.assertEqual(completions.call_args_list[0].kwargs["model"], "gpt-5.6-sol")
        self.assertEqual(completions.call_args_list[1].kwargs["model"], "gpt-5.6-sol")
        concept_system = completions.call_args_list[0].kwargs["messages"][0]["content"]
        self.assertIn("Never use speech bubbles", concept_system)
        concept_user = completions.call_args_list[0].kwargs["messages"][1]["content"]
        self.assertIn("must not contain speech bubbles", concept_user)
        qa_system = completions.call_args_list[1].kwargs["messages"][0]["content"]
        self.assertIn("independently from 0 to 100", qa_system)
        for forbidden_family in (
            "speech bubbles",
            "speed lines",
            "panel grids",
            "comic panels",
            "superhero-comic exaggeration",
        ):
            self.assertIn(forbidden_family, content[0]["text"])
            self.assertIn(forbidden_family, qa_system)
        qa_content = completions.call_args_list[1].kwargs["messages"][1]["content"]
        self.assertIn("not as points capped", qa_content[0]["text"])
        self.assertTrue(qa_content[1]["image_url"]["url"].startswith("data:image/png;base64,"))
        qa_schema = completions.call_args_list[1].kwargs["response_format"]["json_schema"]["schema"]
        for field in (
            "article_fit",
            "human_editorial_resonance",
            "thumbnail_clarity",
            "house_style_match",
            "technical_cleanliness",
        ):
            self.assertIn("Independent 0-100 score", qa_schema["properties"][field]["description"])

        entry = load_generated_image_manifest()["files"][output.name]
        self.assertEqual(entry["sha256"], hashlib.sha256(output.read_bytes()).hexdigest())
        self.assertEqual(entry["renderer_model"], "gpt-image-2")
        self.assertEqual(entry["planner"]["model"], "gpt-5.6-sol")
        self.assertEqual(entry["quality_review"]["model"], "gpt-5.6-sol")
        self.assertEqual(len(entry["references"]), 3)
        self.assertEqual(entry["dimensions"], {"width": 1200, "height": 675})
        self.assertEqual(entry["bytes"], output.stat().st_size)
        self.assertEqual(entry["alt"], PASSING_REVIEW["alt"])
        self.assertNotEqual(entry["alt"], CONCEPT["alt"])
        self.assertIn("Primary request", entry["prompt"])

        attached = attach_generated_main_image(
            POST, config(image_public_ref="committed-image-sha")
        )
        self.assertEqual(
            build_field_data(attached)["main-image"]["alt"], PASSING_REVIEW["alt"]
        )

    def test_default_client_allows_one_long_request_without_sdk_retries(self) -> None:
        client, edit, _, _ = self.fake_client()
        with patch("pipeline.image_generation.OpenAI", return_value=client) as openai:
            generate_missing_main_image(POST, config())

        self.assertEqual(openai.call_args.kwargs["max_retries"], 0)
        self.assertEqual(GENERATED_IMAGE_TIMEOUT_SECONDS, 600.0)
        self.assertEqual(openai.call_args.kwargs["timeout"], 600.0)
        edit.assert_called_once()

    def test_mixed_background_image_calls_are_rejected_without_saving(self) -> None:
        client, create, generate, completions = self.fake_client()
        completed = client.responses.retrieve.return_value.output[0]
        client.responses.retrieve.return_value.output = [
            completed,
            SimpleNamespace(
                type="image_generation_call",
                status="failed",
                result=None,
            ),
        ]

        with self.assertRaisesRegex(RuntimeError, "exactly one image generation call"):
            generate_missing_main_image(POST, config(), client=client)

        create.assert_called_once()
        generate.assert_not_called()
        self.assertEqual(completions.call_count, 1)
        self.assertFalse(generated_image_path(POST).exists())
        self.assertFalse(self.manifest_path.exists())

    def test_failed_semantic_review_saves_nothing_and_does_not_retry(self) -> None:
        failed_review = {
            **PASSING_REVIEW,
            "technical_cleanliness": 20,
            "material_defect": True,
            "issues": ["Visible lettering"],
            "passed": False,
        }
        client, edit, generate, completions = self.fake_client(review=failed_review)

        with self.assertRaisesRegex(RuntimeError, "Nothing was saved.*no replacement"):
            generate_missing_main_image(POST, config(), client=client)

        edit.assert_called_once()
        generate.assert_not_called()
        self.assertEqual(completions.call_count, 2)
        self.assertFalse(generated_image_path(POST).exists())
        self.assertFalse(self.manifest_path.exists())

    def test_forbidden_planned_motif_stops_before_image_generation(self) -> None:
        for forbidden_motif in (
            "blank speech balloon",
            "dramatic speed lines",
            "panel grid",
            "three comic panels",
            "superhero-comic exaggeration",
        ):
            with self.subTest(forbidden_motif=forbidden_motif):
                forbidden_concept = {
                    **CONCEPT,
                    "motifs": ["marketer", forbidden_motif, "boardroom"],
                    "scene": f"A marketer crosses a boardroom with {forbidden_motif}.",
                }
                client, create, generate, completions = self.fake_client(
                    concept=forbidden_concept
                )

                with self.assertRaisesRegex(RuntimeError, "forbidden by the image skill"):
                    generate_missing_main_image(POST, config(), client=client)

                create.assert_not_called()
                generate.assert_not_called()
                completions.assert_called_once()
                self.assertFalse(generated_image_path(POST).exists())
                self.assertFalse(self.manifest_path.exists())

    def test_weight_point_scores_are_rejected_as_malformed(self) -> None:
        weighted_points = {
            **PASSING_REVIEW,
            "article_fit": 29,
            "human_editorial_resonance": 24,
            "thumbnail_clarity": 18,
            "house_style_match": 15,
            "technical_cleanliness": 10,
        }
        client, create, generate, completions = self.fake_client(review=weighted_points)

        with self.assertRaisesRegex(RuntimeError, "category-weight points"):
            generate_missing_main_image(POST, config(), client=client)

        create.assert_called_once()
        generate.assert_not_called()
        self.assertEqual(completions.call_count, 2)
        self.assertFalse(generated_image_path(POST).exists())
        self.assertFalse(self.manifest_path.exists())

    def test_missing_post_render_review_alt_saves_nothing(self) -> None:
        client, edit, generate, completions = self.fake_client(
            review={**PASSING_REVIEW, "alt": ""}
        )

        with self.assertRaisesRegex(RuntimeError, "empty final ALT"):
            generate_missing_main_image(POST, config(), client=client)

        edit.assert_called_once()
        generate.assert_not_called()
        self.assertEqual(completions.call_count, 2)
        self.assertFalse(generated_image_path(POST).exists())
        self.assertFalse(self.manifest_path.exists())

    def test_manifest_registration_requires_quality_review_alt(self) -> None:
        final_bytes, dimensions = prepare_blog_main_png(png_bytes())

        with self.assertRaisesRegex(RuntimeError, "ALT text from its quality review"):
            record_generated_image(
                POST,
                generated_image_filename(POST),
                final_bytes,
                renderer_model="gpt-image-2",
                planner_model="gpt-5.6-sol",
                qa_model="gpt-5.6-sol",
                concept=CONCEPT,
                quality_review={**PASSING_REVIEW, "alt": ""},
                references=reference_manifest(
                    validated_style_references(CONCEPT["reference_ids"])
                ),
                prompt="one prompt",
                dimensions=dimensions,
            )

        self.assertFalse(self.manifest_path.exists())

    def test_invalid_raw_image_stops_before_quality_review_or_save(self) -> None:
        client, edit, _, completions = self.fake_client(image_bytes=b"not an image")

        with self.assertRaisesRegex(RuntimeError, "cannot decode"):
            generate_missing_main_image(POST, config(), client=client)

        edit.assert_called_once()
        self.assertEqual(completions.call_count, 1)
        self.assertFalse(generated_image_path(POST).exists())

    def test_missing_reference_stops_before_image_generation(self) -> None:
        missing = self.reference_directory / "03-human-craft-paper-automation.png"
        missing.unlink()
        client, edit, _, completions = self.fake_client()

        with self.assertRaisesRegex(RuntimeError, "missing or unreadable"):
            generate_missing_main_image(POST, config(), client=client)

        edit.assert_not_called()
        self.assertEqual(completions.call_count, 1)

    def test_stable_filename_is_descriptive_and_collision_safe(self) -> None:
        other = {
            **POST,
            "url": "https://www.linkedin.com/feed/update/urn:li:ugcPost:9876543210987654321",
        }

        first = generated_image_filename(POST)
        self.assertTrue(first.startswith("2026-08-25-ai-is-changing-how-customers-discover-and-evaluate-"))
        self.assertTrue(first.endswith(".png"))
        self.assertEqual(first, generated_image_filename(POST))
        self.assertNotEqual(first, generated_image_filename(other))

    def test_filename_is_unchanged_when_seo_enrichment_adds_a_headline(self) -> None:
        raw_post = {key: value for key, value in POST.items() if key != "headline"}
        enriched_post = {**raw_post, "headline": "A newly generated SEO headline"}

        self.assertEqual(
            generated_image_filename(raw_post),
            generated_image_filename(enriched_post),
        )

    def test_prepared_raw_post_image_attaches_after_seo_headline_is_added(self) -> None:
        raw_post = {key: value for key, value in POST.items() if key != "headline"}
        self.write_registered_image(raw_post)

        attached = attach_generated_main_image(
            {**raw_post, "headline": "A newly generated SEO headline"},
            config(image_public_ref="commit-sha"),
        )

        self.assertIn("/commit-sha/images/generated/", attached["generated_main_image"]["url"])

    def test_reuses_registered_png_without_openai_calls(self) -> None:
        output, _ = self.write_registered_image()
        client, edit, generate, completions = self.fake_client()

        result = generate_missing_main_image(POST, config(), client=client)

        self.assertEqual(result["action"], "reused")
        self.assertEqual(Path(result["path"]), output)
        edit.assert_not_called()
        generate.assert_not_called()
        completions.assert_not_called()

    def test_unregistered_generated_path_is_never_overwritten(self) -> None:
        generated_image_path(POST).write_bytes(png_bytes())
        client, edit, _, _ = self.fake_client()

        with self.assertRaisesRegex(RuntimeError, "will not be overwritten"):
            generate_missing_main_image(POST, config(), client=client)

        edit.assert_not_called()

    def test_generated_fallback_attaches_only_as_commit_pinned_nested_url(self) -> None:
        self.write_registered_image()

        enriched = attach_generated_main_image(POST, config(image_public_ref="abc123"))

        self.assertEqual(enriched["images"], [])
        self.assertEqual(enriched["generated_main_image"]["alt"], PASSING_REVIEW["alt"])
        self.assertEqual(
            enriched["generated_main_image"]["url"],
            "https://raw.githubusercontent.com/GiacomoIono/linkedin-posts-clean/abc123/"
            f"images/generated/{generated_image_filename(POST)}",
        )

    def test_source_image_post_never_keeps_generated_fallback(self) -> None:
        source_post = {
            **POST,
            "images": [{"url": "https://example.com/source.jpg", "alt": "Source"}],
            "generated_main_image": {"url": "https://example.com/generated.png"},
        }

        attached = attach_generated_main_image(source_post, config())

        self.assertEqual(attached["images"], source_post["images"])
        self.assertNotIn("generated_main_image", attached)

    def test_manifest_checksum_mismatch_stops_before_webflow(self) -> None:
        output, _ = self.write_registered_image()
        output.write_bytes(png_bytes(1200, 675, (1, 2, 3)))

        with self.assertRaisesRegex(RuntimeError, "manifest checksum"):
            attach_generated_main_image(POST, config())

    def test_public_png_must_match_manifest_checksum(self) -> None:
        _, final_bytes = self.write_registered_image()
        request_get = Mock(return_value=SimpleNamespace(status_code=200, content=final_bytes))
        sleep = Mock()

        url = wait_for_generated_image_public(
            POST,
            config(image_public_ref="abc123"),
            request_get=request_get,
            sleep_fn=sleep,
        )

        self.assertIn("/abc123/images/generated/", url)
        request_get.assert_called_once_with(url, timeout=30)
        sleep.assert_not_called()

    def test_wrong_public_checksum_stops_before_webflow(self) -> None:
        self.write_registered_image()
        different_bytes, _ = prepare_blog_main_png(png_bytes(color=(4, 5, 6)))
        request_get = Mock(
            return_value=SimpleNamespace(status_code=200, content=different_bytes)
        )
        sleep = Mock()

        with self.assertRaisesRegex(RuntimeError, "Stopping before Webflow"):
            wait_for_generated_image_public(
                POST,
                config(image_public_ref="abc123"),
                request_get=request_get,
                sleep_fn=sleep,
            )

        self.assertEqual(request_get.call_count, PUBLIC_IMAGE_MAX_ATTEMPTS)
        self.assertEqual(sleep.call_count, PUBLIC_IMAGE_MAX_ATTEMPTS - 1)

    def test_prepared_bytes_are_valid_png(self) -> None:
        final_bytes, dimensions = prepare_blog_main_png(png_bytes())

        self.assertTrue(is_valid_prepared_png_bytes(final_bytes))
        self.assertEqual(dimensions["width"] * 9, dimensions["height"] * 16)


if __name__ == "__main__":
    unittest.main()
