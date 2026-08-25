from __future__ import annotations

import base64
import binascii
import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

import requests
from openai import OpenAI

from .config import IMAGE_DIR, PipelineConfig
from .enrichment import fill_placeholders, load_prompts
from .generated_images import (
    ensure_generated_filename_available,
    image_sha256,
    record_generated_image,
    validate_registered_generated_image,
)
from .utils import post_identity, sanitize_text, soft_trim, strip_html_to_text

GENERATED_IMAGE_SIZE = "1536x864"
GENERATED_IMAGE_QUALITY = "high"
GENERATED_IMAGE_FORMAT = "jpeg"
GENERATED_IMAGE_COMPRESSION = 90
GENERATED_IMAGE_TIMEOUT_SECONDS = 180.0
GENERATED_IMAGE_ALT_MAX = 180
MAX_IMAGE_PROMPT_CONTENT = 6000
MAX_WEBFLOW_IMAGE_BYTES = 4_000_000
PUBLIC_IMAGE_MAX_ATTEMPTS = 10
PUBLIC_IMAGE_RETRY_SECONDS = 5
RAW_REPOSITORY_URL = (
    "https://raw.githubusercontent.com/GiacomoIono/linkedin-posts-clean"
)
POST_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def source_images(post: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        image
        for image in post.get("images", []) or []
        if isinstance(image, dict) and str(image.get("url") or "").strip()
    ]


def linkedin_reports_image(post: dict[str, Any]) -> bool:
    return post.get("linkedin_has_image") is True


def missing_linkedin_source_image_error() -> RuntimeError:
    return RuntimeError(
        "LinkedIn reports that this post contains an image, but no matching source image "
        "exists at the top level of images/. Add the source file before publishing; an AI "
        "fallback will not replace LinkedIn media."
    )


def post_date(post: dict[str, Any]) -> str:
    value = str(post.get("published_at") or "")[:10]
    if not POST_DATE_RE.fullmatch(value):
        raise RuntimeError(
            "Cannot generate an image because the LinkedIn publication date is missing or invalid."
        )
    return value


def generated_image_filename(post: dict[str, Any]) -> str:
    return f"{post_date(post)}.jpeg"


def generated_image_path(post: dict[str, Any]) -> Path:
    return IMAGE_DIR / generated_image_filename(post)


def generated_image_url(post: dict[str, Any], public_ref: str) -> str:
    safe_ref = quote((public_ref or "main").strip() or "main", safe="/")
    filename = quote(generated_image_filename(post), safe="")
    return f"{RAW_REPOSITORY_URL}/{safe_ref}/images/{filename}"


def jpeg_dimensions(image_bytes: bytes) -> tuple[int, int] | None:
    if len(image_bytes) < 4 or not image_bytes.startswith(b"\xff\xd8"):
        return None

    index = 2
    start_of_frame_markers = {
        0xC0,
        0xC1,
        0xC2,
        0xC3,
        0xC5,
        0xC6,
        0xC7,
        0xC9,
        0xCA,
        0xCB,
        0xCD,
        0xCE,
        0xCF,
    }
    while index < len(image_bytes):
        if image_bytes[index] != 0xFF:
            index += 1
            continue
        while index < len(image_bytes) and image_bytes[index] == 0xFF:
            index += 1
        if index >= len(image_bytes):
            return None

        marker = image_bytes[index]
        index += 1
        if marker in {0x01, *range(0xD0, 0xD9)}:
            continue
        if marker in {0xD9, 0xDA} or index + 2 > len(image_bytes):
            return None

        segment_length = int.from_bytes(image_bytes[index : index + 2], "big")
        if segment_length < 2 or index + segment_length > len(image_bytes):
            return None
        if marker in start_of_frame_markers:
            if segment_length < 7:
                return None
            height = int.from_bytes(image_bytes[index + 3 : index + 5], "big")
            width = int.from_bytes(image_bytes[index + 5 : index + 7], "big")
            return width, height
        index += segment_length
    return None


def expected_image_dimensions() -> tuple[int, int]:
    width, height = GENERATED_IMAGE_SIZE.split("x", 1)
    return int(width), int(height)


def is_valid_jpeg_bytes(image_bytes: bytes) -> bool:
    return (
        len(image_bytes) <= MAX_WEBFLOW_IMAGE_BYTES
        and image_bytes.startswith(b"\xff\xd8\xff")
        and image_bytes.endswith(b"\xff\xd9")
        and jpeg_dimensions(image_bytes) == expected_image_dimensions()
    )


def is_valid_jpeg_file(path: Path) -> bool:
    try:
        return is_valid_jpeg_bytes(path.read_bytes())
    except OSError:
        return False


def article_headline(post: dict[str, Any], plain_text: str) -> str:
    headline = sanitize_text(str(post.get("headline") or ""))
    if headline:
        return headline
    first_line = next(
        (line.strip() for line in plain_text.splitlines() if line.strip()), ""
    )
    return soft_trim(first_line or "LinkedIn post", 160)


def build_image_prompt(post: dict[str, Any], prompts: dict[str, str]) -> str:
    plain_text = strip_html_to_text(post.get("content", ""))
    user_prompt = fill_placeholders(
        prompts["image_user"],
        {
            "HEADLINE": article_headline(post, plain_text),
            "CONTENT": plain_text[:MAX_IMAGE_PROMPT_CONTENT],
        },
    )
    return f"{prompts['image_system']}\n\n{user_prompt}"


def response_image_bytes(response: Any) -> bytes:
    data = getattr(response, "data", None)
    first = data[0] if isinstance(data, list) and data else None
    encoded = str(getattr(first, "b64_json", "") or "")
    if not encoded:
        raise RuntimeError("OpenAI returned no JPEG image data.")
    try:
        image_bytes = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise RuntimeError("OpenAI returned invalid base64 image data.") from exc
    if not is_valid_jpeg_bytes(image_bytes):
        raise RuntimeError(
            "OpenAI returned an invalid JPEG, the wrong dimensions, or a file larger than Webflow's 4 MB limit."
        )
    return image_bytes


def write_generated_jpeg_atomically(
    path: Path,
    image_bytes: bytes,
    post: dict[str, Any],
    model: str,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    try:
        temporary_path.write_bytes(image_bytes)
        record_generated_image(post, path.name, image_bytes, model)
        temporary_path.replace(path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def generate_missing_main_image(
    post: dict[str, Any],
    config: PipelineConfig,
    client: OpenAI | None = None,
) -> dict[str, Any]:
    if source_images(post):
        return {"action": "skipped_source_images"}
    if linkedin_reports_image(post):
        raise missing_linkedin_source_image_error()

    target = generated_image_path(post)
    registration = ensure_generated_filename_available(target, post)
    if registration is not None and is_valid_jpeg_file(target):
        return {
            "action": "reused",
            "path": str(target),
            "url": generated_image_url(post, config.image_public_ref),
        }

    if not config.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is missing.")

    prompts = load_prompts()
    prompt = build_image_prompt(post, prompts)
    image_client = client or OpenAI(
        api_key=config.openai_api_key,
        timeout=GENERATED_IMAGE_TIMEOUT_SECONDS,
    )
    response = image_client.images.generate(
        model=config.openai_image_model,
        prompt=prompt,
        n=1,
        size=GENERATED_IMAGE_SIZE,
        quality=GENERATED_IMAGE_QUALITY,
        output_format=GENERATED_IMAGE_FORMAT,
        output_compression=GENERATED_IMAGE_COMPRESSION,
        background="opaque",
    )
    image_bytes = response_image_bytes(response)
    write_generated_jpeg_atomically(
        target,
        image_bytes,
        post,
        config.openai_image_model,
    )
    print(
        "Generated one fallback main image with "
        f"{config.openai_image_model}: {target.relative_to(IMAGE_DIR.parent)}"
    )
    return {
        "action": "generated",
        "path": str(target),
        "url": generated_image_url(post, config.image_public_ref),
    }


def generated_image_alt(post: dict[str, Any]) -> str:
    plain_text = strip_html_to_text(post.get("content", ""))
    headline = article_headline(post, plain_text)
    return soft_trim(
        f"Editorial illustration representing {headline}", GENERATED_IMAGE_ALT_MAX
    )


def attach_generated_main_image(
    post: dict[str, Any], config: PipelineConfig
) -> dict[str, Any]:
    enriched = dict(post)
    if source_images(post):
        enriched.pop("generated_main_image", None)
        return enriched
    if linkedin_reports_image(post):
        raise missing_linkedin_source_image_error()

    target = generated_image_path(post)
    if not target.is_file():
        raise RuntimeError(
            "This LinkedIn post has no source image and no generated fallback JPEG. "
            "Run the image-preparation stage before the Webflow pipeline."
        )
    validate_registered_generated_image(target, post_identity(post))
    if not is_valid_jpeg_file(target):
        raise RuntimeError(
            "The registered generated fallback is not a valid 1536 x 864 JPEG. "
            "Stopping before Webflow."
        )

    enriched["generated_main_image"] = {
        "url": generated_image_url(post, config.image_public_ref),
        "alt": generated_image_alt(post),
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
    if linkedin_reports_image(post):
        raise missing_linkedin_source_image_error()

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
                and is_valid_jpeg_bytes(response.content)
                and image_sha256(response.content) == expected_hash
            ):
                print(f"Generated fallback image is publicly available: {url}")
                return url
            last_problem = f"HTTP {response.status_code} or invalid JPEG response"
        except requests.RequestException as exc:
            last_problem = str(exc)

        if attempt < PUBLIC_IMAGE_MAX_ATTEMPTS:
            print(
                "Generated fallback image is not public yet "
                f"({last_problem}). Retrying in {PUBLIC_IMAGE_RETRY_SECONDS} seconds."
            )
            sleep_fn(PUBLIC_IMAGE_RETRY_SECONDS)

    raise RuntimeError(
        "The generated fallback JPEG was committed but is not available from its public GitHub URL: "
        f"{last_problem}. Stopping before Webflow."
    )
