from __future__ import annotations

import base64
import json
import mimetypes
import os
from typing import Any

import requests
from openai import OpenAI

from .config import (
    LEGACY_POSTED_TWEETS_PATH,
    LEGACY_PROMPTS_PATH,
    LEGACY_TWEET_PATH,
    POSTED_TWEETS_PATH,
    PROMPTS_PATH,
    TWEET_PATH,
    PipelineConfig,
)
from .enrichment import completion_kwargs, response_text
from .utils import load_json, mirror_json, sanitize_text, soft_trim, strip_html_to_text, write_json


X_API_BASE_URL = "https://api.x.com/2"
TWEET_MAX_CHARS = 280


class XPostingError(RuntimeError):
    pass


def load_tweet_prompts() -> dict[str, str]:
    path = PROMPTS_PATH if PROMPTS_PATH.exists() else LEGACY_PROMPTS_PATH
    if not path.exists():
        raise RuntimeError(f"Missing prompts file: {PROMPTS_PATH}")
    doc = json.loads(path.read_text(encoding="utf-8"))
    prompt_sets = doc.get("tweet_generation")
    if not isinstance(prompt_sets, list) or not prompt_sets:
        raise RuntimeError("prompts.json must contain a non-empty tweet_generation array.")

    desired_id = os.getenv("TWEET_PROMPT_ID", "").strip()
    chosen = None
    if desired_id:
        chosen = next((item for item in prompt_sets if item.get("id") == desired_id), None)
    if chosen is None:
        chosen = prompt_sets[0]

    if not isinstance(chosen.get("tweet_system"), str) or not isinstance(chosen.get("tweet_user"), str):
        raise RuntimeError("Selected tweet prompt must contain tweet_system and tweet_user strings.")
    return {"tweet_system": chosen["tweet_system"], "tweet_user": chosen["tweet_user"]}


def replace_double_braces(template: str, mapping: dict[str, str]) -> str:
    output = template
    for key, value in mapping.items():
        output = output.replace("{{" + key + "}}", value)
    return output


def parse_tweet_response(response) -> dict[str, Any]:
    raw = response_text(response, "tweet JSON")
    if raw.startswith("```"):
        raw = raw.strip("`").strip()
        if raw.lower().startswith("json"):
            raw = raw[4:].strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"OpenAI returned invalid tweet JSON: {exc}. Raw output: {raw[:500]}") from exc
    if not isinstance(data, dict):
        raise RuntimeError("OpenAI returned tweet JSON that is not an object.")
    return data


def generate_tweet(post: dict[str, Any], config: PipelineConfig) -> dict[str, Any]:
    if not config.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is missing.")

    prompts = load_tweet_prompts()
    source_text = strip_html_to_text(post.get("content", ""))
    image_urls = [image.get("url") for image in post.get("images", []) or [] if isinstance(image, dict) and image.get("url")]
    image_urls = image_urls[:4]
    user_msg = replace_double_braces(
        prompts["tweet_user"],
        {
            "linkedin_post_text": source_text[:8000],
            "image_urls": "\n".join(image_urls) if image_urls else "None",
        },
    )

    user_content: list[dict[str, Any]] = [{"type": "text", "text": user_msg}]
    for url in image_urls:
        user_content.append({"type": "image_url", "image_url": {"url": url}})

    client = OpenAI(api_key=config.openai_api_key)
    response = client.chat.completions.create(
        **completion_kwargs(
            config,
            [
                {"role": "system", "content": prompts["tweet_system"]},
                {"role": "user", "content": user_content},
            ],
        )
    )
    data = parse_tweet_response(response)
    tweet_text = soft_trim(sanitize_text(str(data.get("tweet", ""))), TWEET_MAX_CHARS)
    if not tweet_text:
        raise RuntimeError(f"OpenAI returned tweet JSON without a non-empty tweet: {data}")

    allowed_urls = set(image_urls)
    selected_images = []
    for item in data.get("images", []) or []:
        if not isinstance(item, dict):
            continue
        url = item.get("url")
        if url in allowed_urls:
            selected_images.append({"url": url, "alt": sanitize_text(str(item.get("alt", "")))})

    return {
        "content": tweet_text,
        "url": post.get("url", ""),
        "published_at": post.get("published_at", ""),
        "images": selected_images,
    }


def load_posted_tweets() -> dict[str, Any]:
    data = load_json(POSTED_TWEETS_PATH, None)
    if data is None:
        data = load_json(LEGACY_POSTED_TWEETS_PATH, {"posted": []})
    if isinstance(data, list):
        data = {"posted": data}
    if not isinstance(data, dict) or not isinstance(data.get("posted"), list):
        data = {"posted": []}
    return data


def save_posted_tweets(data: dict[str, Any]) -> None:
    mirror_json(POSTED_TWEETS_PATH, LEGACY_POSTED_TWEETS_PATH, data)


def already_posted(posted_doc: dict[str, Any], linkedin_url: str) -> dict[str, Any] | None:
    for item in posted_doc.get("posted", []):
        if isinstance(item, dict) and item.get("linkedin_url") == linkedin_url:
            return item
    return None


def x_headers(access_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {access_token}"}


def download_image(url: str) -> tuple[bytes, str, str]:
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    content_type = response.headers.get("Content-Type") or mimetypes.guess_type(url)[0] or "image/jpeg"
    extension = mimetypes.guess_extension(content_type.split(";")[0].strip()) or ".jpg"
    return response.content, content_type, "image" + extension


def upload_media(access_token: str, image: dict[str, Any]) -> str:
    url = image.get("url")
    if not url:
        raise XPostingError("Image is missing a URL.")
    content, content_type, filename = download_image(url)
    response = requests.post(
        f"{X_API_BASE_URL}/media/upload",
        headers={**x_headers(access_token), "Content-Type": "application/json"},
        json={
            "media": base64.b64encode(content).decode("ascii"),
            "media_category": "tweet_image",
            "media_type": content_type.split(";")[0],
            "shared": False,
        },
        timeout=60,
    )
    if response.status_code >= 400:
        raise XPostingError(f"X media upload failed: {response.status_code} {response.text}")
    data = response.json()
    media_id = data.get("data", {}).get("id") or data.get("media_id_string") or data.get("media_id")
    if not media_id:
        raise XPostingError(f"X media upload response did not include an id: {data}")

    alt = sanitize_text(str(image.get("alt", "")))
    if alt:
        meta_response = requests.post(
            f"{X_API_BASE_URL}/media/metadata",
            headers={**x_headers(access_token), "Content-Type": "application/json"},
            json={"id": str(media_id), "metadata": {"alt_text": {"text": alt[:1000]}}},
            timeout=30,
        )
        if meta_response.status_code >= 400:
            print(f"X media metadata failed for {media_id}: {meta_response.status_code} {meta_response.text}")
    return str(media_id)


def create_post(access_token: str, tweet_text: str, media_ids: list[str]) -> dict[str, Any]:
    payload: dict[str, Any] = {"text": tweet_text}
    if media_ids:
        payload["media"] = {"media_ids": media_ids}
    response = requests.post(
        f"{X_API_BASE_URL}/tweets",
        headers={**x_headers(access_token), "Content-Type": "application/json"},
        json=payload,
        timeout=30,
    )
    if response.status_code >= 400:
        raise XPostingError(f"X post failed: {response.status_code} {response.text}")
    return response.json()


def post_to_x(tweet: dict[str, Any], config: PipelineConfig) -> dict[str, Any]:
    if not config.x_access_token:
        raise XPostingError("X_ACCESS_TOKEN is missing.")

    tweet_text = (tweet.get("content") or "").strip()
    linkedin_url = (tweet.get("url") or "").strip()
    if not tweet_text:
        raise XPostingError("Tweet content is empty.")

    posted_doc = load_posted_tweets()
    if linkedin_url and not config.force_x_post:
        existing = already_posted(posted_doc, linkedin_url)
        if existing:
            print("X post skipped: LinkedIn URL already appears in posted_tweets ledger.")
            return {"action": "skipped", "tweet_url": existing.get("tweet_url", "")}

    media_ids = []
    for image in tweet.get("images", []) or []:
        if len(media_ids) >= 4:
            break
        try:
            media_ids.append(upload_media(config.x_access_token, image))
        except Exception as exc:
            print(f"X image upload skipped: {exc}")

    response = create_post(config.x_access_token, tweet_text, media_ids)
    tweet_id = str(response.get("data", {}).get("id") or "")
    if not tweet_id:
        raise XPostingError(f"X post response did not include an id: {response}")

    tweet_url = f"https://x.com/i/web/status/{tweet_id}"
    posted_doc.setdefault("posted", []).append(
        {
            "linkedin_url": linkedin_url,
            "tweet_id": tweet_id,
            "tweet_url": tweet_url,
        }
    )
    save_posted_tweets(posted_doc)
    print(f"X post published: {tweet_url}")
    return {"action": "posted", "tweet_id": tweet_id, "tweet_url": tweet_url}
