from __future__ import annotations

from types import SimpleNamespace
import unittest

from pipeline.enrichment import explicit_context_alt_text, fallback_alt_text, generate_alt, has_missing_image_alt


class FakeCompletions:
    def __init__(self) -> None:
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        message = SimpleNamespace(content="A chart showing steady revenue growth.")
        choice = SimpleNamespace(message=message, finish_reason="stop")
        return SimpleNamespace(choices=[choice], usage=None)


class EnrichmentAltTests(unittest.TestCase):
    def test_has_missing_image_alt_detects_empty_alt_on_image_url(self) -> None:
        post = {"images": [{"url": "https://example.com/train.jpg", "alt": ""}]}

        self.assertTrue(has_missing_image_alt(post))

    def test_has_missing_image_alt_ignores_posts_without_images(self) -> None:
        post = {"images": []}

        self.assertFalse(has_missing_image_alt(post))

    def test_fallback_alt_prefers_explicit_picture_context(self) -> None:
        text = "A few travel notes. In the picture: the new shiny EuroCity Swiss train from Zurich to Milan."

        self.assertEqual(
            fallback_alt_text(text),
            "The new shiny EuroCity Swiss train from Zurich to Milan.",
        )

    def test_explicit_context_alt_is_empty_without_picture_context(self) -> None:
        text = "A few travel notes about trains and timing."

        self.assertEqual(explicit_context_alt_text(text), "")

    def test_generate_alt_includes_image_url_in_prompt_text(self) -> None:
        completions = FakeCompletions()
        client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
        config = SimpleNamespace(openai_model="gpt-test")
        prompts = {
            "alt_system": "System prompt",
            "alt_user": "Image source URL:\n{IMAGE_URL}\n\nPost context:\n{CONTEXT}",
        }
        image_url = "https://example.com/images/2026-06-01_1.jpg"

        alt = generate_alt(client, config, image_url, "Post context", prompts)

        self.assertEqual(alt, "A chart showing steady revenue growth.")
        messages = completions.kwargs["messages"]
        prompt_text = messages[1]["content"][0]["text"]
        self.assertIn(image_url, prompt_text)
        self.assertIn("Post context", prompt_text)


if __name__ == "__main__":
    unittest.main()
