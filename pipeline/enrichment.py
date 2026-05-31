from __future__ import annotations

import json
import os
import re
from typing import Any

from openai import OpenAI

from .config import PROMPTS_PATH, PipelineConfig
from .utils import sanitize_text, soft_trim, strip_html_to_text


HEADLINE_MAX = 70
DESCRIPTION_MAX = 160
ALT_MAX = 180
EMOJI_RE = re.compile(r"[\U00010000-\U0010ffff]")
IMAGE_CONTEXT_RE = re.compile(
    r"\b(in the (picture|photo|image)|pictured|the (picture|photo|image) shows|photo shows|image shows)\b",
    re.IGNORECASE,
)


def load_prompts() -> dict[str, str]:
    if not PROMPTS_PATH.exists():
        raise RuntimeError(f"Missing prompts file: {PROMPTS_PATH}")

    doc = json.loads(PROMPTS_PATH.read_text(encoding="utf-8"))
    prompt_sets = doc.get("linkedin_post_enrichment")
    if not isinstance(prompt_sets, list) or not prompt_sets:
        raise RuntimeError("prompts.json must contain a non-empty linkedin_post_enrichment array.")

    desired_id = os.getenv("LINKEDIN_PROMPT_PROFILE", "").strip()
    chosen = None
    if desired_id:
        chosen = next((item for item in prompt_sets if item.get("id") == desired_id), None)
    if chosen is None:
        chosen = prompt_sets[0]

    required = ["seo_system", "seo_user", "alt_system", "alt_user"]
    for key in required:
        if not isinstance(chosen.get(key), str):
            raise RuntimeError(f"Selected enrichment prompt is missing {key}.")
    return chosen


def fill_placeholders(template: str, mapping: dict[str, str]) -> str:
    output = template
    for key, value in mapping.items():
        output = output.replace("{" + key + "}", value)
    return output


def completion_kwargs(config: PipelineConfig, messages: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "model": config.openai_model,
        "messages": messages,
    }


def response_text(response, label: str) -> str:
    choice = response.choices[0] if response.choices else None
    text = ((choice.message.content if choice and choice.message else None) or "").strip()
    if text:
        return text
    finish_reason = getattr(choice, "finish_reason", "unknown") if choice else "missing_choice"
    usage = getattr(response, "usage", None)
    raise RuntimeError(f"OpenAI returned empty {label} output (finish_reason={finish_reason}, usage={usage}).")


def parse_json_response(response, label: str) -> dict[str, Any]:
    raw = response_text(response, label)
    if raw.startswith("```"):
        raw = raw.strip("`").strip()
        if raw.lower().startswith("json"):
            raw = raw[4:].strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"OpenAI returned invalid {label} JSON: {exc}. Raw output: {raw[:500]}") from exc
    if not isinstance(data, dict):
        raise RuntimeError(f"OpenAI returned {label} JSON that is not an object.")
    return data


def clean_alt(value: str) -> str:
    value = EMOJI_RE.sub("", value or "")
    return soft_trim(sanitize_text(value), ALT_MAX)


def has_missing_image_alt(post: dict[str, Any] | None) -> bool:
    if not isinstance(post, dict):
        return False
    for image in post.get("images", []) or []:
        if isinstance(image, dict) and image.get("url") and not str(image.get("alt") or "").strip():
            return True
    return False


def explicit_context_alt_text(plain_text: str) -> str:
    text = strip_html_to_text(plain_text)
    sentences = re.split(r"(?<=[.!?])\s+", text)
    for sentence in sentences:
        if not IMAGE_CONTEXT_RE.search(sentence):
            continue
        alt = re.sub(r"^in the (picture|photo|image)\s*:\s*", "", sentence.strip(), flags=re.IGNORECASE)
        alt = re.sub(r"^the (picture|photo|image) shows\s+", "", alt, flags=re.IGNORECASE)
        alt = re.sub(r"^(photo|image) shows\s+", "", alt, flags=re.IGNORECASE)
        alt = clean_alt(alt)
        if alt:
            return alt[0].upper() + alt[1:]
    return ""


def fallback_alt_text(plain_text: str) -> str:
    explicit_alt = explicit_context_alt_text(plain_text)
    if explicit_alt:
        return explicit_alt

    text = strip_html_to_text(plain_text)
    summary = clean_alt(text)
    if summary:
        return clean_alt(f"Visual accompanying LinkedIn post about {summary}")
    return "Visual accompanying LinkedIn post"


def generate_seo(client: OpenAI, config: PipelineConfig, plain_text: str, prompts: dict[str, str]) -> dict[str, str]:
    user_msg = fill_placeholders(
        prompts["seo_user"],
        {
            "CONTENT": plain_text[:4000],
            "HEADLINE_MAX": str(HEADLINE_MAX),
            "TITLE_MAX": str(HEADLINE_MAX),
            "DESC_MAX": str(DESCRIPTION_MAX),
        },
    )
    response = client.chat.completions.create(
        **completion_kwargs(
            config,
            [
                {"role": "system", "content": prompts["seo_system"]},
                {"role": "user", "content": user_msg},
            ],
        )
    )
    data = parse_json_response(response, "SEO")
    headline = soft_trim(sanitize_text(str(data.get("headline", ""))), HEADLINE_MAX)
    description = soft_trim(sanitize_text(str(data.get("description", ""))), DESCRIPTION_MAX)
    if not headline or not description:
        raise RuntimeError(f"OpenAI returned incomplete SEO JSON: {data}")
    return {"headline": headline, "description": description}


def generate_alt(client: OpenAI, config: PipelineConfig, image_url: str, plain_text: str, prompts: dict[str, str]) -> str:
    user_intro = fill_placeholders(prompts["alt_user"], {"CONTEXT": plain_text[:700]})
    response = client.chat.completions.create(
        **completion_kwargs(
            config,
            [
                {"role": "system", "content": prompts["alt_system"]},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": user_intro},
                        {"type": "image_url", "image_url": {"url": image_url}},
                    ],
                },
            ],
        )
    )
    alt = clean_alt(response_text(response, "ALT text"))
    if not alt:
        raise RuntimeError("OpenAI returned empty ALT text.")
    return alt


def generate_context_alt(client: OpenAI, config: PipelineConfig, plain_text: str, prompts: dict[str, str]) -> str:
    user_msg = (
        "The image input could not be analyzed. Generate one accessible ALT text sentence "
        "from the post context only. Prefer any explicit picture/photo description in the text. "
        "Use 8-18 words, avoid opinions, emojis, hashtags, and the phrase Image of.\n\n"
        f"Post context:\n{plain_text[:1000]}"
    )
    response = client.chat.completions.create(
        **completion_kwargs(
            config,
            [
                {"role": "system", "content": prompts["alt_system"]},
                {"role": "user", "content": user_msg},
            ],
        )
    )
    alt = clean_alt(response_text(response, "context ALT text"))
    if not alt:
        raise RuntimeError("OpenAI returned empty context ALT text.")
    return alt


def populate_missing_alt(
    client: OpenAI,
    config: PipelineConfig,
    item: dict[str, Any],
    plain_text: str,
    prompts: dict[str, str],
) -> str | None:
    image_url = item.get("url")
    if not image_url or (item.get("alt") or "").strip():
        return None

    try:
        item["alt"] = generate_alt(client, config, image_url, plain_text, prompts)
        return "vision"
    except Exception as exc:
        print(f"ALT vision generation failed for {image_url}: {exc}")

    explicit_alt = explicit_context_alt_text(plain_text)
    if explicit_alt:
        item["alt"] = explicit_alt
        return "explicit_context"

    try:
        item["alt"] = generate_context_alt(client, config, plain_text, prompts)
        return "context"
    except Exception as exc:
        print(f"ALT context generation failed for {image_url}: {exc}")

    item["alt"] = fallback_alt_text(plain_text)
    return "local_fallback"


def populate_missing_alts_for_post(
    post: dict[str, Any],
    client: OpenAI,
    config: PipelineConfig,
    prompts: dict[str, str],
) -> tuple[dict[str, Any], dict[str, int]]:
    plain_text = strip_html_to_text(post.get("content", ""))
    alt_sources = {"vision": 0, "explicit_context": 0, "context": 0, "local_fallback": 0}
    enriched = dict(post)
    images = []

    for image in post.get("images", []) or []:
        item = dict(image)
        source = populate_missing_alt(client, config, item, plain_text, prompts)
        if source:
            alt_sources[source] += 1
        images.append(item)

    enriched["images"] = images
    return enriched, alt_sources


def log_alt_sources(alt_sources: dict[str, int]) -> None:
    updated_alt = sum(alt_sources.values())
    print(f"ALT text updated for {updated_alt} image(s): {alt_sources}.")


def backfill_missing_alt(post: dict[str, Any], config: PipelineConfig) -> dict[str, Any]:
    if not config.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is missing.")

    prompts = load_prompts()
    client = OpenAI(api_key=config.openai_api_key)
    enriched, alt_sources = populate_missing_alts_for_post(post, client, config, prompts)
    log_alt_sources(alt_sources)
    return enriched


def enrich_post(post: dict[str, Any], config: PipelineConfig) -> dict[str, Any]:
    if not config.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is missing.")

    plain_text = strip_html_to_text(post.get("content", ""))
    prompts = load_prompts()
    client = OpenAI(api_key=config.openai_api_key)

    seo = generate_seo(client, config, plain_text, prompts)
    enriched = dict(post)
    enriched["headline"] = seo["headline"]
    enriched["description"] = seo["description"]
    enriched.pop("seo", None)
    print(f"SEO generated with {config.openai_model}: {seo}")

    enriched, alt_sources = populate_missing_alts_for_post(enriched, client, config, prompts)
    log_alt_sources(alt_sources)
    return enriched
