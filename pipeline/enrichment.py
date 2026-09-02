from __future__ import annotations

import json
import os
import re
from typing import Any

from openai import OpenAI

from .config import PROMPTS_PATH, PipelineConfig
from .utils import sanitize_text, soft_trim, strip_html_to_text

HEADLINE_MIN = 45
HEADLINE_TARGET_MIN = 48
HEADLINE_TARGET_MAX = 58
HEADLINE_MAX = 60
DESCRIPTION_TARGET_MIN = 145
DESCRIPTION_TARGET_MAX = 155
DESCRIPTION_MAX = 160
DESCRIPTION_MIN_WORDS = 3
SEO_SOURCE_MIN_WORDS = 5
SEO_MAX_ATTEMPTS = 2
INSUFFICIENT_SOURCE_SENTINEL = "__INSUFFICIENT_SOURCE__"
ALT_MAX = 180
EMOJI_RE = re.compile(
    r"[\u203c\u2049\u20e3\u2122\u2139\u2194-\u21ff\u2300-\u23ff\u24c2\u25aa-\u27bf\ufe0f"
    r"\U0001f1e6-\U0001f1ff\U0001f300-\U0001faff]"
)
BAD_AI_CASE_RE = re.compile(r"\b(?:ai|Ai)\b")
HASHTAG_RE = re.compile(r"(?<!\w)#\w+")
SWISS_NUMBER_RE = re.compile(r"\b\d{1,3}(?:['\u2019]\d{3})+\b")
PLACEHOLDER_RE = re.compile(r"\{([A-Z][A-Z0-9_]*)\}")
MARKDOWN_RE = re.compile(
    r"(?:\*|`|__|~~|(?<!\w)_[^_\n]+_(?!\w)|!?\[[^\]\n]+\]\([^)]+\)|"
    r"^(?:#{1,6}|>|[-+]|\d+\.)\s)"
)
WORD_RE = re.compile(r"\b[\w'-]+\b")
DESCRIPTION_END_RE = re.compile(r'''[.?]["'\u2019\u201d)]*$''')
SENTENCE_CANDIDATE_RE = re.compile(r'''[.?]["'\u2019\u201d)]*(?=\s|$)''')
NON_TERMINAL_ABBREVIATIONS = frozenset(
    {
        "apr",
        "approx",
        "assn",
        "aug",
        "ave",
        "bros",
        "co",
        "corp",
        "dec",
        "dept",
        "dr",
        "e.g",
        "ed",
        "est",
        "etc",
        "feb",
        "fig",
        "gen",
        "gov",
        "hon",
        "i.e",
        "inc",
        "jan",
        "jr",
        "jul",
        "jun",
        "ltd",
        "mar",
        "mr",
        "mrs",
        "ms",
        "nov",
        "no",
        "oct",
        "p",
        "pp",
        "prof",
        "rev",
        "sec",
        "sep",
        "sept",
        "sr",
        "st",
        "u.k",
        "u.s",
        "vol",
        "vs",
    }
)
SEO_OUTPUT_KEYS = frozenset({"headline", "description"})
OFFICIAL_NAMES = (
    "ChatGPT Pro",
    "ChatGPT",
    "OpenAI",
    "YouTube",
    "LinkedIn",
    "Google Search",
    "AI Overviews",
    "airBaltic",
    "eCommerce",
)
GENERIC_DESCRIPTION_OPENINGS = (
    "discover",
    "explore",
    "learn",
    "read more",
    "find out",
    "this post explains",
    "everything you need to know",
)
GENERIC_DESCRIPTION_OPENING_RE = re.compile(
    r"^(?:" + "|".join(re.escape(opening) for opening in GENERIC_DESCRIPTION_OPENINGS) + r")\b",
    re.IGNORECASE,
)
IMAGE_CONTEXT_RE = re.compile(
    r"\b(in the (picture|photo|image)|pictured|the (picture|photo|image) shows|photo shows|image shows)\b",
    re.IGNORECASE,
)


class InsufficientSeoSourceError(RuntimeError):
    pass


def prompt_text(value: Any, key: str) -> str:
    if isinstance(value, str) and value.strip():
        return value
    if isinstance(value, list) and value and all(isinstance(line, str) for line in value):
        rendered = "\n".join(value)
        if rendered.strip():
            return rendered
    raise RuntimeError(f"Selected enrichment prompt is missing {key}.")


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

    selected = dict(chosen)
    required = [
        "seo_system",
        "seo_user",
        "link_system",
        "link_user",
        "link_verify_system",
        "link_verify_user",
        "link_coverage_verify_system",
        "link_coverage_verify_user",
        "alt_system",
        "alt_user",
        "image_system",
        "image_user",
        "image_qa_system",
        "image_qa_user",
    ]
    for key in required:
        selected[key] = prompt_text(chosen.get(key), key)
    return selected


def fill_placeholders(template: str, mapping: dict[str, str]) -> str:
    return PLACEHOLDER_RE.sub(lambda match: mapping.get(match.group(1), match.group(0)), template)


def seo_prompt_mapping(
    content: str = "",
    *,
    image_context: str = "",
    current_title: str = "",
    current_description: str = "",
    target_keyword: str = "",
) -> dict[str, str]:
    return {
        "CONTENT": content,
        "IMAGE_CONTEXT": image_context or "Not supplied.",
        "CURRENT_TITLE": current_title or "Not supplied.",
        "CURRENT_DESCRIPTION": current_description or "Not supplied.",
        "TARGET_KEYWORD": target_keyword or "Not supplied.",
        "TITLE_MIN": str(HEADLINE_MIN),
        "TITLE_TARGET_MIN": str(HEADLINE_TARGET_MIN),
        "TITLE_TARGET_MAX": str(HEADLINE_TARGET_MAX),
        "HEADLINE_MAX": str(HEADLINE_MAX),
        "TITLE_MAX": str(HEADLINE_MAX),
        "DESC_TARGET_MIN": str(DESCRIPTION_TARGET_MIN),
        "DESC_TARGET_MAX": str(DESCRIPTION_TARGET_MAX),
        "DESC_MAX": str(DESCRIPTION_MAX),
    }


def first_post_text(post: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = post.get(key)
        if isinstance(value, str):
            text = strip_html_to_text(value)
            if text:
                return text
    return ""


def seo_context_from_post(post: dict[str, Any]) -> dict[str, str]:
    image_context = first_post_text(post, "imageContext", "image_context")
    if not image_context:
        supplied_alts = [
            strip_html_to_text(str(image.get("alt") or ""))
            for image in post.get("images", []) or []
            if isinstance(image, dict) and str(image.get("alt") or "").strip()
        ]
        image_context = "; ".join(alt for alt in supplied_alts if alt)

    return {
        "image_context": image_context,
        "current_title": first_post_text(post, "currentTitle", "current_title", "headline", "title"),
        "current_description": first_post_text(
            post,
            "currentDescription",
            "current_description",
            "description",
        ),
        "target_keyword": first_post_text(post, "targetKeyword", "target_keyword"),
    }


def render_seo_system_prompt(prompts: dict[str, str]) -> str:
    return fill_placeholders(prompts["seo_system"], seo_prompt_mapping())


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


def responses_text(response, label: str) -> str:
    text = str(getattr(response, "output_text", "") or "").strip()
    if text:
        return text
    raise RuntimeError(f"OpenAI returned empty {label} output.")


def parse_json_response(response, label: str) -> dict[str, Any]:
    raw = response_text(response, label)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"OpenAI returned invalid {label} JSON: {exc}. Raw output: {raw[:500]}") from exc
    if not isinstance(data, dict):
        raise RuntimeError(f"OpenAI returned {label} JSON that is not an object.")
    return data


def normalise_metadata_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def letter_word_count(value: str) -> int:
    return sum(any(character.isalpha() for character in token) for token in WORD_RE.findall(value))


def seo_sentence_count(value: str) -> int:
    count = 0
    for match in SENTENCE_CANDIDATE_RE.finditer(value):
        punctuation = value[match.start()]
        is_final = not value[match.end() :].strip()
        if punctuation == "." and not is_final:
            token_match = re.search(r"([A-Za-z][A-Za-z.]*)$", value[: match.start()])
            token = token_match.group(1) if token_match else ""
            is_initialism = bool(re.fullmatch(r"(?:[A-Za-z]\.)+[A-Za-z]", token))
            if token.casefold() in NON_TERMINAL_ABBREVIATIONS or is_initialism:
                continue
        count += 1
    return count


def wrong_official_capitalisation(value: str) -> list[str]:
    wrong = []
    for official_name in OFFICIAL_NAMES:
        pattern = rf"(?<!\w){re.escape(official_name)}(?!\w)"
        for match in re.finditer(pattern, value, flags=re.IGNORECASE):
            if match.group(0) != official_name:
                wrong.append(official_name)
                break
    return wrong


def validate_seo_payload(data: dict[str, Any]) -> dict[str, str]:
    keys = set(data)
    if keys != SEO_OUTPUT_KEYS:
        missing = sorted(SEO_OUTPUT_KEYS - keys)
        unexpected = sorted(keys - SEO_OUTPUT_KEYS)
        details = []
        if missing:
            details.append(f"missing keys: {', '.join(missing)}")
        if unexpected:
            details.append(f"unexpected keys: {', '.join(unexpected)}")
        raise RuntimeError(f"SEO JSON must contain exactly headline and description ({'; '.join(details)}).")

    for key in SEO_OUTPUT_KEYS:
        if not isinstance(data[key], str):
            raise RuntimeError(f"SEO JSON field {key} must be a string.")

    headline = normalise_metadata_text(data["headline"])
    description = normalise_metadata_text(data["description"])
    if headline == INSUFFICIENT_SOURCE_SENTINEL or description == INSUFFICIENT_SOURCE_SENTINEL:
        if headline == description == INSUFFICIENT_SOURCE_SENTINEL:
            raise InsufficientSeoSourceError(
                "The supplied material cannot support accurate SEO metadata. Supply a more complete post body."
            )
        raise RuntimeError("SEO JSON must use the insufficient-source signal for both fields.")
    errors = []

    if not headline:
        errors.append("headline must be non-empty")
    elif len(headline) < HEADLINE_MIN:
        errors.append(f"headline must contain at least {HEADLINE_MIN} characters")
    elif len(headline) > HEADLINE_MAX:
        errors.append(f"headline must not exceed {HEADLINE_MAX} characters")

    if not description:
        errors.append("description must be non-empty")
    elif len(description) > DESCRIPTION_MAX:
        errors.append(f"description must not exceed {DESCRIPTION_MAX} characters")
    elif letter_word_count(description) < DESCRIPTION_MIN_WORDS:
        errors.append(f"description must contain at least {DESCRIPTION_MIN_WORDS} words")
    elif not DESCRIPTION_END_RE.search(description):
        errors.append("description must be a complete sentence ending with a full stop or question mark")
    else:
        sentence_count = seo_sentence_count(description)
        if not 1 <= sentence_count <= 2:
            errors.append("description must contain one or two complete sentences")

    for field_name, value in (("headline", headline), ("description", description)):
        if "\u2014" in value:
            errors.append(f"{field_name} must not contain an em dash")
        if "!" in value:
            errors.append(f"{field_name} must not contain an exclamation mark")
        if EMOJI_RE.search(value):
            errors.append(f"{field_name} must not contain emoji")
        if BAD_AI_CASE_RE.search(value):
            errors.append(f'{field_name} must write artificial intelligence as "AI"')
        if SWISS_NUMBER_RE.search(value):
            errors.append(f"{field_name} must use international comma-separated numbers")
        if MARKDOWN_RE.search(value):
            errors.append(f"{field_name} must not contain Markdown")
        wrong_names = wrong_official_capitalisation(value)
        if wrong_names:
            errors.append(f"{field_name} must preserve official capitalisation: {', '.join(wrong_names)}")

    if headline:
        letters = "".join(character for character in headline if character.isalpha())
        if letters and letters == letters.upper():
            errors.append("headline must not use all capital letters")

    if description:
        if HASHTAG_RE.search(description):
            errors.append("description must not contain hashtags")
        if "..." in description or "\u2026" in description:
            errors.append("description must not contain unfinished ellipses")
        if GENERIC_DESCRIPTION_OPENING_RE.match(description):
            errors.append("description must not use a generic opening")

    if errors:
        raise RuntimeError("SEO metadata violated the publishing contract: " + "; ".join(errors) + ".")
    return {"headline": headline, "description": description}


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


def generate_seo(
    client: OpenAI,
    config: PipelineConfig,
    plain_text: str,
    prompts: dict[str, str],
    *,
    image_context: str = "",
    current_title: str = "",
    current_description: str = "",
    target_keyword: str = "",
) -> dict[str, str]:
    source = plain_text.strip()
    if not source:
        raise RuntimeError("SEO source post body is empty.")
    authoritative_source = f"{source} {image_context}".strip()
    if letter_word_count(authoritative_source) < SEO_SOURCE_MIN_WORDS:
        raise InsufficientSeoSourceError(
            f"SEO source material must contain at least {SEO_SOURCE_MIN_WORDS} words. "
            "Supply a more complete post body or image context."
        )

    system_msg = render_seo_system_prompt(prompts)
    user_msg = fill_placeholders(
        prompts["seo_user"],
        seo_prompt_mapping(
            source,
            image_context=image_context,
            current_title=current_title,
            current_description=current_description,
            target_keyword=target_keyword,
        ),
    )
    last_error: RuntimeError | None = None

    for attempt in range(1, SEO_MAX_ATTEMPTS + 1):
        attempt_user_msg = user_msg
        if last_error is not None:
            attempt_user_msg += (
                "\n\nCorrection required:\n"
                f"The previous response was rejected because {last_error}\n"
                "Rewrite both fields and return a new JSON object that follows every requirement."
            )

        response = client.chat.completions.create(
            **completion_kwargs(
                config,
                [
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": attempt_user_msg},
                ],
            )
        )
        try:
            return validate_seo_payload(parse_json_response(response, "SEO"))
        except InsufficientSeoSourceError:
            raise
        except RuntimeError as exc:
            last_error = exc
            if attempt == SEO_MAX_ATTEMPTS:
                break

    raise RuntimeError(
        f"OpenAI returned invalid SEO metadata after {SEO_MAX_ATTEMPTS} attempts. Last error: {last_error}"
    ) from last_error


def generate_alt_with_responses(
    client: OpenAI,
    config: PipelineConfig,
    image_url: str,
    plain_text: str,
    prompts: dict[str, str],
) -> str:
    user_intro = fill_placeholders(prompts["alt_user"], {"CONTEXT": plain_text[:700], "IMAGE_URL": image_url})
    response = client.responses.create(
        model=config.openai_model,
        instructions=prompts["alt_system"],
        input=[
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": user_intro},
                    {"type": "input_image", "image_url": image_url},
                ],
            }
        ],
    )
    alt = clean_alt(responses_text(response, "ALT text"))
    if not alt:
        raise RuntimeError("OpenAI returned empty ALT text.")
    return alt


def generate_alt_with_chat(
    client: OpenAI,
    config: PipelineConfig,
    image_url: str,
    plain_text: str,
    prompts: dict[str, str],
) -> str:
    user_intro = fill_placeholders(prompts["alt_user"], {"CONTEXT": plain_text[:700], "IMAGE_URL": image_url})
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


def generate_alt(client: OpenAI, config: PipelineConfig, image_url: str, plain_text: str, prompts: dict[str, str]) -> str:
    try:
        return generate_alt_with_responses(client, config, image_url, plain_text, prompts)
    except Exception as responses_exc:
        try:
            return generate_alt_with_chat(client, config, image_url, plain_text, prompts)
        except Exception as chat_exc:
            raise RuntimeError(f"Responses API failed: {responses_exc}; Chat Completions failed: {chat_exc}") from chat_exc


def generate_context_alt(
    client: OpenAI,
    config: PipelineConfig,
    plain_text: str,
    prompts: dict[str, str],
    image_url: str = "",
) -> str:
    user_msg = (
        "The image input could not be analyzed. Generate one conservative accessible ALT text sentence "
        "from the post context only. Do not pretend to see the image. Prefer any explicit picture/photo "
        "description in the text. Otherwise, describe it generically as a visual related to the post topic. "
        "Use 8-18 words, avoid opinions, emojis, hashtags, and phrases like Image of or Picture of.\n\n"
        f"Image source URL:\n{image_url or 'Unavailable'}\n\n"
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
        item["alt"] = generate_context_alt(client, config, plain_text, prompts, image_url)
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


def enrich_post(post: dict[str, Any], config: PipelineConfig) -> dict[str, Any]:
    if not config.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is missing.")

    plain_text = strip_html_to_text(post.get("content", ""))
    prompts = load_prompts()
    client = OpenAI(api_key=config.openai_api_key)

    seo = generate_seo(client, config, plain_text, prompts, **seo_context_from_post(post))
    enriched = dict(post)
    enriched["headline"] = seo["headline"]
    enriched["description"] = seo["description"]
    enriched.pop("seo", None)
    print(f"SEO generated with {config.openai_model}: {seo}")

    enriched, alt_sources = populate_missing_alts_for_post(enriched, client, config, prompts)
    log_alt_sources(alt_sources)
    return enriched
