from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from pipeline.enrichment import (
    DESCRIPTION_MAX,
    HEADLINE_MAX,
    fill_placeholders,
    generate_seo,
    load_prompts,
)
from pipeline.webflow import post_headline


class EnrichmentSeoTests(unittest.TestCase):
    def setUp(self) -> None:
        self.prompts = load_prompts()
        self.config = SimpleNamespace(openai_model="gpt-5-nano")

    @staticmethod
    def fake_client(payload: object) -> tuple[SimpleNamespace, Mock]:
        response = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=json.dumps(payload)),
                    finish_reason="stop",
                )
            ],
            usage=None,
        )
        create = Mock(return_value=response)
        client = SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(create=create),
            )
        )
        return client, create

    def test_prompt_placeholders_and_json_contract(self) -> None:
        rendered = fill_placeholders(
            self.prompts["seo_user"],
            {
                "CONTENT": "A post about AI measurement.",
                "HEADLINE_MAX": str(HEADLINE_MAX),
                "TITLE_MAX": str(HEADLINE_MAX),
                "DESC_MAX": str(DESCRIPTION_MAX),
            },
        )

        self.assertEqual(HEADLINE_MAX, 60)
        self.assertEqual(DESCRIPTION_MAX, 160)
        self.assertNotIn("{CONTENT}", rendered)
        self.assertNotIn("{HEADLINE_MAX}", rendered)
        self.assertNotIn("{DESC_MAX}", rendered)
        self.assertIn('"headline": "title"', rendered)
        self.assertIn('"description": "description"', rendered)

    def test_generate_seo_uses_expected_keys_and_hard_limits(self) -> None:
        client, create = self.fake_client(
            {
                "headline": "H" * (HEADLINE_MAX + 1),
                "description": "D" * (DESCRIPTION_MAX + 1),
            }
        )

        result = generate_seo(client, self.config, "A supplied post body.", self.prompts)

        self.assertEqual(set(result), {"headline", "description"})
        self.assertEqual(len(result["headline"]), HEADLINE_MAX)
        self.assertEqual(len(result["description"]), DESCRIPTION_MAX)

        messages = create.call_args.kwargs["messages"]
        self.assertEqual(messages[0]["content"], self.prompts["seo_system"])
        self.assertIn("A supplied post body.", messages[1]["content"])
        self.assertIn(f"{HEADLINE_MAX} characters", messages[1]["content"])
        self.assertIn(f"{DESCRIPTION_MAX} characters", messages[1]["content"])

    def test_incompatible_webflow_keys_are_rejected(self) -> None:
        client, _ = self.fake_client(
            {
                "name": "A title using the Webflow field name",
                "post-summary": "A description using the Webflow field name",
            }
        )

        with self.assertRaisesRegex(RuntimeError, "incomplete SEO JSON"):
            generate_seo(client, self.config, "A supplied post body.", self.prompts)

    def test_webflow_fallback_uses_the_same_headline_limit(self) -> None:
        fallback = post_headline({"content": "H" * (HEADLINE_MAX + 1)})

        self.assertEqual(len(fallback), HEADLINE_MAX)


if __name__ == "__main__":
    unittest.main()
