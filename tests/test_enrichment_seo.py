from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from pipeline.enrichment import (
    DESCRIPTION_MAX,
    DESCRIPTION_TARGET_MAX,
    DESCRIPTION_TARGET_MIN,
    HEADLINE_MAX,
    HEADLINE_MIN,
    HEADLINE_TARGET_MAX,
    HEADLINE_TARGET_MIN,
    INSUFFICIENT_SOURCE_SENTINEL,
    InsufficientSeoSourceError,
    SEO_MAX_ATTEMPTS,
    enrich_post,
    fill_placeholders,
    generate_seo,
    load_prompts,
    render_seo_system_prompt,
    seo_context_from_post,
    seo_prompt_mapping,
    validate_seo_payload,
)
from pipeline.webflow import build_field_data, post_headline


BENCHMARKS = [
    {
        "headline": "Google Search ads are outgrowing YouTube. AI is why",
        "description": (
            "Google Search ad revenue is growing faster than YouTube as AI unlocks new placements, while video "
            "ad saturation may be pushing users towards subscriptions."
        ),
    },
    {
        "headline": "Can marketers still measure the AI customer journey?",
        "description": (
            "A Sephora journey map shows how AI creates new paths to purchase, making attribution harder and "
            "forcing marketers to rethink how they measure influence."
        ),
    },
    {
        "headline": "How Bending Spoons generates $1 million per employee",
        "description": (
            "Bending Spoons buys fading apps, cuts staff and folds them into a lean AI operating model that "
            "generates nearly $1 million in revenue per employee."
        ),
    },
    {
        "headline": "I tried airBaltic's free Starlink WiFi at 35,000 feet",
        "description": (
            "I tested airBaltic's free Starlink WiFi at 35,000 feet. It was fast, reliable and felt more like "
            "home internet than any in-flight connection I had tried."
        ),
    },
    {
        "headline": "Would ChatGPT recommend you to a hiring manager?",
        "description": (
            "I used ChatGPT to find relevant candidates in 16 minutes. The experiment raises a new career "
            "question: would it recommend your profile to a hiring manager?"
        ),
    },
]


class EnrichmentSeoTests(unittest.TestCase):
    def setUp(self) -> None:
        self.prompts = load_prompts()
        self.config = SimpleNamespace(openai_model="gpt-5.6-sol")

    @staticmethod
    def fake_client(*payloads: object) -> tuple[SimpleNamespace, Mock]:
        responses = [
            SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content=payload if isinstance(payload, str) else json.dumps(payload),
                        ),
                        finish_reason="stop",
                    )
                ],
                usage=None,
            )
            for payload in payloads
        ]
        create = Mock(side_effect=responses)
        client = SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(create=create),
            )
        )
        return client, create

    def test_prompt_contains_skill_policy_and_pipeline_json_adapter(self) -> None:
        system_prompt = render_seo_system_prompt(self.prompts)
        user_prompt = fill_placeholders(
            self.prompts["seo_user"],
            seo_prompt_mapping(
                "A post about AI measurement.",
                image_context="A chart comparing search and video revenue.",
                current_title="A vague current title",
                current_description="A vague current description.",
                target_keyword="AI marketing measurement",
            ),
        )

        required_skill_rules = [
            "The meta description is also used as the visible post summary.",
            "when browsing the website",
            "Complete post body.",
            "Supplied image context, when present.",
            "Current title and description, when present.",
            "Optional target keyword, when present.",
            "cannot support an accurate title and description",
            'returning "__INSUFFICIENT_SOURCE__" as both JSON values',
            "ChatGPT, ChatGPT Pro, OpenAI, YouTube, LinkedIn, Google Search, AI Overviews, AI Mode and airBaltic",
            "Everything you need to know",
            "Here's proof",
            'Use "here\'s why" only when it genuinely improves clarity.',
            "not a consultant writing a corporate brochure",
            "Expert but accessible.",
            "Curious and optimistic about technology.",
            "Personal without oversharing.",
            "Active voice.",
            "Engagement-bait questions.",
            'Use "AI marketing" in normal prose.',
            'Reserve "AI Marketing revolution" for the established positioning phrase.',
            "150,000, not 150'000.",
            "Drop a meaningful statistical qualifier such as average, median, sample size or test duration",
            "Do not claim that exact pixel-width validation was performed",
            "Check factual accuracy against the post body and image context.",
        ]
        for rule in required_skill_rules:
            with self.subTest(rule=rule):
                self.assertIn(rule, system_prompt)

        for benchmark in BENCHMARKS:
            self.assertIn(benchmark["headline"], system_prompt)
            self.assertIn(benchmark["description"], system_prompt)

        self.assertIn(f"Aim for {HEADLINE_TARGET_MIN} to {HEADLINE_TARGET_MAX} characters", system_prompt)
        self.assertIn(f"Accept {HEADLINE_MIN} to {HEADLINE_MAX} characters", system_prompt)
        self.assertIn(
            f"Aim for {DESCRIPTION_TARGET_MIN} to {DESCRIPTION_TARGET_MAX} characters",
            system_prompt,
        )
        self.assertIn(f"Never exceed {DESCRIPTION_MAX} characters", system_prompt)
        self.assertIn('"headline": "title"', user_prompt)
        self.assertIn('"description": "description"', user_prompt)
        self.assertIn("A chart comparing search and video revenue.", user_prompt)
        self.assertIn("A vague current title", user_prompt)
        self.assertIn("A vague current description.", user_prompt)
        self.assertIn("AI marketing measurement", user_prompt)
        self.assertNotIn("titleCharacters", user_prompt)
        self.assertNotIn("descriptionCharacters", user_prompt)
        for placeholder in seo_prompt_mapping():
            self.assertNotIn("{" + placeholder + "}", system_prompt)
            self.assertNotIn("{" + placeholder + "}", user_prompt)

    def test_all_approved_benchmarks_pass_the_objective_contract(self) -> None:
        self.assertEqual([len(item["headline"]) for item in BENCHMARKS], [51, 52, 52, 53, 48])
        self.assertEqual([len(item["description"]) for item in BENCHMARKS], [155, 152, 147, 153, 155])

        for benchmark in BENCHMARKS:
            with self.subTest(headline=benchmark["headline"]):
                self.assertEqual(validate_seo_payload(benchmark), benchmark)

    def test_objective_contract_rejects_publication_errors(self) -> None:
        valid = BENCHMARKS[0]
        invalid_cases = [
            ({**valid, "extra": "value"}, "unexpected keys"),
            ({**valid, "headline": 123}, "headline must be a string"),
            ({**valid, "headline": "Too short"}, f"at least {HEADLINE_MIN} characters"),
            ({**valid, "headline": "H" * (HEADLINE_MAX + 1)}, f"not exceed {HEADLINE_MAX}"),
            ({**valid, "description": "D" * DESCRIPTION_MAX + "."}, f"not exceed {DESCRIPTION_MAX}"),
            ({**valid, "headline": valid["headline"].replace(". ", " — ")}, "em dash"),
            ({**valid, "headline": valid["headline"] + "!"}, "exclamation mark"),
            ({**valid, "description": valid["description"].replace("AI", "Ai")}, 'as "AI"'),
            (
                {**valid, "description": "Discover how AI is changing marketing measurement for brands."},
                "generic opening",
            ),
            ({**valid, "description": "AI is changing measurement #marketing."}, "hashtags"),
            ({**valid, "description": "AI is changing measurement for marketers ☀."}, "emoji"),
            ({**valid, "description": "AI is changing measurement for marketers..."}, "unfinished ellipses"),
            ({**valid, "description": valid["description"].removesuffix(".")}, "complete sentence"),
            ({**valid, "description": "."}, "at least 3 words"),
            ({**valid, "description": "_ _ _."}, "at least 3 words"),
            ({**valid, "description": "2024 2025 2026."}, "at least 3 words"),
            (
                {
                    **valid,
                    "description": "AI changes search. Marketers lose attribution. Measurement must adapt.",
                },
                "one or two complete sentences",
            ),
            (
                {
                    **valid,
                    "description": "AI changes shopping. eCommerce teams adapt. airBaltic tests new tools.",
                },
                "one or two complete sentences",
            ),
            (
                {
                    **valid,
                    "description": "An [AI measurement guide](https://example.com) can clarify attribution.",
                },
                "Markdown",
            ),
            ({**valid, "description": "Inline `AI output` can distort attribution."}, "Markdown"),
            ({**valid, "description": "An *AI output* can distort attribution."}, "Markdown"),
            ({**valid, "description": "An _AI output_ can distort attribution."}, "Markdown"),
            ({**valid, "description": "An __AI output__ can distort attribution."}, "Markdown"),
            ({**valid, "description": "AI attribution carries a copyright mark ©️."}, "emoji"),
            ({**valid, "description": "AI attribution changed after test 1️⃣."}, "emoji"),
            (
                {
                    **valid,
                    "headline": "Ecommerce growth is changing how brands sell online",
                },
                "eCommerce",
            ),
            (
                {
                    **valid,
                    "headline": "I tried airBaltic's free WiFi at 35'000 feet today",
                },
                "comma-separated numbers",
            ),
            ({**valid, "headline": "**" + valid["headline"] + "**"}, "Markdown"),
            (
                {**valid, "headline": "Would ChatGPT pro recommend you for a senior role?"},
                "ChatGPT Pro",
            ),
            ({**valid, "headline": valid["headline"].upper()}, "all capital letters"),
        ]

        for payload, message in invalid_cases:
            with self.subTest(message=message), self.assertRaisesRegex(RuntimeError, message):
                validate_seo_payload(payload)

    def test_objective_contract_avoids_prefix_and_sentence_ending_false_positives(self) -> None:
        valid_payloads = [
            {
                **BENCHMARKS[0],
                "headline": "AI models are changing how marketers measure search",
            },
            {
                **BENCHMARKS[0],
                "headline": "Google searches are changing how marketers measure ads",
            },
            {
                **BENCHMARKS[0],
                "headline": "Why every browser is adding an AI mode to search",
            },
            {
                **BENCHMARKS[0],
                "description": (
                    "Learning systems can change attribution without making every result easier to interpret."
                ),
            },
            {
                **BENCHMARKS[0],
                "description": 'AI measurement is changing attribution. Can marketers still prove influence?"',
            },
            {
                **BENCHMARKS[0],
                "description": (
                    "U.S. marketers saw paid clicks rise 3.5%. AI attribution still became harder to interpret."
                ),
            },
            {
                **BENCHMARKS[0],
                "description": "Google Search vs. YouTube shows a change. AI attribution remains difficult.",
            },
            {
                **BENCHMARKS[0],
                "description": "Dr. Smith tested AI search behaviour. Attribution remained difficult.",
            },
            {
                **BENCHMARKS[0],
                "description": "Acme Inc. AI researchers tested search behaviour. Attribution remained difficult.",
            },
            {
                **BENCHMARKS[0],
                "description": "OpenAI ranked No. 1 for AI search tests. The result still needs careful attribution.",
            },
            {
                **BENCHMARKS[0],
                "description": "Alex Smith Jr. outlined an AI strategy. The decision still needs evidence.",
            },
        ]

        for payload in valid_payloads:
            with self.subTest(payload=payload):
                self.assertEqual(validate_seo_payload(payload), payload)

    def test_source_and_optional_context_reach_chat_client_without_application_mutation_or_slicing(self) -> None:
        client, create = self.fake_client(BENCHMARKS[0])
        source = (
            "Complete source evidence for an accurate metadata decision. "
            + "A" * 4100
            + " Literal placeholders {TITLE_MIN} {TITLE_MAX} {DESC_MAX}. FINAL-SOURCE-FACT"
        )

        result = generate_seo(
            client,
            self.config,
            source,
            self.prompts,
            image_context="A supplied chart shows revenue growth.",
            current_title="A current title",
            current_description="A current description.",
            target_keyword="AI revenue growth",
        )

        self.assertEqual(result, BENCHMARKS[0])
        messages = create.call_args.kwargs["messages"]
        self.assertEqual(messages[0]["content"], render_seo_system_prompt(self.prompts))
        self.assertIn(source, messages[1]["content"])
        self.assertIn("FINAL-SOURCE-FACT", messages[1]["content"])
        self.assertIn("Literal placeholders {TITLE_MIN} {TITLE_MAX} {DESC_MAX}.", messages[1]["content"])
        self.assertIn("A supplied chart shows revenue growth.", messages[1]["content"])
        self.assertIn("A current title", messages[1]["content"])
        self.assertIn("A current description.", messages[1]["content"])
        self.assertIn("AI revenue growth", messages[1]["content"])

    def test_blank_post_body_fails_before_an_openai_call(self) -> None:
        client, create = self.fake_client(BENCHMARKS[0])

        with self.assertRaisesRegex(RuntimeError, "source post body is empty"):
            generate_seo(client, self.config, "   ", self.prompts)

        create.assert_not_called()

    def test_source_below_minimum_word_count_requests_more_content_without_openai(self) -> None:
        client, create = self.fake_client(BENCHMARKS[0])

        with self.assertRaisesRegex(InsufficientSeoSourceError, "must contain at least 5 words"):
            generate_seo(client, self.config, "Too little evidence.", self.prompts)

        create.assert_not_called()

    def test_supplied_image_context_can_complete_a_short_post_source(self) -> None:
        client, create = self.fake_client(BENCHMARKS[0])

        result = generate_seo(
            client,
            self.config,
            "Thin body only.",
            self.prompts,
            image_context="A detailed chart supplies the supported revenue comparison used by the metadata.",
        )

        self.assertEqual(result, BENCHMARKS[0])
        self.assertEqual(create.call_count, 1)

    def test_model_can_request_more_source_without_a_fabricated_title(self) -> None:
        insufficient = {
            "headline": INSUFFICIENT_SOURCE_SENTINEL,
            "description": INSUFFICIENT_SOURCE_SENTINEL,
        }
        client, create = self.fake_client(insufficient)

        with self.assertRaisesRegex(InsufficientSeoSourceError, "Supply a more complete post body"):
            generate_seo(
                client,
                self.config,
                "This source has enough words but no usable evidence for accurate metadata.",
                self.prompts,
            )

        self.assertEqual(create.call_count, 1)

    def test_invalid_metadata_is_regenerated_instead_of_trimmed(self) -> None:
        invalid = {
            "headline": "H" * (HEADLINE_MAX + 1),
            "description": BENCHMARKS[0]["description"],
        }
        client, create = self.fake_client(invalid, BENCHMARKS[1])

        result = generate_seo(
            client,
            self.config,
            "A complete supplied post body with supporting evidence.",
            self.prompts,
        )

        self.assertEqual(result, BENCHMARKS[1])
        self.assertEqual(create.call_count, 2)
        retry_prompt = create.call_args_list[1].kwargs["messages"][1]["content"]
        self.assertIn("Correction required", retry_prompt)
        self.assertIn(f"headline must not exceed {HEADLINE_MAX} characters", retry_prompt)

    def test_markdown_fenced_json_is_rejected_then_regenerated(self) -> None:
        fenced = "```json\n" + json.dumps(BENCHMARKS[0]) + "\n```"
        client, create = self.fake_client(fenced, BENCHMARKS[0])

        result = generate_seo(
            client,
            self.config,
            "A complete supplied post body with supporting evidence.",
            self.prompts,
        )

        self.assertEqual(result, BENCHMARKS[0])
        self.assertEqual(create.call_count, 2)
        retry_prompt = create.call_args_list[1].kwargs["messages"][1]["content"]
        self.assertIn("invalid SEO JSON", retry_prompt)

    def test_invalid_metadata_fails_closed_after_bounded_retries(self) -> None:
        invalid = {
            "headline": "H" * (HEADLINE_MAX + 1),
            "description": BENCHMARKS[0]["description"],
        }
        client, create = self.fake_client(*([invalid] * SEO_MAX_ATTEMPTS))

        with self.assertRaisesRegex(RuntimeError, f"after {SEO_MAX_ATTEMPTS} attempts"):
            generate_seo(client, self.config, "A complete supplied post body with supporting evidence.", self.prompts)

        self.assertEqual(create.call_count, SEO_MAX_ATTEMPTS)

    def test_post_adapter_collects_only_supplied_optional_context(self) -> None:
        post = {
            "imageContext": "<p>A chart showing paid clicks rising.</p>",
            "images": [{"alt": "An unused fallback ALT because explicit context wins."}],
            "headline": "Current headline",
            "description": "Current description.",
            "target_keyword": "paid search clicks",
        }

        self.assertEqual(
            seo_context_from_post(post),
            {
                "image_context": "A chart showing paid clicks rising.",
                "current_title": "Current headline",
                "current_description": "Current description.",
                "target_keyword": "paid search clicks",
            },
        )
        self.assertEqual(
            seo_context_from_post({"images": [{"alt": "First chart."}, {"alt": "Second chart."}]}),
            {
                "image_context": "First chart.; Second chart.",
                "current_title": "",
                "current_description": "",
                "target_keyword": "",
            },
        )

    def test_offline_wiring_maps_validated_metadata_to_webflow(self) -> None:
        client, _ = self.fake_client(BENCHMARKS[2])
        config = SimpleNamespace(openai_api_key="test-key", openai_model="gpt-5.6-sol")
        post = {
            "content": (
                "<p>Bending Spoons buys fading apps, cuts staff and folds them into a lean AI operating model "
                "that generates nearly $1 million in revenue per employee.</p>"
            ),
            "url": "https://www.linkedin.com/feed/update/example",
            "published_at": "2026-09-01T08:00:00",
            "images": [],
        }

        with patch("pipeline.enrichment.OpenAI", return_value=client), patch("builtins.print"):
            enriched = enrich_post(post, config)
        field_data = build_field_data(enriched)

        self.assertEqual(enriched["content"], post["content"])
        self.assertEqual(enriched["headline"], BENCHMARKS[2]["headline"])
        self.assertEqual(enriched["description"], BENCHMARKS[2]["description"])
        self.assertEqual(field_data["name"], BENCHMARKS[2]["headline"])
        self.assertEqual(field_data["post-summary"], BENCHMARKS[2]["description"])

    def test_webflow_fallback_uses_the_same_headline_limit(self) -> None:
        fallback = post_headline({"content": "H" * (HEADLINE_MAX + 1)})

        self.assertEqual(len(fallback), HEADLINE_MAX)


if __name__ == "__main__":
    unittest.main()
