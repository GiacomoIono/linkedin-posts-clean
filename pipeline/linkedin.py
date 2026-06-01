from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

from .config import IMAGE_DIR


LINKEDIN_CHANGE_LOG_URL = "https://api.linkedin.com/rest/memberChangeLogs"
LINKEDIN_VERSION = "202312"
IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp")
RAW_IMAGE_BASE_URL = "https://raw.githubusercontent.com/GiacomoIono/linkedin-posts-clean/refs/heads/main/images/"
IMAGE_SEQUENCE_RE = re.compile(r"_(\d+)(?=\.[^.]+$)")


def image_filename_sort_key(filename: str) -> tuple[int, int, str]:
    match = IMAGE_SEQUENCE_RE.search(filename.lower())
    if not match:
        return (1, 0, filename)
    return (0, int(match.group(1)), filename)


def find_images_for_date(post_date: str) -> list[dict[str, str]]:
    if not IMAGE_DIR.is_dir():
        return []

    filenames = [
        item.name
        for item in IMAGE_DIR.iterdir()
        if item.is_file() and item.name.startswith(post_date) and item.name.lower().endswith(IMAGE_EXTENSIONS)
    ]
    filenames.sort(key=image_filename_sort_key)

    return [{"url": RAW_IMAGE_BASE_URL + filename, "alt": ""} for filename in filenames]


def paragraph_html(raw_text: str) -> str:
    paragraphs = []
    for paragraph in (raw_text or "").strip().split("\n\n"):
        cleaned = paragraph.strip()
        if not cleaned:
            continue
        paragraphs.append(f"<p>{cleaned.replace(chr(10), '<br>')}</p>")
        paragraphs.append("<p>&nbsp;</p>")
    return "".join(paragraphs)


def extract_post(element: dict[str, Any]) -> dict[str, Any] | None:
    if element.get("resourceName") != "ugcPosts" or element.get("method") != "CREATE":
        return None

    activity = element.get("activity", {})
    content = activity.get("specificContent", {}).get("com.linkedin.ugc.ShareContent", {})
    raw_text = content.get("shareCommentary", {}).get("text", "")
    timestamp = int(element.get("capturedAt") or 0)
    resource_id = element.get("resourceId", "")

    if not raw_text.strip() or not timestamp or not resource_id:
        return None

    published_at = datetime.fromtimestamp(timestamp / 1000, timezone.utc)
    post_date = published_at.strftime("%Y-%m-%d")

    return {
        "content": paragraph_html(raw_text),
        "url": f"https://www.linkedin.com/feed/update/{resource_id}",
        "published_at": published_at.isoformat().replace("+00:00", ""),
        "images": find_images_for_date(post_date),
    }


def fetch_latest_linkedin_post(access_token: str, lookback_hours: int = 48) -> dict[str, Any] | None:
    if not access_token:
        raise RuntimeError("LINKEDIN_ACCESS_TOKEN is missing.")

    start_time = int((datetime.now(timezone.utc) - timedelta(hours=lookback_hours)).timestamp() * 1000)
    headers = {
        "Authorization": f"Bearer {access_token}",
        "LinkedIn-Version": LINKEDIN_VERSION,
    }
    params = {
        "q": "memberAndApplication",
        "count": 500,
        "startTime": start_time,
    }

    response = requests.get(LINKEDIN_CHANGE_LOG_URL, headers=headers, params=params, timeout=30)
    print(f"LinkedIn API response status: {response.status_code}")
    if response.status_code != 200:
        raise RuntimeError(f"LinkedIn API failed: {response.status_code} {response.text}")

    latest_post = None
    latest_timestamp = -1
    for element in response.json().get("elements", []):
        post = extract_post(element)
        timestamp = int(element.get("capturedAt") or 0)
        if post and timestamp > latest_timestamp:
            latest_post = post
            latest_timestamp = timestamp

    return latest_post
