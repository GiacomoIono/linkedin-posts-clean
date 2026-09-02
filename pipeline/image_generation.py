from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
import time
import unicodedata
from contextlib import ExitStack
from pathlib import Path
from typing import Any
from urllib.parse import quote

import requests
from openai import OpenAI

from .config import GENERATED_IMAGE_DIR, PipelineConfig
from .enrichment import load_prompts, response_text
from .generated_images import (
    ensure_generated_filename_available,
    image_sha256,
    record_generated_image,
    validate_registered_generated_image,
)
from .image_processing import (
    MAX_GENERATED_IMAGE_BYTES,
    is_valid_prepared_png_bytes,
    is_valid_prepared_png_file,
    inspect_raw_image,
    prepare_blog_main_png,
)
from .image_references import (
    HUMAN_CONSEQUENCE_REFERENCE_IDS,
    StyleReference,
    reference_catalog_prompt,
    reference_manifest,
    validated_style_references,
)
from .utils import post_identity, sanitize_text, soft_trim, strip_html_to_text

RAW_GENERATION_SIZE = "1536x864"
RAW_GENERATION_QUALITY = "high"
RAW_GENERATION_FORMAT = "png"
GENERATED_IMAGE_TIMEOUT_SECONDS = 180.0
GENERATED_IMAGE_ALT_MAX = 180
MAX_IMAGE_PROMPT_CONTENT = 6000
PUBLIC_IMAGE_MAX_ATTEMPTS = 10
PUBLIC_IMAGE_RETRY_SECONDS = 5
RAW_REPOSITORY_URL = "https://raw.githubusercontent.com/GiacomoIono/linkedin-posts-clean"
POST_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

CONCEPT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "use_case",
        "central_claim",
        "tension",
        "audience",
        "emotional_register",
        "motifs",
        "scene",
        "backdrop",
        "subject",
        "mood",
        "reference_ids",
        "alt",
    ],
    "properties": {
        "use_case": {"type": "string", "enum": ["illustration-story", "stylized-concept"]},
        "central_claim": {"type": "string"},
        "tension": {"type": "string"},
        "audience": {"type": "string"},
        "emotional_register": {"type": "string"},
        "motifs": {
            "type": "array",
            "minItems": 2,
            "maxItems": 3,
            "items": {"type": "string"},
        },
        "scene": {"type": "string"},
        "backdrop": {"type": "string"},
        "subject": {"type": "string"},
        "mood": {"type": "string"},
        "reference_ids": {
            "type": "array",
            "minItems": 3,
            "maxItems": 3,
            "items": {"type": "string"},
        },
        "alt": {"type": "string"},
    },
}

QUALITY_REVIEW_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "article_fit",
        "human_editorial_resonance",
        "thumbnail_clarity",
        "house_style_match",
        "technical_cleanliness",
        "material_defect",
        "issues",
        "passed",
        "rationale",
        "alt",
    ],
    "properties": {
        "article_fit": {"type": "integer", "minimum": 0, "maximum": 100},
        "human_editorial_resonance": {"type": "integer", "minimum": 0, "maximum": 100},
        "thumbnail_clarity": {"type": "integer", "minimum": 0, "maximum": 100},
        "house_style_match": {"type": "integer", "minimum": 0, "maximum": 100},
        "technical_cleanliness": {"type": "integer", "minimum": 0, "maximum": 100},
        "material_defect": {"type": "boolean"},
        "issues": {"type": "array", "items": {"type": "string"}},
        "passed": {"type": "boolean"},
        "rationale": {"type": "string"},
        "alt": {"type": "string"},
    },
}


def source_images(post: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        image
        for image in post.get("images", []) or []
        if isinstance(image, dict) and str(image.get("url") or "").strip()
    ]


def post_date(post: dict[str, Any]) -> str:
    value = str(post.get("published_at") or "")[:10]
    if not POST_DATE_RE.fullmatch(value):
        raise RuntimeError(
            "Cannot generate an image because the LinkedIn publication date is missing or invalid."
        )
    return value


def article_headline(post: dict[str, Any], plain_text: str) -> str:
    headline = sanitize_text(str(post.get("headline") or ""))
    if headline:
        return headline
    return soft_trim(plain_text or "LinkedIn post", 160)


def _filename_slug(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", normalized.lower()).strip("-")
    if len(slug) > 52:
        slug = slug[:52].rsplit("-", 1)[0]
    return slug or "linkedin-post"


def generated_image_filename(post: dict[str, Any]) -> str:
    plain_text = strip_html_to_text(str(post.get("content") or ""))
    # The pre-Webflow stage sees the raw post, while the main stage adds an SEO
    # headline. Derive the stable slug only from source content shared by both.
    slug = _filename_slug(plain_text)
    source_url = post_identity(post)
    if not source_url:
        raise RuntimeError("Cannot name a generated image without a LinkedIn post URL.")
    url_hash = hashlib.sha256(source_url.encode("utf-8")).hexdigest()[:10]
    return f"{post_date(post)}-{slug}-{url_hash}.png"


def generated_image_path(post: dict[str, Any]) -> Path:
    return GENERATED_IMAGE_DIR / generated_image_filename(post)


def generated_image_url(post: dict[str, Any], public_ref: str) -> str:
    safe_ref = quote((public_ref or "main").strip() or "main", safe="/")
    filename = quote(generated_image_filename(post), safe="")
    return f"{RAW_REPOSITORY_URL}/{safe_ref}/images/generated/{filename}"


def _structured_response_format(name: str, schema: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "json_schema",
        "json_schema": {"name": name, "strict": True, "schema": schema},
    }


def _parse_structured_chat_response(response: Any, label: str) -> dict[str, Any]:
    raw = response_text(response, label)
    try:
        result = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"OpenAI returned invalid {label} JSON.") from exc
    if not isinstance(result, dict):
        raise RuntimeError(f"OpenAI returned {label} JSON that is not an object.")
    return result


def plan_image_concept(
    post: dict[str, Any],
    config: PipelineConfig,
    client: OpenAI,
    prompts: dict[str, str],
) -> dict[str, Any]:
    plain_text = strip_html_to_text(str(post.get("content") or ""))
    headline = article_headline(post, plain_text)
    user_prompt = prompts["image_user"].format(
        HEADLINE=headline,
        CONTENT=plain_text[:MAX_IMAGE_PROMPT_CONTENT],
        REFERENCE_CATALOG=reference_catalog_prompt(),
    )
    response = client.chat.completions.create(
        model=config.openai_model,
        messages=[
            {"role": "system", "content": prompts["image_system"]},
            {"role": "user", "content": user_prompt},
        ],
        response_format=_structured_response_format("blog_main_image_concept", CONCEPT_SCHEMA),
    )
    concept = _parse_structured_chat_response(response, "image concept")
    if not all(str(concept.get(key) or "").strip() for key in (
        "central_claim", "tension", "audience", "emotional_register", "scene", "backdrop", "subject", "mood", "alt"
    )):
        raise RuntimeError("OpenAI returned an incomplete image concept.")
    motifs = concept.get("motifs")
    if not isinstance(motifs, list) or not 2 <= len(motifs) <= 3:
        raise RuntimeError("OpenAI must choose two or three concrete motifs for the sole image concept.")
    concept["alt"] = soft_trim(sanitize_text(str(concept["alt"])), GENERATED_IMAGE_ALT_MAX)
    if not concept["alt"]:
        raise RuntimeError("OpenAI returned an empty ALT description for the image concept.")
    validated_style_references(concept.get("reference_ids") or [])
    return concept


def ordered_references(reference_ids: list[str]) -> tuple[StyleReference, ...]:
    references = list(validated_style_references(reference_ids))
    human_index = next(
        index
        for index, reference in enumerate(references)
        if reference.reference_id in HUMAN_CONSEQUENCE_REFERENCE_IDS
    )
    references.insert(0, references.pop(human_index))
    return tuple(references)


def build_generation_prompt(
    concept: dict[str, Any], references: tuple[StyleReference, ...]
) -> str:
    return "\n".join(
        [
            f"Use case: {concept['use_case']}",
            "Asset type: borderless blog main image",
            f"Primary request: {concept['scene']}",
            "Input images: "
            f"Image 1: style reference for human narrative - {references[0].description}; "
            f"Image 2: style reference for medium and palette - {references[1].description}; "
            f"Image 3: style reference for composition or metaphor - {references[2].description}",
            f"Scene/backdrop: {concept['backdrop']}",
            f"Subject: {concept['subject']}",
            "Style/medium: muted, human-centered noir editorial illustration; graphic-novel ink linework; charcoal, graphite, and restrained watercolor/gouache washes; tactile paper grain; sophisticated rather than superhero-comic styling",
            "Composition/framing: cinematic 16:9 landscape; full bleed; readable focal hierarchy at thumbnail size; no ornamental frame",
            f"Lighting/mood: {concept['mood']}; shadows retain detail and are never crushed to pure black",
            "Color palette: greyscale-led, low-saturation stone and charcoal midtones with one or two restrained blue, rust, amber, burgundy, or forest accents",
            "Text: none",
            "Constraints: concept must communicate the article's central thesis; synthesize the references' visual family without copying their literal subjects or layouts; prefer a clear human emotional anchor; non-photorealistic; no title, caption, lettering, logo, watermark, or visible brand mark",
            "Avoid: neon or flashy colors, pitch-black areas, glossy 3D, flat corporate vector art, generic stock imagery, visual clutter, ornate mechanical spectacle, and comic-panel borders",
        ]
    )


def response_image_bytes(response: Any) -> bytes:
    data = getattr(response, "data", None)
    if not isinstance(data, list) or len(data) != 1:
        raise RuntimeError("OpenAI must return exactly one image result.")
    encoded = str(getattr(data[0], "b64_json", "") or "")
    if not encoded:
        raise RuntimeError("OpenAI returned no image data.")
    try:
        return base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise RuntimeError("OpenAI returned invalid base64 image data.") from exc


def generate_one_raw_image(
    client: OpenAI,
    config: PipelineConfig,
    prompt: str,
    references: tuple[StyleReference, ...],
) -> bytes:
    with ExitStack() as stack:
        reference_files = [stack.enter_context(reference.path.open("rb")) for reference in references]
        response = client.images.edit(
            model=config.openai_image_model,
            image=reference_files,
            prompt=prompt,
            n=1,
            size=RAW_GENERATION_SIZE,
            quality=RAW_GENERATION_QUALITY,
            output_format=RAW_GENERATION_FORMAT,
            background="opaque",
        )
    return response_image_bytes(response)


def review_raw_image(
    client: OpenAI,
    config: PipelineConfig,
    image_bytes: bytes,
    concept: dict[str, Any],
    prompts: dict[str, str],
) -> dict[str, Any]:
    encoded = base64.b64encode(image_bytes).decode("ascii")
    review_prompt = prompts["image_qa_user"].format(
        CENTRAL_CLAIM=concept["central_claim"],
        TENSION=concept["tension"],
        SCENE=concept["scene"],
    )
    response = client.chat.completions.create(
        model=config.openai_model,
        messages=[
            {"role": "system", "content": prompts["image_qa_system"]},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": review_prompt},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{encoded}"},
                    },
                ],
            },
        ],
        response_format=_structured_response_format("blog_main_image_quality_review", QUALITY_REVIEW_SCHEMA),
    )
    review = _parse_structured_chat_response(response, "image quality review")
    score_fields = (
        "article_fit",
        "human_editorial_resonance",
        "thumbnail_clarity",
        "house_style_match",
        "technical_cleanliness",
    )
    try:
        scores = {field: int(review[field]) for field in score_fields}
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError("OpenAI returned incomplete image quality scores.") from exc
    if any(score < 0 or score > 100 for score in scores.values()):
        raise RuntimeError("OpenAI returned an image quality score outside 0-100.")
    review["alt"] = soft_trim(
        sanitize_text(str(review.get("alt") or "")), GENERATED_IMAGE_ALT_MAX
    )
    if not review["alt"]:
        raise RuntimeError(
            "OpenAI returned an empty final ALT description after reviewing the image."
        )
    weighted_score = round(
        scores["article_fit"] * 0.30
        + scores["human_editorial_resonance"] * 0.25
        + scores["thumbnail_clarity"] * 0.20
        + scores["house_style_match"] * 0.15
        + scores["technical_cleanliness"] * 0.10,
        2,
    )
    review["weighted_score"] = weighted_score
    passes = (
        review.get("passed") is True
        and review.get("material_defect") is False
    )
    if not passes:
        issues = review.get("issues") if isinstance(review.get("issues"), list) else []
        summary = "; ".join(str(issue) for issue in issues) or str(review.get("rationale") or "quality review failed")
        raise RuntimeError(
            "The sole generated image failed semantic quality review. Nothing was saved and "
            f"no replacement was generated in this run: {summary}"
        )
    return review


def _save_generated_image(
    target: Path,
    image_bytes: bytes,
    post: dict[str, Any],
    config: PipelineConfig,
    concept: dict[str, Any],
    quality_review: dict[str, Any],
    references: tuple[StyleReference, ...],
    prompt: str,
    dimensions: dict[str, int],
) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = target.with_suffix(target.suffix + ".tmp")
    try:
        temporary_path.write_bytes(image_bytes)
        temporary_path.replace(target)
        try:
            record_generated_image(
                post,
                target.name,
                image_bytes,
                renderer_model=config.openai_image_model,
                planner_model=config.openai_model,
                qa_model=config.openai_model,
                concept=concept,
                quality_review=quality_review,
                references=reference_manifest(references),
                prompt=prompt,
                dimensions=dimensions,
            )
        except Exception:
            target.unlink(missing_ok=True)
            raise
    finally:
        temporary_path.unlink(missing_ok=True)


def generate_missing_main_image(
    post: dict[str, Any],
    config: PipelineConfig,
    client: OpenAI | None = None,
) -> dict[str, Any]:
    if source_images(post):
        return {"action": "skipped_source_images"}

    target = generated_image_path(post)
    registration = ensure_generated_filename_available(target, post)
    if registration is not None:
        if not is_valid_prepared_png_file(target):
            raise RuntimeError(
                "The registered generated fallback is not a valid 16:9 PNG under "
                f"{MAX_GENERATED_IMAGE_BYTES:,} bytes."
            )
        return {
            "action": "reused",
            "path": str(target),
            "url": generated_image_url(post, config.image_public_ref),
        }

    if not config.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is missing.")

    prompts = load_prompts()
    image_client = client or OpenAI(
        api_key=config.openai_api_key,
        timeout=GENERATED_IMAGE_TIMEOUT_SECONDS,
        max_retries=0,
    )
    concept = plan_image_concept(post, config, image_client, prompts)
    references = ordered_references(list(concept["reference_ids"]))
    prompt = build_generation_prompt(concept, references)

    # Hard one-image invariant: this is the sole image-generation call in the run.
    raw_image_bytes = generate_one_raw_image(image_client, config, prompt, references)
    inspect_raw_image(raw_image_bytes)
    quality_review = review_raw_image(
        image_client, config, raw_image_bytes, concept, prompts
    )
    prepared_bytes, dimensions = prepare_blog_main_png(raw_image_bytes)
    if not is_valid_prepared_png_bytes(prepared_bytes):
        raise RuntimeError(
            "Prepared output is not a valid exact-16:9 PNG under "
            f"{MAX_GENERATED_IMAGE_BYTES:,} bytes."
        )
    _save_generated_image(
        target,
        prepared_bytes,
        post,
        config,
        concept,
        quality_review,
        references,
        prompt,
        dimensions,
    )
    print(
        "Generated and reviewed one fallback main image with "
        f"{config.openai_image_model}: {target.relative_to(GENERATED_IMAGE_DIR.parent.parent)}"
    )
    return {
        "action": "generated",
        "path": str(target),
        "url": generated_image_url(post, config.image_public_ref),
    }


def attach_generated_main_image(
    post: dict[str, Any], config: PipelineConfig
) -> dict[str, Any]:
    enriched = dict(post)
    if source_images(post):
        enriched.pop("generated_main_image", None)
        return enriched

    target = generated_image_path(post)
    if not target.is_file():
        raise RuntimeError(
            "This LinkedIn post has no source image and no generated fallback PNG. "
            "Run the image-preparation stage before the Webflow pipeline."
        )
    registration = validate_registered_generated_image(target, post_identity(post))
    if not is_valid_prepared_png_file(target):
        raise RuntimeError(
            "The registered generated fallback is not a valid exact-16:9 PNG under "
            f"{MAX_GENERATED_IMAGE_BYTES:,} bytes. Stopping before Webflow."
        )

    alt = soft_trim(sanitize_text(str(registration.get("alt") or "")), GENERATED_IMAGE_ALT_MAX)
    if not alt:
        raise RuntimeError("The registered generated fallback has no ALT text. Stopping before Webflow.")
    enriched["generated_main_image"] = {
        "url": generated_image_url(post, config.image_public_ref),
        "alt": alt,
    }
    return enriched


def wait_for_generated_image_public(
    post: dict[str, Any],
    config: PipelineConfig,
    request_get: Any | None = None,
    sleep_fn: Any | None = None,
) -> str:
    if source_images(post):
        return ""

    target = generated_image_path(post)
    registration = validate_registered_generated_image(target, post_identity(post))
    expected_hash = str(registration.get("sha256") or "")
    request_get = request_get or requests.get
    sleep_fn = sleep_fn or time.sleep
    url = generated_image_url(post, config.image_public_ref)
    last_problem = "unknown error"
    for attempt in range(1, PUBLIC_IMAGE_MAX_ATTEMPTS + 1):
        try:
            response = request_get(url, timeout=30)
            if (
                response.status_code == 200
                and is_valid_prepared_png_bytes(response.content)
                and image_sha256(response.content) == expected_hash
            ):
                print(f"Generated fallback image is publicly available: {url}")
                return url
            last_problem = f"HTTP {response.status_code} or invalid PNG/checksum response"
        except requests.RequestException as exc:
            last_problem = str(exc)

        if attempt < PUBLIC_IMAGE_MAX_ATTEMPTS:
            print(
                "Generated fallback image is not public yet "
                f"({last_problem}). Retrying in {PUBLIC_IMAGE_RETRY_SECONDS} seconds."
            )
            sleep_fn(PUBLIC_IMAGE_RETRY_SECONDS)

    raise RuntimeError(
        "The generated fallback PNG was committed but is not available from its public "
        f"GitHub URL: {last_problem}. Stopping before Webflow."
    )
