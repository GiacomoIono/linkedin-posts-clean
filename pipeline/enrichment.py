from __future__ import annotations

import json
import os
from typing import Any

from openai import OpenAI

from .config import LEGACY_PROMPTS_PATH, PROMPTS_PATH, PipelineConfig
from .utils import sanitize_text, soft_trim, strip_html_to_text


HEADLINE_MAX = 70
DESCRIPTION_MAX = 160


def load_prompts() -> dict[str, str]:
    path = PROMPTS_PATH if PROMPTS_PATH.exists() else LEGACY_PROMPTS_PATH
    if not path.exists():
        raise RuntimeError(f"Missing prompts file: {PROMPTS_PATH}")

    doc = json.loads(path.read_text(encoding="utf-8"))
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
    alt = sanitize_text(response_text(response, "ALT text"))
    if not alt:
        raise RuntimeError("OpenAI returned empty ALT text.")
    return alt


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

    updated_alt = 0
    images = []
    for image in post.get("images", []) or []:
        item = dict(image)
        if item.get("url") and not (item.get("alt") or "").strip():
            try:
                item["alt"] = generate_alt(client, config, item["url"], plain_text, prompts)
                updated_alt += 1
            except Exception as exc:
                print(f"ALT generation failed for {item.get('url')}: {exc}")
        images.append(item)
    enriched["images"] = images
    print(f"ALT text updated for {updated_alt} image(s).")
    return enriched
